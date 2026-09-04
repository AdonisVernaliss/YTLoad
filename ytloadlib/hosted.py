from __future__ import annotations

from dataclasses import asdict
from http.cookies import CookieError, SimpleCookie
from http.server import ThreadingHTTPServer
from importlib.resources import files
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import parse_qs, quote, urlsplit

from . import __version__
from .environment import detect_environment
from .hosted_state import PublicState, prepare_public_request
from .inspection import inspect_public_url
from .models import AppConfig
from .public_policy import (PUBLIC_FILE_BYTES, PUBLIC_JOB_MAX_SECONDS, PUBLIC_RESULT_TTL,
                            PUBLIC_SESSION_BYTES, PUBLIC_STREAM_TIMEOUT, PUBLIC_TOTAL_BYTES)
from .web import WorkspaceHandler


class PublicServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, storage, origin, proxy_key, base_path='/ytload'):
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
            raise ValueError('Set a complete HTTPS public origin without a path.')
        if not isinstance(proxy_key, str) or not proxy_key.isascii() or len(proxy_key) < 32:
            raise ValueError('Set YTLOAD_PROXY_KEY to a random secret of at least 32 characters.')
        if not re.fullmatch(r'(?:/[A-Za-z0-9_-]+)*/?', base_path):
            raise ValueError('The public base path must contain simple path segments.')
        super().__init__(address, PublicHandler)
        try:
            self.origin = origin.rstrip('/')
            self.authority = parsed.netloc
            self.proxy_key = proxy_key
            self.base_path = base_path.rstrip('/')
            self.state = PublicState(Path(storage))
            self.environment = detect_environment()
            self.inspection_lock = threading.Lock()
            self.requests = threading.BoundedSemaphore(32)
        except Exception:
            super().server_close()
            raise

    def process_request(self, request, client_address):
        request.settimeout(30)
        if not self.requests.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.requests.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.requests.release()

    def server_close(self):
        if hasattr(self, 'state'):
            self.state.close()
        super().server_close()


class PublicHandler(WorkspaceHandler):
    def _access(self):
        key = self.headers.get('X-YTLoad-Proxy-Key', '')
        if not key.isascii() or not secrets.compare_digest(key, self.server.proxy_key) or self.headers.get('X-Forwarded-Host') != self.server.authority or self.headers.get('X-Forwarded-Proto') != 'https':
            self._send(403, {'error': 'Use the public workspace address.'})
            return None
        parts = urlsplit(self.path)
        prefix = self.server.base_path + '/'
        if not parts.path.startswith(prefix):
            self._send(404, {'error': 'Page not found.'})
            return None
        if self.command == 'POST' and self.headers.get('Origin') != self.server.origin:
            self._send(403, {'error': 'Only this workspace can change its downloads.'})
            return None
        return parts.path[len(self.server.base_path):]

    def _session(self, create=False):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            identifier = cookie['ytload-public-session'].value if 'ytload-public-session' in cookie else ''
        except CookieError:
            identifier = ''
        session = self.server.state.session(identifier)
        if session is None and create:
            try:
                client = str(ipaddress.ip_address(self.headers.get('X-YTLoad-Client-IP', '')))
                session = self.server.state.new_session(client)
            except ValueError as exc:
                self._send(429, {'error': str(exc)}, extra={'Retry-After': '60'})
                return None
        if session is None:
            self._send(401, {'error': 'Your temporary session expired. Reload the workspace.'})
            return None
        return session

    def _token(self, session, file_request=False):
        token = parse_qs(urlsplit(self.path).query).get('token', [''])[0] if file_request else self.headers.get('X-YTLoad-Token', '')
        if not token.isascii() or not secrets.compare_digest(token, session.token):
            self._send(403, {'error': 'Reload the workspace to reconnect.'})
            return False
        return True

    def do_GET(self):
        path = self._access()
        if path is None:
            return
        try:
            static = {'/', '/app.js', '/style.css', '/icon.svg', '/i18n.mjs', '/appearance.js', '/links.mjs', '/hosted-ui.mjs'}
            if path in static:
                extra = {}
                name = 'index.html' if path == '/' else path[1:]
                data = files('ytloadlib').joinpath('static', name).read_bytes()
                if path == '/':
                    session = self._session(create=True)
                    if session is None:
                        return
                    data = data.replace(b'="/', b'="' + self.server.base_path.encode() + b'/')
                    data = data.replace(b'__WORKSPACE_TOKEN__', session.token.encode()).replace(b'__WORKSPACE_BASE__', self.server.base_path.encode())
                    extra['Set-Cookie'] = f'ytload-public-session={session.identifier}; Path={self.server.base_path}/; Secure; HttpOnly; SameSite=Strict'
                kind = 'text/javascript' if name.endswith(('.js', '.mjs')) else mimetypes.guess_type(name)[0]
                self._send(200, data, kind + '; charset=utf-8', extra)
                return
            session = self._session()
            if session is None or not self._token(session, path.startswith('/api/files/')):
                return
            if path == '/api/config':
                environment = asdict(self.server.environment)
                environment['browsers'] = []
                for value in environment.values():
                    if isinstance(value, dict):
                        value['path'] = None
                config = asdict(AppConfig(output_root='Temporary server storage'))
                self._send(200, {'config': config, 'environment': environment, 'version': __version__, 'platform': 'server', 'remote': True, 'hosted': True,
                                 'public_policy': {'file_bytes': PUBLIC_FILE_BYTES, 'session_bytes': PUBLIC_SESSION_BYTES,
                                                   'total_bytes': PUBLIC_TOTAL_BYTES, 'job_max_seconds': PUBLIC_JOB_MAX_SECONDS,
                                                   'result_ttl_seconds': PUBLIC_RESULT_TTL}})
            elif path == '/api/jobs':
                self._send(200, self.server.state.snapshot(session))
            elif path.startswith('/api/files/'):
                self._file(path, session)
            else:
                self._send(404, {'error': 'Page not found.'})
        except (ValueError, LookupError, OSError) as exc:
            if not isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                self._send(404, {'error': 'This file or download is no longer available.'})

    def _file(self, path, session):
        parts = path.split('/')
        if len(parts) != 5:
            raise LookupError('Unknown file.')
        state = self.server.state
        identifier = parts[3]
        file_path = state.begin_file(session, identifier, int(parts[4]))
        try:
            self.connection.settimeout(PUBLIC_STREAM_TIMEOUT)
            with file_path.open('rb') as handle:
                size = os.fstat(handle.fileno()).st_size
                start, end = 0, size - 1
                requested = self.headers.get('Range')
                if requested:
                    match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
                    if not match or not any(match.groups()) or size == 0:
                        self._send(416, {}, extra={'Content-Range': f'bytes */{size}'})
                        return
                    if match[1]:
                        start = int(match[1])
                        end = min(int(match[2]), end) if match[2] else end
                    else:
                        start = max(0, size - int(match[2]))
                    if start > end or start >= size:
                        self._send(416, {}, extra={'Content-Range': f'bytes */{size}'})
                        return
                self.send_response(206 if requested else 200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(max(0, end - start + 1)))
                self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(file_path.name))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Cache-Control', 'private, no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                if requested:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                handle.seek(start)
                remaining = max(0, end - start + 1)
                while remaining:
                    data = handle.read(min(1024 * 1024, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        finally:
            state.end_file(session, identifier)

    def do_POST(self):
        path = self._access()
        if path is None:
            return
        session = self._session()
        if session is None or not self._token(session):
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected JSON content.')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                self.close_connection = True
                raise ValueError('Request is empty or too large.')
            self.connection.settimeout(10)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected a JSON object.')
            state = self.server.state
            if path == '/api/jobs':
                self._send(202, {'ids': state.add(session, payload)})
            elif path == '/api/inspect':
                state.allow_inspection(session)
                if payload.get('browser'):
                    raise ValueError('Browser sign-in is available in the local app only.')
                if not self.server.inspection_lock.acquire(blocking=False):
                    raise ValueError('Another link check is in progress. Please wait.')
                try:
                    self._send(200, inspect_public_url(payload.get('url'), user_agent=payload.get('user_agent'), youtube_client=payload.get('youtube_client', 'auto')))
                finally:
                    self.server.inspection_lock.release()
            else:
                match = re.fullmatch(r'/api/(cancel|retry)', path)
                if not match:
                    self._send(404, {'error': 'Action not found.'})
                    return
                with state.lock:
                    identifier = payload.get('id')
                    if not isinstance(identifier, str):
                        raise ValueError('Choose a download.')
                    state.owned(session, identifier)
                    if match[1] == 'cancel':
                        state.jobs.cancel(identifier)
                        self._send(200, {'ok': True})
                    else:
                        self._send(202, {'ids': state.retry(session, identifier)})
        except LookupError:
            self._send(404, {'error': 'This download is not available in your session.'})
        except (ValueError, OSError, TypeError) as exc:
            if not isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                self._send(400, {'error': str(exc).replace(str(self.server.state.root), '[storage]')})


def serve_public(origin, storage, *, port=8765, host='127.0.0.1', base_path='/ytload'):
    server = PublicServer((host, port), Path(storage), origin, os.environ.get('YTLOAD_PROXY_KEY', ''), base_path)
    print(f'YTLoad public backend listening on {host}:{server.server_port}. Public workspace: {server.origin}{server.base_path}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping public downloads.', flush=True)
    finally:
        server.server_close()
    return 0
