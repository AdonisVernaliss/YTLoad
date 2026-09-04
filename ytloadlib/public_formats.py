from __future__ import annotations

import math

from .direct_resolver import reduce_headers, safe_media_url
from .formats import video_format_policy
from .public_policy import PUBLIC_FILE_BYTES


PUBLIC_QUALITIES = ('best', '2160p', '1440p', '1080p', '720p', '480p', 'small')


def _video(item):
    return isinstance(item, dict) and bool(item.get('vcodec') and item['vcodec'] != 'none')


def _audio(item):
    return isinstance(item, dict) and bool(item.get('acodec') and item['acodec'] != 'none')


def _within_height(item, height):
    value = item.get('height')
    return height is None or type(value) in (int, float) and value <= height


def _size(item):
    for key, approximate in (('filesize', False), ('filesize_approx', True)):
        value = item.get(key)
        if type(value) in (int, float) and value >= 0 and math.isfinite(value):
            return int(value), approximate
    return None, False


def _last(formats, predicate):
    return next((item for item in reversed(formats) if predicate(item)), None)


def estimate_quality(formats, quality, video_container, *, limit=PUBLIC_FILE_BYTES):
    source = [item for item in formats if isinstance(item, dict) and not item.get('has_drm')]
    policy = video_format_policy(quality, video_container)
    small = quality.lower() == 'small'
    allowed_size = lambda item: not small or type(item.get('filesize')) not in (int, float) or item['filesize'] <= 250 * 1024 ** 2
    video = _last(source, lambda item: _video(item) and not _audio(item) and _within_height(item, policy.height) and allowed_size(item)
                  and (not policy.compatible or str(item.get('vcodec', '')).startswith('avc1')))
    audio = _last(source, lambda item: _audio(item) and not _video(item)
                  and (not policy.compatible or item.get('ext') == 'm4a'))
    selected = [video, audio] if video and audio else []
    if not selected:
        progressive = _last(source, lambda item: _video(item) and _audio(item) and _within_height(item, policy.height) and allowed_size(item)
                            and (not policy.compatible or (str(item.get('vcodec', '')).startswith('avc1') and item.get('ext') == 'mp4')))
        selected = [progressive] if progressive else []
    sizes = [_size(item) for item in selected]
    total = sum(value for value, _ in sizes) if selected and all(value is not None for value, _ in sizes) else None
    approximate = total is not None and any(value for _, value in sizes)
    return {
        'quality': quality,
        'policy': 'compatible' if policy.compatible else 'source',
        'bytes': total,
        'approximate': approximate,
        'over_limit': total is not None and total > limit,
        'near_limit': total is not None and .95 * limit <= total <= limit,
    }


def public_direct_candidate(info):
    if not isinstance(info, dict) or info.get('is_live'):
        return None
    formats = info.get('formats')
    if not isinstance(formats, list):
        return None
    candidates = []
    for item in formats:
        if not _video(item) or not _audio(item) or item.get('has_drm') or item.get('protocol') != 'https':
            continue
        url = safe_media_url(item.get('url'))
        headers, unreplayable, private = reduce_headers(info, item)
        if not url or private or set(unreplayable) - {'user-agent'}:
            continue
        value, approximate = _size(item)
        height = item.get('height') if type(item.get('height')) in (int, float) else None
        candidates.append({'url': url, 'height': height, 'container': item.get('ext') if isinstance(item.get('ext'), str) else None,
                           'bytes': value, 'approximate': approximate})
    return candidates[-1] if candidates else None


def inspection_plans(info, *, limit=PUBLIC_FILE_BYTES):
    formats = info.get('formats') if isinstance(info, dict) else None
    if not isinstance(formats, list):
        formats = []
    estimates = {
        'source': {quality: estimate_quality(formats, quality, 'auto', limit=limit) for quality in PUBLIC_QUALITIES},
        'compatible': {quality: estimate_quality(formats, quality, 'mp4', limit=limit) for quality in (*PUBLIC_QUALITIES, 'compatible')},
    }
    return {'estimates': estimates, 'direct': public_direct_candidate(info), 'public_limit_bytes': limit}
