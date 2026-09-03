import unittest
from dataclasses import replace

from ytloadlib.models import AppConfig, DownloadRequest
from ytloadlib.runner import FailureKind, ProcessResult, classify_failure, run_download


class FailureClassificationTests(unittest.TestCase):
    def test_rate_limit_is_distinct_from_connection_failure(self):
        kind = classify_failure('ERROR: Unable to download subtitles: HTTP Error 429: Too Many Requests')
        self.assertEqual(kind.value, 'rate_limit')

    def test_terminal_error_takes_priority_over_unrelated_warnings(self):
        output = 'WARNING: Some formats require a GVS PO Token\nERROR: unable to download video data: HTTP Error 403: Forbidden'
        self.assertEqual(classify_failure(output), FailureKind.HTTP_403)

    def test_classifies_403(self):
        self.assertEqual(classify_failure('HTTP Error 403: Forbidden'), FailureKind.HTTP_403)

    def test_classifies_sign_in(self):
        self.assertEqual(classify_failure('Sign in to confirm you are not a bot'), FailureKind.AUTH)

    def test_classifies_po_token(self):
        self.assertEqual(classify_failure('formats require a GVS PO Token'), FailureKind.PO_TOKEN)

    def test_classifies_js_challenge(self):
        self.assertEqual(classify_failure('Failed to solve JavaScript challenge'), FailureKind.CHALLENGE)

    def test_classifies_format_error(self):
        self.assertEqual(classify_failure('Requested format is not available'), FailureKind.FORMAT)

    def test_other(self):
        self.assertEqual(classify_failure('disk is full'), FailureKind.DISK)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(output_root='/tmp/out', archive=False)
        self.req = DownloadRequest(mode='video')

    def test_success_does_not_retry(self):
        commands = []
        def executor(cmd):
            commands.append(cmd)
            return ProcessResult(0, 'ok')
        result = run_download(self.req, 'https://youtu.be/a', self.config, available_browsers=['chrome'], executor=executor)
        self.assertTrue(result.success)
        self.assertEqual(len(commands), 1)
        self.assertNotIn('--cookies-from-browser', commands[0])

    def test_retry_uses_alternative_player_without_cookies(self):
        commands = []
        outcomes = [ProcessResult(1, 'HTTP Error 403: Forbidden'), ProcessResult(0, 'ok')]
        def executor(cmd):
            commands.append(cmd)
            return outcomes.pop(0)
        result = run_download(self.req, 'https://youtu.be/a', self.config, available_browsers=['chrome'], executor=executor)
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)
        self.assertNotIn('--cookies-from-browser', commands[0])
        self.assertNotIn('--cookies-from-browser', commands[1])
        self.assertIn('--extractor-args', commands[1])

    def test_non_retryable_error_stops(self):
        commands = []
        def executor(cmd):
            commands.append(cmd)
            return ProcessResult(1, 'disk is full')
        result = run_download(self.req, 'https://youtu.be/a', self.config, available_browsers=['chrome'], executor=executor)
        self.assertFalse(result.success)
        self.assertEqual(result.failure_kind, FailureKind.DISK)
        self.assertEqual(len(commands), 1)

    def test_retryable_without_browser_is_bounded(self):
        commands = []
        def executor(cmd):
            commands.append(cmd)
            return ProcessResult(1, 'HTTP Error 403: Forbidden')
        result = run_download(self.req, 'https://youtu.be/a', self.config, available_browsers=[], executor=executor)
        self.assertFalse(result.success)
        self.assertEqual(len(commands), 2)

    def test_explicit_browser_starts_with_cookies_then_safari(self):
        req = replace(self.req, browser='firefox')
        commands = []
        outcomes = [ProcessResult(1, 'HTTP Error 403: Forbidden'), ProcessResult(0, 'ok')]
        def executor(cmd):
            commands.append(cmd)
            return outcomes.pop(0)
        result = run_download(req, 'https://youtu.be/a', self.config, available_browsers=['firefox'], executor=executor)
        self.assertTrue(result.success)
        self.assertIn('--cookies-from-browser', commands[0])
        self.assertNotIn('--extractor-args', commands[0])
        self.assertIn('--extractor-args', commands[1])

    def test_dry_run_never_executes(self):
        req = replace(self.req, dry_run=True)
        called = False
        def executor(cmd):
            nonlocal called
            called = True
            return ProcessResult(0, '')
        result = run_download(req, 'https://youtu.be/a', self.config, available_browsers=['chrome'], executor=executor)
        self.assertTrue(result.success)
        self.assertFalse(called)
        self.assertEqual(result.attempts, 0)
        self.assertTrue(result.commands)


if __name__ == '__main__':
    unittest.main()
