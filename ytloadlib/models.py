from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Mode = Literal["video", "audio", "subs", "metadata"]
ChannelScope = Literal["videos", "shorts", "streams", "all", "auto"]
SponsorPolicy = Literal["off", "mark", "remove"]


@dataclass(slots=True)
class AppConfig:
    output_root: str = str(Path.home() / "Downloads" / "YTLoad")
    browser: str | None = None
    quality: str = "best"
    archive: bool = True
    sub_langs: str = "default"
    print_commands: bool = False
    sponsorblock: SponsorPolicy = "off"


@dataclass(slots=True)
class DownloadRequest:
    urls: list[str] = field(default_factory=list)
    mode: Mode = "video"
    quality: str = "best"
    audio_format: str = "best"
    output_root: str | None = None
    channel_scope: ChannelScope = "auto"
    playlist_items: str | None = None
    date_after: str | None = None
    date_before: str | None = None
    max_filesize: str | None = None
    archive: bool | None = None
    video_container: str = "auto"
    subtitles: str = "none"
    sub_langs: str = "default"
    embed_subs: bool = False
    write_info_json: bool = False
    write_description: bool = False
    write_thumbnail: bool = False
    write_comments: bool = False
    embed_metadata: bool = True
    sponsorblock: SponsorPolicy = "off"
    browser: str | None = None
    user_agent: str | None = None
    youtube_client: str = "auto"
    dry_run: bool = False
    print_command: bool = False
    fail_fast: bool = False
    no_playlist: bool = False
    transcript_formats: list[str] = field(default_factory=list)
    transcript_timestamps: bool = False
    limit_rate: str | None = None
    passthrough: list[str] = field(default_factory=list)
