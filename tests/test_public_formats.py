import json
import unittest

from ytloadlib.public_formats import estimate_quality, inspection_plans, public_direct_candidate


def format_item(identifier, *, height=None, video='none', audio='none', ext='mp4', filesize=None, approx=None,
                protocol='https', url=None, headers=None, cookies=None):
    return {
        'format_id': identifier,
        'height': height,
        'vcodec': video,
        'acodec': audio,
        'ext': ext,
        'filesize': filesize,
        'filesize_approx': approx,
        'protocol': protocol,
        'url': url or f'https://r1---sn-example.googlevideo.com/videoplayback?id={identifier}',
        'http_headers': headers or {},
        'cookies': cookies,
    }


class PublicEstimateTests(unittest.TestCase):
    def test_exact_progressive_size(self):
        formats = [format_item('18', height=1080, video='avc1', audio='mp4a', filesize=900)]
        estimate = estimate_quality(formats, '1080p', 'auto', limit=1000)
        self.assertEqual(estimate['bytes'], 900)
        self.assertFalse(estimate['approximate'])
        self.assertFalse(estimate['near_limit'])

    def test_approximate_and_dash_sizes(self):
        exact = [format_item('137', height=1080, video='avc1', filesize=700),
                 format_item('140', audio='mp4a', ext='m4a', filesize=200)]
        estimate = estimate_quality(exact, '1080p', 'auto', limit=1000)
        self.assertEqual(estimate['bytes'], 900)
        self.assertFalse(estimate['approximate'])
        approximate = [format_item('137', height=1080, video='avc1', approx=700),
                       format_item('140', audio='mp4a', ext='m4a', filesize=200)]
        estimate = estimate_quality(approximate, '1080p', 'auto', limit=1000)
        self.assertEqual(estimate['bytes'], 900)
        self.assertTrue(estimate['approximate'])

    def test_unknown_component_keeps_estimate_unknown(self):
        formats = [format_item('137', height=1080, video='avc1', filesize=700),
                   format_item('140', audio='mp4a', ext='m4a')]
        estimate = estimate_quality(formats, '1080p', 'auto', limit=1000)
        self.assertIsNone(estimate['bytes'])
        self.assertFalse(estimate['over_limit'])
        self.assertFalse(estimate['near_limit'])

    def test_capped_and_small_presets_do_not_use_known_oversized_or_unknown_height_video(self):
        formats = [
            format_item('unknown-height', video='vp09', filesize=100),
            format_item('large-720', height=720, video='avc1', filesize=251 * 1024 ** 2),
            format_item('audio', audio='mp4a', ext='m4a', filesize=20),
        ]
        self.assertEqual(estimate_quality(formats, '1080p', 'auto')['bytes'], 251 * 1024 ** 2 + 20)
        self.assertIsNone(estimate_quality(formats, 'small', 'auto')['bytes'])

    def test_over_and_near_limit_flags(self):
        over = estimate_quality([format_item('18', height=1080, video='avc1', audio='mp4a', filesize=1001)], 'best', 'auto', limit=1000)
        near = estimate_quality([format_item('18', height=1080, video='avc1', audio='mp4a', filesize=950)], 'best', 'auto', limit=1000)
        self.assertTrue(over['over_limit'])
        self.assertFalse(over['near_limit'])
        self.assertFalse(near['over_limit'])
        self.assertTrue(near['near_limit'])

    def test_height_cap_and_compatible_policy_follow_real_selection(self):
        formats = [
            format_item('avc', height=1080, video='avc1.640028', filesize=700),
            format_item('vp9', height=2160, video='vp09.00.51.08', ext='webm', filesize=1800),
            format_item('aac', audio='mp4a.40.2', ext='m4a', filesize=200),
            format_item('opus', audio='opus', ext='webm', filesize=100),
        ]
        capped = estimate_quality(formats, '1080p', 'auto', limit=5000)
        compatible = estimate_quality(formats, 'best', 'mp4', limit=5000)
        self.assertEqual(capped['bytes'], 800)
        self.assertEqual(compatible['bytes'], 900)
        self.assertEqual(compatible['policy'], 'compatible')


class PublicDirectTests(unittest.TestCase):
    def test_only_safe_progressive_googlevideo_is_exposed(self):
        item = format_item('18', height=360, video='avc1', audio='mp4a', filesize=200, headers={'User-Agent': 'Mozilla/5.0'})
        candidate = public_direct_candidate({'formats': [item]})
        self.assertEqual(candidate['url'], item['url'])
        self.assertEqual(candidate['bytes'], 200)
        self.assertEqual(candidate['height'], 360)

    def test_direct_rejects_non_progressive_private_and_untrusted_candidates(self):
        cases = [
            format_item('video', video='avc1', audio='none'),
            format_item('dash', video='avc1', audio='mp4a', protocol='http_dash_segments'),
            format_item('host', video='avc1', audio='mp4a', url='https://media.example.com/video'),
            format_item('cookie', video='avc1', audio='mp4a', cookies='private'),
            format_item('auth', video='avc1', audio='mp4a', headers={'Authorization': 'Bearer private'}),
            format_item('referer', video='avc1', audio='mp4a', headers={'Referer': 'https://www.youtube.com/'}),
        ]
        for item in cases:
            with self.subTest(item=item['format_id']):
                self.assertIsNone(public_direct_candidate({'formats': [item]}))
        live = format_item('live', video='avc1', audio='mp4a')
        self.assertIsNone(public_direct_candidate({'is_live': True, 'formats': [live]}))

    def test_size_metadata_never_contains_media_urls_or_headers(self):
        item = format_item('18', height=360, video='avc1', audio='mp4a', filesize=200,
                           headers={'Accept': '*/*'})
        plan = inspection_plans({'formats': [item]}, limit=1000)
        encoded = json.dumps(plan['estimates'])
        self.assertNotIn('googlevideo', encoded)
        self.assertNotIn('headers', encoded.lower())
        self.assertIn('url', plan['direct'])


if __name__ == '__main__':
    unittest.main()
