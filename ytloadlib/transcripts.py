from __future__ import annotations

import html
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Cue:
    start: float
    end: float
    text: str


def _seconds(value: str) -> float:
    parts = value.replace(',', '.').split(':')
    return sum(float(part) * 60 ** index for index, part in enumerate(reversed(parts)))


def _clean(value: str) -> str:
    return ' '.join(html.unescape(re.sub(r'<[^>]*>', '', value)).split())


def parse_captions(content: str, extension: str) -> list[Cue]:
    cues = []
    extension = extension.lower().lstrip('.')
    rolling = extension == 'json3' or (extension == 'vtt' and bool(re.search(r'<\d{2}:\d{2}', content)))
    if extension == 'json3':
        data = json.loads(content)
        for event in data.get('events', []):
            text = _clean(''.join(segment.get('utf8', '') for segment in event.get('segs', [])))
            start = float(event.get('tStartMs', 0)) / 1000
            end = start + float(event.get('dDurationMs', 0)) / 1000
            if text and end > start:
                cues.append(Cue(start, end, text))
    elif extension in {'vtt', 'srt'}:
        timing = re.compile(r'((?:\d+:)?\d{2}:\d{2}[.,]\d+)\s+-->\s+((?:\d+:)?\d{2}:\d{2}[.,]\d+)')
        for block in re.split(r'\n\s*\n', content.replace('\r\n', '\n').lstrip('\ufeff')):
            lines = block.splitlines()
            if lines and lines[0].startswith(('NOTE', 'STYLE', 'REGION')):
                continue
            for index, line in enumerate(lines):
                match = timing.match(line)
                if match:
                    start, end = map(_seconds, match.groups())
                    text = _clean(' '.join(lines[index + 1:]))
                    if text and 0 <= start < end:
                        cues.append(Cue(start, end, text))
                    break
    else:
        raise ValueError(f'Unsupported caption format: {extension}. Choose VTT, SRT or JSON3.')
    cues.sort(key=lambda cue: cue.start)
    result = []
    previous = None
    for cue in cues:
        words = cue.text.split()
        if rolling and previous and cue.start < previous.end:
            old = previous.text.split()
            for count in range(min(len(old), len(words)), 0, -1):
                if old[-count:] == words[:count] and (count > 1 or words == old):
                    words = words[count:]
                    break
        if words:
            result.append(Cue(cue.start, cue.end, ' '.join(words)))
        previous = cue
    if not result:
        raise ValueError('No readable captions were found in this subtitle file.')
    return result


def timestamp(seconds: float, separator: str = '.', milliseconds: bool = True) -> str:
    total = round(seconds * 1000)
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    base = f'{hours:02}:{minutes:02}:{secs:02}'
    return f'{base}{separator}{ms:03}' if milliseconds else base


def export_transcript(source: Path, formats: list[str], timestamps: bool = False) -> list[Path]:
    cues = parse_captions(source.read_text(encoding='utf-8-sig'), source.suffix)
    outputs = []
    for extension in dict.fromkeys(formats):
        target = source.with_suffix('.' + extension)
        if extension not in {'txt', 'srt', 'vtt', 'json'}:
            raise ValueError(f'Unsupported transcript format: {extension}')
        if target == source:
            outputs.append(source)
            continue
        if extension == 'txt':
            content = '\n'.join((f'[{timestamp(cue.start, milliseconds=False)}] ' if timestamps else '') + cue.text for cue in cues) + '\n'
        elif extension == 'json':
            content = json.dumps({'segments': [asdict(cue) for cue in cues]}, ensure_ascii=False, indent=2) + '\n'
        else:
            separator = ',' if extension == 'srt' else '.'
            content = 'WEBVTT\n\n' if extension == 'vtt' else ''
            for index, cue in enumerate(cues, 1):
                content += f'{index}\n{timestamp(cue.start, separator)} --> {timestamp(cue.end, separator)}\n{cue.text}\n\n'
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=source.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            os.replace(temporary, target)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        outputs.append(target)
    return outputs
