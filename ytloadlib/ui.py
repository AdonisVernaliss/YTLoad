from __future__ import annotations

from pathlib import Path
from typing import Callable

from .models import AppConfig, DownloadRequest
from .validation import FORMATS, validate_request, validate_url
from .urls import split_links


def interactive_request(config: AppConfig, *, input_fn: Callable[[str], str] = input,
                        print_fn: Callable[..., None] = print) -> DownloadRequest:
    def ask(prompt):
        answer = input_fn(prompt).strip()
        if answer.lower() in {'q', 'quit'}:
            raise KeyboardInterrupt
        return answer

    def choose(prompt, choices, default):
        while True:
            value = ask(prompt).lower() or default
            if value in choices:
                return value
            print_fn('Choose one of: ' + ', '.join(choices))

    def yes(prompt, default=False):
        value = choose(prompt + (' [Y/n]: ' if default else ' [y/N]: '), ['y', 'n', 'yes', 'no'], 'y' if default else 'n')
        return value in {'y', 'yes'}

    print_fn('\nYTLoad')
    print_fn('Video, audio and transcripts. Saved to your computer.')
    print_fn('Press Enter for defaults. Type q at any prompt to cancel.\n')
    print_fn('1 / LINKS')
    print_fn('Paste one or more video, playlist or channel URLs, separated by spaces.')
    while True:
        try:
            urls = split_links(ask('Links: '))
            if not urls or len(urls) > 100:
                raise ValueError('Add between 1 and 100 URLs.')
            for url in urls:
                validate_url(url)
            break
        except ValueError as exc:
            print_fn(str(exc))
    print_fn('\n2 / WHAT TO SAVE')
    print_fn('  1  Video       Picture and sound')
    print_fn('  2  Audio       Sound only')
    print_fn('  3  Transcript  Existing captions as text or timed subtitles')
    print_fn('  4  Details     Metadata JSON without downloading media')
    mode = {'1': 'video', '2': 'audio', '3': 'subs', '4': 'metadata'}[choose('Choose [1]: ', ['1', '2', '3', '4'], '1')]
    request = DownloadRequest(urls=urls, mode=mode, quality=config.quality, sub_langs=config.sub_langs,
                              browser=config.browser, sponsorblock=config.sponsorblock, print_command=config.print_commands)
    if mode == 'video':
        print_fn('\nQUALITY — an upper limit, never an upscale')
        print_fn('  1 Best available   2 Up to 4K   3 Up to 1440p   4 Up to 1080p')
        print_fn('  5 Up to 720p       6 Compatible H.264   7 Small file   8 Up to 480p')
        qualities = {'1': 'best', '2': '2160p', '3': '1440p', '4': '1080p', '5': '720p', '6': 'compatible', '7': 'small', '8': '480p'}
        default = next((key for key, value in qualities.items() if value == config.quality), '1')
        request.quality = qualities[choose(f'Choose [{default}]: ', list(qualities), default)]
        print_fn('\nFORMAT: 1 Auto (source codecs)   2 MP4 (H.264/AAC)   3 MKV')
        print_fn('Auto preserves source quality. MP4 works in most players and editors.')
        request.video_container = {'1': 'auto', '2': 'mp4', '3': 'mkv'}[choose('Choose [1]: ', ['1', '2', '3'], '1')]
    elif mode == 'audio':
        print_fn('\nFORMAT: 1 Original   2 MP3   3 M4A   4 Opus   5 FLAC   6 WAV')
        print_fn('FLAC and WAV are larger. They do not improve the source quality.')
        request.audio_format = {'1': 'best', '2': 'mp3', '3': 'm4a', '4': 'opus', '5': 'flac', '6': 'wav'}[choose('Choose [1]: ', list('123456'), '1')]
    if mode == 'subs' or (mode in {'video', 'audio'} and yes('Also save a transcript?')):
        print_fn('\nTRANSCRIPT')
        print_fn('Captions must already exist on the source. Original subtitle files are kept.')
        print_fn('  1 Prefer creator captions, otherwise automatic   2 Creator only   3 Automatic only')
        request.subtitles = {'1': 'both', '2': 'authored', '3': 'auto'}[choose('Caption source [1]: ', list('123'), '1')]
        print_fn('Languages: default (English if available), en.*, ru.*, orig (original automatic track), all')
        request.sub_langs = ask(f'Languages [{config.sub_langs}]: ') or config.sub_langs
        while True:
            formats = [item.strip().lower() for item in (ask('Export formats: txt,srt,vtt,json [txt]: ') or 'txt').split(',')]
            if formats and all(item in FORMATS for item in formats):
                request.transcript_formats = list(dict.fromkeys(formats))
                break
            print_fn('Choose one or more of: txt,srt,vtt,json')
        if 'txt' in formats:
            request.transcript_timestamps = yes('Include timestamps in TXT?')
        if mode == 'video':
            request.embed_subs = yes('Also embed subtitles in the video?')
    print_fn('\n3 / DESTINATION')
    request.output_root = ask(f'Save to [{config.output_root}]: ') or config.output_root
    if yes('Show advanced options?'):
        request.channel_scope = choose('Channel section: auto/videos/shorts/streams/all [auto]: ', ['auto', 'videos', 'shorts', 'streams', 'all'], 'auto')
        request.browser = choose('Browser sign-in: none/chrome/firefox/safari/edge/brave/chromium [none]: ', ['none', 'chrome', 'firefox', 'safari', 'edge', 'brave', 'chromium'], 'none')
        if request.browser == 'none':
            request.browser = None
        request.playlist_items = ask('Playlist items (e.g. 1:10,15; blank = all): ') or None
        request.date_after = ask('Uploaded after (YYYYMMDD; blank = any): ') or None
        request.date_before = ask('Uploaded before (YYYYMMDD; blank = any): ') or None
        request.max_filesize = ask('Maximum file size (e.g. 2G; blank = unlimited): ') or None
        request.limit_rate = ask('Speed limit (e.g. 5M; blank = unlimited): ') or None
        request.sponsorblock = choose('SponsorBlock: off/mark/remove [off]: ', ['off', 'mark', 'remove'], 'off')
        request.archive = None if yes('Skip completed media in collections?', config.archive) else False
        request.write_info_json = yes('Save video details as JSON?', mode == 'metadata')
        request.write_thumbnail = yes('Save thumbnail?')
        request.write_description = yes('Save description?')
        request.write_comments = yes('Save comments? This can be slow and large.')
        request.print_command = yes('Print the underlying download command?', config.print_commands)
    validate_request(request)
    print_fn('\nREADY TO DOWNLOAD')
    print_fn(f'  Links       {len(request.urls)}')
    print_fn(f'  Save        {mode}')
    if mode == 'video':
        print_fn(f'  Quality     {request.quality} / {request.video_container}')
    elif mode == 'audio':
        print_fn(f'  Format      {request.audio_format}')
    print_fn(f'  Transcript  {", ".join(request.transcript_formats) or "not included"}')
    print_fn(f'  Folder      {Path(request.output_root).expanduser()}')
    if not yes('Start download?', True):
        raise KeyboardInterrupt
    return request
