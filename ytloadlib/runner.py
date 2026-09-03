from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
from typing import Callable

from .command import PROGRESS_PREFIX, RESULT_PREFIX, build_command
from .models import AppConfig, DownloadRequest
from .transcripts import export_transcript


class FailureKind(str, Enum):
    HTTP_403 = 'http_403'
    AUTH = 'auth'
    PO_TOKEN = 'po_token'
    CHALLENGE = 'challenge'
    FORMAT = 'format'
    NO_SUBTITLES = 'no_subtitles'
    CANCELLED = 'cancelled'
    NETWORK = 'network'
    RATE_LIMIT = 'rate_limit'
    DISK = 'disk'
    OTHER = 'other'


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    output: str


@dataclass(frozen=True, slots=True)
class DownloadRunResult:
    success: bool
    returncode: int
    output: str
    attempts: int
    failure_kind: FailureKind | None
    commands: tuple[tuple[str, ...], ...]
    files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


_RETRYABLE = {FailureKind.HTTP_403, FailureKind.AUTH, FailureKind.PO_TOKEN, FailureKind.CHALLENGE, FailureKind.FORMAT}


def classify_failure(output: str) -> FailureKind:
    text = (failure_detail(output) or output).lower()
    for kind, phrases in (
        (FailureKind.RATE_LIMIT, ('http error 429', '429 too many requests')),
        (FailureKind.PO_TOKEN, ('po token', 'po_token', 'proof of origin')),
        (FailureKind.HTTP_403, ('http error 403', '403 forbidden')),
        (FailureKind.AUTH, ('sign in to confirm', 'not a bot', 'login required', 'authentication')),
        (FailureKind.CHALLENGE, ('javascript challenge', 'js challenge', 'challenge solver', 'failed to solve')),
        (FailureKind.FORMAT, ('requested format is not available', 'no video formats found')),
        (FailureKind.DISK, ('no space left', 'disk is full', 'permission denied')),
        (FailureKind.NETWORK, ('timed out', 'unable to download', 'connection refused', 'name resolution')),
    ):
        if any(phrase in text for phrase in phrases):
            return kind
    return FailureKind.OTHER


def failure_message(kind: FailureKind | None) -> str:
    return {
        FailureKind.HTTP_403: 'The site refused this download. Update yt-dlp and try again; select browser cookies only if this video requires your account.',
        FailureKind.AUTH: 'This video requires sign-in. Select a browser where you are already signed in, then retry.',
        FailureKind.PO_TOKEN: 'YouTube requires a PO token. Update yt-dlp or configure a supported PO Token Provider.',
        FailureKind.CHALLENGE: 'YouTube could not complete its JavaScript challenge. Update yt-dlp and install or update Deno.',
        FailureKind.FORMAT: 'This quality or codec is unavailable. Try Best available with the Auto container.',
        FailureKind.NO_SUBTITLES: 'No captions matched your selection. Try another language or include automatic captions. This app exports existing captions; it does not transcribe audio.',
        FailureKind.CANCELLED: 'Cancelled. Partial media files are kept so a retry can resume them.',
        FailureKind.NETWORK: 'A network request failed. Check your connection or wait before retrying if the site is rate-limiting requests.',
        FailureKind.RATE_LIMIT: 'The site is limiting requests (HTTP 429). Wait before retrying and request fewer caption languages at a time.',
        FailureKind.DISK: 'Could not write the download. Check free disk space and folder permissions.',
    }.get(kind, 'The download could not finish. Open the activity log for details.')


def redact_output(value: str) -> str:
    value = re.sub(r'https?://\S+', '[URL]', value)
    value = value.replace(str(Path.home()), '~')
    return re.sub(r'(?i)(authorization|cookie|password|token)\s*[:=]\s*\S+', r'\1=[redacted]', value)


def failure_detail(output: str) -> str:
    errors = re.findall(r'^ERROR:\s*(.+)', output, re.M | re.I)
    return redact_output(errors[-1].strip())[:1000] if errors else ''


def _stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=5)
        proc.wait(timeout=3)
    except (OSError, subprocess.SubprocessError):
        try:
            if os.name == 'posix':
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        proc.wait(timeout=3)


def run_process(command: list[str], *, on_line: Callable[[str], None] | None = None,
                cancel_event: threading.Event | None = None, stream: bool = True) -> ProcessResult:
    if cancel_event and cancel_event.is_set():
        return ProcessResult(130, 'Cancelled')
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace',
                                bufsize=1, start_new_session=os.name == 'posix')
    except OSError as exc:
        return ProcessResult(127, str(exc))
    lines: deque[str] = deque(maxlen=300)
    incoming: queue.Queue = queue.Queue(maxsize=128)
    stopped = threading.Event()

    def read_output():
        try:
            for line in proc.stdout:
                while not stopped.is_set():
                    try:
                        incoming.put(line, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if stopped.is_set():
                    break
        finally:
            stopped.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while not stopped.is_set() or not incoming.empty() or proc.poll() is None:
            if cancel_event and cancel_event.is_set():
                _stop_process(proc)
                return ProcessResult(130, ''.join(lines) + '\nCancelled')
            try:
                line = incoming.get(timeout=0.1)
            except queue.Empty:
                continue
            if on_line:
                on_line(line.rstrip('\r\n'))
            if not line.startswith((RESULT_PREFIX, PROGRESS_PREFIX)):
                safe = redact_output(line[:2000])
                lines.append(safe)
                if stream:
                    print(safe, end='')
        return ProcessResult(proc.wait(), ''.join(lines))
    except KeyboardInterrupt:
        _stop_process(proc)
        return ProcessResult(130, ''.join(lines) + '\nCancelled')
    finally:
        stopped.set()
        if proc.poll() is None:
            _stop_process(proc)
        reader.join(timeout=1)
        proc.stdout.close()


def printable_command(command: list[str]) -> str:
    return shlex.join(command)


def run_download(request: DownloadRequest, url: str, config: AppConfig, *,
                 available_browsers: list[str] | None = None,
                 executor: Callable[[list[str]], ProcessResult] | None = None,
                 on_event: Callable[[dict], None] | None = None,
                 cancel_event: threading.Event | None = None) -> DownloadRunResult:
    browser = request.browser
    specs = [(browser, False)]
    if request.youtube_client == 'auto':
        specs.append((browser, True))
    commands = [build_command(request, url, config, browser=b, safari_fallback=s) for b, s in specs]
    if request.dry_run:
        return DownloadRunResult(True, 0, '', 0, None, (tuple(commands[0]),))
    root = Path(request.output_root or config.output_root).expanduser().resolve()
    files: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    history: list[tuple[str, ...]] = []
    last = ProcessResult(1, '')
    kind = None
    caption_count = 0

    def emit(event):
        if on_event:
            on_event(event)

    def add_file(value):
        if not isinstance(value, str) or value in {'NA', ''}:
            return None
        path = Path(value).resolve()
        if not path.is_relative_to(root):
            raise ValueError('A generated file path is outside the output folder.')
        if path.is_file() and str(path) not in files:
            files.append(str(path))
            emit({'type': 'file', 'path': str(path), 'name': path.name})
        return path

    def consume(line):
        nonlocal caption_count
        if line.startswith(PROGRESS_PREFIX):
            try:
                data = json.loads(line[len(PROGRESS_PREFIX):])
                emit({'type': 'progress', **data})
                if on_event is None:
                    downloaded, total = data.get('downloaded'), data.get('total')
                    if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and total:
                        print(f'\rDownloading: {min(100, downloaded / total * 100):5.1f}%', end='', flush=True)
            except (ValueError, TypeError):
                pass
            return
        if not line.startswith(RESULT_PREFIX):
            safe = redact_output(line[:2000])
            emit({'type': 'log', 'text': safe})
            if line.startswith('WARNING:') and safe not in warnings:
                warnings.append(safe)
            return
        try:
            data = json.loads(line[len(RESULT_PREFIX):])
            if isinstance(data.get('title'), str):
                emit({'type': 'title', 'text': data['title']})
            for download in data.get('requested_downloads', [data]):
                media_path = add_file(download.get('filepath'))
                add_file(download.get('infojson_filename'))
                if media_path:
                    for extension in ('.info.json', '.description', '.jpg', '.jpeg', '.png', '.webp'):
                        add_file(str(media_path.with_suffix(extension)))
            captions = data.get('requested_subtitles', data.get('subtitles'))
            if isinstance(captions, dict):
                for caption in captions.values():
                    source = add_file(caption.get('filepath'))
                    if source and source.is_file():
                        caption_count += 1
                        if source.stat().st_size > 100 * 1024 * 1024:
                            raise ValueError('The caption file exceeds the 100 MB export limit.')
                        formats = request.transcript_formats or (['txt'] if request.mode == 'subs' else [])
                        if formats:
                            for path in export_transcript(source, formats, request.transcript_timestamps):
                                add_file(str(path))
            if (request.subtitles != 'none' or request.transcript_formats or request.mode == 'subs') and not captions:
                message = 'No captions matched the selected source and language for this item.'
                if message not in warnings:
                    warnings.append(message)
                emit({'type': 'log', 'text': message})
        except (ValueError, OSError, TypeError, AttributeError) as exc:
            errors.append(str(exc))
            emit({'type': 'log', 'text': f'Transcript export failed: {exc}'})

    for index, command in enumerate(commands):
        if cancel_event and cancel_event.is_set():
            last = ProcessResult(130, 'Cancelled')
            break
        history.append(tuple(command))
        if request.print_command:
            print(f'$ {printable_command(command)}', file=sys.stderr)
        if index:
            emit({'type': 'log', 'text': 'Retrying with an alternative YouTube player.'})
        try:
            if executor:
                last = executor(command)
                for line in last.output.splitlines():
                    consume(line)
            else:
                last = run_process(command, on_line=consume, cancel_event=cancel_event, stream=on_event is None)
        except KeyboardInterrupt:
            last = ProcessResult(130, 'Cancelled')
        if last.returncode == 0:
            if errors:
                last = ProcessResult(1, '\n'.join(errors))
                kind = FailureKind.OTHER
                break
            if request.mode == 'subs' and not caption_count:
                last = ProcessResult(1, failure_message(FailureKind.NO_SUBTITLES))
                kind = FailureKind.NO_SUBTITLES
                break
            return DownloadRunResult(True, 0, last.output, index + 1, None, tuple(history), tuple(files), tuple(warnings))
        kind = FailureKind.CANCELLED if last.returncode == 130 else classify_failure(last.output)
        if kind not in _RETRYABLE:
            break
    if last.returncode == 130:
        kind = FailureKind.CANCELLED
    return DownloadRunResult(False, last.returncode, last.output, len(history), kind, tuple(history), tuple(files), tuple(warnings))
