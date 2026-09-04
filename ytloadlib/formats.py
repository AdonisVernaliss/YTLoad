from __future__ import annotations

from dataclasses import dataclass


_HEIGHTS = {
    '2160p': 2160,
    '1440p': 1440,
    '1080p': 1080,
    '720p': 720,
    '480p': 480,
}


@dataclass(frozen=True, slots=True)
class VideoFormatPolicy:
    selector: str
    container: str
    compatible: bool
    height: int | None


def quality_height(preset: str) -> int | None:
    return _HEIGHTS.get(preset.lower(), 720 if preset.lower() == 'small' else None)


def quality_selector(preset: str) -> str:
    preset = preset.lower()
    if preset == 'best':
        return 'bestvideo+bestaudio/best'
    if preset in _HEIGHTS:
        height = _HEIGHTS[preset]
        return f'bestvideo[height<={height}]+bestaudio/best[height<={height}]'
    if preset == 'compatible':
        return 'bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]'
    if preset == 'small':
        return 'bestvideo[height<=720][filesize<=?250M]+bestaudio/best[height<=720][filesize<=?250M]'
    raise ValueError(f'Unknown quality preset: {preset}')


def video_format_policy(preset: str, video_container: str) -> VideoFormatPolicy:
    preset = preset.lower()
    compatible = video_container == 'mp4' or preset == 'compatible'
    height = quality_height(preset)
    selector = quality_selector(preset)
    if compatible:
        cap = f'[height<={height}]' if height else ''
        selector = f'bestvideo[vcodec^=avc1]{cap}+bestaudio[ext=m4a]/best[vcodec^=avc1][ext=mp4]{cap}'
    container = 'mkv' if video_container == 'mkv' or not compatible else 'mp4'
    return VideoFormatPolicy(selector, container, compatible, height)
