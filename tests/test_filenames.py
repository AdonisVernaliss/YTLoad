import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from ytloadlib.command import build_command
from ytloadlib.models import AppConfig, DownloadRequest


@unittest.skipUnless(shutil.which('yt-dlp'), 'yt-dlp is not installed')
class FilenameTests(unittest.TestCase):
    def test_long_unicode_names_keep_video_id_and_quality_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / 'source.json'
            metadata.write_text(json.dumps({'id': 'AbCdEf123_-', 'title': 'Длинное название ' * 100,
                                            'channel': 'Канал ' * 100, 'upload_date': '20260903',
                                            'url': 'https://example.org/video.mp4', 'ext': 'mp4',
                                            'extractor': 'generic', 'webpage_url': 'https://example.org/video.mp4'}))
            request = DownloadRequest(quality='480p', video_container='mp4', output_root=directory)
            command = build_command(request, 'https://youtube.com/@example/videos', AppConfig())
            template = command[command.index('-o') + 1]
            filename_options = ['-o', template]
            if '--trim-filenames' in command:
                filename_options += ['--trim-filenames', command[command.index('--trim-filenames') + 1]]
            result = subprocess.run(['yt-dlp', '--ignore-config', '--no-warnings', '--simulate', '--get-filename',
                                     *filename_options, '--load-info-json', str(metadata)],
                                    capture_output=True, text=True, timeout=15, check=True)
            path = Path(result.stdout.strip())
            self.assertTrue(path.name.endswith('[AbCdEf123_-] [480p-mp4].mp4'), path.name)
            self.assertLessEqual(len(path.name.encode()), 180)
            self.assertLessEqual(len(path.parent.parent.name.encode()), 100)
