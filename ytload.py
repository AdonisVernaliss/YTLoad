#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from ytloadlib import __version__
from ytloadlib.cli import parse_args, request_from_args
from ytloadlib.config import load_config
from ytloadlib.environment import detect_environment
from ytloadlib.recovery import recovery_advice
from ytloadlib.runner import failure_message, printable_command, run_download
from ytloadlib.ui import interactive_request
from ytloadlib.urls import expand_channel_url


def _print_doctor() -> int:
    report = detect_environment()
    print('YTLoad doctor')
    print(f'  yt-dlp : {report.yt_dlp.version or "missing"} ({report.yt_dlp.path or "not found"})')
    print(f'  ffmpeg : {report.ffmpeg.version or "missing"} ({report.ffmpeg.path or "not found"})')
    print(f'  deno   : {report.deno.version or "missing"} ({report.deno.path or "not found"})')
    print(f'  browsers: {", ".join(report.browsers) if report.browsers else "none detected"}')
    if not report.yt_dlp.installed:
        print('\nInstall yt-dlp first. See README.md for OS-specific commands.')
        return 1
    if not report.ffmpeg.installed:
        print('\nWarning: ffmpeg is missing; merging/conversion/post-processing will be limited.')
    if not report.deno.installed:
        print('Note: deno is optional but useful for current YouTube JavaScript challenges.')
    return 0


def _expanded_urls(request):
    expanded: list[str] = []
    for url in request.urls:
        expanded.extend(expand_channel_url(url, request.channel_scope))

    return list(dict.fromkeys(expanded))


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(argv if argv is not None else sys.argv[1:])
    if ns.version:
        print(__version__)
        return 0
    if ns.doctor:
        return _print_doctor()

    if ns.serve:
        from ytloadlib.hosted import serve_public
        if not ns.public_origin or not ns.storage or ns.ui or ns.cli or ns.lan or ns.urls or ns.url_file:
            print('Use --serve with --public-origin and --storage, without local workspace or download options.', file=sys.stderr)
            return 2
        try:
            return serve_public(ns.public_origin, ns.storage, port=ns.port or 8765, host=ns.host, base_path=ns.base_path)
        except (OSError, ValueError) as exc:
            print(f'Cannot start public backend: {exc}', file=sys.stderr)
            return 2

    try:
        config = load_config(Path(ns.config).expanduser() if ns.config else None)
    except ValueError as exc:
        print(f'Config error: {exc}', file=sys.stderr)
        return 2

    if ns.ui or (not ns.urls and not ns.url_file and not ns.cli and not ns.dry_run):
        from ytloadlib.web import serve
        try:
            return serve(config, port=ns.port, open_browser=not ns.no_open, lan=ns.lan)
        except (OSError, ValueError) as exc:
            print(f'Cannot open the workspace: {exc}', file=sys.stderr)
            return 2

    if ns.urls or ns.url_file:
        try:
            request = request_from_args(ns, config)
        except (OSError, ValueError, UnicodeError) as exc:
            print(f'Invalid download request: {exc}', file=sys.stderr)
            return 2
    else:
        try:
            request = interactive_request(config)
        except (KeyboardInterrupt, EOFError):
            print('\nCancelled.')
            return 130
        except ValueError as exc:
            print(f'Invalid download request: {exc}', file=sys.stderr)
            return 2

    if not request.urls:
        print('No URLs supplied.', file=sys.stderr)
        return 2

    if request.dry_run:
        for url in _expanded_urls(request):
            result = run_download(request, url, config)
            print(printable_command(list(result.commands[0])))
        return 0

    env = detect_environment()
    if not env.yt_dlp.installed:
        print('yt-dlp is not installed. Run the installer for your OS.', file=sys.stderr)
        return 127
    if request.mode in {'video', 'audio'} and not env.ffmpeg.installed:
        print('FFmpeg is required for video merging and audio extraction. Run the installer for your OS.', file=sys.stderr)
        return 127

    root = Path(request.output_root or config.output_root).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / '.ytload-state').mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f'Cannot create output directory {root}: {exc}', file=sys.stderr)
        return 2

    failures = 0
    for url in _expanded_urls(request):
        result = run_download(request, url, config, available_browsers=list(env.browsers))
        if result.returncode == 130:
            print('\nCancelled. Partial downloads can be resumed.')
            return 130
        if not result.success:
            failures += 1
            print(f'\nDownload failed for: {url}', file=sys.stderr)
            print(f'Failure type: {result.failure_kind.value if result.failure_kind else "unknown"}', file=sys.stderr)
            print(failure_message(result.failure_kind), file=sys.stderr)
            for hint in recovery_advice(result.failure_kind, result.output, request):
                print(f'Next: {hint["text"]}', file=sys.stderr)
            if request.fail_fast:
                break
        else:
            for file in result.files:
                print(f'\nSaved: {file}')
        for warning in result.warnings:
            print(f'Warning: {warning}', file=sys.stderr)

    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
