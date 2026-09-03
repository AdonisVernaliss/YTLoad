import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from ytloadlib.models import AppConfig, DownloadRequest
from ytloadlib.runner import run_download


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


@unittest.skipUnless(all(shutil.which(name) for name in ('yt-dlp', 'ffmpeg', 'ffprobe')), 'Media tools are not installed')
class MediaIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name).resolve()
        source = cls.root / 'source'
        source.mkdir()
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=green:s=160x90:r=12',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source / 'sample.mp4')], check=True, timeout=15)
        (source / 'captions.vtt').write_text('WEBVTT\n\n00:00:00.000 --> 00:00:00.900\nA sample caption.\n', encoding='utf-8')
        (source / 'index.html').write_text('<html><title>Sample video</title><video controls><source src="sample.mp4" type="video/mp4"><track kind="subtitles" src="captions.vtt" srclang="en" label="English"></video></html>', encoding='utf-8')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(source)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/index.html'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.directory.cleanup()

    def download(self, name, **options):
        output = self.root / name
        output.mkdir()
        events = []
        result = run_download(DownloadRequest(output_root=str(output), **options), self.url,
                              AppConfig(output_root=str(output)), on_event=events.append)
        self.assertTrue(result.success, result.output)
        return result, events

    def test_video_download_contains_video_and_audio(self):
        result, events = self.download('video')
        media = next(path for path in result.files if Path(path).suffix in {'.mp4', '.mkv'})
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', media], capture_output=True, text=True, check=True)
        self.assertEqual({stream['codec_type'] for stream in json.loads(probe.stdout)['streams']}, {'audio', 'video'})
        self.assertTrue(any(event['type'] == 'progress' for event in events))

    def test_audio_conversion_and_transcript_together(self):
        result, _ = self.download('audio', mode='audio', audio_format='mp3', subtitles='both', sub_langs='en', transcript_formats=['txt', 'srt', 'json'])
        self.assertTrue(any(path.endswith('.mp3') for path in result.files))
        transcript = next(Path(path) for path in result.files if path.endswith('.txt'))
        self.assertEqual(transcript.read_text(), 'A sample caption.\n')
        self.assertTrue(any(path.endswith('.srt') for path in result.files))

    def test_transcripts_without_downloading_media(self):
        result, _ = self.download('transcripts', mode='subs', subtitles='both', sub_langs='en', transcript_formats=['txt', 'srt', 'vtt', 'json'])
        self.assertEqual({Path(path).suffix for path in result.files}, {'.txt', '.srt', '.vtt', '.json'})
        self.assertFalse(any(Path(path).suffix in {'.mp4', '.mkv', '.mp3'} for path in result.files))

    def test_metadata_has_a_downloadable_result(self):
        result, _ = self.download('metadata', mode='metadata')
        path = next(Path(path) for path in result.files if path.endswith('.info.json'))
        self.assertEqual(json.loads(path.read_text())['title'], 'Sample video (1)')
