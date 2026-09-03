import json
import os
import unittest

from ytloadlib.direct_resolver import ProbeError, metadata_command, reduce_metadata


def media(identifier, video='avc1.640028', audio='none', **extra):
    return {'format_id': identifier, 'url': 'https://rr1---sn-example.googlevideo.com/videoplayback?expire=2000000000&ip=192.0.2.1&sig=temporary',
            'protocol': 'https', 'vcodec': video, 'acodec': audio, 'ext': 'mp4', 'height': 1080,
            'filesize': 12345, **extra}


class ResolverTests(unittest.TestCase):
    def test_fixed_metadata_command_never_requests_media_or_user_configuration(self):
        command = metadata_command('https://youtube.com/shorts/AbCdEf123_-?si=tracking', 'web_safari', 'Mozilla/5.0')
        for flag in ('--ignore-config', '--skip-download', '--no-check-formats', '--no-cache-dir', '--no-playlist', '--dump-single-json'):
            self.assertIn(flag, command)
        self.assertEqual(command[-2:], ['--', 'https://www.youtube.com/watch?v=AbCdEf123_-'])
        self.assertIn('youtube:player_client=default,web_safari', command)
        self.assertNotIn('--cookies-from-browser', command)

    def test_resolver_rejects_collections_local_urls_and_untrusted_options(self):
        for url in ('http://127.0.0.1/a', 'https://youtube.com/redirect?q=http://localhost', 'https://youtube.com/@channel',
                    'https://youtube.com.attacker.test/watch?v=AbCdEf123_-'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                metadata_command(url)
        with self.assertRaises(ValueError):
            metadata_command('https://youtu.be/AbCdEf123_-', user_agent='x\r\nCookie: secret')

    def test_only_selected_formats_are_returned_without_private_metadata(self):
        data = {'title': 'private title', 'cookies': 'top-level-secret', 'requested_downloads': [{'filepath': '/private/file'}],
                'http_headers': {'Cookie': 'cookie-secret', 'Authorization': 'Bearer auth-secret', 'User-Agent': 'Mozilla/5.0'},
                'formats': [media('18', audio='mp4a.40.2', height=360), media('137'), media('140', video='none', audio='mp4a.40.2')]}
        result = reduce_metadata(data)
        self.assertEqual([item['role'] for item in result['formats']], ['progressive', 'best-video', 'best-audio'])
        encoded = json.dumps(result)
        for secret in ('private title', 'top-level-secret', 'cookie-secret', 'auth-secret', '/private/file'):
            self.assertNotIn(secret, encoded)
        self.assertTrue(all(item['requires_private_headers'] for item in result['formats']))
        self.assertTrue(all(item['url'] is None for item in result['formats']))

    def test_expiry_and_replay_constraints_do_not_expose_extra_options(self):
        item = reduce_metadata({'formats': [media('18', audio='aac', http_headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://youtube.com/?token=private', 'X-Secret': 'hidden'},
                    downloader_options={'http_chunk_size': 1048576, 'external_downloader_args': ['secret']})]})['formats'][0]
        self.assertEqual(item['expires_at'], 2000000000)
        self.assertTrue(item['ip_binding_hint'])
        self.assertEqual(item['download_options'], {'http_chunk_size': 1048576})
        self.assertNotIn('private', json.dumps(item['http_headers']))
        self.assertNotIn('hidden', json.dumps(item))
        self.assertTrue(item['unreplayable_headers'])

    def test_unsafe_media_destinations_and_drm_never_reach_browser(self):
        for url in ('http://rr1.googlevideo.com/video', 'https://127.0.0.1/video', 'https://googlevideo.com.attacker.test/video',
                    'https://user:password@rr1.googlevideo.com/video', 'https://rr1.googlevideo.com:8443/video'):
            result = reduce_metadata({'formats': [media('137', url=url)]})
            self.assertIsNone(result['formats'][1]['url'])
        item = reduce_metadata({'formats': [media('137', has_drm=True)]})['formats'][1]
        self.assertIsNone(item['url'])

    def test_manifest_is_not_reported_as_progressive_or_direct_https(self):
        result = reduce_metadata({'formats': [media('hls', audio='aac', protocol='m3u8_native')]})
        self.assertFalse(result['formats'][0]['available'])
        self.assertFalse(result['formats'][1]['available'])
        item = reduce_metadata({'formats': [media('dash', protocol='http_dash_segments')]})['formats'][1]
        self.assertEqual(item['protocol'], 'http_dash_segments')

    def test_empty_metadata_does_not_become_a_successful_resolution(self):
        with self.assertRaises(ProbeError):
            reduce_metadata({'formats': []})


class ResolverProcessTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'The development resolver uses POSIX process groups.')
    def test_bounded_capture_and_timeout_do_not_leak_subprocess_output(self):
        import sys
        from ytloadlib.direct_resolver import read_metadata
        with self.assertRaisesRegex(ProbeError, 'metadata-too-large'):
            read_metadata([sys.executable, '-c', "print('secret' * 1000)"], output_limit=128)
        with self.assertRaisesRegex(ProbeError, 'resolution-timeout'):
            read_metadata([sys.executable, '-c', "import time; time.sleep(3)"], timeout=.1)

    def test_https_alternative_is_available_when_best_video_is_a_manifest(self):
        result = reduce_metadata({'formats': [media('137'), media('hls', protocol='m3u8_native')]})
        self.assertEqual(result['formats'][1]['format_id'], 'hls')
        alternatives = [item for item in result['formats'] if item['role'] == 'dash-video']
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(alternatives[0]['format_id'], '137')

    def test_nonfinite_and_huge_sizes_do_not_break_metadata_sanitization(self):
        item = reduce_metadata({'formats': [media('18', audio='aac', filesize=10 ** 400, filesize_approx=float('nan'))]})['formats'][0]
        self.assertIsNone(item['filesize'])
        self.assertIsNone(item['filesize_approx'])

    @unittest.skipUnless(os.name == 'posix', 'The development resolver uses POSIX process groups.')
    def test_exited_parent_cannot_leave_pipe_readers_past_the_deadline(self):
        import sys
        import time
        from ytloadlib.direct_resolver import read_metadata
        command = [sys.executable, '-c', "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time;time.sleep(3)']); print('{}', flush=True)"]
        started = time.monotonic()
        with self.assertRaisesRegex(ProbeError, 'resolution-timeout'):
            read_metadata(command, timeout=.1)
        self.assertLess(time.monotonic() - started, 1.5)


if __name__ == '__main__':
    unittest.main()
