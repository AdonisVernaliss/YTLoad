from __future__ import annotations

from pathlib import Path


def output_template_for(root: Path, kind: str, section: str | None = None, media_kind: str = 'video') -> str:
    root = Path(str(root.expanduser()).replace('%', '%%'))
    if kind == 'video' or kind == 'generic':
        folder = {'audio': 'Audio', 'subs': 'Transcripts', 'metadata': 'Metadata'}.get(media_kind, 'Videos')
        path = root / folder / '%(title).100B [%(id)s].%(ext)s'
    elif kind == 'playlist':
        path = root / 'Playlists' / '%(playlist_title).100B' / '%(playlist_index)03d - %(title).100B [%(id)s].%(ext)s'
    elif kind in {'channel', 'channel_section'}:
        display = (section or 'videos').capitalize()
        path = root / 'Channels' / '%(channel).100B' / display / '%(upload_date)s - %(title).100B [%(id)s].%(ext)s'
    else:
        path = root / 'Videos' / '%(title).100B [%(id)s].%(ext)s'
    return str(path)
