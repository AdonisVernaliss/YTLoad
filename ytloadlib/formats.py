from __future__ import annotations


_HEIGHTS = {
    "2160p": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
}


def quality_selector(preset: str) -> str:
    preset = preset.lower()
    if preset == "best":
        return "bestvideo+bestaudio/best"
    if preset in _HEIGHTS:
        h = _HEIGHTS[preset]
        return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
    if preset == "compatible":
        return "bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]"
    if preset == "small":
        return "bestvideo[height<=720][filesize<=?250M]+bestaudio/best[height<=720][filesize<=?250M]"
    raise ValueError(f"Unknown quality preset: {preset}")
