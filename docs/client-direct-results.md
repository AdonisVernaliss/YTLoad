# Client-direct measurements

## Current conclusion

**Cross-IP viability is not measured yet.** There are no finalized trials from a supported browser with independently confirmed different resolver and browser egress. The correct result is `not measured`, not 0%.

The development control below predates the finalized schema and is retained only as diagnostic history. It does not enter the four rates.

## Preliminary embedded-browser control

Date: 2026-09-03. Resolver: macOS, yt-dlp 2026.08.19, default player client and no browser cookies. Browser: embedded Chromium on the resolver machine. Egress relation was not independently verified.

| Selected role | Format | Protocol / container | Codecs | Dimensions | Reported size | Old sample result |
| --- | --- | --- | --- | --- | --- | --- |
| Progressive | 18 | HTTPS / MP4 | H.264 + AAC | 360 × 640 | 5.34 MiB | unobservable Fetch failure |
| Best video | 616 | HLS / MP4 | VP9, no audio | 1080 × 1920 | Unknown | unsupported protocol |
| Direct video alternative | 399 | HTTPS / MP4 | AV1, no audio | 1080 × 1920 | 2.99 MiB | unobservable Fetch failure |
| Best audio | 251-1 | HTTPS / WebM | Opus | Audio only | 0.93 MiB | unobservable Fetch failure |

The old control used a larger first-position range and plain GET. It did not run the required 64 KiB start and offset pair. Six HTTPS requests exposed no response, status or bytes to JavaScript. This cannot be attributed to CORS, IP binding, 403, redirect or transport without additional diagnostics.

Native navigation was requested, but open, playback and download were not confirmed. No complete stream or merged output was produced. No media passed through the resolver.

The historical sanitized data is stored in [client-direct-control.json](client-direct-control.json). It contains no source URL, title, signed media URL, cookie, raw IP or raw error.

## Required result table

Fill this table only from exported schema 2 reports:

| Cohort | Resolver | Progressive native download | Progressive Fetch at both ranges | DASH video + audio Fetch |
| --- | --- | --- | --- | --- |
| Same IP | Not measured | Not measured | Not measured | Not measured |
| Cross IP | Not measured | Not measured | Not measured | Not measured |

Required platform evidence:

| Platform | Same-IP | Cross-IP Wi-Fi | Cross-IP mobile |
| --- | --- | --- | --- |
| Chrome on macOS | Pending | Pending | Optional |
| Safari on macOS | Pending | Pending | Optional |
| Firefox on desktop | Pending | Pending | Optional |
| Safari on iPhone | Pending | Optional | Pending |
| Chrome on Android | Pending if available | Optional | Pending if available |

Use [the exact test procedure](client-direct-probe.md#exact-mac-and-phone-cross-ip-procedure). The page calculates all four rates for same-IP and cross-IP separately after cases are finalized.

## Decision status

No evidence currently justifies Streams/OPFS or ffmpeg.wasm work. The decision remains open until the representative cross-IP corpus has enough finalized cases across actual target browsers.
