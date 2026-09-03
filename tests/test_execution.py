import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ytloadlib.models import AppConfig, DownloadRequest
from ytloadlib.runner import ProcessResult, run_download, run_process


class ExecutionTests(unittest.TestCase):
    def test_cancel_interrupts_a_silent_process(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.15, cancelled.set)
        timer.start()
        start = time.monotonic()
        try:
            result = run_process([sys.executable, '-c', 'import time; time.sleep(30)'], cancel_event=cancelled, stream=False)
        finally:
            timer.cancel()
        self.assertEqual(result.returncode, 130)
        self.assertLess(time.monotonic() - start, 5)

    def test_logs_are_bounded(self):
        result = run_process([sys.executable, '-c', 'print("line\\n" * 10000)'], stream=False)
        self.assertEqual(result.returncode, 0)
        self.assertLess(len(result.output), 100000)

    def test_subtitle_export_is_part_of_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'Example.en.vtt'
            source.write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello world\n', encoding='utf-8')
            marker = 'YTLOAD_RESULT:' + json.dumps({'filepath': str(root / 'Example.mp4'), 'subtitles': {'en': {'filepath': str(source)}}})
            result = run_download(DownloadRequest(mode='subs', transcript_formats=['txt', 'srt']), 'https://youtu.be/a',
                                  AppConfig(output_root=directory), executor=lambda _: ProcessResult(0, marker))
            self.assertTrue(result.success, result.output)
            self.assertEqual(source.with_suffix('.txt').read_text(), 'Hello world\n')
            self.assertIn(str(source.with_suffix('.srt')), result.files)

    def test_no_captions_is_not_reported_as_success_in_text_mode(self):
        marker = 'YTLOAD_RESULT:' + json.dumps({'filepath': 'NA', 'subtitles': None})
        result = run_download(DownloadRequest(mode='subs'), 'https://youtu.be/a', AppConfig(), executor=lambda _: ProcessResult(0, marker))
        self.assertFalse(result.success)
        self.assertEqual(result.failure_kind.value, 'no_subtitles')

    def test_does_not_read_browser_cookies_without_selection(self):
        commands = []
        def execute(command):
            commands.append(command)
            return ProcessResult(1, 'HTTP Error 403: Forbidden')
        run_download(DownloadRequest(), 'https://youtu.be/a', AppConfig(), available_browsers=['chrome'], executor=execute)
        self.assertTrue(all('--cookies-from-browser' not in command for command in commands))

    def test_subtitle_path_outside_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'private.vtt'
            source.write_text('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nPrivate\n')
            marker = 'YTLOAD_RESULT:' + json.dumps({'subtitles': {'en': {'filepath': str(source)}}})
            result = run_download(DownloadRequest(mode='subs', transcript_formats=['txt']), 'https://youtu.be/a',
                                  AppConfig(output_root=str(root / 'downloads')), executor=lambda _: ProcessResult(0, marker))
            self.assertFalse(result.success)
            self.assertFalse(source.with_suffix('.txt').exists())

    def test_explicit_public_mode_overrides_configured_browser(self):
        commands = []
        def execute(command):
            commands.append(command)
            return ProcessResult(0, 'ok')
        run_download(DownloadRequest(browser=None), 'https://youtu.be/a', AppConfig(browser='chrome'), executor=execute)
        self.assertNotIn('--cookies-from-browser', commands[0])

    def test_cancel_when_process_closes_output_early(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.15, cancelled.set)
        timer.start()
        start = time.monotonic()
        try:
            result = run_process([sys.executable, '-c', 'import os,time; os.close(1); os.close(2); time.sleep(2)'], cancel_event=cancelled, stream=False)
        finally:
            timer.cancel()
        self.assertEqual(result.returncode, 130)
        self.assertLess(time.monotonic() - start, 1.5)
