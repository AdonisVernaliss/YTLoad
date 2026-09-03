from __future__ import annotations

from collections import deque
from dataclasses import asdict, replace
from pathlib import Path
import threading
import time
import uuid

from .models import AppConfig
from .runner import failure_detail, failure_message, run_download
from .urls import expand_channel_url
from .validation import request_from_payload


class JobManager:
    def __init__(self, config: AppConfig, prepare_request=None, check_file=None):
        self.config = config
        self.prepare_request = prepare_request
        self.check_file = check_file
        self._jobs = {}
        self._queue = deque()
        self._condition = threading.Condition(threading.RLock())
        self._closing = False
        self._worker = threading.Thread(target=self._work, daemon=True, name='download-queue')
        self._worker.start()

    def add(self, payload: dict) -> list[str]:
        request = request_from_payload(payload, self.config)
        if self.prepare_request:
            request = self.prepare_request(request)
        urls = list(dict.fromkeys(expanded for url in request.urls for expanded in expand_channel_url(url, request.channel_scope)))
        root = Path(request.output_root or self.config.output_root).expanduser().resolve()
        with self._condition:
            if self._closing:
                raise ValueError('The workspace is closing.')
            active = sum(job['status'] in {'queued', 'running', 'cancelling'} for job in self._jobs.values())
            if active + len(urls) > 100:
                raise ValueError('The queue is full. Wait for some downloads to finish.')
            root.mkdir(parents=True, exist_ok=True)
            (root / '.ytload-state').mkdir(exist_ok=True)
            while len(self._jobs) + len(urls) > 200:
                old = next((key for key, job in self._jobs.items() if job['status'] in {'completed', 'failed', 'cancelled'}), None)
                if old is None:
                    break
                del self._jobs[old]
            ids = []
            for url in urls:
                identifier = uuid.uuid4().hex
                self._jobs[identifier] = {
                    'id': identifier, 'url': url, 'title': url, 'mode': request.mode, 'status': 'queued',
                    'created': time.time(), 'output_root': str(root), 'progress': {}, 'logs': deque(maxlen=150),
                    'files': [], 'warnings': [], 'error': None, 'error_detail': None,
                    'request': replace(request, urls=[url], output_root=str(root)),
                    'cancel': threading.Event(),
                }
                ids.append(identifier)
                self._queue.append(identifier)
            self._condition.notify_all()
            return ids

    def snapshot(self) -> list[dict]:
        with self._condition:
            return [{key: list(value) if key in {'logs', 'files', 'warnings'} else dict(value) if key == 'progress' else value
                     for key, value in job.items() if key not in {'request', 'cancel'}} for job in self._jobs.values()]

    def cancel(self, identifier: str) -> None:
        with self._condition:
            job = self._jobs.get(identifier)
            if job is None:
                raise ValueError('This download is no longer in the queue.')
            if job['status'] == 'queued':
                job['status'] = 'cancelled'
            elif job['status'] == 'running':
                job['status'] = 'cancelling'
            job['cancel'].set()

    def retry(self, identifier: str) -> list[str]:
        with self._condition:
            job = self._jobs.get(identifier)
            if not job or job['status'] not in {'failed', 'cancelled'}:
                raise ValueError('Only failed or cancelled downloads can be retried.')
            payload = asdict(job['request'])
            payload['channel_scope'] = 'auto'
            for key in ('passthrough', 'dry_run', 'print_command'):
                payload.pop(key)
        return self.add(payload)

    def file_path(self, identifier: str, index: int) -> Path:
        with self._condition:
            job = self._jobs.get(identifier)
            if not job or index < 0 or index >= len(job['files']):
                raise ValueError('This file is no longer available.')
            path = Path(job['files'][index]).resolve()
            if not path.is_relative_to(Path(job['output_root']).resolve()) or not path.is_file():
                raise ValueError('This file was moved or removed.')
            return path

    def close(self) -> None:
        with self._condition:
            self._closing = True
            for identifier in list(self._jobs):
                if self._jobs[identifier]['status'] in {'queued', 'running', 'cancelling'}:
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
                    self.check_file(event['path'])
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
            try:
                result = run_download(job['request'], job['url'], self.config,
                                      on_event=lambda event: self._event(identifier, event), cancel_event=job['cancel'])
                result_files = result.files
                if self.check_file:
                    result_files = tuple(path for path in result.files if Path(path).is_file())
                    for path in result_files:
                        self.check_file(path)
                with self._condition:
                    job['status'] = 'cancelled' if job['cancel'].is_set() or result.returncode == 130 else 'completed' if result.success else 'failed'
                    job['error'] = failure_message(result.failure_kind) if not result.success else None
                    job['error_detail'] = failure_detail(result.output) if not result.success else None
                    job['warnings'] = list(result.warnings)
                    job['files'] = list(dict.fromkeys([*job['files'], *result_files]))
                    if not result.success and result.output:
                        job['logs'].append(result.output[-6000:])
            except Exception as exc:
                with self._condition:
                    job['status'] = 'cancelled' if job['cancel'].is_set() else 'failed'
                    job['error'] = f'Could not complete this download: {exc}'
