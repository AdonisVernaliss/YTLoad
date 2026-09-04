# Client-direct viability experiment

This development-only experiment measures whether a browser can download or read YouTube media directly after yt-dlp resolves formats on another public IP.

The normal CLI, local workspace, hosted queues and server-side downloader remain unchanged. The probe has no media proxy, file-serving endpoint, ffmpeg.wasm, OPFS pipeline or automatic fallback.

## Traffic boundary

```text
Browser → private HTTPS test URL → resolver metadata API → yt-dlp
YouTube / googlevideo.com → browser
```

Only the page, resolver request and allowlisted metadata may traverse a temporary Cloudflare Tunnel. Media URLs are used by native browser navigation or Fetch on the client. The probe cannot relay media because it exposes no download, file or proxy endpoint.

Cloudflare treats Quick Tunnels as development facilities and assigns a temporary random hostname. Do not use this procedure for production. Cloudflare also places conditions on serving video and large-file traffic through its network. See [Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) and [video delivery guidance](https://developers.cloudflare.com/fundamentals/reference/policies-compliances/delivering-videos-with-cloudflare/).

## Start locally

Requirements: Python 3.10+, current yt-dlp and its YouTube JavaScript runtime. FFmpeg is not used by the direct probe.

```bash
cd /path/to/YTLoad
python3 ytload.py doctor
yt-dlp --version
python3 -m ytloadlib.direct_probe
```

Open the private connection URL printed by the last command. Its fragment contains a generated key. The page removes the fragment after reading it. Refreshing requires the original connection URL again. Ctrl+C cancels the resolver and stops the server.

The resolver accepts one canonical YouTube video or Short. It runs yt-dlp with a fixed metadata-only argument list, a 45-second process-group deadline and bounded output. It returns only selected format data. Cookies, raw yt-dlp JSON, titles, signed URLs, raw errors and complete IP addresses are absent from exported reports.

## Exact Mac and phone cross-IP procedure

Use a source you are allowed to download. First complete one case before attempting the full corpus.

### Terminal 1 on the Mac

Run the official temporary Quick Tunnel command:

```bash
cloudflared tunnel --url http://127.0.0.1:8766 --no-autoupdate --loglevel info
```

If cloudflared reports that Quick Tunnels are disabled by a default `~/.cloudflared/config.yml`, use a separate temporary configuration without changing an existing production Tunnel configuration:

```bash
printf '{}\n' > /tmp/ytload-quick-tunnel.yml
cloudflared tunnel --config /tmp/ytload-quick-tunnel.yml --url http://127.0.0.1:8766 --no-autoupdate --loglevel info
```

Wait for a URL in this form:

```text
https://random-words.trycloudflare.com
```

Keep this terminal running. Do not enable debug logging because it can record request headers. The Tunnel may show origin connection errors until Terminal 2 starts the probe.

### Terminal 2 on the Mac

Replace the example origin with the exact temporary URL from Terminal 1:

```bash
cd /path/to/YTLoad
python3 -m ytloadlib.direct_probe --port 8766 --origin https://random-words.trycloudflare.com
```

The terminal prints a complete private connection URL ending in `/#key=...`. Transfer that whole URL to the phone through a private channel. The fragment is not part of an HTTP request, but anyone who receives the complete link can use the resolver while it runs.

### Verify different egress

On the Mac:

```bash
curl -s https://www.cloudflare.com/cdn-cgi/trace | sed -n 's/^ip=//p'
```

On the phone:

1. Turn Wi-Fi off.
2. Turn off any VPN, iCloud Private Relay routing that makes the comparison ambiguous, and Mac-based tethering.
3. Open `https://www.cloudflare.com/cdn-cgi/trace` and read the `ip=` line.
4. Confirm that the phone and Mac values differ. Do not copy either value into the report.

If one device uses IPv4 and the other IPv6, they are still different egress paths. Leave the UI relation unverified if routing remains ambiguous.

### Run one iPhone test

1. Open the complete private connection URL in iPhone Safari over 4G/5G.
2. Select **Safari · iPhone**, enter the numeric browser version, select **iPhone**, **Mobile data**, and **Different public IPs · primary**.
3. Check the egress-confirmation box.
4. Enter the matching corpus case number, source type and YouTube URL.
5. Select **Default** and press **Resolve formats**.
6. On the progressive card, press **Open source / try saving**.
7. Return to the probe tab. Mark **Source opened**, **Playback worked**, and **A complete file was downloaded** only when each happened. Use **Record failed** for a definite failure or **Finalize as unknown** if the result cannot be established. Press **Finalize observation** after confirmed observations.
8. Run **Range 0–65535** and **Range 65536–131071** on the progressive card.
9. Find **dash-video**, or an HTTP(S) **best-video**, and run **Range 0–65535**. Run the same first Range on **best-audio** or **dash-audio**.
10. A result without an exposed response remains `unobservable-failure`. Add a manual category only with evidence.
11. Press **Finalize case for metrics**. The page lists any required missing evidence.
12. Press **Export sanitized report** and save the JSON in Files.
13. Repeat the same case with the **Default + Safari** player client as a separate trial only if the default client failed.
14. Repeat for the corpus, without treating retries as new sources.
15. Press Ctrl+C in Terminal 2, then Terminal 1.

### Repeat the browser matrix

Keep the same resolver and private connection URL for one comparison batch.

- **Chrome on the resolver Mac:** open the private HTTPS link on the Mac and select **Same public IP · control** after confirming the Mac browser has no VPN or relay that changes its egress. Save the native progressive file through Chrome and verify the completed file before marking a download.
- **Safari on the resolver Mac:** repeat the same source and case ID in Safari as a separate trial. Use Safari's download control and verify the complete file in Downloads.
- **Firefox on desktop:** run one same-IP trial on the resolver network and one cross-IP trial on another desktop connection. Confirm both egress values before selecting the relation.
- **Safari on iPhone:** run the same case once on the Mac's Wi-Fi for the same-IP control and once with Wi-Fi off for the cross-IP mobile trial. If media opens, use **Share → Save to Files**, wait for completion and verify playback from Files before marking a complete download.
- **Chrome on Android:** repeat the Wi-Fi and mobile-data trials. Use Chrome's download action when available, wait for completion and verify the file in Downloads before marking a complete download.
- **Desktop cross-IP:** use a second computer on a separately verified connection, such as a different ISP or phone hotspot. Home Wi-Fi shared with the resolver is a same-IP control unless the trace values prove otherwise.

Resolve each browser trial again because media URLs can expire or depend on the resolver session. Preserve the same corpus case ID, and never classify an opened player or partial file as a completed download.

The Quick Tunnel address stops working when cloudflared exits. This procedure creates no custom DNS record and changes no production frontend configuration.

## Browser diagnostics

Fetch uses normal browser security, `mode: cors`, omitted credentials, no referrer and redirect rejection. Never start a browser with web security disabled.

A successful Fetch response is recorded as `readable-200` or `readable-206`. Visible 403 and 429 responses are distinct. If Fetch rejects before exposing a response, the automatic outcome is `unobservable-failure`; JavaScript cannot safely label it CORS, redirect, DNS, TLS or a hidden HTTP error.

For iPhone Safari diagnostics:

1. Enable **Settings → Safari → Advanced → Web Inspector**.
2. Connect the iPhone to the Mac by USB.
3. In desktop Safari, enable the Develop menu and choose the probe page under the iPhone.
4. Inspect Console and Network while repeating one Range test.
5. Record only one of the predefined categories. Do not export a HAR or copy signed URLs.

For Android Chrome, enable USB debugging and use desktop Chrome at `chrome://inspect`. For desktop Chrome, Firefox and Safari, use their normal Network and Console tools. CDP or Web Inspector may observe a failure but must not modify browser security, request headers, cookies or source URLs.

Classification rules:

| Outcome | Required evidence |
| --- | --- |
| readable-200 | Fetch exposes a valid non-empty media response with status 200. |
| readable-206 | Fetch exposes a valid partial media response and matching Content-Range. |
| cors-rejected | DevTools explicitly reports a CORS policy rejection. |
| http-403 / http-429 | The HTTP status is exposed by Fetch or visible in Network tools. |
| redirect-failure | Network tools show the blocked redirect chain. |
| network-dns-tls-failure | Network tools identify DNS, TLS or transport failure. |
| ip-session-header-failure | A controlled same-source comparison establishes the dependency. |
| unsupported-protocol | The chosen format is a manifest or segmented protocol outside this reader. |
| unknown | Available evidence does not support a narrower result. |

A source URL can require the resolver IP, cookies or matching headers, and some protocols are not browser-readable. yt-dlp documents these limitations in its [extracted URL FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#i-extracted-a-video-url-but-it-does-not-play-on-another-machine--in-my-web-browser). Fetch failures without a response are constrained by the browser's [CORS and response model](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch).

## Measurement definitions

Reports show same-IP and cross-IP cohorts separately. Embedded Chromium and unverified egress are excluded.

- **Resolver success rate:** successful resolutions divided by all eligible resolution attempts.
- **Progressive native-direct success rate:** finalized resolved cases with a confirmed complete progressive download divided by all finalized resolved cases. Open or playback-only observations remain visible but do not count as downloads.
- **Progressive JS-readable success rate:** finalized resolved cases where both required progressive Range positions return validated 206 responses with matching `Content-Range`, divided by all finalized resolved cases. Status 200 can prove general readability, but it does not prove that an offset Range was honored.
- **DASH pair JS-readable success rate:** finalized resolved cases where the selected direct video and direct audio first ranges are both readable divided by all finalized resolved cases.

Missing formats and observed failures therefore reduce practical coverage. Partial cases do not enter the three media rates. Resolver failures always remain in resolver metrics. The report also retains opened, playback, downloaded, failed, unknown and pending native observations.

The browser's `download` attribute cannot force arbitrary cross-origin downloads, and native behavior varies by browser and response headers. See [MDN's download attribute notes](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/a#download).

## Corpus

Copy the committed worksheet and fill URLs only in the ignored local copy:

```bash
cp docs/client-direct-corpus.csv docs/client-direct-corpus.local.csv
```

The 24 rows cover Shorts, short regular videos, ordinary videos, music, long content, 1080p, 1440p, 4K and H.264/VP9/AV1 targets across twelve channel groups. Choose distinct permitted public sources. A target describes desired coverage; the resolver output records the actual available codec and quality.

Keep the local URL mapping and any private sources out of Git. Use the same case ID on every browser. Do not count one source or retry as multiple independent corpus items.

Minimum matrix:

| Browser | Same-IP control | Cross-IP Wi-Fi | Cross-IP mobile |
| --- | --- | --- | --- |
| Chrome on macOS | Required | Required | Optional tethered check |
| Safari on macOS | Required | Required | Optional tethered check |
| Firefox on desktop | Required | Required | Optional |
| Safari on iPhone | Required | Optional | Required |
| Chrome on Android | Required if available | Optional | Required if available |

Do not make statistical claims from the first case. Report the four rates with numerators, denominators, unique sources, retries, unresolved attempts and browser/network strata.

## Windows portability

Run the probe inside WSL2 or a Linux container on Windows. Process-group cleanup uses POSIX semantics. The normal Windows CLI remains unchanged.

1. Install WSL2 using Microsoft's [WSL guide](https://learn.microsoft.com/en-us/windows/wsl/install).
2. Clone or copy only the repository source into the WSL home directory.
3. Run `bash install-linux.sh`.
4. Start the same probe and cloudflared commands inside WSL2.
5. Keep the public test URL in `--origin`; no frontend, DNS or source change is required when switching test hosts.
6. Stop both processes on the Mac before starting them on Windows.

A future stable tunnel can use the same tunnel credentials on one active machine at a time. That production step is outside this experiment.

## Production decision

The completed cross-IP iPhone Safari and same-IP desktop Chrome trials established the production boundary:

- JavaScript could not read the tested progressive, best-video or best-audio googlevideo responses.
- Chrome DevTools explicitly confirmed missing CORS permission.
- Native iPhone Safari opened and played a progressive source, while a complete file save remained unconfirmed.
- A Cloudflare Worker received HTTP 403 for a fresh URL that returned HTTP 206 from the resolver Mac.

YTLoad therefore does not use Fetch, OPFS or ffmpeg.wasm for production media transfer and does not use a Worker or R2 media relay. Production may expose a sanitized progressive audio-and-video URL through explicit native navigation as a best-effort option. The hosted yt-dlp/FFmpeg backend remains the compatibility path, and YTLoad Local handles work beyond public limits.

Keep this probe for regression checks and additional browser observations. Do not infer a representative success percentage from the confirmed trials. See [the recorded results](client-direct-results.md).
