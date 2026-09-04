from __future__ import annotations

from collections import deque
from dataclasses import asdict, replace
from pathlib import Path
import shutil
import threading
import time
import uuid

from .models import AppConfig
from .recovery import recovery_advice
from .runner import failure_detail, failure_message, run_download
from .urls import expand_channel_url
from .validation import request_from_payload


_TERMINAL = {'completed', 'failed', 'cancelled'}
_ACTIVE = {'queued', 'running', 'cancelling'}


class JobManager:
    def __init__(self, config: AppConfig, prepare_request=None, check_file=None, hosted=False,
                 before_job=None, isolated_storage=False):
        self.config = config
        self.hosted = hosted
        self.prepare_request = prepare_request
        self.check_file = check_file
        self.before_job = before_job
        self.isolated_storage = isolated_storage
        self._jobs = {}
        self._queue = deque()
        self._condition = threading.Condition(threading.RLock())
        self._closing = False
        self._worker = threading.Thread(target=self._work, daemon=True, name='download-queue')
        self._worker.start()

    def _request(self, payload):
        request = request_from_payload(payload, self.config)
        return self.prepare_request(request) if self.prepare_request else request

    def add(self, payload: dict) -> list[str]:
        request = self._request(payload)
        urls = list(dict.fromkeys(expanded for url in request.urls for expanded in expand_channel_url(url, request.channel_scope)))
        root = Path(request.output_root or self.config.output_root).expanduser().resolve()
        return self._enqueue(request, urls, root)

    def _enqueue(self, request, urls, root, reuse_root=None):
        with self._condition:
            if self._closing:
                raise ValueError('The workspace is closing.')
            active = sum(job['status'] in _ACTIVE for job in self._jobs.values())
            if active + len(urls) > 100:
                raise ValueError('The queue is full. Wait for some downloads to finish.')
            root.mkdir(parents=True, exist_ok=True)
            if not self.isolated_storage:
                (root / '.ytload-state').mkdir(exist_ok=True)
            while len(self._jobs) + len(urls) > 200:
                old = next((key for key, job in self._jobs.items() if job['status'] in _TERMINAL
                            and (not self.isolated_storage or job['result_expired'])), None)
                if old is None:
                    break
                del self._jobs[old]
            ids = []
            for url in urls:
                identifier = uuid.uuid4().hex
                output_root = Path(reuse_root).resolve() if reuse_root else root / identifier if self.isolated_storage else root
                if self.isolated_storage and (not output_root.is_relative_to(root) and reuse_root is None):
                    raise ValueError('The temporary job folder is invalid.')
                output_root.mkdir(parents=True, exist_ok=True)
                (output_root / '.ytload-state').mkdir(exist_ok=True)
                self._jobs[identifier] = {
                    'id': identifier, 'url': url, 'title': url, 'mode': request.mode, 'status': 'queued',
                    'created': time.time(), 'started_at': None, 'finished_at': None, 'result_expired': False,
                    'output_root': str(output_root), 'progress': {}, 'logs': deque(maxlen=150),
                    'files': [], 'warnings': [], 'error': None, 'error_detail': None, 'recovery': [],
                    'request': replace(request, urls=[url], output_root=str(output_root)),
                    'cancel': threading.Event(), 'policy_error': None,
                }
                ids.append(identifier)
                self._queue.append(identifier)
            self._condition.notify_all()
            return ids

    def snapshot(self) -> list[dict]:
        with self._condition:
            return [{key: [dict(item) for item in value] if key == 'recovery' else list(value) if key in {'logs', 'files', 'warnings'} else dict(value) if key == 'progress' else value
                     for key, value in job.items() if key not in {'request', 'cancel', 'policy_error'}} for job in self._jobs.values()]

    def cancel(self, identifier: str) -> None:
        with self._condition:
            job = self._jobs.get(identifier)
            if job is None:
                raise ValueError('This download is no longer in the queue.')
            if job['status'] == 'queued':
                job['status'] = 'cancelled'
                job['finished_at'] = time.time()
            elif job['status'] == 'running':
                job['status'] = 'cancelling'
            job['cancel'].set()

    def retry(self, identifier: str) -> list[str]:
        with self._condition:
            job = self._jobs.get(identifier)
            if not job or job['status'] not in {'failed', 'cancelled'} or job['result_expired']:
                raise ValueError('Only unexpired failed or cancelled downloads can be retried.')
            payload = asdict(job['request'])
            payload['channel_scope'] = 'auto'
            for key in ('passthrough', 'dry_run', 'print_command'):
                payload.pop(key)
            output_root = job['output_root']
        request = self._request(payload)
        return self._enqueue(request, request.urls, Path(output_root), reuse_root=output_root)

    def file_path(self, identifier: str, index: int) -> Path:
        with self._condition:
            job = self._jobs.get(identifier)
            if not job or job['result_expired'] or index < 0 or index >= len(job['files']):
                raise ValueError('This file is no longer available.')
            path = Path(job['files'][index]).resolve()
            if not path.is_relative_to(Path(job['output_root']).resolve()) or not path.is_file():
                raise ValueError('This file was moved or removed.')
            return path

    def expire(self, identifier: str, reason: str) -> None:
        with self._condition:
            job = self._jobs.get(identifier)
            if not job or job['status'] not in _TERMINAL or job['result_expired']:
                return
            output_root = Path(job['output_root']).resolve()
            shared = any(other['id'] != identifier and other['output_root'] == str(output_root) and not other['result_expired']
                         for other in self._jobs.values())
            if self.isolated_storage:
                if not shared:
                    base = Path(self.config.output_root).expanduser().resolve()
                    if not output_root.is_relative_to(base) or output_root == base:
                        raise ValueError('The temporary job folder is invalid.')
                    if output_root.exists():
                        shutil.rmtree(output_root, ignore_errors=False)
            else:
                for value in job['files']:
                    path = Path(value).resolve()
                    if path.is_relative_to(output_root):
                        path.unlink(missing_ok=True)
            job['files'] = []
            job['result_expired'] = True
            job['error'] = reason
            job['error_detail'] = None
            job['recovery'] = []

    def close(self) -> None:
        with self._condition:
            self._closing = True
            for identifier in list(self._jobs):
                if self._jobs[identifier]['status'] in _ACTIVE:
                    self.cancel(identifier)
            self._condition.notify_all()
        self._worker.join(timeout=8)

    def _event(self, identifier: str, event: dict):
        with self._condition:
            job = self._jobs[identifier]
            if event['type'] == 'log':
                job['logs'].append(event['text'])
            elif event['type'] == 'title':
                job['title'] = event['text']
            elif event['type'] == 'progress':
                job['progress'] = {key: value for key, value in event.items() if key != 'type'}
                if isinstance(event.get('title'), str) and event['title'] != 'NA':
                    job['title'] = event['title']
            elif event['type'] == 'file' and event['path'] not in job['files']:
                if self.check_file:
                    try:
                        self.check_file(event['path'])
                    except Exception as exc:
                        job['policy_error'] = str(exc)
                        raise
                job['files'].append(event['path'])

    def _work(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._closing)
                if self._closing:
                    return
                identifier = self._queue.popleft()
                job = self._jobs.get(identifier)
                if not job or job['status'] != 'queued':
                    continue
                job['status'] = 'running'
                job['started_at'] = time.time()
            try:
                if self.before_job:
                    self.before_job(job['request'])
                result = run_download(job['request'], job['url'], self.config,
                                      on_event=lambda event: self._event(identifier, event), cancel_event=job['cancel'])
                result_files = result.files
                if self.check_file:
                    result_files = tuple(path for path in result.files if Path(path).is_file())
                    for path in result_files:
                        self.check_file(path)
                with self._condition:
                    policy_error = job.pop('policy_error', None)
                    job['status'] = 'cancelled' if job['cancel'].is_set() or result.returncode == 130 else 'completed' if result.success else 'failed'
                    job['finished_at'] = time.time()
                    job['error'] = policy_error or (failure_message(result.failure_kind) if not result.success else None)
                    job['error_detail'] = None if policy_error else failure_detail(result.output) if not result.success else None
                    job['warnings'] = list(result.warnings)
                    job['recovery'] = [] if policy_error else recovery_advice(result.failure_kind, result.output, job['request'], hosted=self.hosted)
                    job['files'] = list(dict.fromkeys([*job['files'], *result_files]))
                    if not result.success and result.output:
                        job['logs'].append(result.output[-6000:])
            except Exception as exc:
                with self._condition:
                    policy_error = job.pop('policy_error', None)
                    job['status'] = 'cancelled' if job['cancel'].is_set() else 'failed'
                    job['finished_at'] = time.time()
                    job['error'] = policy_error or str(exc)
                    job['error_detail'] = None
                    job['recovery'] = []
