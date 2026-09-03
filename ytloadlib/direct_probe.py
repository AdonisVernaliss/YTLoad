from __future__ import annotations

import argparse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import os
import secrets
import signal
import threading
import time
from urllib.parse import urlsplit

from .direct_resolver import ProbeError, resolve_direct


class ProbeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, *, key, origin=None):
        if not isinstance(key, str) or not key.isascii() or len(key) < 32:
            raise ValueError('Set YTLOAD_PROBE_KEY to a random secret of at least 32 ASCII characters.')
        if not origin and address[0] not in {'127.0.0.1', 'localhost'}:
            raise ValueError('Set an explicit --origin for remote development access.')
        self.stopping = threading.Event()
        self.resolution_lock = threading.Lock()
        super().__init__(address, ProbeHandler)
        self.origin = origin or f'http://127.0.0.1:{self.server_port}'
        parts = urlsplit(self.origin)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password or parts.path not in {'', '/'} or parts.query or parts.fragment:
            super().server_close()
            raise ValueError('Use a complete development origin without a path.')
        self.origin = self.origin.rstrip('/')
        self.authority = parts.netloc
        self.key = key
        self.rate_lock = threading.Lock()
        self.requests = deque()
        self.connections = threading.BoundedSemaphore(8)

    def process_request(self, request, client_address):
        request.settimeout(15)
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def server_close(self):
        self.stopping.set()
        super().server_close()
        if self.resolution_lock.acquire(timeout=3):
            self.resolution_lock.release()


class ProbeHandler(BaseHTTPRequestHandler):
    server_version = 'YTLoadProbe'
    sys_version = ''

    def log_message(self, format, *args):
        return

    def send_data(self, status, value, kind='application/json; charset=utf-8', extra=None):
        data = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' https://*.googlevideo.com; img-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def host_allowed(self):
        if self.headers.get('Host') != self.server.authority:
            self.send_data(403, {'error': 'invalid-host'})
            return False
        return True

    def do_GET(self):
        if not self.host_allowed():
            return
        assets = {'/': ('direct.html', 'text/html'), '/direct-page.mjs': ('direct-page.mjs', 'text/javascript'),
                  '/direct-core.mjs': ('direct-core.mjs', 'text/javascript'), '/direct.css': ('direct.css', 'text/css')}
        if self.path not in assets:
            self.send_data(404, {'error': 'not-found'})
            return
        name, kind = assets[self.path]
        self.send_data(200, files('ytloadlib').joinpath('static', name).read_bytes(), kind + '; charset=utf-8')

    def do_POST(self):
        if not self.host_allowed():
            return
        if self.path != '/api/resolve':
            self.send_data(404, {'error': 'not-found'})
            return
        authorization = self.headers.get('Authorization', '')
        if self.headers.get('Origin') != self.server.origin or not authorization.isascii() or not secrets.compare_digest(authorization, 'Bearer ' + self.server.key):
            self.send_data(403, {'error': 'invalid-origin-or-key'})
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise ValueError()
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) - {'url', 'youtube_client', 'user_agent'}:
                raise ValueError()
            if self.server.stopping.is_set():
                self.send_data(503, {'error': 'stopping'})
                return
            with self.server.rate_lock:
                now = time.monotonic()
                while self.server.requests and now - self.server.requests[0] >= 60:
                    self.server.requests.popleft()
                if len(self.server.requests) >= 5:
                    self.send_data(429, {'error': 'probe-rate-limit'}, extra={'Retry-After': '60'})
                    return
                self.server.requests.append(now)
            if not self.server.resolution_lock.acquire(blocking=False):
                self.send_data(429, {'error': 'resolver-busy'}, extra={'Retry-After': '15'})
                return
            try:
                if self.server.stopping.is_set():
                    self.send_data(503, {'error': 'stopping'})
                    return
                data = resolve_direct(payload.get('url'), payload.get('youtube_client', 'default'), payload.get('user_agent'), cancel=self.server.stopping)
            finally:
                self.server.resolution_lock.release()
            self.send_data(200, data)
        except ProbeError as exc:
            self.send_data(502, {'error': exc.reason})
        except (ValueError, TypeError, OSError):
            self.close_connection = True
            self.send_data(400, {'error': 'invalid-resolver-request'})


def main():
    parser = argparse.ArgumentParser(description='Development-only YouTube resolver and direct-browser experiment. No media proxy or server downloads.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--origin', help='Exact development origin when using a private HTTPS reverse proxy')
    args = parser.parse_args()
    key = os.environ.get('YTLOAD_PROBE_KEY') or secrets.token_urlsafe(32)
    try:
        server = ProbeServer((args.host, args.port), key=key, origin=args.origin)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'Development probe: {server.origin}/#key={key}', flush=True)
    print('Keep the connection link private. Only metadata passes through this server. Ctrl+C stops the experiment.', flush=True)
    def stop(signum, frame):
        server.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.stopping.set()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
