import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_distribution_files_exist(self):
        required = [
            'README.md', 'config.example.json', 'install-macos.sh',
            'install-linux.sh', 'install-windows.ps1', 'LICENSE', '.gitignore',
        ]
        for name in required:
            self.assertTrue((ROOT / name).exists(), name)

    def test_posix_installers_are_executable(self):
        for name in ('install-macos.sh', 'install-linux.sh'):
            mode = (ROOT / name).stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, name)

    def test_readme_contains_core_examples(self):
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        for token in ('--quality 1080p', '--audio mp3', '--channel all', '--dry-run', 'doctor'):
            self.assertIn(token, text)


class SmokeTests(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(ROOT / 'ytload.py'), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=env,
            timeout=20,
        )

    def test_help(self):
        proc = self.run_cli('--help')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('YTLoad', proc.stdout)

    def test_dry_run_video(self):
        proc = self.run_cli('https://youtu.be/example', '--quality', '1080p', '--dry-run')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('yt-dlp', proc.stdout)
        self.assertIn('height<=1080', proc.stdout)

    def test_doctor_with_fake_tools(self):
        with tempfile.TemporaryDirectory() as td:
            bindir = Path(td)
            for name, version in [('yt-dlp', '2026.08.27'), ('ffmpeg', 'ffmpeg version 8.0'), ('deno', 'deno 2.4.0')]:
                f = bindir / name
                f.write_text(f'#!/bin/sh\necho "{version}"\n', encoding='utf-8')
                f.chmod(0o755)
            env = os.environ.copy()
            env['PATH'] = str(bindir)
            proc = self.run_cli('doctor', env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('2026.08.27', proc.stdout)
        self.assertIn('ffmpeg version 8.0', proc.stdout)
        self.assertIn('deno 2.4.0', proc.stdout)


    def test_missing_explicit_config_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli('https://youtu.be/a', '--dry-run', '--config', str(Path(directory) / 'missing.json'))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)
