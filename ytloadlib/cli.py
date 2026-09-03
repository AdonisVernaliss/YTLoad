from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .connection import YOUTUBE_CLIENTS
from .models import AppConfig, DownloadRequest

QUALITY_CHOICES = ('best', '2160p', '1440p', '1080p', '720p', '480p', 'compatible', 'small')
AUDIO_CHOICES = ('best', 'mp3', 'm4a', 'opus', 'wav', 'flac')
BROWSERS = ('chrome', 'chromium', 'firefox', 'edge', 'brave', 'safari')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='ytload',
        description='YTLoad: video, audio and transcripts with yt-dlp.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('urls', nargs='*', help='Video, playlist, channel, or other yt-dlp-supported URL(s)')
    p.add_argument('--url-file', help='Text file containing one URL per line')
    p.add_argument('-q', '--quality', choices=QUALITY_CHOICES, help='Video quality preset')
    p.add_argument('--ui', action='store_true', help='Open the local browser interface')
    p.add_argument('--cli', action='store_true', help='Use the interactive terminal wizard')
    p.add_argument('--port', type=int, default=0, help='Local interface port; 0 selects a free port')
    p.add_argument('--no-open', action='store_true', help='Start the interface without opening a browser')
    p.add_argument('--serve', action='store_true', help='Run an isolated public backend behind an HTTPS reverse proxy')
    p.add_argument('--public-origin', help='Public HTTPS origin for --serve')
    p.add_argument('--base-path', default='/ytload', help='Public workspace path prefix')
    p.add_argument('--storage', help='Dedicated temporary storage directory for --serve')
    p.add_argument('--host', default='127.0.0.1', help='Backend bind address for --serve')
    p.add_argument('--lan', action='store_true', help='Allow phone access on a private local network using a connection key')
    p.add_argument('--container', choices=('auto', 'mp4', 'mkv'), default='auto', help='Auto keeps source codecs; MP4 selects compatible H.264/AAC')

    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--audio', nargs='?', const='best', choices=AUDIO_CHOICES, metavar='FORMAT', help='Download audio only; optionally convert to FORMAT')
    mode.add_argument('--subs', action='store_true', help='Download subtitles/captions only')
    mode.add_argument('--metadata', action='store_true', help='Download metadata only (no media)')

    p.add_argument('--with-subs', nargs='?', const='both', choices=('authored', 'auto', 'both'), metavar='TYPE', help='Download subtitles alongside media')
    p.add_argument('--subs-mode', choices=('authored', 'auto', 'both'), default='both', help='Subtitle type for --subs')
    p.add_argument('--sub-langs', help='yt-dlp subtitle language expression, e.g. ru-orig,en or all; "orig" prefers *-orig')
    p.add_argument('--embed-subs', action='store_true', help='Embed downloaded subtitles into media when possible')
    p.add_argument('--transcript', nargs='?', const='txt', help='Export captions as txt,srt,vtt,json; works alone or alongside media')
    p.add_argument('--timestamps', action='store_true', help='Include timestamps in text transcripts')

    p.add_argument('--channel', choices=('auto', 'videos', 'shorts', 'streams', 'all'), default='auto', help='Channel section to download')
    p.add_argument('-I', '--items', dest='playlist_items', help='Playlist item spec, e.g. 1:20,25,-1')
    p.add_argument('--date-after', help='Only items uploaded on/after date accepted by yt-dlp')
    p.add_argument('--date-before', help='Only items uploaded on/before date accepted by yt-dlp')
    p.add_argument('--max-filesize', help='Skip files larger than size, e.g. 2G or 700M')
    p.add_argument('--limit-rate', help='Maximum download speed, e.g. 5M')

    archive = p.add_mutually_exclusive_group()
    archive.add_argument('--archive', dest='archive', action='store_true', help='Use the persistent download archive')
    archive.add_argument('--no-archive', dest='archive', action='store_false', help='Disable the persistent download archive')
    p.set_defaults(archive=None)

    p.add_argument('-o', '--output', dest='output_root', help='Output root directory')
    p.add_argument('--browser', choices=(*BROWSERS, 'none'), help='Use browser cookies; none disables the configured browser')
    p.add_argument('--user-agent', help='Optional User-Agent matching the selected browser session')
    p.add_argument('--youtube-client', choices=YOUTUBE_CLIENTS, default='auto', help='Auto retries supported failures with default + web_safari; explicit choices disable player fallback')
    p.add_argument('--sponsorblock', choices=('off', 'mark', 'remove'), help='SponsorBlock policy')

    p.add_argument('--info-json', action='store_true', help='Write .info.json metadata')
    p.add_argument('--description', action='store_true', help='Write video description')
    p.add_argument('--thumbnail', action='store_true', help='Write thumbnail')
    p.add_argument('--comments', action='store_true', help='Write comments into info JSON (can be slow/large)')
    p.add_argument('--no-embed-metadata', dest='embed_metadata', action='store_false', default=True, help='Do not embed media metadata/chapters')

    p.add_argument('--no-playlist', action='store_true', help='Never follow playlist context from a video URL')
    p.add_argument('--dry-run', action='store_true', help='Print generated command without executing it')
    p.add_argument('--print-command', action='store_true', help='Print each yt-dlp command before execution')
    p.add_argument('--fail-fast', action='store_true', help='Stop on the first top-level URL failure')
    p.add_argument('--config', help='Path to JSON config file')
    p.add_argument('--passthrough', nargs=argparse.REMAINDER, default=[], help='Pass all remaining arguments directly to yt-dlp')
    p.add_argument('--version', action='store_true', help='Show wrapper version and exit')
    p.add_argument('--doctor', action='store_true', help=argparse.SUPPRESS)
    return p


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    args = list(argv) if argv is not None else None
    if args is not None and args and args[0] == 'doctor':
        args = ['--doctor', *args[1:]]
    ns = build_parser().parse_args(args)
    if ns.doctor:
        ns.urls = []
    return ns


def read_urls(positional: Sequence[str], url_file: str | None) -> list[str]:
    urls = [u.strip() for u in positional if u.strip()]
    if url_file:
        path = Path(url_file).expanduser()
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                urls.append(line)
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def request_from_args(ns: argparse.Namespace, config: AppConfig) -> DownloadRequest:
    mode = 'video'
    audio_format = 'best'
    subtitles = 'none'
    if ns.audio is not None:
        mode = 'audio'
        audio_format = ns.audio
    elif ns.subs:
        mode = 'subs'
        subtitles = ns.subs_mode
    elif ns.metadata:
        mode = 'metadata'
    if ns.with_subs:
        subtitles = ns.with_subs

    from .validation import validate_request
    return validate_request(DownloadRequest(
        urls=read_urls(ns.urls, ns.url_file),
        mode=mode,
        quality=ns.quality or config.quality,
        audio_format=audio_format,
        video_container=ns.container,
        output_root=ns.output_root or config.output_root,
        channel_scope=ns.channel,
        playlist_items=ns.playlist_items,
        date_after=ns.date_after,
        date_before=ns.date_before,
        max_filesize=ns.max_filesize,
        limit_rate=ns.limit_rate,
        archive=ns.archive,
        subtitles=subtitles,
        sub_langs=ns.sub_langs or config.sub_langs,
        embed_subs=ns.embed_subs,
        write_info_json=ns.info_json or mode == 'metadata',
        write_description=ns.description,
        write_thumbnail=ns.thumbnail,
        write_comments=ns.comments,
        embed_metadata=ns.embed_metadata,
        sponsorblock=ns.sponsorblock or config.sponsorblock,
        browser=config.browser if ns.browser is None else None if ns.browser == 'none' else ns.browser,
        user_agent=ns.user_agent,
        youtube_client=ns.youtube_client,
        dry_run=ns.dry_run,
        print_command=ns.print_command or config.print_commands,
        fail_fast=ns.fail_fast,
        no_playlist=ns.no_playlist,
        transcript_formats=ns.transcript.split(',') if ns.transcript else [],
        transcript_timestamps=ns.timestamps,
        passthrough=list(ns.passthrough or []),
    ))
