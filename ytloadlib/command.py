from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .connection import connection_options
from .formats import quality_selector
from .models import AppConfig, DownloadRequest
from .templates import output_template_for
from .urls import classify_url
from .validation import validate_url

SPONSOR_CATEGORIES = 'sponsor,selfpromo,interaction,intro,outro,preview,music_offtopic'
RESULT_PREFIX = 'YTLOAD_RESULT:'
PROGRESS_PREFIX = 'YTLOAD_PROGRESS:'
RESULT_TEMPLATE = 'after_video:YTLOAD_RESULT:%(.{requested_subtitles,title,requested_downloads})j'
PROGRESS_TEMPLATE = 'download:YTLOAD_PROGRESS:{"downloaded":%(progress.downloaded_bytes|null)j,"total":%(progress.total_bytes,progress.total_bytes_estimate|null)j,"speed":%(progress.speed|null)j,"eta":%(progress.eta|null)j,"status":%(progress.status|null)j,"title":%(info.title|null)j,"index":%(info.playlist_index|null)j,"count":%(info.playlist_count|null)j}'


def _subtitle_languages(value: str) -> str | None:
    value = (value or '').strip()
    if not value or value == 'default':
        return None
    if value == 'orig':
        return '.*-orig'
    if value == 'all':
        return 'all,-live_chat'
    return value


def _archive_enabled(request: DownloadRequest, config: AppConfig, kind: str) -> bool:
    if request.mode not in {'video', 'audio'} or request.transcript_formats or request.subtitles != 'none':
        return False
    if request.archive is not None:
        return request.archive
    return bool(config.archive and kind in {'playlist', 'channel', 'channel_section'})


def archive_profile(request: DownloadRequest) -> str:
    names = ('mode', 'quality', 'audio_format', 'video_container', 'subtitles', 'sub_langs',
             'embed_subs', 'embed_metadata', 'sponsorblock', 'write_info_json', 'write_description',
             'write_thumbnail', 'write_comments')
    value = json.dumps({name: getattr(request, name) for name in names}, sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def build_command(request: DownloadRequest, url: str, config: AppConfig,
                  browser: str | None = None, safari_fallback: bool = False) -> list[str]:
    url = validate_url(url)
    info = classify_url(url)
    root = Path(request.output_root or config.output_root).expanduser().resolve()
    template = output_template_for(root, info.kind, section=info.section, media_kind=request.mode)
    if request.mode in {'video', 'audio'}:
        profile = f'{request.quality}-{request.video_container}' if request.mode == 'video' else request.audio_format
        if request.sponsorblock != 'off':
            profile += f'-{request.sponsorblock}'
        if request.embed_subs:
            subtitle_profile = f'{request.subtitles}:{request.sub_langs}'
            profile += '-subs-' + hashlib.sha256(subtitle_profile.encode()).hexdigest()[:8]
        if not request.embed_metadata:
            profile += '-plain'
        template = template.replace('.%(ext)s', f' [{profile}].%(ext)s')
    cmd = ['yt-dlp', '--ignore-config', '--no-color', '--newline', '--continue', '--no-force-overwrites',
           '--socket-timeout', '20', '--retries', '5', '--fragment-retries', '5',
           '--retry-sleep', 'http:exp=1:20', '--abort-on-unavailable-fragments',
           '--windows-filenames', '-o', template,
           '--progress', '--progress-delta', '0.5', '--progress-template', PROGRESS_TEMPLATE,
           '--print', RESULT_TEMPLATE]
    cmd.append('--abort-on-error' if request.fail_fast else '--no-abort-on-error')
    if info.kind == 'video' or request.no_playlist:
        cmd.append('--no-playlist')
    if request.mode == 'video':
        selector = quality_selector(request.quality or config.quality)
        compatible = request.video_container == 'mp4' or request.quality == 'compatible'
        if compatible:
            height = {'2160p': 2160, '1440p': 1440, '1080p': 1080, '720p': 720, '480p': 480, 'small': 720}.get(request.quality)
            cap = f'[height<={height}]' if height else ''
            selector = f'bestvideo[vcodec^=avc1]{cap}+bestaudio[ext=m4a]/best[vcodec^=avc1][ext=mp4]{cap}'
        container = 'mp4' if compatible else 'mkv'
        if request.video_container == 'mkv':
            container = 'mkv'
        cmd.extend(['-f', selector, '--merge-output-format', container])
        if request.video_container != 'auto' or compatible:
            cmd.extend(['--remux-video', container])
    elif request.mode == 'audio':
        cmd.extend(['-f', 'bestaudio/best', '-x'])
        if request.audio_format and request.audio_format != 'best':
            cmd.extend(['--audio-format', request.audio_format, '--audio-quality', '0'])
    elif request.mode in {'subs', 'metadata'}:
        cmd.append('--skip-download')
    else:
        raise ValueError(f'Unsupported mode: {request.mode}')
    subtitle_policy = request.subtitles
    if (request.mode == 'subs' or request.transcript_formats) and subtitle_policy == 'none':
        subtitle_policy = 'both'
    if info.kind != 'generic':
        cmd.extend(['--sleep-requests', '0.75'])
        if subtitle_policy != 'none':
            cmd.extend(['--sleep-subtitles', '5'])
    if subtitle_policy != 'none':
        if subtitle_policy in {'authored', 'both'}:
            cmd.append('--write-subs')
        if subtitle_policy in {'auto', 'both'}:
            cmd.append('--write-auto-subs')
        languages = _subtitle_languages(request.sub_langs or config.sub_langs)
        if languages:
            cmd.extend(['--sub-langs', languages])
        cmd.extend(['--sub-format', 'vtt/srt/json3'])
        if request.embed_subs and request.mode == 'video':
            cmd.append('--embed-subs')
    if request.mode == 'metadata' or request.write_info_json or request.write_comments:
        cmd.append('--write-info-json')
    for enabled, flag in ((request.write_description, '--write-description'),
                          (request.write_thumbnail, '--write-thumbnail'), (request.write_comments, '--write-comments')):
        if enabled:
            cmd.append(flag)
    if request.embed_metadata and request.mode in {'video', 'audio'}:
        cmd.extend(['--embed-metadata', '--embed-chapters'])
    for value, flag in ((request.playlist_items, '--playlist-items'), (request.date_after, '--dateafter'),
                        (request.date_before, '--datebefore'), (request.max_filesize, '--max-filesize'),
                        (request.limit_rate, '--limit-rate')):
        if value:
            cmd.extend([flag, value])
    if _archive_enabled(request, config, info.kind):
        archive = root / '.ytload-state' / f'{request.mode}-{archive_profile(request)}.txt'
        cmd.extend(['--download-archive', str(archive)])
    if request.sponsorblock == 'mark':
        cmd.extend(['--sponsorblock-mark', SPONSOR_CATEGORIES])
    elif request.sponsorblock == 'remove':
        cmd.extend(['--sponsorblock-remove', SPONSOR_CATEGORIES])
    cmd.extend(connection_options(browser or request.browser, request.user_agent, request.youtube_client, safari_fallback))
    cmd.extend(request.passthrough)
    cmd.extend(['--', url])
    return cmd
