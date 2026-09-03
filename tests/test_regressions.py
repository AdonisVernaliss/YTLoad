import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ytloadlib.cli import parse_args, request_from_args
from ytloadlib.command import build_command
from ytloadlib.config import load_config
from ytloadlib.models import AppConfig, DownloadRequest


class RegressionTests(unittest.TestCase):
    def test_audio_can_include_subtitles(self):
        request = request_from_args(parse_args(['https://youtu.be/a', '--audio', 'mp3', '--with-subs']), AppConfig())
        self.assertEqual(request.subtitles, 'both')

    def test_configuration_rejects_invalid_shapes_and_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for value in ([], None, {'archive': 'false'}, {'quality': 'wrong'}, {'output_root': 42}, {'browser': []}):
                with self.subTest(value=value):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        load_config(path)

    def test_bulk_downloads_do_not_hide_postprocessing_failures(self):
        command = build_command(DownloadRequest(), 'https://youtube.com/playlist?list=PL1', AppConfig())
        self.assertNotIn('--ignore-errors', command)

    def test_output_and_archive_distinguish_quality_and_mode(self):
        config = AppConfig(output_root='/tmp/output')
        request = DownloadRequest(archive=True)
        commands = [build_command(r, 'https://youtube.com/playlist?list=PL1', config) for r in (
            request, replace(request, quality='720p'), replace(request, mode='audio', audio_format='mp3'))]
        for flag in ('-o', '--download-archive'):
            self.assertEqual(len({c[c.index(flag) + 1] for c in commands}), 3)

    def test_subtitles_are_not_skipped_by_media_archive(self):
        command = build_command(DownloadRequest(mode='subs', archive=True), 'https://youtube.com/playlist?list=PL1', AppConfig())
        self.assertNotIn('--download-archive', command)

    def test_invalid_url_is_rejected_before_execution(self):
        for url in ('--exec=touch bad', 'file:///etc/passwd', 'not a URL', 'https://user:password@example.org/video'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                request_from_args(parse_args([url] if not url.startswith('--') else ['--', url]), AppConfig())

    def test_cut_and_original_video_have_distinct_output_paths(self):
        config = AppConfig(output_root='/tmp/output')
        commands = [build_command(DownloadRequest(sponsorblock=policy), 'https://youtu.be/a', config) for policy in ('off', 'remove')]
        self.assertNotEqual(commands[0][commands[0].index('-o') + 1], commands[1][commands[1].index('-o') + 1])

    def test_sidecar_refresh_is_not_disabled(self):
        command = build_command(DownloadRequest(mode='subs', subtitles='authored'), 'https://youtu.be/a', AppConfig())
        self.assertNotIn('--no-overwrites', command)
