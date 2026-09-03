from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class UrlInfo:
    kind: str
    section: str | None = None
    has_playlist_context: bool = False


_CHANNEL_PREFIXES = ('@', 'channel/', 'c/', 'user/')
_SECTIONS = {'videos', 'shorts', 'streams'}


def unwrap_links(text: str) -> str:
    text = re.sub(r'\[[^\]\r\n]*\]\((https?://[^\s<>]+)\)', r'\1', text)
    return re.sub(r'<(https?://[^<>\s]+)>', r'\1', text)


def split_links(text: str) -> list[str]:
    return list(dict.fromkeys(unwrap_links(text).split()))


def normalize_youtube_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if not _youtube_host(host) or parts.port not in {None, 443 if parts.scheme == 'https' else 80}:
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    clean = [(key, value) for key, value in query if key.lower() not in {'si', 'feature'} and not key.lower().startswith('utm_')]
    segments = parts.path.strip('/').split('/')
    identifier = None
    if host == 'youtu.be' and len(segments) == 1:
        identifier = segments[0]
    elif len(segments) == 2 and segments[0] in {'shorts', 'live', 'embed'}:
        identifier = segments[1]
    elif segments == ['watch']:
        identifier = next((value for key, value in query if key == 'v'), None)
    if identifier and re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
        clean = [('v', identifier), *((key, value) for key, value in clean if key != 'v')]
        return urlunsplit(('https', 'www.youtube.com', '/watch', urlencode(clean), parts.fragment))
    if clean != query:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(clean), parts.fragment))
    return url


def _youtube_host(host: str) -> bool:
    host = host.lower().split(':')[0]
    return host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'music.youtube.com'}


def classify_url(url: str) -> UrlInfo:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    path = parts.path.strip('/')
    query = parse_qs(parts.query)

    if not _youtube_host(host):
        return UrlInfo('generic')

    if host == 'youtu.be' and path:
        return UrlInfo('video', has_playlist_context='list' in query)

    if path == 'watch' and 'v' in query:
        return UrlInfo('video', has_playlist_context='list' in query)

    if path == 'playlist' and 'list' in query:
        return UrlInfo('playlist')

    segments = [s for s in path.split('/') if s]
    if len(segments) >= 2 and segments[0] in {'shorts', 'live', 'embed'}:
        return UrlInfo('video', has_playlist_context='list' in query)
    if segments:
        last = segments[-1]
        section = last if last in _SECTIONS else None
        root_segments = segments[:-1] if section else segments
        root = '/'.join(root_segments)
        if root.startswith('@') or root.startswith('channel/') or root.startswith('c/') or root.startswith('user/'):
            return UrlInfo('channel_section' if section else 'channel', section=section)

    return UrlInfo('generic')


def _strip_channel_section(url: str) -> str:
    parts = urlsplit(url.strip())
    segments = [s for s in parts.path.split('/') if s]
    if segments and segments[-1] in _SECTIONS:
        segments = segments[:-1]
    path = '/' + '/'.join(segments)
    return urlunsplit((parts.scheme or 'https', parts.netloc, path, '', ''))


def expand_channel_url(url: str, scope: str = 'auto') -> list[str]:
    info = classify_url(url)
    if info.kind not in {'channel', 'channel_section'}:
        return [url]
    if scope == 'auto':
        return [url]
    root = _strip_channel_section(url).rstrip('/')
    if scope == 'all':
        return [f'{root}/{section}' for section in ('videos', 'shorts', 'streams')]
    if scope not in _SECTIONS:
        raise ValueError(f'Unknown channel scope: {scope}')
    return [f'{root}/{scope}']
