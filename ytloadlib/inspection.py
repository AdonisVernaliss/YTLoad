from __future__ import annotations

import json
import subprocess

from .connection import connection_options
from .runner import classify_failure, failure_message, redact_output
from .validation import BROWSERS, validate_url


def inspect_url(url: str, browser: str | None = None, *, user_agent: str | None = None,
                youtube_client: str = 'auto') -> dict:
    url = validate_url(url)
    if browser is not None and (not isinstance(browser, str) or browser not in BROWSERS):
        raise ValueError('Choose a supported browser.')
    command = ['yt-dlp', '--ignore-config', '--no-warnings', '--no-color', '--skip-download',
               '--dump-single-json', '--flat-playlist', '--playlist-end', '5', '--no-playlist',
               '--socket-timeout', '12', '--retries', '1']
    command.extend(connection_options(browser, user_agent, youtube_client))
    command.extend(['--', url])
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Checking this link took too long. You can still add it to the queue or try again.') from exc
    if result.returncode:
        detail = next((line for line in reversed(result.stderr.splitlines()) if line.startswith('ERROR:')), '')
        raise ValueError(failure_message(classify_failure(result.stderr)) + (' ' + redact_output(detail[:500]) if detail else ''))
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError('The site returned an unreadable preview. Try downloading directly.') from exc
    languages = {}
    for source, key in (('authored', 'subtitles'), ('auto', 'automatic_captions')):
        for code, tracks in (data.get(key) or {}).items():
            if code == 'live_chat':
                continue
            languages.setdefault(code, {'code': code, 'name': tracks[0].get('name', code) if tracks else code, 'sources': []})['sources'].append(source)
    return {
        'title': data.get('title') or 'Untitled', 'channel': data.get('channel') or data.get('uploader'),
        'duration': data.get('duration'), 'is_collection': data.get('_type') in {'playlist', 'multi_video'},
        'count': data.get('playlist_count') or data.get('n_entries'),
        'heights': sorted({f['height'] for f in data.get('formats', []) if isinstance(f.get('height'), (int, float))}, reverse=True),
        'languages': list(languages.values()),
    }
