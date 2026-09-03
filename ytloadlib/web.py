from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
from importlib.resources import files
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import shutil
import subprocess
import sys
import threading
from urllib.parse import parse_qs, quote, urlsplit
import webbrowser

from . import __version__
from .environment import detect_environment
from .inspection import inspect_url
from .jobs import JobManager
from .models import AppConfig


class WorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, config, lan=False):
        super().__init__(address, WorkspaceHandler)
        self.token = secrets.token_urlsafe(32)
        self.lan = lan
        self.access_key = secrets.token_urlsafe(24)
        self.session_key = secrets.token_urlsafe(32)
        self.authority = f'{address[0]}:{self.server_port}'
        self.config = config
        self.environment = detect_environment()
        self.jobs = JobManager(config)
        self.inspection_lock = threading.Lock()
        self.folder_lock = threading.Lock()

    def server_close(self):
        if hasattr(self, 'jobs'):
            self.jobs.close()
        super().server_close()


class WorkspaceHandler(BaseHTTPRequestHandler):
    server_version = 'YTLoad'
    sys_version = ''

    def log_message(self, format, *args):
        return

    def _send(self, status, data, content_type='application/json; charset=utf-8', extra=None):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, api=False):
        authority = self.server.authority
        if self.headers.get('Host') != authority:
            self._send(403, {'error': 'Use the local workspace address printed in the terminal.'})
            return False
        if self.server.lan and urlsplit(self.path).path != '/connect':
            try:
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                session = cookie['ytload-session'].value if 'ytload-session' in cookie else ''
            except CookieError:
                session = ''
            if not session.isascii() or not secrets.compare_digest(session, self.server.session_key):
                self._send(403, {'error': 'Open the private connection link printed in the host terminal.'})
                return False
        if self.command == 'POST' and self.headers.get('Origin') != f'http://{authority}':
            self._send(403, {'error': 'Only the local workspace can change downloads.'})
            return False
        if api:
            token = self.headers.get('X-YTLoad-Token', '')
            if self.command == 'GET' and self.path.startswith('/api/files/'):
                token = parse_qs(urlsplit(self.path).query).get('token', [''])[0]
            if not token.isascii() or not secrets.compare_digest(token, self.server.token):
                self._send(403, {'error': 'Reload the workspace to reconnect.'})
                return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self._authorized(api=path.startswith('/api/')):
            return
        try:
            if path == '/connect':
                key = parse_qs(urlsplit(self.path).query).get('key', [''])[0]
                if not self.server.lan or not key.isascii() or not secrets.compare_digest(key, self.server.access_key):
                    self._send(403, {'error': 'Invalid connection key.'})
                    return
                self._send(303, {}, extra={'Location': '/', 'Set-Cookie': f'ytload-session={self.server.session_key}; Path=/; HttpOnly; SameSite=Strict'})
            elif path in {'/', '/app.js', '/style.css', '/icon.svg', '/i18n.mjs', '/appearance.js', '/links.mjs'}:
                name = 'index.html' if path == '/' else path[1:]
                data = files('ytloadlib').joinpath('static', name).read_bytes()
                if path == '/':
                    data = data.replace(b'__WORKSPACE_TOKEN__', self.server.token.encode()).replace(b'__WORKSPACE_BASE__', b'')
                content_type = 'text/javascript' if name.endswith(('.js', '.mjs')) else mimetypes.guess_type(name)[0]
                self._send(200, data, content_type + '; charset=utf-8')
            elif path == '/api/config':
                self._send(200, {'config': asdict(self.server.config), 'environment': asdict(self.server.environment),
                                 'version': __version__, 'platform': sys.platform, 'remote': self.server.lan})
            elif path == '/api/jobs':
                self._send(200, self.server.jobs.snapshot())
            elif path.startswith('/api/files/'):
                parts = path.split('/')
                if len(parts) != 5:
                    raise ValueError('Unknown file.')
                file_path = self.server.jobs.file_path(parts[3], int(parts[4]))
                with file_path.open('rb') as handle:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(os.fstat(handle.fileno()).st_size))
                    self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(file_path.name))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Referrer-Policy', 'no-referrer')
                    self.end_headers()
                    shutil.copyfileobj(handle, self.wfile, length=1024 * 1024)
            else:
                self._send(404, {'error': 'Page not found.'})
        except (ValueError, OSError) as exc:
            if not isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                self._send(404, {'error': str(exc)})

    def do_POST(self):
        if not self._authorized(api=True):
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
            path = urlsplit(self.path).path
            if path == '/api/jobs':
                if not self.server.environment.yt_dlp.installed:
                    raise ValueError('yt-dlp is missing. Run the installer for your operating system, then restart this app.')
                if payload.get('mode', 'video') in {'video', 'audio'} and not self.server.environment.ffmpeg.installed:
                    raise ValueError('FFmpeg is required for video and audio. Run the installer, then restart this app.')
                self._send(201, {'ids': self.server.jobs.add(payload)})
            elif path in {'/api/cancel', '/api/retry'}:
                identifier = payload.get('id')
                if not isinstance(identifier, str):
                    raise ValueError('Choose a download.')
                if path == '/api/cancel':
                    self.server.jobs.cancel(identifier)
                    self._send(200, {'ok': True})
                else:
                    self._send(201, {'ids': self.server.jobs.retry(identifier)})
            elif path == '/api/inspect':
                if not self.server.inspection_lock.acquire(blocking=False):
                    self._send(409, {'error': 'A link is already being checked. Please wait.'})
                    return
                try:
                    self._send(200, inspect_url(payload.get('url'), payload.get('browser'),
                                               user_agent=payload.get('user_agent'), youtube_client=payload.get('youtube_client', 'auto')))
                finally:
                    self.server.inspection_lock.release()
            elif path == '/api/choose-folder':
                if not self.server.folder_lock.acquire(blocking=False):
                    raise ValueError('A folder dialog is already open.')
                try:
                    self._send(200, {'path': choose_folder()})
                finally:
                    self.server.folder_lock.release()
            elif path == '/api/open-folder':
                value = payload.get('path')
                if not isinstance(value, str) or not value.strip() or '\x00' in value:
                    raise ValueError('Enter a folder path.')
                folder = Path(value).expanduser().resolve()
                if not folder.is_dir():
                    raise ValueError('This folder does not exist yet. Start a download to create it.')
                open_folder(folder)
                self._send(200, {'ok': True})
            else:
                self._send(404, {'error': 'Action not found.'})
        except (ValueError, OSError, TypeError, subprocess.SubprocessError) as exc:
            if not isinstance(exc, (BrokenPipeError, ConnectionResetError)):
                self._send(400, {'error': str(exc)})


def choose_folder() -> str | None:
    if sys.platform == 'darwin':
        command = ['osascript', '-e', 'POSIX path of (choose folder with prompt "Choose your download folder")']
    else:
        script = ('import tkinter as t; from tkinter import filedialog; '
                  'root=t.Tk(); root.withdraw(); root.attributes("-topmost", True); '
                  'print(filedialog.askdirectory(title="Choose your download folder")); root.destroy()')
        command = [sys.executable, '-c', script]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        if '-128' in result.stderr:
            return None
        raise ValueError('The folder dialog is unavailable. Paste a folder path into the field instead.')
    return result.stdout.strip() or None


def open_folder(folder: Path) -> None:
    if sys.platform == 'win32':
        os.startfile(str(folder))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def lan_address() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        try:
            connection.connect(('192.0.2.1', 9))
            address = connection.getsockname()[0]
        except OSError as exc:
            raise ValueError('Connect this computer to your local network before using --lan.') from exc
    if not any(ipaddress.ip_address(address) in network for network in (
        ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'), ipaddress.ip_network('192.168.0.0/16'),
    )):
        raise ValueError('No private local network address found. --lan requires a private IPv4 network.')
    return address


def create_server(config: AppConfig, port: int = 0, lan: bool = False) -> WorkspaceServer:
    if not 0 <= port <= 65535:
        raise ValueError('Port must be between 0 and 65535.')
    return WorkspaceServer((lan_address() if lan else '127.0.0.1', port), config, lan=lan)


def serve(config: AppConfig, *, port: int = 0, open_browser: bool = True, lan: bool = False) -> int:
    server = create_server(config, port, lan=lan)
    url = f'http://{server.authority}/'
    if lan:
        url += 'connect?key=' + server.access_key
    print(f'\nYTLoad\n\nOpen {url}\nKeep this terminal open. Press Ctrl+C to stop.\n', flush=True)
    if lan:
        print('Open this private link on a phone on the same network. Treat it like a password.\nUse a trusted home network; LAN mode uses HTTP.\n', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopping downloads and closing the workspace.')
    finally:
        server.server_close()
    return 0
