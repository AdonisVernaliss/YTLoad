import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ytloadlib.models import AppConfig
from ytloadlib.runner import DownloadRunResult


class FakeDelivery:
    def __init__(self):
        self.records = {}
        self.submitted = []
        self.cancelled = []
        self.expired = []
        self.discarded = []

    def submit(self, identifier, paths):
        self.submitted.append((identifier, tuple(paths)))
        self.records.setdefault(identifier, {
            'status': 'waiting_delivery', 'ready_at': None, 'expires_at': None,
            'error': None, 'files': [], 'uses_local_files': False,
        })

    def snapshot(self, identifier):
        value = self.records.get(identifier)
        return dict(value) if value else None

    def cancel(self, identifier):
        self.cancelled.append(identifier)
        if identifier in self.records:
            self.records[identifier]['status'] = 'cancelled'

    def retry(self, identifier):
        self.records[identifier]['status'] = 'waiting_delivery'

    def expire(self, identifier):
        self.expired.append(identifier)
        if identifier in self.records:
            self.records[identifier]['status'] = 'expired'

    def discard(self, identifier):
        value = self.records.get(identifier)
        if not value or value['status'] != 'expired':
            return False
        del self.records[identifier]
        self.discarded.append(identifier)
        return True

    def discard_expired(self):
        identifiers = [identifier for identifier, value in self.records.items() if value['status'] == 'expired']
        for identifier in identifiers:
            del self.records[identifier]
            self.discarded.append(identifier)
        return len(identifiers)

    def close(self):
        pass


def wait_for_job(state, session, status='completed'):
    for _ in range(100):
        jobs = state.jobs.snapshot()
        if jobs and jobs[0]['status'] == status:
            state.cleanup()
            return jobs[0]['id']
        time.sleep(.01)
    raise AssertionError('job did not finish')


class HostedDeliveryLifecycleTests(unittest.TestCase):
    def test_waiting_delivery_does_not_consume_result_ttl(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_RESULT_TTL

        delivery = FakeDelivery()
        finished = time.time() - PUBLIC_RESULT_TTL - 10

        def download(request, url, config, **kwargs):
            path = Path(request.output_root) / 'sample.mp4'
            path.write_bytes(b'1234')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(path),))

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory), delivery=delivery)
            try:
                session = state.new_session('203.0.113.30')
                state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})
                identifier = wait_for_job(state, session)
                with state.jobs._condition:
                    state.jobs._jobs[identifier]['finished_at'] = finished
                state.cleanup()
                job = state.snapshot(session)[0]
                self.assertEqual(job['status'], 'waiting_delivery')
                self.assertFalse(job['result_expired'])
                self.assertEqual(job['files'], [])
            finally:
                state.close()

    def test_ready_ttl_starts_at_successful_publication(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_RESULT_TTL

        delivery = FakeDelivery()

        def download(request, url, config, **kwargs):
            path = Path(request.output_root) / 'sample.mp4'
            path.write_bytes(b'1234')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(path),))

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory), delivery=delivery)
            try:
                session = state.new_session('203.0.113.31')
                state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})
                identifier = wait_for_job(state, session)
                ready_at = time.time()
                delivery.records[identifier].update({
                    'status': 'ready', 'ready_at': ready_at,
                    'expires_at': ready_at + PUBLIC_RESULT_TTL,
                    'files': [{'name': 'sample.mp4', 'size': 4, 'url': '/ytload/media/ticket'}],
                })
                with patch('ytloadlib.hosted_state.time.time', return_value=ready_at + PUBLIC_RESULT_TTL - 1):
                    state.cleanup()
                self.assertFalse(state.snapshot(session)[0]['result_expired'])
                with patch('ytloadlib.hosted_state.time.time', return_value=ready_at + PUBLIC_RESULT_TTL + 1):
                    state.cleanup()
                self.assertTrue(state.snapshot(session)[0]['result_expired'])
                self.assertIn(identifier, delivery.expired)
                state.cleanup()
                self.assertIn(identifier, delivery.discarded)
            finally:
                state.close()

    def test_hosted_r2_mode_never_opens_local_file_delivery(self):
        from ytloadlib.hosted_state import PublicState

        delivery = FakeDelivery()
        with tempfile.TemporaryDirectory() as directory:
            state = PublicState(Path(directory), delivery=delivery)
            try:
                session = state.new_session('203.0.113.32')
                session.jobs.add('missing')
                with self.assertRaisesRegex(LookupError, 'cloud delivery'):
                    state.begin_file(session, 'missing', 0)
            finally:
                state.close()


if __name__ == '__main__':
    unittest.main()
