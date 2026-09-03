import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ytloadlib.config import AppConfig, default_config_path, load_config
from ytloadlib.formats import quality_selector


class ConfigTests(unittest.TestCase):
    def test_default_config_path_posix(self):
        with mock.patch('ytloadlib.config.os.name', 'posix'), mock.patch.dict(os.environ, {'HOME': '/tmp/home'}, clear=False):
            self.assertEqual(default_config_path(), Path('/tmp/home/.config/ytload/config.json'))

    def test_load_config_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch('ytloadlib.config.default_config_path', return_value=Path(td) / 'missing.json'):
                cfg = load_config()
        self.assertIsInstance(cfg, AppConfig)
        self.assertEqual(cfg.quality, 'best')
        self.assertTrue(cfg.archive)
        self.assertEqual(Path(cfg.output_root).name, 'YTLoad')

    def test_load_config_reads_supported_keys_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'config.json'
            path.write_text(json.dumps({
                'quality': '1080p',
                'browser': 'firefox',
                'archive': False,
                'unknown': 'ignored',
            }), encoding='utf-8')
            cfg = load_config(path)
        self.assertEqual(cfg.quality, '1080p')
        self.assertEqual(cfg.browser, 'firefox')
        self.assertFalse(cfg.archive)
        self.assertFalse(hasattr(cfg, 'unknown'))


class FormatTests(unittest.TestCase):
    def test_best_selector(self):
        self.assertEqual(quality_selector('best'), 'bestvideo+bestaudio/best')

    def test_height_presets(self):
        for preset, height in [('2160p', 2160), ('1440p', 1440), ('1080p', 1080), ('720p', 720), ('480p', 480)]:
            value = quality_selector(preset)
            self.assertIn(f'height<={height}', value)
            self.assertIn('bestaudio', value)

    def test_compatible_prefers_avc_and_m4a(self):
        value = quality_selector('compatible')
        self.assertIn('vcodec^=avc1', value)
        self.assertIn('bestaudio[ext=m4a]', value)

    def test_small_caps_height_and_filesize_bias(self):
        value = quality_selector('small')
        self.assertIn('height<=720', value)
        self.assertIn('filesize', value)

    def test_unknown_quality_raises(self):
        with self.assertRaises(ValueError):
            quality_selector('8k-magic')


if __name__ == '__main__':
    unittest.main()
