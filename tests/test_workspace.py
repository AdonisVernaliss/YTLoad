import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ytloadlib.models import AppConfig
from ytloadlib.validation import request_from_payload


class RequestValidationTests(unittest.TestCase):
    def test_web_payload_rejects_command_injection_and_wrong_types(self):
        for extra in ({'passthrough': ['--exec', 'bad']}, {'embed_metadata': 'false'},
                      {'mode': []}, {'browser': []}, {'quality': None}, {'transcript_formats': 'txt'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                request_from_payload({'urls': ['https://youtu.be/example'], **extra}, AppConfig())

    def test_text_mode_defaults_to_real_transcript_export(self):
        request = request_from_payload({'urls': ['https://youtu.be/a'], 'mode': 'subs'}, AppConfig())
        self.assertEqual(request.transcript_formats, ['txt'])
        self.assertEqual(request.subtitles, 'both')


class WorkspaceTests(unittest.TestCase):
    def test_failed_job_exposes_the_source_error_without_secrets(self):
        from ytloadlib.jobs import JobManager
        from ytloadlib.runner import ProcessResult
        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.runner.run_process', return_value=ProcessResult(
            1, 'ERROR: Unable to download subtitles: HTTP Error 429: Too Many Requests https://example.org/?token=private-value',
        )):
            manager = JobManager(AppConfig(output_root=directory))
            try:
                manager.add({'urls': ['https://youtu.be/example'], 'mode': 'subs'})
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    job = manager.snapshot()[0]
                    if job['status'] == 'failed':
                        break
                    time.sleep(0.01)
                self.assertEqual(job['status'], 'failed')
                detail = job.get('error_detail', '')
                self.assertIn('HTTP Error 429', detail)
                self.assertNotIn('private-value', detail)
                self.assertNotIn('https://', detail)
                self.assertEqual(manager.snapshot()[0]['error_detail'], detail)
            finally:
                manager.close()

    def test_lan_access_requires_connection_key_and_session(self):
        from ytloadlib.web import create_server
        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.web.lan_address', return_value='127.0.0.1'):
            server = create_server(AppConfig(output_root=directory), port=0, lan=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            try:
                connection.request('GET', '/')
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                self.assertNotIn(server.token, response.read().decode())
                connection.request('GET', '/connect?key=%C3%A4')
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.request('GET', '/connect?key=' + server.access_key)
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                cookie = response.getheader('Set-Cookie').split(';')[0]
                response.read()
                connection.request('GET', '/api/jobs', headers={'Cookie': cookie, 'X-YTLoad-Token': server.token})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                response.read()
                connection.request('GET', '/api/jobs', headers={'X-YTLoad-Token': server.token})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()

    def test_jobs_are_serial_and_cancellable(self):
        from ytloadlib.jobs import JobManager
        from ytloadlib.runner import DownloadRunResult, FailureKind
        entered = threading.Event()
        def download(request, url, config, **kwargs):
            entered.set()
            kwargs['cancel_event'].wait(3)
            return DownloadRunResult(False, 130, '', 1, FailureKind.CANCELLED, ())
        with tempfile.TemporaryDirectory() as directory, patch('ytloadlib.jobs.run_download', download):
            manager = JobManager(AppConfig(output_root=directory))
            try:
                ids = manager.add({'urls': ['https://youtu.be/one', 'https://youtu.be/two']})
                self.assertTrue(entered.wait(1))
                manager.cancel(ids[1])
                self.assertEqual(manager.snapshot()[1]['status'], 'cancelled')
                manager.cancel(ids[0])
            finally:
                manager.close()
            self.assertEqual(manager.snapshot()[0]['status'], 'cancelled')

    def test_http_api_requires_token_and_same_origin(self):
        from ytloadlib.web import create_server
        with tempfile.TemporaryDirectory() as directory:
            server = create_server(AppConfig(output_root=directory), port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f'http://127.0.0.1:{server.server_port}'
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            try:
                for method, path, headers, expected in (
                    ('GET', '/api/jobs', {}, 403),
                    ('GET', '/api/jobs', {'X-YTLoad-Token': '\u00e4'}, 403),
                    ('GET', '/api/jobs', {'X-YTLoad-Token': server.token}, 200),
                    ('POST', '/api/jobs', {'X-YTLoad-Token': server.token, 'Origin': 'https://example.org', 'Content-Type': 'application/json'}, 403),
                    ('POST', '/api/jobs', {'X-YTLoad-Token': server.token, 'Origin': origin, 'Content-Type': 'application/json'}, 400),
                    ('GET', '/', {'Host': 'evil.example'}, 403),
                    ('GET', '/../../ytload.py', {}, 404),
                ):
                    with self.subTest(path=path, headers=list(headers)):
                        connection.request(method, path, body='{}' if method == 'POST' else None, headers=headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, expected, response.read().decode())
                        response.read()
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
