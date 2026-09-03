import http.client
import json
import threading
import unittest
from unittest.mock import patch

from ytloadlib.direct_probe import ProbeServer


class ProbeServerTests(unittest.TestCase):
    def setUp(self):
        self.server = ProbeServer(('127.0.0.1', 0), key='k' * 40)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        defaults = {'Origin': self.server.origin, 'Authorization': 'Bearer ' + 'k' * 40, 'Content-Type': 'application/json'}
        connection.request(method, path, json.dumps(body) if body is not None else None, {**defaults, **headers})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_bind_failure_preserves_the_original_socket_error(self):
        with self.assertRaises(OSError):
            ProbeServer(('127.0.0.1', self.server.server_port), key='k' * 40)

    def test_metadata_requires_key_same_origin_and_strict_body(self):
        payload = {'url': 'https://youtu.be/AbCdEf123_-'}
        self.assertEqual(self.request('POST', '/api/resolve', payload, Authorization='wrong')[0], 403)
        self.assertEqual(self.request('POST', '/api/resolve', payload, Origin='https://attacker.test')[0], 403)
        self.assertEqual(self.request('POST', '/api/resolve', {**payload, 'browser': 'chrome'})[0], 400)
        self.assertEqual(self.request('POST', '/api/resolve', payload, Host='attacker.test')[0], 403)

    def test_probe_has_no_server_download_file_or_proxy_endpoint(self):
        for path in ('/api/jobs', '/api/files/123/0', '/api/proxy?url=https://example.com', '/ytload/'):
            self.assertEqual(self.request('GET', path)[0], 404)
            self.assertEqual(self.request('POST', path, {})[0], 404)

    def test_static_page_contains_no_connection_secret(self):
        status, headers, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertNotIn(b'k' * 40, body)
        self.assertIn('https://*.googlevideo.com', headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_only_metadata_is_returned_and_requests_are_rate_limited(self):
        with patch('ytloadlib.direct_probe.resolve_direct', return_value={'formats': []}) as resolve:
            payload = {'url': 'https://youtu.be/AbCdEf123_-'}
            for _ in range(5):
                self.assertEqual(self.request('POST', '/api/resolve', payload)[0], 200)
            self.assertEqual(self.request('POST', '/api/resolve', payload)[0], 429)
            self.assertEqual(resolve.call_count, 5)

    def test_close_waits_for_active_resolver_cancellation(self):
        started = threading.Event()
        finished = threading.Event()
        def resolve(*args, cancel, **kwargs):
            from ytloadlib.direct_resolver import ProbeError
            started.set()
            cancel.wait(timeout=2)
            finished.set()
            raise ProbeError('cancelled')
        with patch('ytloadlib.direct_probe.resolve_direct', side_effect=resolve):
            client = threading.Thread(target=lambda: self.request('POST', '/api/resolve', {'url': 'https://youtu.be/AbCdEf123_-'}))
            client.start()
            self.assertTrue(started.wait(timeout=1))
            self.server.server_close()
            self.assertTrue(finished.is_set())
            client.join(timeout=2)
            self.assertFalse(client.is_alive())


    def test_shutdown_between_validation_and_lock_cannot_start_resolver(self):
        entered = threading.Event()
        resume = threading.Event()
        responses = []
        class PausedLock:
            def __enter__(self):
                entered.set()
                resume.wait(timeout=2)
            def __exit__(self, *args):
                return False
        self.server.rate_lock = PausedLock()
        with patch('ytloadlib.direct_probe.resolve_direct', return_value={'formats': []}) as resolve:
            client = threading.Thread(target=lambda: responses.append(self.request('POST', '/api/resolve', {'url': 'https://youtu.be/AbCdEf123_-'})))
            client.start()
            self.assertTrue(entered.wait(timeout=1))
            self.server.server_close()
            resume.set()
            client.join(timeout=2)
            self.assertFalse(client.is_alive())
            resolve.assert_not_called()
            self.assertEqual(responses[0][0], 503)


if __name__ == '__main__':
    unittest.main()
