import unittest
from pathlib import Path

from ytloadlib.templates import output_template_for
from ytloadlib.urls import classify_url, expand_channel_url
from ytloadlib.validation import request_from_payload, validate_url
from ytloadlib.models import AppConfig
from ytloadlib.command import build_command


class UrlTests(unittest.TestCase):
    def test_classifies_video(self):
        self.assertEqual(classify_url('https://youtu.be/Mx4yIsbb0Us').kind, 'video')
        self.assertEqual(classify_url('https://www.youtube.com/watch?v=abc123').kind, 'video')

    def test_classifies_playlist(self):
        info = classify_url('https://www.youtube.com/playlist?list=PL123')
        self.assertEqual(info.kind, 'playlist')

    def test_classifies_video_with_playlist_as_video(self):
        info = classify_url('https://www.youtube.com/watch?v=abc&list=PL123')
        self.assertEqual(info.kind, 'video')
        self.assertTrue(info.has_playlist_context)

    def test_classifies_channel_root_and_sections(self):
        self.assertEqual(classify_url('https://youtube.com/@name').kind, 'channel')
        for section in ('videos', 'shorts', 'streams'):
            info = classify_url(f'https://youtube.com/@name/{section}')
            self.assertEqual(info.kind, 'channel_section')
            self.assertEqual(info.section, section)

    def test_expand_channel_all(self):
        urls = expand_channel_url('https://youtube.com/@name', 'all')
        self.assertEqual(urls, [
            'https://youtube.com/@name/videos',
            'https://youtube.com/@name/shorts',
            'https://youtube.com/@name/streams',
        ])

    def test_expand_channel_specific_replaces_existing_section(self):
        self.assertEqual(
            expand_channel_url('https://youtube.com/@name/shorts', 'videos'),
            ['https://youtube.com/@name/videos'],
        )


class TemplateTests(unittest.TestCase):
    def test_single_video_template(self):
        t = output_template_for(Path('/downloads'), 'video')
        self.assertEqual(t, '/downloads/Videos/%(title).100B [%(id)s].%(ext)s')

    def test_playlist_template_has_index(self):
        t = output_template_for(Path('/downloads'), 'playlist')
        self.assertIn('Playlists/%(playlist_title).100B/', t)
        self.assertIn('%(playlist_index)03d', t)

    def test_channel_template_has_channel_section_and_date(self):
        t = output_template_for(Path('/downloads'), 'channel_section', section='shorts')
        self.assertIn('Channels/%(channel).100B/Shorts/', t)
        self.assertIn('%(upload_date)s', t)


if __name__ == '__main__':
    unittest.main()

class AdditionalUrlTemplateTests(unittest.TestCase):
    def test_shared_video_links_have_one_canonical_download_url(self):
        expected = 'https://www.youtube.com/watch?v=AbCdEf123_-'
        for url in (
            'https://youtube.com/shorts/AbCdEf123_-?si=share-code',
            'https://youtu.be/AbCdEf123_-?si=another-code',
            'https://m.youtube.com/watch?v=AbCdEf123_-&feature=share',
            'https://www.youtube.com/embed/AbCdEf123_-?utm_source=chat',
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_url(url), expected)

    def test_canonical_video_url_keeps_time_and_playlist_parameters(self):
        self.assertEqual(
            validate_url('https://youtu.be/AbCdEf123_-?si=share&t=30&list=PL123&index=2'),
            'https://www.youtube.com/watch?v=AbCdEf123_-&t=30&list=PL123&index=2',
        )

    def test_other_sites_and_lookalike_hosts_keep_signed_queries(self):
        for url in (
            'https://example.org/video.mp4?si=signature&token=keep&feature=required',
            'https://youtube.com.example.org/shorts/AbCdEf123_-?si=keep',
        ):
            self.assertEqual(validate_url(url), url)

    def test_share_wrappers_are_removed_before_url_validation(self):
        expected = 'https://www.youtube.com/watch?v=AbCdEf123_-'
        for url in (
            '  https://youtu.be/AbCdEf123_-?si=share  ',
            '[A shared video](https://youtube.com/shorts/AbCdEf123_-?si=share)',
            '<https://youtu.be/AbCdEf123_->',
        ):
            self.assertEqual(validate_url(url), expected)

    def test_normalization_does_not_allow_credential_urls(self):
        with self.assertRaises(ValueError):
            validate_url('https://name:password@youtube.com/shorts/AbCdEf123_-')

    def test_duplicate_share_links_create_one_download_request(self):
        request = request_from_payload({'urls': [
            'https://youtube.com/shorts/AbCdEf123_-?si=one',
            'https://youtu.be/AbCdEf123_-?si=two',
        ]}, AppConfig())
        self.assertEqual(request.urls, ['https://www.youtube.com/watch?v=AbCdEf123_-'])
        self.assertEqual(build_command(request, request.urls[0], AppConfig())[-1], request.urls[0])

    def test_youtube_shorts_and_live_urls_are_single_video(self):
        self.assertEqual(classify_url('https://youtube.com/shorts/abc123').kind, 'video')
        self.assertEqual(classify_url('https://youtube.com/live/abc123').kind, 'video')

    def test_audio_single_video_template_has_audio_directory(self):
        t = output_template_for(Path('/downloads'), 'video', media_kind='audio')
        self.assertEqual(t, '/downloads/Audio/%(title).100B [%(id)s].%(ext)s')
