from __future__ import annotations

import json
import math
import os
import selectors
import signal
import re
import subprocess
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from .connection import connection_options
from .hosted_state import public_url
from .runner import classify_failure


class ProbeError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def metadata_command(url, youtube_client='default', user_agent=None):
    parts = urlsplit(public_url(url))
    identifier = parse_qs(parts.query).get('v', [''])[0]
    if parts.path != '/watch' or not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
        raise ValueError('Use one YouTube video or Short, without a collection.')
    command = ['yt-dlp', '--ignore-config', '--no-warnings', '--no-color', '--skip-download',
               '--dump-single-json', '--no-playlist', '--no-check-formats', '--no-cache-dir',
               '--use-extractors', 'youtube', '--socket-timeout', '12', '--retries', '0',
               '--extractor-retries', '0']
    command.extend(connection_options(None, user_agent, youtube_client))
    return command + ['--', f'https://www.youtube.com/watch?v={identifier}']


def read_metadata(command, *, timeout=45, output_limit=2 * 1024 * 1024, cancel=None):
    if os.name != 'posix':
        raise ProbeError('probe-requires-posix-use-wsl')
    if cancel and cancel.is_set():
        raise ProbeError('cancelled')
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        raise ProbeError('yt-dlp-unavailable') from exc
    buffers = [bytearray(), bytearray()]
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    try:
        for index, pipe in enumerate((process.stdout, process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, index)
        while selector.get_map() or process.poll() is None:
            if cancel and cancel.is_set():
                raise ProbeError('cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError('resolution-timeout')
            for key, _ in selector.select(min(.05, remaining)):
                try:
                    block = os.read(key.fd, 8192)
                except BlockingIOError:
                    continue
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                destination = buffers[key.data]
                if len(destination) + len(block) > (output_limit if key.data == 0 else 65536):
                    raise ProbeError('metadata-too-large')
                destination.extend(block)
        if process.returncode:
            failure = classify_failure(buffers[1].decode('utf-8', errors='replace'))
            raise ProbeError('resolution-' + (failure.value if failure else 'failed'))
        try:
            data = json.loads(buffers[0])
        except (ValueError, UnicodeError) as exc:
            raise ProbeError('invalid-metadata') from exc
        if not isinstance(data, dict):
            raise ProbeError('invalid-metadata')
        return data
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1)
        selector.close()
        process.stdout.close()
        process.stderr.close()


def number(value):
    return value if type(value) in (int, float) and 0 <= value <= 2 ** 53 and math.isfinite(value) else None


def label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.+/-]{1,96}', value) else None


def safe_media_url(value):
    if not isinstance(value, str) or len(value) > 16384 or re.search(r'[\s\x00-\x1f\x7f]', value):
        return None
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or parts.port not in (None, 443) or parts.username or parts.password
                or not re.fullmatch(r'(?:[a-z0-9-]+\.)+googlevideo\.com', parts.hostname or '') or parts.fragment):
            return None
    except ValueError:
        return None
    return value


def reduce_headers(info, item):
    merged = {}
    for source in (info.get('http_headers'), item.get('http_headers')):
        if isinstance(source, dict):
            merged.update({str(key).lower(): value for key, value in source.items()})
    private = bool(info.get('cookies') or item.get('cookies') or any(key in merged for key in ('cookie', 'authorization', 'proxy-authorization')))
    headers = {}
    unreplayable = []
    for key, value in merged.items():
        if key in {'cookie', 'authorization', 'proxy-authorization'}:
            continue
        if key not in {'user-agent', 'accept', 'accept-language', 'referer', 'origin'}:
            unreplayable.append('unlisted-header')
            continue
        if not isinstance(value, str) or len(value) > 1024 or any(ord(char) < 32 or ord(char) > 126 for char in value):
            unreplayable.append(key)
            continue
        if key in {'referer', 'origin'}:
            parts = urlsplit(value)
            if parts.scheme != 'https' or parts.hostname not in {'youtube.com', 'www.youtube.com'} or parts.query or parts.fragment or parts.username or parts.password:
                unreplayable.append(key)
                continue
        headers[key] = value
        if key in {'user-agent', 'referer', 'origin'}:
            unreplayable.append(key)
    return headers, sorted(set(unreplayable)), private


def reduce_metadata(info):
    source = info.get('formats')
    if not isinstance(source, list) or not source:
        raise ProbeError('no-formats')
    if info.get('is_live') or info.get('_type') in {'playlist', 'multi_video'}:
        raise ProbeError('unsupported-source')
    candidates = [item for item in source if isinstance(item, dict) and not item.get('has_drm')]
    def video(item):
        return bool(item.get('vcodec') and item['vcodec'] != 'none')
    def audio(item):
        return bool(item.get('acodec') and item['acodec'] != 'none')
    selectors = {'progressive': lambda item: video(item) and audio(item) and item.get('protocol') in {'http', 'https'},
                 'best-video': lambda item: video(item) and not audio(item),
                 'best-audio': lambda item: audio(item) and not video(item)}
    for role in ('video', 'audio'):
        best = next((item for item in reversed(candidates) if selectors['best-' + role](item)), None)
        if best and best.get('protocol') not in {'http', 'https'}:
            predicate = selectors['best-' + role]
            selectors['dash-' + role] = lambda item, predicate=predicate: predicate(item) and item.get('protocol') == 'https'
    formats = []
    for role, predicate in selectors.items():
        item = next((entry for entry in reversed(candidates) if predicate(entry)), None)
        if item is None:
            formats.append({'role': role, 'available': False, 'url': None})
            continue
        headers, unreplayable, private = reduce_headers(info, item)
        url = safe_media_url(item.get('url'))
        query = parse_qs(urlsplit(url).query) if url else {}
        expiry = query.get('expire', [''])[0]
        options = item.get('downloader_options')
        chunk = number(options.get('http_chunk_size')) if isinstance(options, dict) else None
        formats.append({'role': role, 'available': True, 'format_id': label(item.get('format_id')),
                        'url': url if not private else None, 'protocol': label(item.get('protocol')),
                        'container': label(item.get('ext')), 'vcodec': label(item.get('vcodec')),
                        'acodec': label(item.get('acodec')), 'height': number(item.get('height')),
                        'width': number(item.get('width')), 'fps': number(item.get('fps')),
                        'filesize': number(item.get('filesize')), 'filesize_approx': number(item.get('filesize_approx')),
                        'http_headers': headers, 'unreplayable_headers': unreplayable,
                        'requires_private_headers': private, 'ip_binding_hint': 'ip' in query,
                        'po_token_hint': 'pot' in query,
                        'expires_at': int(expiry) if re.fullmatch(r'\d{1,12}', expiry) else None,
                        'download_options': {'http_chunk_size': chunk} if chunk and chunk <= 10 * 1024 * 1024 else {}})
    return {'schema_version': 1, 'resolution_id': uuid.uuid4().hex, 'resolved_at': int(time.time()),
            'duration': number(info.get('duration')), 'formats': formats}


def resolve_direct(url, youtube_client='default', user_agent=None, *, cancel=None):
    started = time.monotonic()
    result = reduce_metadata(read_metadata(metadata_command(url, youtube_client, user_agent), cancel=cancel))
    result['resolution_ms'] = round((time.monotonic() - started) * 1000)
    result['youtube_client'] = youtube_client
    return result
