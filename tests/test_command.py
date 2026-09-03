import tempfile
import unittest
from pathlib import Path

from ytloadlib.command import build_command
from ytloadlib.models import AppConfig, DownloadRequest


class CommandBuilderTests(unittest.TestCase):
    def setUp(self):
        self.root = '/tmp/ytload-out'
        self.config = AppConfig(output_root=self.root, archive=True, quality='best')

    def test_video_best_builds_split_stream_merge_and_no_playlist_for_video(self):
        req = DownloadRequest(urls=['https://youtu.be/abc'], mode='video')
        cmd = build_command(req, req.urls[0], self.config)
        self.assertEqual(cmd[0], 'yt-dlp')
        self.assertIn('-f', cmd)
        self.assertEqual(cmd[cmd.index('-f') + 1], 'bestvideo+bestaudio/best')
        self.assertIn('--merge-output-format', cmd)
        self.assertIn('mkv', cmd)
        self.assertIn('--no-playlist', cmd)

    def test_video_quality_override(self):
        req = DownloadRequest(mode='video', quality='1080p')
        cmd = build_command(req, 'https://youtu.be/abc', self.config)
        selector = cmd[cmd.index('-f') + 1]
        self.assertIn('height<=1080', selector)

    def test_audio_mp3(self):
        req = DownloadRequest(mode='audio', audio_format='mp3')
        cmd = build_command(req, 'https://youtu.be/abc', self.config)
        self.assertIn('-x', cmd)
        self.assertIn('--audio-format', cmd)
        self.assertEqual(cmd[cmd.index('--audio-format') + 1], 'mp3')
        self.assertIn('bestaudio/best', cmd)

    def test_subtitles_only_auto_original(self):
        req = DownloadRequest(mode='subs', subtitles='auto', sub_langs='orig')
        cmd = build_command(req, 'https://youtu.be/abc', self.config)
        self.assertIn('--skip-download', cmd)
        self.assertIn('--write-auto-subs', cmd)
        self.assertNotIn('--write-subs', cmd)
        self.assertEqual(cmd[cmd.index('--sub-langs') + 1], '.*-orig')

    def test_video_can_embed_both_subtitle_types(self):
        req = DownloadRequest(mode='video', subtitles='both', sub_langs='ru-orig,en', embed_subs=True)
        cmd = build_command(req, 'https://youtu.be/abc', self.config)
        self.assertIn('--write-subs', cmd)
        self.assertIn('--write-auto-subs', cmd)
        self.assertIn('--embed-subs', cmd)
        self.assertEqual(cmd[cmd.index('--sub-langs') + 1], 'ru-orig,en')

    def test_metadata_only_all_extras(self):
        req = DownloadRequest(
            mode='metadata', write_info_json=True, write_description=True,
            write_thumbnail=True, write_comments=True,
        )
        cmd = build_command(req, 'https://youtu.be/abc', self.config)
        for flag in ('--skip-download', '--write-info-json', '--write-description', '--write-thumbnail', '--write-comments'):
            self.assertIn(flag, cmd)

    def test_playlist_bulk_filters_and_archive(self):
        req = DownloadRequest(
            mode='video', playlist_items='1:10', date_after='20260101',
            date_before='20261231', max_filesize='2G', archive=True,
        )
        cmd = build_command(req, 'https://youtube.com/playlist?list=PL1', self.config)
        self.assertEqual(cmd[cmd.index('--playlist-items') + 1], '1:10')
        self.assertEqual(cmd[cmd.index('--dateafter') + 1], '20260101')
        self.assertEqual(cmd[cmd.index('--datebefore') + 1], '20261231')
        self.assertEqual(cmd[cmd.index('--max-filesize') + 1], '2G')
        archive = Path(cmd[cmd.index('--download-archive') + 1])
        self.assertTrue(archive.name.startswith('video-'))
        self.assertIn('.ytload-state', str(archive))

    def test_channel_uses_archive_by_default_from_config(self):
        req = DownloadRequest(mode='video')
        cmd = build_command(req, 'https://youtube.com/@foo/videos', self.config)
        self.assertIn('--download-archive', cmd)
        self.assertIn('Channels/%(channel).100B/Videos/', cmd[cmd.index('-o') + 1])

    def test_single_video_does_not_archive_unless_requested(self):
        req = DownloadRequest(mode='video', archive=None)
        cmd = build_command(req, 'https://youtu.be/abc', self.config)
        self.assertNotIn('--download-archive', cmd)

    def test_sponsorblock_mark_and_remove(self):
        mark = build_command(DownloadRequest(sponsorblock='mark'), 'https://youtu.be/a', self.config)
        remove = build_command(DownloadRequest(sponsorblock='remove'), 'https://youtu.be/a', self.config)
        self.assertIn('--sponsorblock-mark', mark)
        self.assertIn('--sponsorblock-remove', remove)

    def test_browser_and_safari_fallback(self):
        req = DownloadRequest()
        cmd = build_command(req, 'https://youtu.be/a', self.config, browser='chrome', safari_fallback=True)
        self.assertEqual(cmd[cmd.index('--cookies-from-browser') + 1], 'chrome')
        self.assertEqual(cmd[cmd.index('--extractor-args') + 1], 'youtube:player_client=default,web_safari')

    def test_passthrough_is_appended_before_url(self):
        req = DownloadRequest(passthrough=['--limit-rate', '5M'])
        cmd = build_command(req, 'https://youtu.be/a', self.config)
        self.assertEqual(cmd[-4:-2], ['--limit-rate', '5M'])
        self.assertEqual(cmd[-2], '--')
        self.assertEqual(cmd[-1], 'https://youtu.be/a')

    def test_output_override_is_used(self):
        req = DownloadRequest(output_root='/custom')
        cmd = build_command(req, 'https://youtu.be/a', self.config)
        self.assertTrue(cmd[cmd.index('-o') + 1].startswith('/custom/'))


if __name__ == '__main__':
    unittest.main()

class DiagnosticCommandTests(unittest.TestCase):
    def test_command_does_not_hide_ytdlp_warnings(self):
        config = AppConfig(output_root='/tmp/out')
        cmd = build_command(DownloadRequest(), 'https://youtu.be/a', config)
        self.assertNotIn('--no-warnings', cmd)
