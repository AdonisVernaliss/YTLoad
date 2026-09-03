import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ytloadlib.cli import parse_args, request_from_args, read_urls
from ytloadlib.models import AppConfig
from ytloadlib.ui import interactive_request


class CliTests(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(output_root='/tmp/default', quality='best', archive=True, sub_langs='orig')

    def test_default_video_request(self):
        ns = parse_args(['https://youtu.be/a'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.mode, 'video')
        self.assertEqual(req.quality, 'best')
        self.assertEqual(req.urls, ['https://youtu.be/a'])

    def test_audio_shorthand_with_format(self):
        ns = parse_args(['https://youtu.be/a', '--audio', 'mp3'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.mode, 'audio')
        self.assertEqual(req.audio_format, 'mp3')

    def test_subtitles_only(self):
        ns = parse_args(['https://youtu.be/a', '--subs', '--subs-mode', 'auto', '--sub-langs', 'ru-orig'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.mode, 'subs')
        self.assertEqual(req.subtitles, 'auto')
        self.assertEqual(req.sub_langs, 'ru-orig')

    def test_with_subtitles_keeps_video_mode(self):
        ns = parse_args(['https://youtu.be/a', '--with-subs', 'both', '--embed-subs'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.mode, 'video')
        self.assertEqual(req.subtitles, 'both')
        self.assertTrue(req.embed_subs)

    def test_metadata_mode_and_extras(self):
        ns = parse_args(['https://youtu.be/a', '--metadata', '--description', '--thumbnail', '--comments'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.mode, 'metadata')
        self.assertTrue(req.write_description)
        self.assertTrue(req.write_thumbnail)
        self.assertTrue(req.write_comments)

    def test_channel_scope_and_filters(self):
        ns = parse_args(['https://youtube.com/@x', '--channel', 'all', '--items', '1:10', '--date-after', '20260101'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.channel_scope, 'all')
        self.assertEqual(req.playlist_items, '1:10')
        self.assertEqual(req.date_after, '20260101')

    def test_archive_override(self):
        self.assertFalse(request_from_args(parse_args(['https://youtu.be/a', '--no-archive']), self.config).archive)
        self.assertTrue(request_from_args(parse_args(['https://youtu.be/a', '--archive']), self.config).archive)

    def test_browser_dry_run_fail_fast_and_output(self):
        ns = parse_args(['https://youtu.be/a', '--browser', 'chrome', '--dry-run', '--fail-fast', '-o', '/x'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.browser, 'chrome')
        self.assertTrue(req.dry_run)
        self.assertTrue(req.fail_fast)
        self.assertEqual(req.output_root, '/x')

    def test_passthrough(self):
        ns = parse_args(['https://youtu.be/a', '--passthrough', '--limit-rate', '5M', '--retries', '10'])
        req = request_from_args(ns, self.config)
        self.assertEqual(req.passthrough, ['--limit-rate', '5M', '--retries', '10'])

    def test_read_urls_merges_positional_and_file_ignoring_comments(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'urls.txt'
            path.write_text('# comment\nhttps://youtu.be/b\n\nhttps://youtu.be/c\n', encoding='utf-8')
            urls = read_urls(['https://youtu.be/a'], str(path))
        self.assertEqual(urls, ['https://youtu.be/a', 'https://youtu.be/b', 'https://youtu.be/c'])

    def test_doctor_pseudo_command(self):
        ns = parse_args(['doctor'])
        self.assertTrue(ns.doctor)
        self.assertEqual(ns.urls, [])


class InteractiveTests(unittest.TestCase):
    def test_simple_video_flow(self):
        answers = iter([
            'https://youtu.be/a',
            '1',
            '4',
            '1',
            'n',
            '',
            'n',
            'y',
        ])
        req = interactive_request(AppConfig(output_root='/tmp/default'), input_fn=lambda _: next(answers), print_fn=lambda *a, **k: None)
        self.assertEqual(req.urls, ['https://youtu.be/a'])
        self.assertEqual(req.mode, 'video')
        self.assertEqual(req.quality, '1080p')
        self.assertEqual(req.output_root, '/tmp/default')


if __name__ == '__main__':
    unittest.main()
