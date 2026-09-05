import json
import tempfile
import threading
import time
import unittest
from pathlib import Path


class DeferredError(Exception):
    pass


class FakeControl:
    def __init__(self):
        self.calls = []
        self.defer = False
        self.fail_complete = False
        self.bad_publication = False

    def reconcile(self):
        self.calls.append(('reconcile',))

    def reserve(self, batch_id, files):
        self.calls.append(('reserve', batch_id, files))
        if self.defer:
            from ytloadlib.r2_delivery import DeliveryDeferred
            raise DeliveryDeferred('Hosted delivery is at capacity.', 0.01)

    def register_multipart(self, batch_id, key, upload_id):
        self.calls.append(('multipart', batch_id, key, upload_id))

    def touch(self, batch_id):
        self.calls.append(('touch', batch_id))

    def complete_file(self, batch_id, key, size):
        self.calls.append(('complete', batch_id, key, size))
        if self.fail_complete:
            raise OSError('control unavailable')

    def commit(self, batch_id):
        self.calls.append(('commit', batch_id))
        now = time.time()
        return {
            'ready_at': now,
            'expires_at': now + 3600,
            'files': [{'name': 'sample.mp4', 'size': 11 if self.bad_publication else 10, 'url': '/ytload/media/signed-ticket'}],
        }

    def cancel(self, batch_id, safe_unregistered_keys=None, safe_unregistered=False):
        self.calls.append(('cancel', batch_id, tuple(safe_unregistered_keys or ()), safe_unregistered))


class FakeStore:
    def __init__(self):
        self.calls = []
        self.fail_part = False
        self.part_started = threading.Event()
        self.part_release = threading.Event()
        self.block_part = False

    def create(self, item):
        self.calls.append(('create', item.key))
        return 'upload-1'

    def upload_part(self, item, upload_id, number, reader, length):
        self.calls.append(('part', item.key, upload_id, number, length, reader.read()))
        self.part_started.set()
        if self.block_part:
            self.part_release.wait(2)
        if self.fail_part:
            raise OSError('upload failed')
        return f'etag-{number}'

    def complete(self, item, upload_id, parts):
        self.calls.append(('finish', item.key, upload_id, parts))

    def abort(self, item, upload_id):
        self.calls.append(('abort', item.key, upload_id))


class DeliveryConfigurationTests(unittest.TestCase):
    def test_cloud_support_is_optional_but_partial_configuration_fails(self):
        from ytloadlib.r2_delivery import R2DeliveryConfig

        self.assertIsNone(R2DeliveryConfig.from_environ({}))
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            R2DeliveryConfig.from_environ({'YTLOAD_R2_BUCKET': 'media'})

    def test_present_but_blank_cloud_configuration_fails_closed(self):
        from ytloadlib.r2_delivery import R2DeliveryConfig

        names = (
            'YTLOAD_R2_ENDPOINT_URL', 'YTLOAD_R2_BUCKET', 'YTLOAD_R2_ACCESS_KEY_ID',
            'YTLOAD_R2_SECRET_ACCESS_KEY', 'YTLOAD_MEDIA_CONTROL_URL', 'YTLOAD_MEDIA_CONTROL_SECRET',
        )
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            R2DeliveryConfig.from_environ({name: ' ' for name in names})


class MultipartTests(unittest.TestCase):
    def test_bounded_reader_stops_when_cancelled(self):
        from io import BytesIO
        from ytloadlib.r2_delivery import BoundedReader, DeliveryCancelled

        cancel = threading.Event()
        reader = BoundedReader(BytesIO(b'0123456789'), 0, 10, cancel=cancel)
        self.assertEqual(reader.read(2), b'01')
        cancel.set()
        with self.assertRaises(DeliveryCancelled):
            reader.read(2)

    def test_failure_aborts_registered_multipart(self):
        from ytloadlib.r2_delivery import MultipartUploader, UploadItem

        control = FakeControl()
        store = FakeStore()
        store.fail_part = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.mp4'
            path.write_bytes(b'0123456789')
            item = UploadItem('media/a/file', path, 'sample.mp4', 'video/mp4', 10)
            uploader = MultipartUploader(control, store, part_bytes=6, attempts=1, sleep=lambda _: None)
            with self.assertRaisesRegex(OSError, 'upload failed'):
                uploader.upload('a' * 32, item, threading.Event())
        self.assertIn(('abort', item.key, 'upload-1'), store.calls)
        self.assertNotIn(('complete', 'a' * 32, item.key, 10), control.calls)

    def test_cancel_during_upload_aborts_before_completion(self):
        from ytloadlib.r2_delivery import DeliveryCancelled, MultipartUploader, UploadItem

        control = FakeControl()
        store = FakeStore()
        store.block_part = True
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.mp4'
            path.write_bytes(b'0123456789')
            item = UploadItem('media/a/file', path, 'sample.mp4', 'video/mp4', 10)
            uploader = MultipartUploader(control, store, part_bytes=10, attempts=1, sleep=lambda _: None)
            errors = []
            thread = threading.Thread(target=lambda: self._capture(errors, uploader, item, cancel))
            thread.start()
            self.assertTrue(store.part_started.wait(1))
            cancel.set()
            store.part_release.set()
            thread.join(2)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], DeliveryCancelled)
        self.assertIn(('abort', item.key, 'upload-1'), store.calls)
        self.assertFalse(any(call[0] == 'finish' for call in store.calls))

    @staticmethod
    def _capture(errors, uploader, item, cancel):
        try:
            uploader.upload('a' * 32, item, cancel)
        except Exception as exc:
            errors.append(exc)


class DeliveryManagerTests(unittest.TestCase):
    def test_expired_terminal_record_can_be_discarded(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            manager = DeliveryManager(Path(directory), control, store, attempts=1)
            try:
                with manager.condition:
                    manager.records['r' * 32] = {'status': 'expired'}
                    manager.records['s' * 32] = {'status': 'ready'}
                self.assertTrue(manager.discard('r' * 32))
                self.assertFalse(manager.discard('s' * 32))
                self.assertIsNone(manager.snapshot('r' * 32))
                self.assertIsNotNone(manager.snapshot('s' * 32))
            finally:
                manager.close()

    def test_startup_reconciles_and_cleans_journaled_batches(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / '.ytload-r2-delivery.json'
            journal.write_text(json.dumps({'batches': ['b' * 32]}))
            manager = DeliveryManager(Path(directory), control, store, part_bytes=8, attempts=1)
            try:
                self.assertTrue(manager.wait_until_reconciled(1))
            finally:
                manager.close()
        self.assertTrue(any(call[0] == 'cancel' and call[1] == 'b' * 32 for call in control.calls))
        self.assertIn(('reconcile',), control.calls)

    def test_success_publishes_one_hour_ticket_without_exposing_local_path(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'job' / 'sample.mp4'
            path.parent.mkdir()
            path.write_bytes(b'0123456789')
            manager = DeliveryManager(root, control, store, part_bytes=6, attempts=1)
            try:
                manager.submit('j' * 32, [path])
                record = manager.wait_for('j' * 32, {'ready'}, 2)
            finally:
                manager.close()
        self.assertEqual(record['status'], 'ready')
        self.assertAlmostEqual(record['expires_at'] - record['ready_at'], 3600, delta=2)
        self.assertEqual(record['files'][0]['url'], '/ytload/media/signed-ticket')
        self.assertNotIn(directory, json.dumps(record))

    def test_cancelled_manager_aborts_and_releases_cloud_reservation(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        store = FakeStore()
        store.block_part = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'job' / 'sample.mp4'
            path.parent.mkdir()
            path.write_bytes(b'0123456789')
            manager = DeliveryManager(root, control, store, part_bytes=10, attempts=1)
            try:
                manager.submit('k' * 32, [path])
                self.assertTrue(store.part_started.wait(1))
                manager.cancel('k' * 32)
                store.part_release.set()
                record = manager.wait_for('k' * 32, {'cancelled'}, 2)
            finally:
                store.part_release.set()
                manager.close()
        self.assertEqual(record['status'], 'cancelled')
        self.assertTrue(any(call[0] == 'abort' for call in store.calls))
        self.assertTrue(any(call[0] == 'cancel' for call in control.calls))

    def test_close_waits_for_cancelled_body_reader_to_stop(self):
        from ytloadlib.r2_delivery import DeliveryManager

        class StreamingStore(FakeStore):
            def upload_part(self, item, upload_id, number, reader, length):
                self.part_started.set()
                while True:
                    reader.read(1)
                    time.sleep(.005)

        control = FakeControl()
        store = StreamingStore()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'job' / 'sample.mp4'
            path.parent.mkdir()
            path.write_bytes(b'0123456789')
            manager = DeliveryManager(root, control, store, part_bytes=10, attempts=1)
            manager.submit('t' * 32, [path])
            self.assertTrue(store.part_started.wait(1))
            manager.close()
            self.assertFalse(manager.worker.is_alive())
        self.assertTrue(any(call[0] == 'abort' for call in store.calls))
        self.assertTrue(any(call[0] == 'cancel' for call in control.calls))

    def test_failure_after_object_completion_requests_worker_cleanup(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        control.fail_complete = True
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'job' / 'sample.mp4'
            path.parent.mkdir()
            path.write_bytes(b'0123456789')
            manager = DeliveryManager(root, control, store, part_bytes=10, attempts=1)
            try:
                manager.submit('m' * 32, [path])
                record = manager.wait_for('m' * 32, {'failed'}, 2)
            finally:
                manager.close()
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(any(call[0] == 'cancel' for call in control.calls))



    def test_unavailable_capacity_stays_waiting_without_starting_r2_upload(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        control.defer = True
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'job' / 'sample.mp4'
            path.parent.mkdir()
            path.write_bytes(b'0123456789')
            manager = DeliveryManager(root, control, store, part_bytes=10, attempts=1)
            try:
                manager.submit('p' * 32, [path])
                for _ in range(100):
                    if any(call[0] == 'reserve' for call in control.calls):
                        break
                    time.sleep(.01)
                record = manager.snapshot('p' * 32)
                self.assertEqual(record['status'], 'waiting_delivery')
                self.assertIsNone(record['expires_at'])
                self.assertFalse(any(call[0] == 'create' for call in store.calls))
                manager.cancel('p' * 32)
                manager.wait_for('p' * 32, {'cancelled'}, 2)
            finally:
                manager.close()

    def test_untrusted_publication_metadata_is_rejected(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        control.bad_publication = True
        store = FakeStore()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'job' / 'sample.mp4'
            path.parent.mkdir()
            path.write_bytes(b'0123456789')
            manager = DeliveryManager(root, control, store, part_bytes=10, attempts=1)
            try:
                manager.submit('q' * 32, [path])
                record = manager.wait_for('q' * 32, {'failed'}, 2)
            finally:
                manager.close()
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['files'], [])

    def test_cancel_confirms_files_never_started_or_locally_aborted(self):
        from ytloadlib.r2_delivery import DeliveryManager

        control = FakeControl()
        store = FakeStore()
        store.block_part = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / 'job' / 'one.mp4'
            second = root / 'job' / 'two.mp4'
            first.parent.mkdir()
            first.write_bytes(b'0123456789')
            second.write_bytes(b'abcdefghij')
            manager = DeliveryManager(root, control, store, part_bytes=10, attempts=1)
            try:
                manager.submit('n' * 32, [first, second])
                self.assertTrue(store.part_started.wait(1))
                manager.cancel('n' * 32)
                store.part_release.set()
                manager.wait_for('n' * 32, {'cancelled'}, 2)
            finally:
                store.part_release.set()
                manager.close()
        reservation = next(call for call in control.calls if call[0] == 'reserve')
        reserved_keys = {item['key'] for item in reservation[2]}
        cancel = next(call for call in control.calls if call[0] == 'cancel')
        self.assertEqual(set(cancel[2]), reserved_keys)
        self.assertFalse(cancel[3])



if __name__ == '__main__':
    unittest.main()
