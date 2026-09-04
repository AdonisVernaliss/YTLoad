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
from .public_policy import (PUBLIC_COLLECTION_ITEMS, PUBLIC_CRITICAL_FREE_BYTES, PUBLIC_EXPIRED_ERROR,
                            PUBLIC_FILE_BYTES, PUBLIC_FILE_LIMIT_ERROR, PUBLIC_GLOBAL_QUEUE,
                            PUBLIC_JOB_MAX_SECONDS, PUBLIC_RESULT_TTL, PUBLIC_SESSION_BYTES,
                            PUBLIC_SESSION_QUEUE, PUBLIC_SESSION_TTL, PUBLIC_START_FREE_BYTES,
                            PUBLIC_STORAGE_ERROR, PUBLIC_TOTAL_BYTES)
from .urls import expand_channel_url
from .validation import request_from_payload, validate_url


PUBLIC_ARGS = ['--use-extractors', 'youtube.*', '--match-filters', '!is_live',
               '--sleep-interval', '1', '--max-sleep-interval', '3']
ACTIVE = {'queued', 'running', 'cancelling'}
FILE_BYTES = PUBLIC_FILE_BYTES
SESSION_TTL = PUBLIC_SESSION_TTL
SESSION_BYTES = PUBLIC_SESSION_BYTES
TOTAL_BYTES = PUBLIC_TOTAL_BYTES


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
        raise ValueError(PUBLIC_FILE_LIMIT_ERROR)


def protect_command(request):
    request.passthrough = list(PUBLIC_ARGS)
    return request


def prepare_public_request(request):
    if request.browser or request.output_root or request.write_comments or request.sponsorblock != 'off' or request.passthrough or request.limit_rate:
        raise ValueError('Browser sign-in, local folders, comments and SponsorBlock are available in the local app only.')
    if len(request.urls) > 3:
        raise ValueError('Add up to 3 links per public batch.')
    request.urls = list(dict.fromkeys(public_url(url) for url in request.urls))
    items = request.playlist_items or f'1:{PUBLIC_COLLECTION_ITEMS}'
    match = re.fullmatch(r'([1-9]\d{0,6})(?::([1-9]\d{0,6}))?', items)
    if not match or not 0 <= int(match[2] or match[1]) - int(match[1]) < PUBLIC_COLLECTION_ITEMS:
        raise ValueError('Public collections support up to 50 items per section. Use a range such as 1:50 or 51:100.')
    request.playlist_items = items
    request.max_filesize = request.max_filesize or '5G'
    request.limit_rate = None
    if byte_limit(request.max_filesize) > PUBLIC_FILE_BYTES:
        raise ValueError('The public web maximum file size is 5 GB. Choose a lower limit or use YTLoad Local.')
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
    readers: dict[str, int] = field(default_factory=dict)
    abandoned: bool = False


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
        self.purge = set()
        self.jobs = JobManager(AppConfig(output_root=str(self.root)), prepare_request=protect_command, check_file=check_public_file,
                               hosted=True, before_job=self._before_job, isolated_storage=True)
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
            if session is None or session.abandoned:
                return None
            session.touched = time.monotonic()
            return session

    def _require_active(self, session):
        if self.sessions.get(session.identifier) is not session or session.abandoned:
            raise LookupError('This temporary session expired.')

    def allow_inspection(self, session):
        with self.lock:
            self._require_active(session)
            now = time.monotonic()
            while session.inspections and now - session.inspections[0] >= 60:
                session.inspections.popleft()
            if len(session.inspections) >= 5:
                raise ValueError('Please wait before checking more links.')
            session.inspections.append(now)

    def _before_job(self, request):
        if request.mode in {'video', 'audio'} and shutil.disk_usage(self.root).free < PUBLIC_START_FREE_BYTES:
            raise ValueError('At least 12 GB of free disk space is required to start a public media download.')

    def _quota(self, session, request=None):
        jobs = self.jobs.snapshot()
        if sum(job['status'] in ACTIVE for job in jobs) >= PUBLIC_GLOBAL_QUEUE:
            raise ValueError('The public queue is full. Please wait for a download to finish.')
        if sum(job['id'] in session.jobs and job['status'] in ACTIVE for job in jobs) >= PUBLIC_SESSION_QUEUE:
            raise ValueError('You can have up to 4 queued or running public downloads.')
        now = time.monotonic()
        while session.submissions and now - session.submissions[0] >= 60:
            session.submissions.popleft()
        if len(session.submissions) >= 6:
            raise ValueError('Please wait before adding more downloads.')
        if self.storage_size(self.root) >= TOTAL_BYTES or self.storage_size(session.root) >= SESSION_BYTES:
            raise ValueError(PUBLIC_STORAGE_ERROR)
        if request is not None:
            self._before_job(request)

    def add(self, session, payload):
        if not isinstance(payload, dict):
            raise ValueError('Expected a JSON object.')
        request = request_from_payload(payload, AppConfig())
        if 'output_root' not in payload:
            request.output_root = None
        request = prepare_public_request(request)
        expanded = {url for value in request.urls for url in expand_channel_url(value, request.channel_scope)}
        with self.lock:
            self._require_active(session)
            self._quota(session, request)
            jobs = self.jobs.snapshot()
            if sum(job['id'] in session.jobs and job['status'] in ACTIVE for job in jobs) + len(expanded) > PUBLIC_SESSION_QUEUE or sum(job['status'] in ACTIVE for job in jobs) + len(expanded) > PUBLIC_GLOBAL_QUEUE:
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
        self._require_active(session)
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

    def begin_file(self, session, identifier, index):
        with self.lock:
            self.owned(session, identifier)
            path = self.jobs.file_path(identifier, index)
            check_public_file(path)
            session.readers[identifier] = session.readers.get(identifier, 0) + 1
            return path

    def end_file(self, session, identifier):
        with self.lock:
            count = session.readers.get(identifier, 0)
            if count <= 1:
                session.readers.pop(identifier, None)
            else:
                session.readers[identifier] = count - 1

    def snapshot(self, session):
        with self.lock:
            self._require_active(session)
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
            monotonic_now = time.monotonic()
            wall_now = time.time()
            jobs = self.jobs.snapshot()
            indexed = {job['id']: job for job in jobs}
            for session in self.sessions.values():
                if not session.abandoned and monotonic_now - session.touched >= SESSION_TTL and not session.readers:
                    session.abandoned = True
                if session.abandoned:
                    for job_id in session.jobs:
                        self.purge.add(job_id)
                        self.reasons[job_id] = PUBLIC_EXPIRED_ERROR
                        job = indexed.get(job_id)
                        if job and job['status'] in ACTIVE:
                            self.jobs.cancel(job_id)
            jobs = self.jobs.snapshot()
            total = self.storage_size(self.root)
            low_disk = shutil.disk_usage(self.root).free < PUBLIC_CRITICAL_FREE_BYTES
            for session in self.sessions.values():
                session_size = self.storage_size(session.root)
                over_storage = total >= TOTAL_BYTES or session_size >= SESSION_BYTES
                for job in jobs:
                    if job['id'] not in session.jobs:
                        continue
                    if job['status'] in ACTIVE:
                        runtime = job.get('started_at') and wall_now - job['started_at'] >= PUBLIC_JOB_MAX_SECONDS
                        if session.abandoned or over_storage or low_disk or runtime:
                            reason = (PUBLIC_EXPIRED_ERROR if session.abandoned
                                      else 'The public job reached the 3 hour processing limit. Use YTLoad Local for longer work.' if runtime
                                      else 'A public storage safety limit was reached. Use YTLoad Local for larger downloads.')
                            self.reasons[job['id']] = reason
                            if session.abandoned or over_storage or low_disk:
                                self.purge.add(job['id'])
                            self.jobs.cancel(job['id'])
                    elif job['status'] in {'completed', 'failed', 'cancelled'} and not session.readers.get(job['id']):
                        expired = job.get('finished_at') and wall_now - job['finished_at'] >= PUBLIC_RESULT_TTL
                        if job['id'] in self.purge or expired:
                            reason = self.reasons.get(job['id'], PUBLIC_EXPIRED_ERROR) if job['id'] in self.purge else PUBLIC_EXPIRED_ERROR
                            self.jobs.expire(job['id'], reason)
                            self.purge.discard(job['id'])
            jobs = self.jobs.snapshot()
            active = {job['id'] for job in jobs if job['status'] in ACTIVE}
            for identifier, session in list(self.sessions.items()):
                if not session.abandoned or session.readers or session.jobs.intersection(active):
                    continue
                for job in jobs:
                    if job['id'] in session.jobs and job['status'] in {'completed', 'failed', 'cancelled'}:
                        self.jobs.expire(job['id'], PUBLIC_EXPIRED_ERROR)
                        self.purge.discard(job['id'])
                if session.root.exists():
                    shutil.rmtree(session.root, ignore_errors=False)
                del self.sessions[identifier]
            known = {job['id'] for job in self.jobs.snapshot()}
            self.reasons = {key: value for key, value in self.reasons.items() if key in known}
            self.purge.intersection_update(known)
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
