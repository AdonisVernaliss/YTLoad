from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .jobs import JobManager
from .models import AppConfig
from .urls import expand_channel_url
from .validation import request_from_payload, validate_url


PUBLIC_ARGS = ['--use-extractors', 'youtube.*', '--match-filters', '!is_live',
               '--sleep-interval', '1', '--max-sleep-interval', '3']
ACTIVE = {'queued', 'running', 'cancelling'}
FILE_BYTES = 256 * 1024 ** 2
SESSION_TTL = 3600
SESSION_BYTES = 1024 ** 3
TOTAL_BYTES = 4 * 1024 ** 3


def public_url(value: str) -> str:
    value = validate_url(value)
    parts = urlsplit(value)
    if parts.scheme != 'https' or parts.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'} or parts.port not in {None, 443}:
        raise ValueError('The public workspace accepts HTTPS YouTube video, playlist and channel links only.')
    path = unquote(parts.path).rstrip('/')
    query = parse_qs(parts.query)
    video = path == '/watch' and re.fullmatch(r'[A-Za-z0-9_-]{11}', query.get('v', [''])[0])
    playlist = path == '/playlist' and re.fullmatch(r'[A-Za-z0-9_-]{10,200}', query.get('list', [''])[0])
    channel = re.fullmatch(r'/(?:@[^/\s]+|(?:channel|c|user)/[^/\s]+)(?:/(?:videos|shorts|streams))?', path)
    if not (video or playlist or channel):
        raise ValueError('Use a direct YouTube video, playlist or channel link.')
    return value


def byte_limit(value: str) -> float:
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([KMGTP]?)(?:i?B)?', value, re.I)
    if not match:
        raise ValueError('Enter a size such as 100M.')
    return float(match[1]) * 1024 ** ('KMGTP'.index(match[2].upper()) + 1 if match[2] else 0)


def check_public_file(value):
    path = Path(value)
    if path.stat().st_size > FILE_BYTES:
        path.unlink()
        raise ValueError('The processed file exceeds the public 256 MB limit. Choose a smaller format or use the local app.')


def protect_command(request):
    request.passthrough = list(PUBLIC_ARGS)
    return request


def prepare_public_request(request):
    if request.browser or request.output_root or request.write_comments or request.sponsorblock != 'off' or request.passthrough:
        raise ValueError('Browser sign-in, local folders, comments and SponsorBlock are available in the local app only.')
    if len(request.urls) > 3:
        raise ValueError('Add up to 3 links per public batch.')
    request.urls = list(dict.fromkeys(public_url(url) for url in request.urls))
    items = request.playlist_items or '1:50'
    match = re.fullmatch(r'([1-9]\d{0,6})(?::([1-9]\d{0,6}))?', items)
    if not match or not 0 <= int(match[2] or match[1]) - int(match[1]) < 50:
        raise ValueError('Public collections support up to 50 items per section. Use a range such as 1:50 or 51:100.')
    request.playlist_items = items
    request.max_filesize = request.max_filesize or '256M'
    request.limit_rate = request.limit_rate or '2M'
    if byte_limit(request.max_filesize) > 256 * 1024 ** 2 or byte_limit(request.limit_rate) > 2 * 1024 ** 2:
        raise ValueError('Public downloads allow up to 256 MB per file and 2 MB/s. Use the local app for larger downloads.')
    request.fail_fast = False
    return protect_command(request)


@dataclass
class PublicSession:
    identifier: str
    token: str
    root: Path
    touched: float = field(default_factory=time.monotonic)
    jobs: set[str] = field(default_factory=set)
    submissions: deque = field(default_factory=deque)
    inspections: deque = field(default_factory=deque)
    readers: int = 0


class PublicState:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = self.root / '.ytload-public'
        if any(self.root.iterdir()) and not marker.is_file():
            raise ValueError('Choose an empty directory dedicated to public temporary downloads.')
        if os.name != 'posix':
            raise ValueError('The hosted backend requires Linux or macOS. Local mode also supports Windows.')
        import fcntl
        self.storage_lock = marker.open('a+b')
        try:
            fcntl.flock(self.storage_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.storage_lock.close()
            raise ValueError('Another public backend is using this storage directory.') from exc
        marker.chmod(0o600)
        for path in self.root.iterdir():
            if re.fullmatch(r'[a-f0-9]{32}', path.name) and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        self.lock = threading.RLock()
        self.sessions = {}
        self.creations = {}
        self.salt = secrets.token_bytes(32)
        self.reasons = {}
        self.jobs = JobManager(AppConfig(output_root=str(self.root)), prepare_request=protect_command, check_file=check_public_file, hosted=True)
        self.closed = threading.Event()
        self.monitor = threading.Thread(target=self._monitor, daemon=True, name='temporary-storage')
        self.monitor.start()

    def new_session(self, client: str) -> PublicSession:
        now = time.monotonic()
        key = hashlib.sha256(self.salt + client.encode()).hexdigest()
        with self.lock:
            self.creations = {ip: times for ip, times in self.creations.items() if times and now - times[-1] < 3600}
            times = self.creations.get(key, deque())
            while times and now - times[0] >= 3600:
                times.popleft()
            if len(self.sessions) >= 100 or len(self.creations) >= 1000 or len(times) >= 8:
                raise ValueError('The public workspace is busy. Please try again later.')
            times.append(now)
            self.creations[key] = times
            session = PublicSession(secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.root / secrets.token_hex(16))
            self.sessions[session.identifier] = session
            return session

    def session(self, identifier: str):
        with self.lock:
            session = self.sessions.get(identifier)
            if session:
                session.touched = time.monotonic()
            return session

    def allow_inspection(self, session):
        with self.lock:
            now = time.monotonic()
            while session.inspections and now - session.inspections[0] >= 60:
                session.inspections.popleft()
            if len(session.inspections) >= 5:
                raise ValueError('Please wait before checking more links.')
            session.inspections.append(now)

    def _quota(self, session):
        jobs = self.jobs.snapshot()
        if sum(job['status'] in ACTIVE for job in jobs) >= 12:
            raise ValueError('The public queue is full. Please wait for a download to finish.')
        if sum(job['id'] in session.jobs and job['status'] in ACTIVE for job in jobs) >= 4:
            raise ValueError('You can have up to 4 active public downloads.')
        now = time.monotonic()
        while session.submissions and now - session.submissions[0] >= 60:
            session.submissions.popleft()
        if len(session.submissions) >= 6:
            raise ValueError('Please wait before adding more downloads.')
        size = self.storage_size(self.root)
        if size >= TOTAL_BYTES or self.storage_size(session.root) >= SESSION_BYTES or shutil.disk_usage(self.root).free < 1024 ** 3:
            raise ValueError('Temporary storage is full. Save your files and try again later, or use the local app.')

    def add(self, session, payload):
        if not isinstance(payload, dict):
            raise ValueError('Expected a JSON object.')
        request = request_from_payload(payload, AppConfig())
        if 'output_root' not in payload:
            request.output_root = None
        request = prepare_public_request(request)
        expanded = {url for value in request.urls for url in expand_channel_url(value, request.channel_scope)}
        with self.lock:
            self._quota(session)
            jobs = self.jobs.snapshot()
            if sum(job['id'] in session.jobs and job['status'] in ACTIVE for job in jobs) + len(expanded) > 4 or sum(job['status'] in ACTIVE for job in jobs) + len(expanded) > 12:
                raise ValueError('There is not enough space in the public queue for this batch.')
            values = asdict(request)
            for key in ('passthrough', 'dry_run', 'print_command'):
                values.pop(key)
            values['output_root'] = str(session.root)
            ids = self.jobs.add(values)
            session.jobs.update(ids)
            session.submissions.append(time.monotonic())
            return ids

    def owned(self, session, identifier):
        if identifier not in session.jobs:
            raise LookupError('This download is not available in your session.')
        return identifier

    def retry(self, session, identifier):
        with self.lock:
            self.owned(session, identifier)
            self._quota(session)
            ids = self.jobs.retry(identifier)
            session.jobs.update(ids)
            session.submissions.append(time.monotonic())
            return ids

    def snapshot(self, session):
        with self.lock:
            result = []
            for job in self.jobs.snapshot():
                if job['id'] not in session.jobs:
                    continue
                job['files'] = [Path(value).name for value in job['files']]
                job['output_root'] = 'Temporary server storage'
                if job['id'] in self.reasons:
                    job['warnings'].append(self.reasons[job['id']])
                for key in ('logs', 'warnings'):
                    job[key] = [value.replace(str(self.root), '[storage]') for value in job[key]]
                for key in ('error', 'error_detail'):
                    if job[key]:
                        job[key] = job[key].replace(str(self.root), '[storage]')
                result.append(job)
            return result

    @staticmethod
    def storage_size(root):
        total = 0
        for path in root.rglob('*'):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass
        return total

    def cleanup(self):
        with self.lock:
            now = time.monotonic()
            jobs = self.jobs.snapshot()
            active = {job['id'] for job in jobs if job['status'] in ACTIVE}
            for identifier, session in list(self.sessions.items()):
                if now - session.touched >= SESSION_TTL and not session.readers and not session.jobs.intersection(active):
                    shutil.rmtree(session.root, ignore_errors=True)
                    del self.sessions[identifier]
            total = self.storage_size(self.root)
            low_disk = shutil.disk_usage(self.root).free < 1024 ** 3
            for session in self.sessions.values():
                over_quota = total >= TOTAL_BYTES or low_disk or self.storage_size(session.root) >= SESSION_BYTES
                for job in jobs:
                    if job['id'] not in session.jobs or job['status'] not in ACTIVE:
                        continue
                    if over_quota or time.time() - job['created'] >= 1200:
                        self.reasons[job['id']] = 'A public storage or time limit was reached. Use the local app for larger downloads.'
                        self.jobs.cancel(job['id'])
            known = {job['id'] for job in jobs}
            self.reasons = {key: value for key, value in self.reasons.items() if key in known}
            for session in self.sessions.values():
                session.jobs.intersection_update(known)

    def _monitor(self):
        while not self.closed.wait(2):
            try:
                self.cleanup()
            except OSError:
                pass

    def close(self):
        self.closed.set()
        self.monitor.join(timeout=4)
        self.jobs.close()
        self.storage_lock.close()
