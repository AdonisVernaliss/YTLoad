# YTLoad

**Save the video. Keep the audio. Take the words with you.**

A local web workspace, an isolated hosted mode and an English command-line tool for downloading videos, audio, existing captions and metadata with [yt-dlp](https://github.com/yt-dlp/yt-dlp). Choose what to save, set the quality, and keep everything in a folder you control.

**Python 3.10+** · **Web + CLI** · **English / German / Russian UI** · **[MIT License](LICENSE)**

[Quick start](#quick-start) · [Web workspace](#web-workspace) · [Phone access](#phone-and-tablet-access) · [CLI examples](#command-line-interface) · [Troubleshooting](#troubleshooting)

## Features

- **Four download modes:** video with sound, audio only, transcripts only, or metadata.
- **Quality and format controls:** resolution limits, MP4/MKV containers, and original or converted audio.
- **Optional transcripts:** export TXT, SRT, VTT and JSON alongside media, or without downloading the media.
- **A clear web interface:** dark and light themes, EN/DE/RU translations, responsive layouts and a live download queue.
- **Terminal workflows:** an interactive English wizard, direct commands, URL lists, playlists and channel sections.
- **Recoverable downloads:** cancellation, retry, partial-file resuming and separate download records for different settings.
- **Hosted mode:** separate visitor sessions, temporary files and explicit download limits behind an HTTPS proxy.
- **Local operation:** files stay on the host computer; phone access is available on a trusted local network.

The application uses Python's standard library, with no frontend build step or application account. Downloading still requires internet access to the source and any optional services you enable.

## Quick start

Download the source archive from [Releases](https://github.com/AdonisVernaliss/YTLoad/releases/latest), or clone the repository:

```bash
git clone https://github.com/AdonisVernaliss/YTLoad.git
cd YTLoad
```

If Python and the media tools are already installed, download **ytload.pyz** from the same release and run `python3 ytload.pyz --ui`. The source archive includes the installers and desktop launchers.

### 1. Install the tools

| Tool | Needed for |
| --- | --- |
| **Python 3.10+** | Running the application |
| **yt-dlp** | Reading sources and downloading media or captions |
| **FFmpeg** | Video/audio processing; not required for Transcript or Details mode |
| **Deno** | Recommended JavaScript runtime for YouTube challenge support |

The `yt-dlp` and `ffmpeg` executables must be available on your `PATH`. For YouTube, keep yt-dlp and its JavaScript challenge support current; see the [upstream setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

<details>
<summary><strong>macOS</strong></summary>

Install [Homebrew](https://brew.sh/) first, then run:

```bash
bash install-macos.sh
```

The script installs or updates Python, yt-dlp, FFmpeg and Deno using Homebrew.

</details>

<details>
<summary><strong>Windows</strong></summary>

The installer uses [WinGet](https://learn.microsoft.com/en-us/windows/package-manager/winget/). In PowerShell, run:

```powershell
.\install-windows.ps1
```

It installs or updates the media tools and installs Python if no Python command is found. After installation, open a new terminal so newly installed tools are available on `PATH`.

If your PowerShell policy blocks the script, the tools can also be installed individually using their official instructions.

</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
bash install-linux.sh
```

The script supports APT, DNF, Pacman and Zypper. It uses `sudo` for system packages and installs a standalone yt-dlp release into `~/.local/bin` when yt-dlp is absent. Follow its instructions to add that directory to `PATH` if needed.

Deno is not installed automatically by this script. For YouTube challenge support, follow the [Deno installation instructions](https://docs.deno.com/runtime/getting_started/installation/) and the [yt-dlp setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

</details>

Already have the tools? Skip the installers. There is no Python package or Node.js dependency to install for the application itself.

### 2. Check the environment

```bash
python3 ytload.py doctor
```

On Windows, use `py -3` in place of `python3` throughout this README:

```powershell
py -3 ytload.py doctor
```

### 3. Open the workspace

```bash
python3 ytload.py --ui
```

Running `python3 ytload.py` without arguments also opens the web interface. Desktop shortcuts are included: **Start YTLoad.command** for macOS and **Start YTLoad.cmd** for Windows.

Keep the terminal open while downloads run. Press **Ctrl+C** to stop the app and its active downloads.

## Web workspace

1. **Paste links.** Add one or more video, playlist or channel URLs, or import a `.txt` file. **Check link** previews the first link's title, available quality and caption languages.
2. **Choose what to keep.** Select Video, Audio, Transcript or Details. Add transcripts to a video/audio download if needed.
3. **Choose quality, format and destination.** The summary shows your current selection. **More control** contains playlist filters, speed limits and optional extra files.
4. **Add to downloads.** Follow progress, cancel an active item, or retry a failed or cancelled item. Completed files appear as download links. Submitted links stay in the input; use **Clear links** to remove them.

| Mode | Result |
| --- | --- |
| **Video** | Video with sound, with optional captions and transcripts |
| **Audio** | Original audio or MP3, M4A, Opus, WAV or FLAC, with optional transcripts |
| **Transcript** | Existing captions exported as text or timed subtitles; no media download |
| **Details** | Metadata JSON, with optional thumbnail, description or comments |

Shared YouTube links are normalized automatically: Shorts and short links become standard video URLs, tracking parameters such as `si` are removed, and duplicate links are merged. Markdown links are accepted too. Links to other sites keep their query parameters.

After a failure, **Suggested next steps** explains which setting may help. **Retry same settings** repeats the original request. **Retry with current settings** uses the form above for that one link, including a newly selected browser, without changing the links in the input.

Downloads run one at a time. A file can reach 100% before merging, conversion or transcript export finishes; wait for the item to show **Saved**.

Theme and language preferences are remembered in the browser. Changing the interface language does **not** change the caption language. Queue history and activity logs survive a page refresh and clear when the app closes. They are kept in memory, with up to 200 recent jobs and 150 recent log entries per job. Downloaded files remain on disk.

To choose a fixed port or open the browser yourself:

```bash
python3 ytload.py --ui --port 8765 --no-open
```

## Phone and tablet access

Start the app on your computer:

```bash
python3 ytload.py --ui --lan
```

Open the private connection link printed in the terminal on a phone or tablet connected to the **same trusted Wi-Fi/network**. Keep that link private: its key grants access to the workspace.

- **The computer does the downloading.** Keep it awake and leave the app running.
- **Files first save to the computer.** Tap a completed file link to save a copy on your phone. On iPhone, use the browser's download/share controls to save it to Files.
- **Folder controls act on the computer.** Browse, Open folder and typed paths refer to the host, not the phone's filesystem.
- **Use a trusted private network.** LAN mode uses HTTP, binds to a detected private IPv4 address, and requires a connection key and session cookie. Do not expose it through router port forwarding.

Default mode listens only on `127.0.0.1`. Restarting the app generates a new connection key. Guest Wi-Fi isolation, VPN routing or a firewall may prevent devices from reaching one another.

## Command-line interface

### Interactive wizard

```bash
python3 ytload.py --cli
```

The English wizard guides you through links, media type, quality, captions and destination, then asks you to confirm the summary. Press Enter for a default or type `q` to cancel.

### Common examples

Replace `VIDEO_URL`, `PLAYLIST_URL` and `CHANNEL_URL` with real links. The following commands download directly without starting the web server.

**Video up to 1080p, in a compatible MP4 container:**

```bash
python3 ytload.py "VIDEO_URL" --quality 1080p --container mp4
```

**MP3 audio:**

```bash
python3 ytload.py "VIDEO_URL" --audio mp3
```

**English transcripts only, as TXT and SRT:**

```bash
python3 ytload.py "VIDEO_URL" --subs --transcript txt,srt --sub-langs "en.*" --timestamps
```

**Video with a transcript, or audio with a transcript:**

```bash
python3 ytload.py "VIDEO_URL" --quality 1080p --transcript txt,json --sub-langs "de.*"
python3 ytload.py "VIDEO_URL" --audio m4a --transcript txt
```

**Metadata, description and thumbnail without media:**

```bash
python3 ytload.py "VIDEO_URL" --metadata --description --thumbnail
```

**Selected playlist entries or all three channel sections:**

```bash
python3 ytload.py "PLAYLIST_URL" --items "1:10,15,20:30:2,-1"
python3 ytload.py "CHANNEL_URL" --channel all --quality 1080p
```

**A URL list with a speed limit and a custom destination:**

```bash
python3 ytload.py --url-file links.txt --limit-rate 5M -o "~/Downloads/Media"
```

Put one complete URL on each line of `links.txt`.

**Inspect a command without downloading:**

```bash
python3 ytload.py "VIDEO_URL" --quality 1080p --dry-run
python3 ytload.py --help
```

The `ytload` and `ytload.cmd` launchers expose the same options. Use `./ytload` on macOS/Linux or `ytload.cmd` on Windows when its `python` command is available.

<details>
<summary><strong>More CLI options</strong></summary>

| Option | Purpose |
| --- | --- |
| `--date-after 20260101 --date-before 20261231` | Filter by upload date |
| `--channel videos`, `shorts`, `streams` or `all` | Choose channel sections |
| `--max-filesize 2G` | Apply a source file-size limit |
| `--with-subs authored` | Save creator subtitle files alongside media |
| `--embed-subs` | Also embed requested captions into video; combine with `--with-subs` or `--transcript` |
| `--info-json --description --thumbnail` | Save extra files |
| `--comments` | Save comments in metadata JSON; potentially large or slow |
| `--sponsorblock mark` or `--sponsorblock remove` | Mark or remove segments using SponsorBlock community data |
| `--archive` / `--no-archive` | Enable or disable the completed-media archive |
| `--fail-fast` | Stop after a failure instead of continuing the batch |
| `--print-command` | Show the generated yt-dlp command locally |

Advanced users can append yt-dlp arguments after `--passthrough`. Put it last because all remaining arguments are passed through. This option is unavailable in the web interface.

```bash
python3 ytload.py "VIDEO_URL" --passthrough --retries 10
```

For direct download commands, exit codes are **0** for success, **1** for download failure, **2** for invalid input/configuration, **127** for missing required tools and **130** for cancellation.

</details>

## Quality and formats

Quality presets set an **upper limit**, not a target for upscaling. Choosing 1080p for a 720p source keeps it at 720p. Choosing a different container does not create missing quality.

| Setting | Behavior |
| --- | --- |
| **Best available** | Highest available source quality, which may exceed 4K |
| **2160p / 1440p / 1080p / 720p / 480p** | Best available video within the resolution limit |
| **Small** | Up to 720p, filtering known video sizes around 250 MB; not a final-size guarantee |
| **Auto** | Preserves source codecs; separate streams merge into MKV, while a single source may keep its container |
| **MP4** | Selects H.264/AVC video and AAC/M4A audio, then remuxes to MP4 |
| **MKV** | Keeps source codecs in a flexible MKV container |
| **Compatible** | Prefers H.264/AVC and AAC; can also be combined with MKV |
| **Original audio** | Extracts the source audio without requesting a different audio codec |
| **MP3 / M4A / Opus / WAV / FLAC** | Converts with FFmpeg; WAV and FLAC cannot restore quality lost in the source |

If the source lacks a requested codec, try **Best available + Auto**. File-size filters depend on source estimates: unknown sizes or merged output can exceed the selected limit.

## Captions and transcripts

**Transcripts come from captions already available on the source.** The app does not transcribe speech from audio. If a matching track is absent, Transcript mode reports an error; a successful media download may finish with a caption warning.

| Export | Best suited to |
| --- | --- |
| **TXT** | Reading and notes, with optional timestamps |
| **SRT** | Timed subtitles for video editors and players |
| **VTT** | Timed captions for web players |
| **JSON** | Structured segments with `start`, `end` and `text` |

Select one or more formats. Original subtitle files are retained alongside exports. Caption conversion supports VTT, SRT and JSON3, removes markup and cleans overlapping rolling captions while preserving ordinary repeated speech.

<details>
<summary><strong>Caption sources and language selection</strong></summary>

- `--subs` exports captions without media and creates TXT by default.
- `--transcript txt,srt,json` adds those exports to the current mode.
- `--with-subs` saves subtitle sidecars without requiring a readable-text export.
- `--subs-mode authored|auto|both` selects the caption source in Transcript mode.
- `--with-subs authored|auto|both` selects the caption source alongside media.
- `both` prefers creator captions when both sources share a language; it does not create two versions of the same language track.
- `--sub-langs default` uses yt-dlp's default available track, usually English.
- `--sub-langs "en.*"`, `"de.*"` or `"ru.*"` selects matching language tracks. Comma-separated patterns are accepted.
- `--sub-langs orig` selects original automatic tracks ending in `-orig`, when exposed by the source.
- `--sub-langs all` requests available tracks except live chat; translated tracks can make this slow or large.
- `--timestamps` adds timestamps to TXT. SRT and VTT already contain timing.

Use **Check link** to see which caption languages a source exposes. Automatic captions may contain transcription errors.

</details>

## Files and download history

The default destination is `~/Downloads/YTLoad`. Use the web folder field, `-o`, or a local configuration file to change it.

| Folder | Contents |
| --- | --- |
| `Videos/` | Individual videos |
| `Audio/` | Individual audio downloads |
| `Transcripts/` | Standalone caption/transcript exports |
| `Metadata/` | Standalone video details |
| `Playlists/` | Playlist folders, ordered by item number |
| `Channels/` | Channel folders and their selected sections |
| `.ytload-state/` | Completed-media download records |

Transcripts requested alongside media stay next to that media. Media filenames include the video ID and requested quality/container or audio format; long titles are shortened without removing those identifiers. Sponsor removal uses a separate filename so it does not replace the original version.

A video URL containing a playlist parameter downloads **that video only**. To download the collection, use its playlist URL.

Completed media in playlists and channels is tracked separately for each settings profile. Caption requests disable archive checks so missing transcripts can be added later. Completed media is retained; subtitle and other sidecar files can refresh. Partial media files are kept for a retry, subject to the source's support for resuming.

## Browser sessions and connection settings

Chrome sign-in, MP4 output and the alternative YouTube player can be selected directly:

```bash
python3 ytload.py "VIDEO_URL" --browser chrome --container mp4 --youtube-client web_safari
```

This uses Chrome cookies on the computer running YTLoad, selects H.264 video with M4A audio, and merges to MP4. Individual YouTube videos are kept separate from playlist context automatically. No cookie file is exported.

To save a channel's Videos, Shorts and Streams with the same settings:

```bash
python3 ytload.py "CHANNEL_URL" --channel all --container mp4 --browser chrome
```

Use a channel URL such as `https://www.youtube.com/@CHANNEL`, leave Playlist items empty for all entries, and omit `--no-playlist` for collections. `--channel videos`, `shorts` or `streams` selects one section. Completed media is skipped on later runs with the same settings. Private, removed, ongoing live or account-restricted entries may not be downloadable; other entries continue unless `--fail-fast` is selected.

**More control** in the web interface contains Browser sign-in, YouTube player and an optional User-Agent field. The same settings apply to **Check link** and downloads. Connection settings are not remembered in browser storage.

| Player setting | Behavior |
| --- | --- |
| `auto` (default) | Start with yt-dlp defaults; retry a supported download failure once with `default,web_safari` |
| `default` | Use yt-dlp's current default clients without an additional player retry |
| `web_safari` | Use `default,web_safari` immediately without repeating the same attempt |

For a site that requires a matching browser User-Agent, copy the current complete string from `chrome://version` on the host and pass it explicitly:

```bash
python3 ytload.py "VIDEO_URL" --browser chrome --user-agent "YOUR_COMPLETE_BROWSER_USER_AGENT"
```

Leave User-Agent blank normally. A fixed or randomly rotated browser string is not a universal HTTP 403 fix. Cookies, source restrictions, JavaScript challenges, IP address and request limits can also matter; see the [upstream FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ). The Safari client is an optional compatibility path, not a guarantee of access.

A website cannot read a visitor's local Chrome session through this option. It selects a browser on the backend computer. Hosted deployments must not expose the operator's browser session to public users.

## Connection recovery

User-Agent, browser cookies and browser impersonation solve different problems. The wrapper supports explicit User-Agent and `default,web_safari` settings; neither guarantees access or removes a source's rate limit.

- **HTTP 429:** pause instead of repeatedly retrying. Open the source in your usual browser and complete any sign-in or verification it requests. In local YTLoad, select that browser under **More control → Browser sign-in**, then choose **Retry with current settings** on the failed item. Automatic retries do not switch on browser access.
- **Caption 429:** request one language. Even one track can be blocked; **Original** can help avoid translated caption requests when you do not need a specific language. YTLoad adds a five-second pause before each YouTube caption download and spaces extraction requests. These pauses reduce request bursts; they do not clear an existing block.
- **Chrome password prompt on macOS:** this may be the operating system unlocking Chrome's encrypted cookies in Keychain. YTLoad cannot skip that protection and never asks for or stores the password. After you select Chrome, the form keeps that choice for the current page. Connection choices are not saved in browser storage.
- **No impersonate target available:** the running yt-dlp installation lacks an optional browser impersonation dependency. This warning is separate from the final download error. Inspect support with `yt-dlp --list-impersonate-targets`; unavailable rows mean the dependency is absent.

For a pip-managed installation, update the same environment that supplies the `yt-dlp` command:

```bash
python3 -m pip install --upgrade "yt-dlp[default,curl-cffi]"
```

Do not install pip packages into Homebrew's internal yt-dlp environment. To use optional impersonation support, create a separate environment in the source folder, activate it, and launch YTLoad from that terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade "yt-dlp[default,curl-cffi]"
yt-dlp --list-impersonate-targets
python ytload.py --ui
```

On Windows, the equivalent PowerShell commands avoid activation-script policy changes:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "yt-dlp[default,curl-cffi]"
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
yt-dlp --list-impersonate-targets
.\.venv\Scripts\python.exe ytload.py --ui
```

For the portable application, replace `ytload.py` with `ytload.pyz`. FFmpeg and a supported JavaScript runtime such as Deno must still be installed separately. Stop the old workspace before relaunching. The environment must be active in the terminal that starts YTLoad so the child `yt-dlp` process uses it.

The error panel includes **Tool setup help** with copyable commands. A failed request does not automatically install software, overwrite a managed runtime, read another browser's cookies or discard selected captions. In public mode, browser sessions remain unavailable and only the operator can update server dependencies.

See the upstream [rate-limit guidance](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#http-error-429-too-many-requests-or-402-payment-required) and [impersonation dependencies](https://github.com/yt-dlp/yt-dlp#impersonation).

## Configuration and privacy

Copy [config.example.json](config.example.json) to `config.json`, adjust the defaults, and pass it explicitly:

```bash
python3 ytload.py --ui --config config.json
python3 ytload.py "VIDEO_URL" --config config.json
```

Without `--config`, the app looks in:

| Platform | Default config location |
| --- | --- |
| macOS / Linux | `~/.config/ytload/config.json` |
| Windows | `%APPDATA%\ytload\config.json` |

Invalid values and missing explicitly requested config files produce a readable error.

Browser sign-in is optional. Select a browser on the host where you are already signed in, or use:

```bash
python3 ytload.py "VIDEO_URL" --browser chrome
python3 ytload.py "VIDEO_URL" --browser none
```

`--browser none` disables a browser set in the configuration. The app does not export cookie files; yt-dlp uses the selected browser's cookies to access the requested source. Browser profile and keychain permissions still apply.

Browser preferences store appearance and selected format settings, not URLs, destination paths or sign-in selections. The in-memory queue contains URLs and output paths for the current server session. Downloaded metadata and captions may contain source information, so review files before sharing them.

Local configs, cookies, keys, downloads, logs, caches and build output are excluded by [.gitignore](.gitignore). Standard yt-dlp configuration files are ignored by the wrapper; use its own config or explicit CLI options for predictable behavior.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| **Python or yt-dlp not found** | Run the installer, reopen the terminal, and run `doctor`. On Windows, try `py -3`. |
| **FFmpeg not found** | Install the FFmpeg executable and make it available on `PATH`, then restart the app. Transcript and Details mode do not require it. |
| **Requested format is unavailable** | Try Best available with Auto. The source may not provide the selected codec. |
| **No captions found** | Check the available languages and try creator + automatic captions. The source must provide captions. |
| **HTTP 429** | Wait before retrying and request fewer caption languages. The source is limiting requests. |
| **YouTube access or challenge error** | Update yt-dlp and check its [JavaScript runtime setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS). For account-restricted media, explicitly select your signed-in browser. |
| **Download stays at 100%** | Wait for merging, conversion or transcript export to finish. Read the activity log if it fails. |
| **Phone cannot connect** | Use the current private link, keep both devices on the same network, and check host firewall or guest-network isolation. |
| **Folder picker is unavailable** | Paste a folder path into Save to. From a phone, this must be a path on the host computer. |
| **Workspace disconnected** | Keep its terminal open. If the server restarted, reopen its newly printed URL. |

Source availability, authentication, regional restrictions and rate limits remain outside the app's control. It does not remove DRM. Only download media you have permission to save.

Responsive layouts have been checked at widths from 320 to 1920 pixels. This does not certify every device: physical iPhone/Safari behavior and native Windows execution have not yet been verified. Browser download handling and available storage vary by device.

## Hosted mode

YTLoad includes a separate public backend for Linux and macOS. It uses anonymous browser sessions: each session has its own queue view, CSRF token, files and temporary storage. It accepts direct YouTube video, playlist and channel links. Other supported sites, local destinations, browser cookies, comments and SponsorBlock remain available in local mode.

| Limit | Public backend |
| --- | --- |
| Links per batch | 3 |
| Active jobs | 4 per session, 12 globally; one download runs at a time |
| Collection items | 50 per section; use `51:100` for the next batch |
| Media size and speed | 256 MiB per file, up to 2 MiB/s |
| Temporary storage | 1 GiB per session, 4 GiB globally |
| Job lifetime | 20 minutes, including queue time |
| Retention | 1 hour without browser activity; active transfers are retained |

Storage and time limits are checked every two seconds; they are application safeguards, not filesystem quotas. Run the backend as a dedicated unprivileged user, give it a separate storage volume with an operating-system quota, and apply CPU/memory limits through your process supervisor. The backend stops jobs when storage is full or less than 1 GiB of free disk space remains.

The queue is kept in memory. Restarting clears sessions and their previous temporary files. A storage lock prevents two backend instances from using the same directory. Do not share storage between replicas or expect jobs to survive restarts. Users should save completed files to their device before closing the session.

### Run behind an HTTPS reverse proxy

Install the media tools first. Use a new empty directory exclusively for temporary public downloads. Set a random proxy secret of at least 32 characters in the backend environment and the reverse proxy's private configuration; never include it in public HTML or browser requests.

```bash
export YTLOAD_PROXY_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
python3 ytload.py --serve \
  --public-origin https://downloads.example.com \
  --base-path /ytload \
  --storage ./public-storage \
  --port 8765
```

The HTTPS proxy must forward `/ytload/` and its subpaths to `http://127.0.0.1:8765`, preserving the path and streaming responses. Set these headers at the proxy, replacing any client-supplied values:

| Header | Value |
| --- | --- |
| `X-YTLoad-Proxy-Key` | The private shared secret |
| `X-Forwarded-Host` | The exact public authority, e.g. `downloads.example.com` |
| `X-Forwarded-Proto` | `https` |
| `X-YTLoad-Client-IP` | The actual visitor IP supplied by the trusted proxy |

Keep the backend bound to loopback or a private container network. Configure HTTPS, request-rate and connection limits at the proxy. Restrict backend egress to public destinations, including redirects and DNS changes. Do not mount browser profiles, home directories or credentials into its runtime. The public API has no cookie-upload or browser-sign-in endpoint.

Public file downloads require the owning session and support byte ranges for retrying interrupted transfers. Cookies use `Secure`, `HttpOnly` and `SameSite=Strict`; opening the backend over plain HTTP is not a supported public deployment.

### Dependency updates

Automatic package replacement is not enabled. Keep each deployed runtime on a tested yt-dlp version. The release was verified with yt-dlp **2026.08.19**, FFmpeg **8.1** and Deno **2.9.6**.

For managed hosting, check upstream releases daily, build a separate candidate runtime with pinned versions, run the tests and a permitted small video/audio/caption download, then drain the active queue before replacing the runtime. Retain the previous runtime for rollback. Because this release has an in-memory queue, replacing a running instance interrupts its sessions; parallel replicas need their own storage and routing affinity. Updating Python, FFmpeg or yt-dlp during a download is unsupported.

Source restrictions, IP reputation, JavaScript challenges and request limits still apply after an update. User-Agent and Safari compatibility options do not guarantee access to every source.

## Development

The application has no frontend package installation or build step. Node.js is only needed to run the JavaScript tests.

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.mjs
```

Tests cover request validation, command construction, caption conversion, process cancellation, queues, local access protection, public session isolation, resource limits and translations. Media integration tests generate local fixtures and verify real output when **yt-dlp**, **FFmpeg** and **ffprobe** are installed; otherwise those integration tests are skipped. Socket-based tests need permission to bind local ports.

| Path | Responsibility |
| --- | --- |
| `ytload.py` | Application entrypoint |
| `ytloadlib/` | CLI, validation, download execution, transcript conversion and web backends |
| `ytloadlib/static/` | Web interface, styles and translations |
| `tests/` | Python and JavaScript checks |
| `build.py` | Reproducible portable archive builder |

### Portable build

```bash
python3 build.py
python3 dist/ytload.pyz --help
python3 dist/ytload.pyz --ui
```

The resulting `dist/ytload.pyz` contains the application and web assets. It can be moved outside the source folder and accepts the same CLI options. **Python and the external media tools are still required.** The generated archive is ignored by Git and can be attached separately to a release.

### Reporting issues

Include your operating system, Python/yt-dlp/FFmpeg versions from `doctor`, the selected mode and format, and the relevant error text. Remove private URLs, local usernames, connection keys and other credentials before sharing logs.

## License and credits

Released under the [MIT License](LICENSE).

Media extraction is provided by [yt-dlp](https://github.com/yt-dlp/yt-dlp), conversion and merging by [FFmpeg](https://ffmpeg.org/), and JavaScript challenge support by a compatible runtime such as [Deno](https://deno.com/). These tools are installed separately and retain their own licenses. Optional segment data comes from [SponsorBlock](https://sponsor.ajay.app/).
