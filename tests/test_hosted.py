import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ytloadlib.models import AppConfig
from ytloadlib.validation import request_from_payload


class HostedPolicyTests(unittest.TestCase):
    def test_public_requests_reject_local_access_and_untrusted_sources(self):
        from ytloadlib.hosted import prepare_public_request
        for extra in ({'browser': 'chrome'}, {'output_root': '/private'}, {'write_comments': True},
                      {'urls': ['http://127.0.0.1/video']}, {'urls': ['https://youtube.com.attacker.test/watch?v=AbCdEf123_-']},
                      {'urls': ['https://www.youtube.com/redirect?q=http://127.0.0.1']}, {'playlist_items': '1:50000'},
                      {'playlist_items': '-1'}, {'max_filesize': '2G'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                request = request_from_payload({'urls': ['https://youtu.be/AbCdEf123_-'], **extra}, AppConfig(output_root='/data'))
                if 'output_root' not in extra:
                    request.output_root = None
                prepare_public_request(request)

    def test_public_collections_are_bounded_and_use_only_youtube_extractors(self):
        from ytloadlib.hosted import prepare_public_request
        request = request_from_payload({'urls': ['https://youtube.com/@example'], 'channel_scope': 'all'}, AppConfig(output_root='/data'))
        request.output_root = None
        result = prepare_public_request(request)
        self.assertEqual(result.playlist_items, '1:50')
        self.assertEqual(result.max_filesize, '256M')
        self.assertEqual(result.passthrough[result.passthrough.index('--use-extractors') + 1], 'youtube.*')
        self.assertIsNone(result.browser)


class HostedServerTests(unittest.TestCase):
    def setUp(self):
        from ytloadlib.hosted import PublicServer
        self.temp = tempfile.TemporaryDirectory()
        self.server = PublicServer(('127.0.0.1', 0), Path(self.temp.name), 'https://downloads.example.com', 'x' * 40, base_path='/ytload')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, payload=None, cookie=None, token=None, **overrides):
        headers = {'X-YTLoad-Proxy-Key': 'x' * 40, 'X-YTLoad-Client-IP': '203.0.113.5',
                   'X-Forwarded-Host': 'downloads.example.com', 'X-Forwarded-Proto': 'https'}
        if cookie:
            headers['Cookie'] = cookie
        if token:
            headers['X-YTLoad-Token'] = token
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': 'https://downloads.example.com'})
        headers.update(overrides)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request(method, path, body=None if payload is None else json.dumps(payload), headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def session(self):
        import re
        status, headers, body = self.request('GET', '/ytload/')
        self.assertEqual(status, 200)
        self.assertIn('Secure', headers['Set-Cookie'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('Path=/ytload/', headers['Set-Cookie'])
        token = re.search(rb'name="workspace-token" content="([^"]+)"', body).group(1).decode()
        return headers['Set-Cookie'].split(';')[0], token

    def test_backend_requires_proxy_and_same_origin(self):
        self.assertEqual(self.request('GET', '/ytload/', **{'X-YTLoad-Proxy-Key': 'wrong'})[0], 403)
        cookie, token = self.session()
        status, _, _ = self.request('POST', '/ytload/api/jobs', {'urls': ['https://youtu.be/AbCdEf123_-']}, cookie, token,
                                    Origin='https://attacker.test')
        self.assertEqual(status, 403)
        self.assertEqual(self.request('GET', '/ytload/api/jobs', cookie=cookie, token='wrong')[0], 403)

    def test_config_and_assets_do_not_expose_host_details(self):
        cookie, token = self.session()
        status, _, body = self.request('GET', '/ytload/api/config', cookie=cookie, token=token)
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data['hosted'])
        self.assertIsNone(data['config']['browser'])
        self.assertEqual(data['environment']['browsers'], [])
        self.assertNotIn(str(Path.home()).encode(), body)
        self.assertNotIn(self.temp.name.encode(), body)
        self.assertEqual(self.request('GET', '/ytload/links.mjs', cookie=cookie)[0], 200)
        self.assertEqual(self.request('POST', '/ytload/api/choose-folder', {}, cookie, token)[0], 404)

    def test_sessions_cannot_list_download_cancel_or_retry_each_others_jobs(self):
        from ytloadlib.runner import DownloadRunResult
        first, first_token = self.session()
        second, second_token = self.session()
        def download(request, url, config, **kwargs):
            path = Path(request.output_root) / 'sample.mp4'
            path.write_bytes(b'0123456789')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(path),))
        with patch('ytloadlib.jobs.run_download', side_effect=download):
            status, _, body = self.request('POST', '/ytload/api/jobs', {'urls': ['https://youtu.be/AbCdEf123_-']}, first, first_token)
            self.assertEqual(status, 202)
            identifier = json.loads(body)['ids'][0]
            for _ in range(50):
                jobs = json.loads(self.request('GET', '/ytload/api/jobs', cookie=first, token=first_token)[2])
                if jobs[0]['status'] == 'completed':
                    break
                time.sleep(.01)
        self.assertEqual(jobs[0]['status'], 'completed')
        self.assertEqual(json.loads(self.request('GET', '/ytload/api/jobs', cookie=second, token=second_token)[2]), [])
        path = f'/ytload/api/files/{identifier}/0?token={second_token}'
        self.assertEqual(self.request('GET', path, cookie=second)[0], 404)
        for action in ['cancel', 'retry']:
            self.assertEqual(self.request('POST', f'/ytload/api/{action}', {'id': identifier}, second, second_token)[0], 404)
        path = f'/ytload/api/files/{identifier}/0?token={first_token}'
        status, headers, body = self.request('GET', path, cookie=first, Range='bytes=2-5')
        self.assertEqual((status, body), (206, b'2345'))
        self.assertEqual(headers['Content-Range'], 'bytes 2-5/10')
        self.assertEqual(self.request('GET', path, cookie=first, Range='bytes=99-')[0], 416)

    def test_session_expiry_removes_files_and_access(self):
        cookie, token = self.session()
        session = next(iter(self.server.state.sessions.values()))
        session.root.mkdir(exist_ok=True)
        (session.root / 'test.txt').write_text('private')
        session.touched = time.monotonic() - 4000
        self.server.state.cleanup()
        self.assertFalse(session.root.exists())
        self.assertEqual(self.request('GET', '/ytload/api/jobs', cookie=cookie, token=token)[0], 401)


class HostedLifecycleTests(unittest.TestCase):
    def test_storage_cannot_be_opened_by_two_backends(self):
        from ytloadlib.hosted_state import PublicState
        with tempfile.TemporaryDirectory() as directory:
            first = PublicState(Path(directory))
            try:
                with self.assertRaises(ValueError):
                    PublicState(Path(directory))
            finally:
                first.close()

    def test_retry_preserves_a_single_expanded_channel_section(self):
        from ytloadlib.jobs import JobManager
        from ytloadlib.runner import DownloadRunResult, FailureKind
        failure = DownloadRunResult(False, 1, 'ERROR: HTTP Error 403', 1, FailureKind.HTTP_403, ())
        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', return_value=failure):
            manager = JobManager(AppConfig(output_root=directory))
            try:
                ids = manager.add({'urls': ['https://youtube.com/@example'], 'channel_scope': 'all'})
                for _ in range(50):
                    if all(job['status'] == 'failed' for job in manager.snapshot()):
                        break
                    time.sleep(.01)
                retried = manager.retry(ids[0])
                self.assertEqual(len(retried), 1)
                self.assertEqual(next(job['url'] for job in manager.snapshot() if job['id'] == retried[0]), 'https://youtube.com/@example/videos')
            finally:
                manager.close()

    def test_processed_files_cannot_exceed_public_limit(self):
        from ytloadlib.hosted_state import PublicState
        from ytloadlib.runner import DownloadRunResult
        def download(request, url, config, **kwargs):
            path = Path(request.output_root) / 'large.wav'
            path.write_bytes(b'12345678')
            return DownloadRunResult(True, 0, '', 1, None, (), (str(path),))
        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', side_effect=download), patch('ytloadlib.hosted_state.FILE_BYTES', 4, create=True):
            state = PublicState(Path(directory))
            try:
                session = state.new_session('203.0.113.1')
                state.add(session, {'urls': ['https://youtu.be/AbCdEf123_-']})
                for _ in range(50):
                    jobs = state.snapshot(session)
                    if jobs[0]['status'] not in {'queued', 'running'}:
                        break
                    time.sleep(.01)
                self.assertEqual(jobs[0]['status'], 'failed')
                self.assertFalse((session.root / 'large.wav').exists())
                self.assertEqual(jobs[0]['files'], [])
            finally:
                state.close()
