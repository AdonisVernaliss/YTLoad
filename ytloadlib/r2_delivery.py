from __future__ import annotations

from collections import deque
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


DEFAULT_PART_BYTES = 256 * 1024 * 1024
MAX_CONTROL_BYTES = 65536


class DeliveryCancelled(Exception):
    pass


class DeliveryDeferred(Exception):
    def __init__(self, message, delay=30):
        super().__init__(message)
        self.delay = delay


@dataclass(frozen=True)
class R2DeliveryConfig:
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    control_url: str
    control_secret: str
    part_bytes: int = DEFAULT_PART_BYTES
    attempts: int = 3

    @classmethod
    def from_environ(cls, environ=None):
        values = os.environ if environ is None else environ
        names = {
            'endpoint_url': 'YTLOAD_R2_ENDPOINT_URL',
            'bucket': 'YTLOAD_R2_BUCKET',
            'access_key_id': 'YTLOAD_R2_ACCESS_KEY_ID',
            'secret_access_key': 'YTLOAD_R2_SECRET_ACCESS_KEY',
            'control_url': 'YTLOAD_MEDIA_CONTROL_URL',
            'control_secret': 'YTLOAD_MEDIA_CONTROL_SECRET',
        }
        if not any(name in values for name in names.values()):
            return None
        supplied = {field: values.get(name, '').strip() for field, name in names.items()}
        missing = [name for field, name in names.items() if not supplied[field]]
        if missing:
            raise ValueError('Hosted R2 delivery configuration is incomplete: ' + ', '.join(missing))
        for field in ('endpoint_url', 'control_url'):
            parsed = urlsplit(supplied[field])
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or (field == 'endpoint_url' and parsed.path not in {'', '/'}):
                raise ValueError(f'{names[field]} must be a complete HTTPS URL.')
        if not supplied['control_url'].rstrip('/').endswith('/ytload/media-control'):
            raise ValueError('YTLOAD_MEDIA_CONTROL_URL must end with /ytload/media-control.')
        if len(supplied['control_secret']) < 32 or not supplied['control_secret'].isascii():
            raise ValueError('YTLOAD_MEDIA_CONTROL_SECRET must be an ASCII secret of at least 32 characters.')
        part_bytes = int(values.get('YTLOAD_R2_PART_BYTES', DEFAULT_PART_BYTES))
        attempts = int(values.get('YTLOAD_R2_UPLOAD_ATTEMPTS', 3))
        if not 5 * 1024 * 1024 <= part_bytes <= 512 * 1024 * 1024:
            raise ValueError('YTLOAD_R2_PART_BYTES must be between 5 MiB and 512 MiB.')
        if not 1 <= attempts <= 6:
            raise ValueError('YTLOAD_R2_UPLOAD_ATTEMPTS must be between 1 and 6.')
        return cls(**supplied, part_bytes=part_bytes, attempts=attempts)


@dataclass(frozen=True)
class UploadItem:
    key: str
    path: Path
    name: str
    content_type: str
    size: int

    def reservation(self):
        return {'key': self.key, 'name': self.name, 'size': self.size, 'contentType': self.content_type}


class BoundedReader:
    def __init__(self, handle, offset, length, chunk_bytes=8 * 1024 * 1024, cancel=None):
        self.handle = handle
        self.offset = offset
        self.length = length
        self.position = 0
        self.chunk_bytes = chunk_bytes
        self.cancel = cancel
        self._check_cancel()
        self.handle.seek(offset)

    def _check_cancel(self):
        if self.cancel and self.cancel.is_set():
            raise DeliveryCancelled()

    def __len__(self):
        return self.length

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self._check_cancel()
        if whence == 0:
            position = offset
        elif whence == 1:
            position = self.position + offset
        elif whence == 2:
            position = self.length + offset
        else:
            raise ValueError('Invalid seek mode.')
        if not 0 <= position <= self.length:
            raise ValueError('Seek is outside the upload part.')
        self.position = position
        self.handle.seek(self.offset + position)
        return self.position

    def read(self, size=-1):
        self._check_cancel()
        remaining = self.length - self.position
        if remaining <= 0:
            return b''
        requested = self.chunk_bytes if size is None or size < 0 else min(size, self.chunk_bytes)
        data = self.handle.read(min(remaining, requested))
        self.position += len(data)
        return data


class MediaControlClient:
    def __init__(self, url, secret, timeout=30):
        self.url = url.rstrip('/')
        self.secret = secret.encode()
        self.timeout = timeout

    def _post(self, action, payload):
        body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
        path = urlsplit(self.url + '/' + action).path
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        canonical = b'\n'.join((b'POST', path.encode(), timestamp.encode(), nonce.encode(), hashlib.sha256(body).hexdigest().encode()))
        signature = hmac.new(self.secret, canonical, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(signature).rstrip(b'=').decode()
        request = Request(self.url + '/' + action, data=body, method='POST', headers={
            'Authorization': 'YTLoad-HMAC ' + encoded,
            'Content-Type': 'application/json',
            'X-YTLoad-Nonce': nonce,
            'X-YTLoad-Timestamp': timestamp,
        })
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_CONTROL_BYTES + 1)
                if len(raw) > MAX_CONTROL_BYTES:
                    raise OSError('Media control response is too large.')
                value = json.loads(raw)
        except HTTPError as exc:
            raw = exc.read(MAX_CONTROL_BYTES)
            try:
                value = json.loads(raw)
            except (ValueError, TypeError):
                value = {}
            message = value.get('error') if isinstance(value, dict) else None
            if exc.code in {409, 429, 502, 503, 504}:
                raise DeliveryDeferred(message or 'Hosted delivery is temporarily unavailable.') from exc
            raise OSError(message or f'Media control rejected the request with HTTP {exc.code}.') from exc
        except (URLError, TimeoutError) as exc:
            raise DeliveryDeferred('Hosted delivery is temporarily unavailable.') from exc
        except (ValueError, TypeError) as exc:
            raise OSError('Media control returned invalid data.') from exc
        if not isinstance(value, dict):
            raise OSError('Media control returned invalid data.')
        return value

    def reconcile(self):
        return self._post('reconcile', {})

    def reserve(self, batch_id, files):
        return self._post('reserve', {'id': batch_id, 'files': files})

    def register_multipart(self, batch_id, key, upload_id):
        return self._post('multipart', {'id': batch_id, 'key': key, 'upload_id': upload_id})

    def touch(self, batch_id):
        return self._post('touch', {'id': batch_id})

    def complete_file(self, batch_id, key, size):
        return self._post('complete', {'id': batch_id, 'key': key, 'size': size})

    def commit(self, batch_id):
        return self._post('commit', {'id': batch_id})

    def cancel(self, batch_id, safe_unregistered_keys=None, safe_unregistered=False):
        return self._post('cancel', {'id': batch_id, 'safe_unregistered_keys': list(safe_unregistered_keys or ()),
                                     'safe_unregistered': bool(safe_unregistered)})


class Boto3MultipartStore:
    def __init__(self, config):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise ValueError('Hosted R2 delivery requires the optional boto3 package.') from exc
        self.bucket = config.bucket
        self.client = boto3.client(
            's3', endpoint_url=config.endpoint_url, region_name='auto',
            aws_access_key_id=config.access_key_id, aws_secret_access_key=config.secret_access_key,
            config=Config(signature_version='s3v4', connect_timeout=10, read_timeout=120,
                          max_pool_connections=2, retries={'max_attempts': 1, 'mode': 'standard'}),
        )

    def create(self, item):
        response = self.client.create_multipart_upload(
            Bucket=self.bucket, Key=item.key, ContentType=item.content_type,
            Metadata={'filename-encoded': quote(item.name, safe='')},
        )
        return response['UploadId']

    def upload_part(self, item, upload_id, number, reader, length):
        response = self.client.upload_part(Bucket=self.bucket, Key=item.key, UploadId=upload_id,
                                           PartNumber=number, Body=reader, ContentLength=length)
        return response['ETag']

    def complete(self, item, upload_id, parts):
        self.client.complete_multipart_upload(Bucket=self.bucket, Key=item.key, UploadId=upload_id,
                                              MultipartUpload={'Parts': parts})

    def completed(self, item):
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=item.key)
            return response.get('ContentLength') == item.size
        except Exception as exc:
            code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
            if code in {'404', 'NoSuchKey', 'NotFound'}:
                return False
            raise

    def abort(self, item, upload_id):
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=item.key, UploadId=upload_id)

    def list_uploads(self):
        uploads = []
        key_marker = None
        upload_marker = None
        while True:
            arguments = {'Bucket': self.bucket, 'Prefix': 'media/'}
            if key_marker:
                arguments['KeyMarker'] = key_marker
            if upload_marker:
                arguments['UploadIdMarker'] = upload_marker
            response = self.client.list_multipart_uploads(**arguments)
            uploads.extend((item['Key'], item['UploadId']) for item in response.get('Uploads', []))
            if not response.get('IsTruncated'):
                return uploads
            key_marker = response.get('NextKeyMarker')
            upload_marker = response.get('NextUploadIdMarker')
            if not key_marker:
                raise OSError('R2 multipart listing was incomplete.')

    def abort_key(self, key, upload_id):
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)


class MultipartUploader:
    def __init__(self, control, store, part_bytes=DEFAULT_PART_BYTES, attempts=3, sleep=time.sleep, on_create=None, on_abort=None):
        self.control = control
        self.store = store
        self.part_bytes = part_bytes
        self.attempts = attempts
        self.sleep = sleep
        self.on_create = on_create
        self.on_abort = on_abort

    def _retry(self, operation, cancel, reset=None):
        error = None
        for attempt in range(self.attempts):
            if cancel.is_set():
                raise DeliveryCancelled()
            if reset:
                reset()
            try:
                return operation()
            except DeliveryCancelled:
                raise
            except Exception as exc:
                error = exc
                if attempt + 1 < self.attempts:
                    self.sleep(min(4, .5 * 2 ** attempt))
        raise error

    def upload(self, batch_id, item, cancel):
        upload_id = None
        try:
            if cancel.is_set():
                raise DeliveryCancelled()
            if self.on_create:
                self.on_create(item.key)
            upload_id = self.store.create(item)
            self._retry(lambda: self.control.register_multipart(batch_id, item.key, upload_id), cancel)
            parts = []
            with item.path.open('rb') as handle:
                offset = 0
                number = 1
                while offset < item.size:
                    length = min(self.part_bytes, item.size - offset)
                    reader = BoundedReader(handle, offset, length, cancel=cancel)
                    etag = self._retry(lambda: self.store.upload_part(item, upload_id, number, reader, length), cancel, lambda: reader.seek(0))
                    if cancel.is_set():
                        raise DeliveryCancelled()
                    parts.append({'ETag': etag, 'PartNumber': number})
                    self._retry(lambda: self.control.touch(batch_id), cancel)
                    offset += length
                    number += 1
            try:
                self.store.complete(item, upload_id, parts)
            except Exception:
                if not hasattr(self.store, 'completed') or not self.store.completed(item):
                    raise
            if cancel.is_set():
                raise DeliveryCancelled()
            self._retry(lambda: self.control.complete_file(batch_id, item.key, item.size), cancel)
        except Exception:
            if upload_id:
                try:
                    self.store.abort(item, upload_id)
                    if self.on_abort:
                        self.on_abort(item.key)
                except Exception:
                    pass
            raise


class DeliveryManager:
    def __init__(self, root, control, store, part_bytes=DEFAULT_PART_BYTES, attempts=3):
        self.root = Path(root).resolve()
        self.control = control
        self.store = store
        self.part_bytes = part_bytes
        self.attempts = attempts
        self.journal = self.root / '.ytload-r2-delivery.json'
        self.records = {}
        self.queue = deque()
        self.condition = threading.Condition(threading.RLock())
        self.closing = False
        self.reconciled = threading.Event()
        self.worker = threading.Thread(target=self._work, daemon=True, name='r2-delivery')
        self.worker.start()

    @classmethod
    def from_config(cls, root, config):
        return cls(root, MediaControlClient(config.control_url, config.control_secret),
                   Boto3MultipartStore(config), config.part_bytes, config.attempts)

    def _journal_batches(self):
        try:
            value = json.loads(self.journal.read_text())
            batches = value.get('batches', [])
            return [item for item in batches if isinstance(item, str) and len(item) == 32]
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return []

    def _write_journal(self, batches):
        if not batches:
            self.journal.unlink(missing_ok=True)
            return
        temporary = self.journal.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version': 1, 'batches': sorted(batches)}, separators=(',', ':')))
        if os.name == 'posix':
            temporary.chmod(0o600)
        os.replace(temporary, self.journal)

    def _record_batch(self, batch_id, active):
        batches = set(self._journal_batches())
        if active:
            batches.add(batch_id)
        else:
            batches.discard(batch_id)
        self._write_journal(batches)

    def _recover(self):
        batches = set(self._journal_batches())
        safe_unregistered = False
        if hasattr(self.store, 'list_uploads'):
            try:
                for key, upload_id in self.store.list_uploads():
                    self.store.abort_key(key, upload_id)
                    parts = key.split('/')
                    if len(parts) == 3 and len(parts[1]) == 32:
                        batches.add(parts[1])
                safe_unregistered = True
            except Exception:
                pass
        remaining = set()
        for batch_id in batches:
            try:
                self.control.cancel(batch_id, safe_unregistered=safe_unregistered)
            except Exception:
                remaining.add(batch_id)
        self._write_journal(remaining)
        self.control.reconcile()
        self.reconciled.set()

    def wait_until_reconciled(self, timeout=None):
        return self.reconciled.wait(timeout)

    def submit(self, identifier, paths):
        with self.condition:
            if identifier in self.records:
                return
            items = []
            batch_id = secrets.token_hex(16)
            for value in paths:
                path = Path(value).resolve()
                if not path.is_relative_to(self.root) or not path.is_file():
                    raise ValueError('Delivery file is outside temporary storage.')
                name = path.name
                items.append(UploadItem(f'media/{batch_id}/{secrets.token_hex(16)}', path, name,
                                        mimetypes.guess_type(name)[0] or 'application/octet-stream', path.stat().st_size))
            if not items:
                return
            self.records[identifier] = {
                'batch_id': batch_id, 'items': items, 'status': 'waiting_delivery', 'ready_at': None,
                'expires_at': None, 'terminal_at': None, 'error': None, 'files': [], 'cancel': threading.Event(),
                'reserved': False, 'attempts': 0, 'next_at': 0.0, 'uses_local_files': False,
                'cleanup': False, 'attempted_keys': set(), 'confirmed_keys': set(),
            }
            self.queue.append(identifier)
            self.condition.notify_all()

    def snapshot(self, identifier):
        with self.condition:
            record = self.records.get(identifier)
            if not record:
                return None
            return {key: value for key, value in record.items() if key not in {'batch_id', 'items', 'cancel', 'reserved', 'attempts', 'next_at', 'cleanup', 'attempted_keys', 'confirmed_keys'}}

    def wait_for(self, identifier, statuses, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while time.monotonic() < deadline:
                value = self.snapshot(identifier)
                if value and value['status'] in statuses:
                    return value
                self.condition.wait(min(.05, max(0, deadline - time.monotonic())))
        raise TimeoutError('Delivery did not reach the expected state.')

    def discard(self, identifier):
        with self.condition:
            record = self.records.get(identifier)
            if not record or record['status'] != 'expired' or identifier in self.queue:
                return False
            del self.records[identifier]
            return True

    def discard_expired(self):
        with self.condition:
            identifiers = [identifier for identifier, record in self.records.items()
                           if record['status'] == 'expired' and identifier not in self.queue]
            for identifier in identifiers:
                del self.records[identifier]
            return len(identifiers)

    def cancel(self, identifier):
        with self.condition:
            record = self.records.get(identifier)
            if not record or record['status'] in {'cancelled', 'expired'}:
                return
            record['cancel'].set()
            record['cleanup'] = True
            record['status'] = 'cancelling'
            if identifier not in self.queue:
                self.queue.appendleft(identifier)
            self.condition.notify_all()

    def expire(self, identifier):
        with self.condition:
            record = self.records.get(identifier)
            if not record:
                return
            record['cancel'].set()
            record['cleanup'] = True
            record['status'] = 'expiring'
            if identifier not in self.queue:
                self.queue.appendleft(identifier)
            self.condition.notify_all()

    def retry(self, identifier):
        with self.condition:
            record = self.records.get(identifier)
            if not record or record['status'] not in {'failed', 'cancelled'}:
                raise ValueError('Only a failed or cancelled delivery can be retried.')
            record.update({'batch_id': secrets.token_hex(16), 'status': 'waiting_delivery', 'error': None,
                           'files': [], 'cancel': threading.Event(), 'reserved': False, 'attempts': 0, 'terminal_at': None,
                           'next_at': 0.0, 'cleanup': False, 'uses_local_files': False, 'attempted_keys': set(),
                           'confirmed_keys': set()})
            self.queue.append(identifier)
            self.condition.notify_all()
            return [identifier]

    def _cleanup_record(self, record):
        if record['reserved']:
            safe_keys = [item.key for item in record['items']
                         if item.key not in record['attempted_keys'] or item.key in record['confirmed_keys']]
            self.control.cancel(record['batch_id'], safe_unregistered_keys=safe_keys)
            record['reserved'] = False
            self._record_batch(record['batch_id'], False)

    def _defer(self, identifier, record, error):
        if self.closing:
            record['status'] = 'failed'
            record['terminal_at'] = time.time()
            record['error'] = 'Hosted delivery stopped before cleanup completed.'
            record['uses_local_files'] = False
            return
        record['status'] = 'waiting_delivery'
        record['error'] = str(error)
        record['next_at'] = time.monotonic() + max(.01, getattr(error, 'delay', 30))
        self.queue.append(identifier)

    def _process(self, identifier, record):
        if record['cleanup'] or record['cancel'].is_set():
            self._cleanup_record(record)
            record['status'] = 'expired' if record['status'] == 'expiring' else 'cancelled'
            record['terminal_at'] = time.time()
            record['error'] = None
            return
        if record['reserved'] and record['status'] == 'waiting_delivery':
            try:
                self._cleanup_record(record)
                record['batch_id'] = secrets.token_hex(16)
            except Exception as exc:
                self._defer(identifier, record, DeliveryDeferred(str(exc)))
                return
        if not self.reconciled.is_set():
            self.control.reconcile()
            self.reconciled.set()
        try:
            self.control.reserve(record['batch_id'], [item.reservation() for item in record['items']])
            record['reserved'] = True
            self._record_batch(record['batch_id'], True)
            record['status'] = 'uploading'
            record['uses_local_files'] = True
            uploader = MultipartUploader(self.control, self.store, self.part_bytes, self.attempts,
                                           on_create=record['attempted_keys'].add,
                                           on_abort=record['confirmed_keys'].add)
            for item in record['items']:
                uploader.upload(record['batch_id'], item, record['cancel'])
            record['uses_local_files'] = False
            published = uploader._retry(lambda: self.control.commit(record['batch_id']), record['cancel'])
            ready_at = published.get('ready_at')
            expires_at = published.get('expires_at')
            files = published.get('files')
            if not isinstance(ready_at, (int, float)) or not isinstance(expires_at, (int, float)) or not 0 < expires_at - ready_at <= 3600 or not isinstance(files, list) or len(files) != len(record['items']):
                raise OSError('Media control returned an invalid publication.')
            for item, file in zip(record['items'], files):
                if not isinstance(file, dict) or file.get('name') != item.name or file.get('size') != item.size or not isinstance(file.get('url'), str) or not file['url'].startswith('/ytload/media/'):
                    raise OSError('Media control returned an invalid publication.')
            if len({file['url'] for file in files}) != len(files):
                raise OSError('Media control returned an invalid publication.')
            record.update({'status': 'ready', 'ready_at': ready_at, 'expires_at': expires_at,
                           'files': files, 'error': None})
            self._record_batch(record['batch_id'], False)
        except DeliveryCancelled:
            record['uses_local_files'] = False
            record['cleanup'] = True
            self._cleanup_record(record)
            record['status'] = 'cancelled'
            record['terminal_at'] = time.time()
            record['error'] = None
        except DeliveryDeferred as exc:
            record['uses_local_files'] = False
            active = record['reserved']
            try:
                self._cleanup_record(record)
                if active:
                    record['batch_id'] = secrets.token_hex(16)
            except Exception as cleanup_error:
                exc = DeliveryDeferred(str(cleanup_error), getattr(exc, 'delay', 30))
            self._defer(identifier, record, exc)
        except Exception as exc:
            record['uses_local_files'] = False
            record['attempts'] += 1
            try:
                self._cleanup_record(record)
            except Exception as cleanup_error:
                self._defer(identifier, record, DeliveryDeferred(str(cleanup_error)))
                return
            if record['attempts'] < self.attempts:
                record['batch_id'] = secrets.token_hex(16)
                self._defer(identifier, record, DeliveryDeferred('Hosted delivery will retry.', min(30, 2 ** record['attempts'])))
            else:
                record['status'] = 'failed'
                record['terminal_at'] = time.time()
                record['error'] = 'Hosted delivery failed. Retry delivery or use YTLoad Local.'

    def _work(self):
        try:
            self._recover()
        except Exception:
            pass
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.queue or self.closing)
                if self.closing and not self.queue:
                    return
                identifier = self.queue.popleft()
                record = self.records.get(identifier)
                if not record:
                    continue
                delay = record['next_at'] - time.monotonic()
                if delay > 0 and not record['cleanup']:
                    self.queue.append(identifier)
                    self.condition.wait(min(delay, 1))
                    continue
                record['next_at'] = 0
            try:
                self._process(identifier, record)
            except Exception as exc:
                with self.condition:
                    self._defer(identifier, record, DeliveryDeferred(str(exc)))
            finally:
                with self.condition:
                    self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closing = True
            for identifier, record in self.records.items():
                if record['status'] not in {'ready', 'expired', 'cancelled', 'failed'}:
                    record['cancel'].set()
                    record['cleanup'] = True
                    if identifier not in self.queue:
                        self.queue.appendleft(identifier)
            self.condition.notify_all()
        self.worker.join()


def delivery_from_environment(root, environ=None):
    config = R2DeliveryConfig.from_environ(environ)
    return DeliveryManager.from_config(root, config) if config else None
