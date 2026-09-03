from __future__ import annotations

from .models import DownloadRequest
from .runner import FailureKind


def recovery_advice(kind: FailureKind | None, output: str, request: DownloadRequest, *, hosted: bool = False) -> list[dict]:
    result = []
    def add(action, text):
        result.append({'action': action, 'text': text})
    if kind == FailureKind.RATE_LIMIT:
        add('none', 'Pause before retrying. Repeated attempts can prolong a rate limit; changing User-Agent alone does not remove it.')
    if kind in {FailureKind.RATE_LIMIT, FailureKind.HTTP_403, FailureKind.AUTH}:
        if hosted:
            add('none', 'This public workspace cannot use your Chrome session. If sign-in is needed, use the local app on your computer.')
        elif request.browser:
            add('browser', 'Browser sign-in is already enabled. Open the source in that browser, confirm it plays, and complete any sign-in or verification before retrying.')
        else:
            add('browser', 'Open the source in Chrome, confirm it plays, and complete any sign-in or verification. Then select Chrome under Browser sign-in and retry with current settings.')
    if kind == FailureKind.NO_SUBTITLES or (kind == FailureKind.RATE_LIMIT and (request.mode == 'subs' or request.subtitles != 'none' or request.transcript_formats)):
        add('captions', 'Choose one caption language. Even a single track can be rate-limited; try Original if translated captions are not essential, or wait before retrying.')
    if kind == FailureKind.FORMAT:
        add('format', 'Try Best available with Auto to use the formats the source currently provides.')
    text = ' '.join(output.lower().split())
    if 'no impersonate target' in text or 'impersonate target' in text and 'not available' in text:
        add('none' if hosted else 'setup', 'This yt-dlp installation has no browser impersonation target. The optional curl-cffi dependency provides it; it does not sign in to Chrome or remove an HTTP 429 limit.')
    elif kind in {FailureKind.CHALLENGE, FailureKind.PO_TOKEN}:
        add('none' if hosted else 'setup', 'Check yt-dlp and Deno with doctor, then follow the setup guide. Update the tool used by this app before retrying.')
    if hosted and any(item['action'] == 'none' and 'installation' in item['text'] for item in result):
        add('none', 'Server dependencies must be updated by the operator. Installing tools on your phone does not change this public workspace.')
    return result
