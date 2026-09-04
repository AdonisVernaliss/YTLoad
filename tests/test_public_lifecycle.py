from collections import namedtuple
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ytloadlib.models import AppConfig
from ytloadlib.runner import DownloadRunResult, FailureKind


DiskUsage = namedtuple('usage', 'total used free')


def wait_for(manager, identifier, statuses, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = next(item for item in manager.snapshot() if item['id'] == identifier)
        if job['status'] in statuses:
            return job
        time.sleep(.01)
    raise AssertionError(f'job {identifier} did not reach {statuses}')


class PublicResultLifecycleTests(unittest.TestCase):
    def test_finished_result_expires_independently_but_not_during_active_read(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_RESULT_TTL

        def download(request, url, config, **kwargs):
            path = Path(request.output_root) / 'sample.mp4'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'0123456789')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(path),))

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.1')
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                job = wait_for(state.jobs, identifier, {'completed'})
                self.assertIsInstance(job['finished_at'], float)
                path = state.begin_file(session, identifier, 0)
                with patch('ytloadlib.hosted_state.time.time', return_value=job['finished_at'] + PUBLIC_RESULT_TTL + 1):
                    state.cleanup()
                self.assertTrue(path.exists())
                self.assertTrue(state.snapshot(session)[0]['files'])
                state.end_file(session, identifier)
                with patch('ytloadlib.hosted_state.time.time', return_value=job['finished_at'] + PUBLIC_RESULT_TTL + 1):
                    state.cleanup()
                expired = state.snapshot(session)[0]
                self.assertTrue(expired['result_expired'])
                self.assertEqual(expired['files'], [])
                self.assertFalse(path.exists())
                with self.assertRaises(ValueError):
                    state.begin_file(session, identifier, 0)
            finally:
                state.close()

    def test_result_remains_available_before_ttl(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_RESULT_TTL

        def download(request, url, config, **kwargs):
            path = Path(request.output_root) / 'sample.mp4'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'ok')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(path),))

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.2')
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                job = wait_for(state.jobs, identifier, {'completed'})
                with patch('ytloadlib.hosted_state.time.time', return_value=job['finished_at'] + PUBLIC_RESULT_TTL - 1):
                    state.cleanup()
                self.assertTrue(state.begin_file(session, identifier, 0).is_file())
                state.end_file(session, identifier)
            finally:
                state.close()


class PublicRetryTests(unittest.TestCase):
    def test_failed_partial_is_reused_by_retry_until_expiry(self):
        from ytloadlib.hosted_state import PublicState

        roots = []
        calls = 0
        def download(request, url, config, **kwargs):
            nonlocal calls
            calls += 1
            root = Path(request.output_root)
            root.mkdir(parents=True, exist_ok=True)
            roots.append(root)
            partial = root / 'sample.part'
            if calls == 1:
                partial.write_bytes(b'partial')
                return DownloadRunResult(False, 1, 'failed', 1, FailureKind.NETWORK, (), ())
            self.assertTrue(partial.exists())
            result = root / 'sample.mp4'
            result.write_bytes(b'complete')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(result),))

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.7')
                first = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                wait_for(state.jobs, first, {'failed'})
                second = state.retry(session, first)[0]
                wait_for(state.jobs, second, {'completed'})
                self.assertEqual(roots[0], roots[1])
            finally:
                state.close()



    def test_expiring_failed_retry_parent_keeps_shared_files(self):
        from ytloadlib.hosted_state import PublicState

        release = threading.Event()
        calls = 0
        def download(request, url, config, **kwargs):
            nonlocal calls
            calls += 1
            root = Path(request.output_root)
            root.mkdir(parents=True, exist_ok=True)
            result = root / 'completed-before-failure.mp4'
            result.write_bytes(b'complete')
            if calls == 1:
                return DownloadRunResult(False, 1, 'failed', 1, FailureKind.NETWORK, (), (str(result),))
            release.wait(2)
            return DownloadRunResult(True, 0, '', 1, None, (), (str(result),))

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.8')
                first = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                parent = wait_for(state.jobs, first, {'failed'})
                second = state.retry(session, first)[0]
                wait_for(state.jobs, second, {'running'})
                state.jobs.expire(first, 'expired')
                self.assertTrue(Path(parent['files'][0]).is_file())
                release.set()
                wait_for(state.jobs, second, {'completed'})
            finally:
                release.set()
                state.close()


class PublicSessionAbandonmentTests(unittest.TestCase):
    def test_abandonment_cancels_running_and_queued_jobs_then_purges_session(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_SESSION_TTL

        started = threading.Event()
        cancelled = threading.Event()
        release = threading.Event()

        def download(request, url, config, cancel_event=None, **kwargs):
            path = Path(request.output_root) / 'partial.part'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'partial')
            started.set()
            cancel_event.wait(2)
            cancelled.set()
            release.wait(2)
            return DownloadRunResult(False, 130, 'Cancelled', 1, FailureKind.CANCELLED, (), ())

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.20')
                identifiers = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-', 'https://youtu.be/ZyXwVu98765']})
                self.assertTrue(started.wait(1))
                session.touched = time.monotonic() - PUBLIC_SESSION_TTL - 1
                state.cleanup()
                statuses = {job['id']: job['status'] for job in state.jobs.snapshot()}
                self.assertTrue(cancelled.wait(1))
                self.assertIn(statuses[identifiers[0]], {'cancelling', 'cancelled'})
                self.assertEqual(statuses[identifiers[1]], 'cancelled')
                self.assertTrue(session.abandoned)
                self.assertIsNone(state.session(session.identifier))
                release.set()
                for identifier in identifiers:
                    wait_for(state.jobs, identifier, {'cancelled'})
                state.cleanup()
                jobs = {job['id']: job for job in state.jobs.snapshot()}
                self.assertTrue(all(jobs[identifier]['result_expired'] for identifier in identifiers))
                self.assertNotIn(session.identifier, state.sessions)
                self.assertFalse(session.root.exists())
            finally:
                release.set()
                state.close()

    def test_abandoned_running_partial_is_deleted_after_cancellation(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_SESSION_TTL

        started = threading.Event()
        cancelled = threading.Event()
        release = threading.Event()

        def download(request, url, config, cancel_event=None, **kwargs):
            path = Path(request.output_root) / 'partial.part'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'partial')
            started.set()
            cancel_event.wait(2)
            cancelled.set()
            release.wait(2)
            return DownloadRunResult(False, 130, 'Cancelled', 1, FailureKind.CANCELLED, (), ())

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.21')
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                self.assertTrue(started.wait(1))
                output_root = Path(next(job for job in state.jobs.snapshot() if job['id'] == identifier)['output_root'])
                session.touched = time.monotonic() - PUBLIC_SESSION_TTL - 1
                state.cleanup()
                self.assertTrue(cancelled.wait(1))
                self.assertTrue((output_root / 'partial.part').is_file())
                release.set()
                wait_for(state.jobs, identifier, {'cancelled'})
                state.cleanup()
                job = next(job for job in state.jobs.snapshot() if job['id'] == identifier)
                self.assertTrue(job['result_expired'])
                self.assertFalse(output_root.exists())
                self.assertFalse(session.root.exists())
            finally:
                release.set()
                state.close()

    def test_active_reader_delays_abandonment_until_transfer_ends(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_SESSION_TTL

        def download(request, url, config, **kwargs):
            path = Path(request.output_root) / 'sample.mp4'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'0123456789')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(path),))

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.22')
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                wait_for(state.jobs, identifier, {'completed'})
                path = state.begin_file(session, identifier, 0)
                session.touched = time.monotonic() - PUBLIC_SESSION_TTL - 1
                state.cleanup()
                self.assertFalse(session.abandoned)
                self.assertIn(session.identifier, state.sessions)
                self.assertTrue(path.is_file())
                self.assertTrue(state.snapshot(session)[0]['files'])
                state.end_file(session, identifier)
                state.cleanup()
                self.assertNotIn(session.identifier, state.sessions)
                self.assertFalse(path.exists())
                self.assertIsNone(state.session(session.identifier))
            finally:
                state.close()

    def test_abandoned_session_cannot_be_revived_while_job_stops(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_SESSION_TTL

        started = threading.Event()
        cancelled = threading.Event()
        release = threading.Event()

        def download(request, url, config, cancel_event=None, **kwargs):
            started.set()
            cancel_event.wait(2)
            cancelled.set()
            release.wait(2)
            return DownloadRunResult(False, 130, 'Cancelled', 1, FailureKind.CANCELLED, (), ())

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.23')
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                self.assertTrue(started.wait(1))
                session.touched = time.monotonic() - PUBLIC_SESSION_TTL - 1
                state.cleanup()
                self.assertTrue(cancelled.wait(1))
                touched = session.touched
                self.assertIsNone(state.session(session.identifier))
                self.assertEqual(session.touched, touched)
                release.set()
                wait_for(state.jobs, identifier, {'cancelled'})
                state.cleanup()
            finally:
                release.set()
                state.close()

    def test_abandonment_preserves_retry_root_until_running_retry_stops(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.public_policy import PUBLIC_SESSION_TTL

        retry_started = threading.Event()
        retry_cancelled = threading.Event()
        release = threading.Event()
        calls = 0

        def download(request, url, config, cancel_event=None, **kwargs):
            nonlocal calls
            calls += 1
            root = Path(request.output_root)
            root.mkdir(parents=True, exist_ok=True)
            partial = root / 'shared.part'
            partial.write_bytes(b'partial')
            if calls == 1:
                return DownloadRunResult(False, 1, 'failed', 1, FailureKind.NETWORK, (), ())
            retry_started.set()
            cancel_event.wait(2)
            retry_cancelled.set()
            release.wait(2)
            return DownloadRunResult(False, 130, 'Cancelled', 1, FailureKind.CANCELLED, (), ())

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.24')
                parent = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                parent_job = wait_for(state.jobs, parent, {'failed'})
                retry = state.retry(session, parent)[0]
                self.assertTrue(retry_started.wait(1))
                shared_root = Path(parent_job['output_root'])
                session.touched = time.monotonic() - PUBLIC_SESSION_TTL - 1
                state.cleanup()
                self.assertTrue(retry_cancelled.wait(1))
                self.assertTrue(shared_root.is_dir())
                self.assertTrue((shared_root / 'shared.part').is_file())
                self.assertIsNone(state.session(session.identifier))
                release.set()
                wait_for(state.jobs, retry, {'cancelled'})
                state.cleanup()
                jobs = {job['id']: job for job in state.jobs.snapshot()}
                self.assertTrue(jobs[parent]['result_expired'])
                self.assertTrue(jobs[retry]['result_expired'])
                self.assertFalse(shared_root.exists())
                self.assertFalse(session.root.exists())
            finally:
                release.set()
                state.close()


class PublicResourceTests(unittest.TestCase):
    def test_policy_values_and_storage_quotas(self):
        import ytloadlib.hosted_state as hosted_state
        from ytloadlib.public_policy import PUBLIC_FILE_BYTES, PUBLIC_JOB_MAX_SECONDS, PUBLIC_SESSION_BYTES, PUBLIC_TOTAL_BYTES

        self.assertEqual(PUBLIC_FILE_BYTES, 5 * 1024 ** 3)
        self.assertEqual(PUBLIC_SESSION_BYTES, 12 * 1024 ** 3)
        self.assertEqual(PUBLIC_TOTAL_BYTES, 20 * 1024 ** 3)
        self.assertEqual(PUBLIC_JOB_MAX_SECONDS, 3 * 60 * 60)
        with tempfile.TemporaryDirectory() as directory:
            state = hosted_state.PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.3')
                session.root.mkdir(parents=True)
                (session.root / 'held.bin').write_bytes(b'12345')
                with patch.object(hosted_state, 'SESSION_BYTES', 5):
                    with self.assertRaisesRegex(ValueError, 'Temporary storage is full'):
                        state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})
                with patch.object(hosted_state, 'SESSION_BYTES', 100), patch.object(hosted_state, 'TOTAL_BYTES', 5):
                    with self.assertRaisesRegex(ValueError, 'Temporary storage is full'):
                        state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})
            finally:
                state.close()

    def test_heavy_job_requires_starting_disk_reserve_but_metadata_does_not(self):
        import ytloadlib.hosted_state as hosted_state

        success = DownloadRunResult(True, 0, '', 1, None, (), ())
        low = DiskUsage(100, 89, 11)
        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', return_value=success), \
                patch.object(hosted_state, 'PUBLIC_START_FREE_BYTES', 12), patch('ytloadlib.hosted_state.shutil.disk_usage', return_value=low):
            state = hosted_state.PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.4')
                with self.assertRaisesRegex(ValueError, 'free disk space'):
                    state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-'], 'mode': 'video'})
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-'], 'mode': 'metadata'})[0]
                wait_for(state.jobs, identifier, {'completed'})
            finally:
                state.close()

    def test_low_disk_cancels_and_purges_running_job(self):
        import ytloadlib.hosted_state as hosted_state

        started = threading.Event()
        def download(request, url, config, cancel_event=None, **kwargs):
            path = Path(request.output_root) / 'partial.part'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'partial')
            started.set()
            cancel_event.wait(2)
            return DownloadRunResult(False, 130, 'Cancelled', 1, FailureKind.CANCELLED, (), ())

        high = DiskUsage(100, 20, 80)
        low = DiskUsage(100, 96, 4)
        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download), \
                patch.object(hosted_state, 'PUBLIC_START_FREE_BYTES', 20), patch.object(hosted_state, 'PUBLIC_CRITICAL_FREE_BYTES', 5), \
                patch('ytloadlib.hosted_state.shutil.disk_usage', return_value=high):
            state = hosted_state.PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.5')
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                self.assertTrue(started.wait(1))
                with patch('ytloadlib.hosted_state.shutil.disk_usage', return_value=low):
                    state.cleanup()
                    wait_for(state.jobs, identifier, {'cancelled'})
                    state.cleanup()
                job = next(item for item in state.jobs.snapshot() if item['id'] == identifier)
                self.assertTrue(job['result_expired'])
                self.assertFalse(any(session.root.rglob('partial.part')))
            finally:
                state.close()

    def test_runtime_is_measured_from_start_and_allows_more_than_twenty_minutes(self):
        import ytloadlib.hosted_state as hosted_state
        from ytloadlib.public_policy import PUBLIC_JOB_MAX_SECONDS

        started = threading.Event()
        def download(request, url, config, cancel_event=None, **kwargs):
            started.set()
            cancel_event.wait(2)
            return DownloadRunResult(False, 130, 'Cancelled', 1, FailureKind.CANCELLED, (), ())

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            state = hosted_state.PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.6')
                identifier = state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})[0]
                self.assertTrue(started.wait(1))
                job = next(item for item in state.jobs.snapshot() if item['id'] == identifier)
                with patch('ytloadlib.hosted_state.time.time', return_value=job['started_at'] + 20 * 60 + 1):
                    state.cleanup()
                self.assertEqual(next(item for item in state.jobs.snapshot() if item['id'] == identifier)['status'], 'running')
                with patch('ytloadlib.hosted_state.time.time', return_value=job['started_at'] + PUBLIC_JOB_MAX_SECONDS + 1):
                    state.cleanup()
                wait_for(state.jobs, identifier, {'cancelled'})
            finally:
                state.close()


class SerialQueueTests(unittest.TestCase):
    def test_only_one_real_download_runs_at_a_time(self):
        from ytloadlib.jobs import JobManager

        release = threading.Event()
        first_started = threading.Event()
        running = 0
        maximum = 0
        guard = threading.Lock()

        def download(request, url, config, **kwargs):
            nonlocal running, maximum
            with guard:
                running += 1
                maximum = max(maximum, running)
            first_started.set()
            release.wait(1)
            with guard:
                running -= 1
            return DownloadRunResult(True, 0, '', 1, None, (), ())

        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download):
            manager = JobManager(AppConfig(output_root=directory))
            try:
                ids = manager.add({'urls': ['https://youtu.be/AbCdEf123_-', 'https://youtu.be/ZyXwVu98765']})
                self.assertTrue(first_started.wait(1))
                states = {item['id']: item['status'] for item in manager.snapshot()}
                self.assertEqual(sum(value == 'running' for value in states.values()), 1)
                self.assertEqual(sum(value == 'queued' for value in states.values()), 1)
                release.set()
                for identifier in ids:
                    wait_for(manager, identifier, {'completed'})
                self.assertEqual(maximum, 1)
            finally:
                release.set()
                manager.close()


if __name__ == '__main__':
    unittest.main()
