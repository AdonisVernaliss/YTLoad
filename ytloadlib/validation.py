from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path
from urllib.parse import urlsplit

from .connection import validate_connection
from .formats import quality_selector
from .models import AppConfig, DownloadRequest
from .urls import normalize_youtube_url, unwrap_links

BROWSERS = {'chrome', 'chromium', 'firefox', 'edge', 'brave', 'safari'}
FORMATS = {'txt', 'srt', 'vtt', 'json'}


def validate_url(url: str) -> str:
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('Enter a valid HTTP or HTTPS video, playlist or channel URL.')
    url = unwrap_links(url).strip()
    if re.search(r'[\s\x00-\x1f\x7f]', url):
        raise ValueError('Enter a valid HTTP or HTTPS video, playlist or channel URL.')
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise ValueError('The URL is malformed.') from exc
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('Use an HTTP or HTTPS URL without embedded credentials.')
    return normalize_youtube_url(url)


def validate_config(config: AppConfig) -> None:
    for name in ('archive', 'print_commands'):
        if type(getattr(config, name)) is not bool:
            raise ValueError(f'{name} must be true or false.')
    for name in ('output_root', 'quality', 'sub_langs', 'sponsorblock'):
        if not isinstance(getattr(config, name), str) or not getattr(config, name).strip():
            raise ValueError(f'{name} must be a non-empty string.')
    quality_selector(config.quality)
    if config.browser is not None and (not isinstance(config.browser, str) or config.browser not in BROWSERS):
        raise ValueError('Unsupported browser in configuration.')
    if config.sponsorblock not in {'off', 'mark', 'remove'}:
        raise ValueError('Unsupported SponsorBlock policy.')


def validate_request(request: DownloadRequest) -> DownloadRequest:
    if not isinstance(request.urls, list) or not request.urls or len(request.urls) > 100:
        raise ValueError('Add between 1 and 100 URLs per batch.')
    request.urls = list(dict.fromkeys(validate_url(url) for url in request.urls))
    validate_connection(request.user_agent, request.youtube_client)
    for name, options in {
        'mode': {'video', 'audio', 'subs', 'metadata'},
        'audio_format': {'best', 'mp3', 'm4a', 'opus', 'wav', 'flac'},
        'video_container': {'auto', 'mp4', 'mkv'},
        'subtitles': {'none', 'authored', 'auto', 'both'},
        'channel_scope': {'auto', 'videos', 'shorts', 'streams', 'all'},
        'sponsorblock': {'off', 'mark', 'remove'},
    }.items():
        value = getattr(request, name)
        if not isinstance(value, str) or value not in options:
            raise ValueError(f'Invalid {name.replace("_", " ")}.')
    if not isinstance(request.quality, str):
        raise ValueError('Choose a video quality.')
    quality_selector(request.quality)
    if request.browser is not None and (not isinstance(request.browser, str) or request.browser not in BROWSERS):
        raise ValueError('Choose a supported browser or no browser.')
    for name in ('embed_subs', 'write_info_json', 'write_description', 'write_thumbnail', 'write_comments',
                 'embed_metadata', 'dry_run', 'print_command', 'fail_fast', 'no_playlist', 'transcript_timestamps'):
        if type(getattr(request, name)) is not bool:
            raise ValueError(f'{name} must be true or false.')
    if request.archive is not None and type(request.archive) is not bool:
        raise ValueError('archive must be true, false or null.')
    if not isinstance(request.transcript_formats, list) or any(not isinstance(f, str) or f not in FORMATS for f in request.transcript_formats):
        raise ValueError('Transcript formats: txt, srt, vtt, json.')
    request.transcript_formats = list(dict.fromkeys(request.transcript_formats))
    if request.mode == 'subs' and not request.transcript_formats:
        request.transcript_formats = ['txt']
    if request.transcript_formats and request.subtitles == 'none':
        request.subtitles = 'both'
    if request.embed_subs and (request.mode != 'video' or request.subtitles == 'none'):
        raise ValueError('Embedded subtitles require video mode and a subtitle source.')
    if not isinstance(request.sub_langs, str) or not request.sub_langs.strip() or len(request.sub_langs) > 300:
        raise ValueError('Choose subtitle languages, for example en, ru, orig or all.')
    for name in ('output_root', 'playlist_items', 'date_after', 'date_before', 'max_filesize', 'limit_rate'):
        value = getattr(request, name)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 4096 or '\x00' in value or '\n' in value):
            raise ValueError(f'Invalid {name.replace("_", " ")}.')
    for name in ('max_filesize', 'limit_rate'):
        value = getattr(request, name)
        if value and not re.fullmatch(r'[1-9]\d*(?:\.\d+)?[KMGTP]?(?:i?B)?', value, re.I):
            raise ValueError(f'{name.replace("_", " ")} must look like 500M or 2G.')
    if request.playlist_items and not re.fullmatch(r'[\d:,\-]+', request.playlist_items):
        raise ValueError('Playlist items must look like 1:10,15,20.')
    if request.date_after and request.date_before:
        after, before = request.date_after.replace('-', ''), request.date_before.replace('-', '')
        if after.isdigit() and before.isdigit() and after > before:
            raise ValueError('The start date must be before the end date.')
    return request


def request_from_payload(payload: dict, config: AppConfig) -> DownloadRequest:
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object.')
    excluded = {'passthrough', 'dry_run', 'print_command'}
    allowed = {field.name for field in fields(DownloadRequest)} - excluded
    if set(payload) - allowed:
        raise ValueError('Unknown download setting.')
    defaults = dict(output_root=config.output_root, quality=config.quality, sub_langs=config.sub_langs,
                    browser=config.browser, sponsorblock=config.sponsorblock)
    defaults.update(payload)
    return validate_request(DownloadRequest(**defaults))
