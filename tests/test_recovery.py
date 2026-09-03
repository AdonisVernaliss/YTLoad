import tempfile
import time
import unittest
from unittest.mock import patch

from ytloadlib.command import build_command
from ytloadlib.jobs import JobManager
from ytloadlib.models import AppConfig, DownloadRequest
from ytloadlib.runner import FailureKind, ProcessResult, run_download


class RecoveryTests(unittest.TestCase):
    def test_caption_requests_are_spaced_without_silently_enabling_browser_access(self):
        request = DownloadRequest(mode='subs', urls=['https://youtu.be/AbCdEf123_-'])
        command = build_command(request, request.urls[0], AppConfig())
        self.assertIn('--sleep-subtitles', command)
        self.assertGreaterEqual(float(command[command.index('--sleep-subtitles') + 1]), 5)
        self.assertNotIn('--cookies-from-browser', command)

    def test_rate_limit_keeps_the_original_error_and_does_not_retry_immediately(self):
        calls = []
        output = "WARNING: No impersonate target is available\nERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"
        def execute(command):
            calls.append(command)
            return ProcessResult(1, output)
        result = run_download(DownloadRequest(mode='subs'), 'https://youtu.be/AbCdEf123_-', AppConfig(), executor=execute)
        self.assertEqual(result.failure_kind, FailureKind.RATE_LIMIT)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('--cookies-from-browser', calls[0])

    def test_failed_caption_job_offers_recovery_without_exposing_connection_values(self):
        output = "WARNING: The extractor specified to use impersonation for this download, but no impersonate target is available\nERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests"
        with tempfile.TemporaryDirectory() as folder, patch('ytloadlib.runner.run_process', return_value=ProcessResult(1, output)):
            manager = JobManager(AppConfig(output_root=folder))
            try:
                manager.add({'urls': ['https://youtu.be/AbCdEf123_-'], 'mode': 'subs', 'user_agent': 'PrivateBrowser/123'})
                for _ in range(100):
                    job = manager.snapshot()[0]
                    if job['status'] == 'failed':
                        break
                    time.sleep(.01)
                self.assertEqual(job['status'], 'failed')
                actions = {item['action'] for item in job.get('recovery', [])}
                self.assertTrue({'browser', 'captions', 'setup'} <= actions, actions)
                self.assertNotIn('PrivateBrowser/123', str(job.get('recovery')))
            finally:
                manager.close()

    def test_public_advice_never_offers_host_browser_access(self):
        from ytloadlib.recovery import recovery_advice
        output = "ERROR: Unable to download video subtitles for 'en': HTTP Error 429\nWARNING: no impersonate target is available"
        advice = recovery_advice(FailureKind.RATE_LIMIT, output, DownloadRequest(mode='subs'), hosted=True)
        self.assertNotIn('browser', {item['action'] for item in advice})
        self.assertNotIn('setup', {item['action'] for item in advice})
        self.assertIn('captions', {item['action'] for item in advice})

    def test_format_failure_offers_format_recovery_without_unrelated_installation(self):
        from ytloadlib.recovery import recovery_advice
        advice = recovery_advice(FailureKind.FORMAT, 'ERROR: Requested format is not available', DownloadRequest())
        self.assertEqual({item['action'] for item in advice}, {'format'})
