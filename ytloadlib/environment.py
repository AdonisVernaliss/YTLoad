from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import shutil
import subprocess


@dataclass(frozen=True, slots=True)
class ToolInfo:
    name: str
    installed: bool
    path: str | None
    version: str | None


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    yt_dlp: ToolInfo
    ffmpeg: ToolInfo
    deno: ToolInfo
    browsers: tuple[str, ...]


def detect_tool(name: str) -> ToolInfo:
    path = shutil.which(name)
    if not path:
        return ToolInfo(name, False, None, None)
    version = None
    try:
        version_args = [path, '-version'] if name == 'ffmpeg' else [path, '--version']
        proc = subprocess.run(
            version_args,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr).strip() else None
    except (OSError, subprocess.SubprocessError):
        pass
    return ToolInfo(name, True, path, version)


def detect_browsers(platform_name: str | None = None, home: Path | None = None) -> list[str]:
    system = platform_name or platform.system()
    home = home or Path.home()
    found: list[str] = []

    if system == 'Darwin':
        paths = {
            'chrome': Path('/Applications/Google Chrome.app'),
            'chromium': Path('/Applications/Chromium.app'),
            'firefox': Path('/Applications/Firefox.app'),
            'edge': Path('/Applications/Microsoft Edge.app'),
            'brave': Path('/Applications/Brave Browser.app'),
            'safari': Path('/Applications/Safari.app'),
        }
        for name, path in paths.items():
            if path.exists():
                found.append(name)
        return found

    if system == 'Windows':
        candidates = ['chrome', 'chromium', 'firefox', 'msedge', 'brave']
        mapping = {'msedge': 'edge'}
        for exe in candidates:
            if shutil.which(exe):
                name = mapping.get(exe, exe)
                if name not in found:
                    found.append(name)
        profile_hints = {
            'chrome': home / 'AppData' / 'Local' / 'Google' / 'Chrome' / 'User Data',
            'chromium': home / 'AppData' / 'Local' / 'Chromium' / 'User Data',
            'firefox': home / 'AppData' / 'Roaming' / 'Mozilla' / 'Firefox' / 'Profiles',
            'edge': home / 'AppData' / 'Local' / 'Microsoft' / 'Edge' / 'User Data',
            'brave': home / 'AppData' / 'Local' / 'BraveSoftware' / 'Brave-Browser' / 'User Data',
        }
        for name, path in profile_hints.items():
            if name not in found and path.exists():
                found.append(name)
        return found

    candidates = {
        'chrome': ['google-chrome', 'google-chrome-stable'],
        'chromium': ['chromium', 'chromium-browser'],
        'firefox': ['firefox'],
        'edge': ['microsoft-edge', 'microsoft-edge-stable'],
        'brave': ['brave-browser', 'brave'],
    }
    for name, executables in candidates.items():
        if any(shutil.which(exe) for exe in executables):
            found.append(name)


    profile_hints = {
        'chrome': home / '.config' / 'google-chrome',
        'chromium': home / '.config' / 'chromium',
        'firefox': home / '.mozilla' / 'firefox',
        'brave': home / '.config' / 'BraveSoftware' / 'Brave-Browser',
    }
    for name, path in profile_hints.items():
        if name not in found and path.exists():
            found.append(name)
    return found


def pick_browser(available: list[str] | tuple[str, ...], configured: str | None = None) -> str | None:
    if configured and configured in available:
        return configured
    priority = ('chrome', 'firefox', 'brave', 'edge', 'chromium', 'safari')
    for browser in priority:
        if browser in available:
            return browser
    return None


def detect_environment() -> EnvironmentReport:
    return EnvironmentReport(
        yt_dlp=detect_tool('yt-dlp'),
        ffmpeg=detect_tool('ffmpeg'),
        deno=detect_tool('deno'),
        browsers=tuple(detect_browsers()),
    )
