import subprocess
import unittest
from unittest.mock import patch

from ytloadlib.cli import parse_args, request_from_args
from ytloadlib.command import build_command
from ytloadlib.inspection import inspect_url
from ytloadlib.models import AppConfig
from ytloadlib.runner import ProcessResult, run_download
from ytloadlib.validation import request_from_payload


class ConnectionTests(unittest.TestCase):
    def request(self, **settings):
        return request_from_payload({'urls': ['https://www.youtube.com/watch?v=AbCdEf123_-'], **settings}, AppConfig())

    def test_cli_connection_options_reach_mp4_download(self):
        request = request_from_args(parse_args([
            'https://youtube.com/@example', '--channel', 'all', '--container', 'mp4',
            '--browser', 'chrome', '--user-agent', 'Mozilla/5.0 TestBrowser/1.0',
            '--youtube-client', 'web_safari',
        ]), AppConfig())
        command = build_command(request, request.urls[0], AppConfig())
        self.assertEqual(command[command.index('--user-agent') + 1], 'Mozilla/5.0 TestBrowser/1.0')
        self.assertEqual(command[command.index('--cookies-from-browser') + 1], 'chrome')
        self.assertEqual(command[command.index('--extractor-args') + 1], 'youtube:player_client=default,web_safari')
        self.assertEqual(command[command.index('--merge-output-format') + 1], 'mp4')
        self.assertIn('bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]', command[command.index('-f') + 1])
        self.assertNotIn('--no-playlist', command)
        self.assertEqual(request.channel_scope, 'all')

    def test_automatic_retry_keeps_selected_browser_and_user_agent(self):
        request = self.request(browser='chrome', user_agent='Mozilla/5.0 TestBrowser/1.0')
        commands = []
        def execute(command):
            commands.append(command)
            return ProcessResult(1, 'ERROR: HTTP Error 403: Forbidden')
        result = run_download(request, request.urls[0], AppConfig(), executor=execute)
        self.assertEqual(result.attempts, 2)
        self.assertNotIn('--extractor-args', commands[0])
        self.assertEqual(commands[1][commands[1].index('--extractor-args') + 1], 'youtube:player_client=default,web_safari')
        for command in commands:
            self.assertEqual(command[command.index('--user-agent') + 1], 'Mozilla/5.0 TestBrowser/1.0')
            self.assertEqual(command[command.index('--cookies-from-browser') + 1], 'chrome')

    def test_explicit_player_selection_is_not_overridden_or_repeated(self):
        for client, expected in [('default', 'youtube:player_client=default'), ('web_safari', 'youtube:player_client=default,web_safari')]:
            with self.subTest(client=client):
                request = self.request(youtube_client=client)
                result = run_download(request, request.urls[0], AppConfig(), executor=lambda _: ProcessResult(1, 'ERROR: HTTP Error 403: Forbidden'))
                self.assertEqual(result.attempts, 1)
                command = result.commands[0]
                self.assertEqual(command[command.index('--extractor-args') + 1], expected)
                self.assertNotIn('--cookies-from-browser', command)

    def test_user_agent_rejects_invalid_header_values(self):
        for value in ['', ' ', '--exec', 'Mozilla\r\nCookie: secret', 'A\x00B', 'A\tB', 'A' * 1025, [], 2]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.request(user_agent=value)

    def test_player_selection_rejects_unrecognized_values(self):
        for value in [None, [], 'web_safari;po_token=secret', 'all']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.request(youtube_client=value)

    def test_preview_uses_the_selected_connection_settings(self):
        commands = []
        def execute(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, '{"title":"Example","formats":[]}', '')
        with patch('ytloadlib.inspection.subprocess.run', side_effect=execute):
            result = inspect_url('https://youtu.be/AbCdEf123_-', 'chrome', user_agent='Mozilla/5.0 TestBrowser/1.0', youtube_client='web_safari')
        self.assertEqual(result['title'], 'Example')
        self.assertEqual(commands[0][commands[0].index('--user-agent') + 1], 'Mozilla/5.0 TestBrowser/1.0')
        self.assertEqual(commands[0][commands[0].index('--extractor-args') + 1], 'youtube:player_client=default,web_safari')

    def test_preview_rejects_header_injection_before_spawning_a_process(self):
        with self.assertRaises(ValueError):
            inspect_url('https://youtu.be/AbCdEf123_-', user_agent='Mozilla\r\nCookie: secret')
