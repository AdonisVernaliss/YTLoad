from __future__ import annotations

import json
import subprocess

from .connection import connection_options
from .runner import classify_failure, failure_message, redact_output
from .urls import classify_url
from .validation import BROWSERS, validate_url


def _read_inspection(url: str, browser: str | None = None, *, user_agent: str | None = None,
                     youtube_client: str = 'auto') -> dict:
    url = validate_url(url)
    if browser is not None and (not isinstance(browser, str) or browser not in BROWSERS):
        raise ValueError('Choose a supported browser.')
    command = ['yt-dlp', '--ignore-config', '--no-warnings', '--no-color', '--skip-download',
               '--dump-single-json', '--flat-playlist', '--playlist-end', '5',
               '--socket-timeout', '12', '--retries', '1']
    if classify_url(url).kind not in {'playlist', 'channel', 'channel_section'}:
        command.append('--no-playlist')
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
    if not isinstance(data, dict):
        raise ValueError('The site returned an unreadable preview. Try downloading directly.')
    return data


def _summary(data):
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
        'heights': sorted({item['height'] for item in data.get('formats', []) if isinstance(item, dict) and isinstance(item.get('height'), (int, float))}, reverse=True),
        'languages': list(languages.values()),
    }


def inspect_url(url: str, browser: str | None = None, *, user_agent: str | None = None,
                youtube_client: str = 'auto') -> dict:
    return _summary(_read_inspection(url, browser, user_agent=user_agent, youtube_client=youtube_client))


def inspect_public_url(url: str, *, user_agent: str | None = None, youtube_client: str = 'auto') -> dict:
    from .hosted_state import public_url
    from .public_formats import inspection_plans
    url = public_url(url)
    kind = classify_url(url).kind
    data = _read_inspection(url, user_agent=user_agent, youtube_client=youtube_client)
    result = _summary(data)
    if kind in {'playlist', 'channel', 'channel_section'}:
        result['is_collection'] = True
        result.update({'size_estimates': {}, 'direct': None, 'collection_size_notice': True})
    else:
        plans = inspection_plans(data)
        result.update({'size_estimates': plans['estimates'], 'direct': plans['direct'],
                       'public_limit_bytes': plans['public_limit_bytes'], 'collection_size_notice': False})
    return result
