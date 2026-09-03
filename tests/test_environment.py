import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ytloadlib.environment import ToolInfo, detect_browsers, detect_tool, pick_browser


class EnvironmentTests(unittest.TestCase):
    @mock.patch('ytloadlib.environment.subprocess.run')
    @mock.patch('ytloadlib.environment.shutil.which')
    def test_detect_tool_reports_version(self, which, run):
        which.return_value = '/usr/local/bin/yt-dlp'
        run.return_value.stdout = '2026.08.27\n'
        info = detect_tool('yt-dlp')
        self.assertEqual(info, ToolInfo('yt-dlp', True, '/usr/local/bin/yt-dlp', '2026.08.27'))
        run.assert_called_once()

    @mock.patch('ytloadlib.environment.shutil.which', return_value=None)
    def test_detect_tool_missing(self, _):
        info = detect_tool('ffmpeg')
        self.assertFalse(info.installed)
        self.assertIsNone(info.version)

    def test_detect_browsers_macos_from_app_paths(self):
        existing = {
            '/Applications/Google Chrome.app',
            '/Applications/Firefox.app',
            '/Applications/Safari.app',
        }
        with mock.patch('ytloadlib.environment.Path.exists', lambda p: str(p) in existing):
            found = detect_browsers(platform_name='Darwin', home=Path('/Users/test'))
        self.assertIn('chrome', found)
        self.assertIn('firefox', found)
        self.assertIn('safari', found)
        self.assertNotIn('edge', found)

    def test_pick_browser_honors_configured_browser(self):
        self.assertEqual(pick_browser(['firefox', 'chrome'], configured='firefox'), 'firefox')

    def test_pick_browser_uses_priority(self):
        self.assertEqual(pick_browser(['safari', 'firefox']), 'firefox')
        self.assertEqual(pick_browser(['safari']), 'safari')
        self.assertIsNone(pick_browser([]))


if __name__ == '__main__':
    unittest.main()

class WindowsBrowserDetectionTests(unittest.TestCase):
    def test_detect_browsers_windows_from_profile_paths(self):
        home = Path('C:/Users/Test')
        existing_suffixes = {
            'AppData/Local/Google/Chrome/User Data',
            'AppData/Roaming/Mozilla/Firefox/Profiles',
            'AppData/Local/Microsoft/Edge/User Data',
            'AppData/Local/BraveSoftware/Brave-Browser/User Data',
        }
        original_exists = Path.exists
        def fake_exists(p):
            normalized = str(p).replace('\\', '/')
            return any(normalized.endswith(s) for s in existing_suffixes)
        with mock.patch('ytloadlib.environment.Path.exists', fake_exists), mock.patch('ytloadlib.environment.shutil.which', return_value=None):
            found = detect_browsers(platform_name='Windows', home=home)
        self.assertIn('chrome', found)
        self.assertIn('firefox', found)
        self.assertIn('edge', found)
        self.assertIn('brave', found)

class ToolVersionArgumentTests(unittest.TestCase):
    @mock.patch('ytloadlib.environment.subprocess.run')
    @mock.patch('ytloadlib.environment.shutil.which')
    def test_ffmpeg_uses_single_dash_version_flag(self, which, run):
        which.return_value = '/usr/bin/ffmpeg'
        run.return_value.stdout = 'ffmpeg version 8.0\n'
        detect_tool('ffmpeg')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/ffmpeg', '-version'])
