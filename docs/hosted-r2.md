# Guarded R2 hosted delivery

YTLoad can publish completed Hosted results through a private Cloudflare R2 bucket. This mode keeps control traffic on the existing reverse proxy or Tunnel and moves final media delivery to a dedicated Media Worker.

```text
Direct Web:       source -> browser
Hosted control:   browser -> HTTPS proxy -> home backend
Hosted processing: source -> yt-dlp and FFmpeg -> local temporary file
Hosted delivery:  home backend -> private R2 -> Media Worker -> browser
Local Web / CLI:  source -> local device
```

The backend never sends a completed R2-mode file through `/api/files`. It uses one yt-dlp/FFmpeg worker and one independent R2 uploader. Local Web and CLI do not import or require the R2 dependency.

## Safety model

The Media Worker owns a single Durable Object capacity ledger. Admission is atomic and uses this invariant:

```text
ready object bytes + complete active multipart reservations + prospective batch bytes <= 8,000,000,000
```

The full batch is reserved before the first multipart upload begins. A reservation continues to count while an upload is incomplete, while cleanup is pending, and while a ready object is downloadable. New publication stops when reconciliation is missing, stale, incomplete, inconsistent, or above the cap.

The 8 GB decimal cap leaves room for one maximum 5 GiB result while remaining below the 10 GB-month R2 Standard free storage allowance when this dedicated bucket is the only R2 use on the account. It is an application guard, not a Cloudflare billing limit. Free-tier usage is account-wide, request charges also matter, and credentials used outside this application can bypass its ledger. Use a dedicated bucket and bucket-scoped credentials, enable billing notifications, and review the Cloudflare dashboard.

R2 Standard currently includes 10 GB-month of storage, 1 million Class A operations, 10 million Class B operations, and free Internet egress each month. A 5 GiB upload with 256 MiB parts uses about 20 `UploadPart` calls plus multipart creation and completion. Reconciliation lists objects every 15 minutes. Deletes and multipart aborts are currently free operations. Verify current values in the [R2 pricing documentation](https://developers.cloudflare.com/r2/pricing/) before deployment.

The Workers Free plan currently has an account-wide 100,000-request daily allowance. The sample configuration includes a Worker Rate Limiting binding that permits 30 media requests per object per minute and fails closed when the binding is unavailable. The counter is local to each Cloudflare location and eventually consistent, so it limits cheap Range amplification without serving as billing accounting. It does not cap R2 Class B operations or total account billing. The Worker returns R2 bodies as streams and does not place media in Worker memory or cache.

The SQLite Durable Object used for the small global ledger is available on Workers Free. Free-plan Durable Object limits fail instead of creating usage charges when exceeded. The ledger stores one compact JSON row and ordinary media requests bypass it. The 15-minute Cron only triggers one Durable Object request; R2 listing, reconciliation and cleanup run inside the Durable Object because Free-plan Cron invocations have a 10 ms CPU limit while Durable Object requests have a larger CPU allowance. Check the current [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/) and [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) before deployment.

## Lifecycle

```text
queued -> running -> waiting_delivery -> uploading -> ready -> expired
```

- `waiting_delivery` and `uploading` count against the existing four-job session and twelve-job global limits.
- The result clock does not run while the local result waits for R2 or uploads.
- The one-hour clock begins only when every object in the batch is confirmed and committed as `ready`.
- Each ticket is HMAC-signed, object-bound and expires with the result.
- `GET` and `HEAD` support one byte range, including suffix ranges. Valid ranges return `206`; invalid or multiple ranges return `416`.
- Worker responses set `Content-Length`, `Content-Range` when needed, `Accept-Ranges`, a safe attachment filename, `no-store`, `nosniff` and `no-referrer`.
- Ticket replay and sharing are possible until expiry. IP binding is intentionally avoided because it breaks mobile network changes and privacy relays.

Normal expiry and stale-upload cleanup run in the Worker every 15 minutes. The backend also aborts listed multipart uploads and reconciles the ledger on startup. A one-day R2 object and incomplete-multipart lifecycle is the emergency layer; Cloudflare lifecycle deletion is not minute-precise and can occur after the logical ticket has expired.

If the processing host turns off after publication, the signed link remains usable until expiry because reads and scheduled cleanup are Cloudflare-side. If it turns off during processing, current startup cleanup removes the local temporary job. If it turns off during upload, the full reservation stays charged to the ledger; Worker cleanup aborts registered multipart work only when absence can be confirmed. The emergency lifecycle covers the narrow interval between multipart creation and registration.

An ambiguous multipart creation or failed abort keeps the full reservation fail-closed. Time alone never releases it. On the next processing-host startup, YTLoad completes the S3 multipart listing, aborts every listed upload, and only then tells the Worker that remaining incomplete keys are safe to release. If that authoritative pass cannot complete, the reservation remains counted even after the R2 lifecycle has removed the underlying parts. Restore the processing host and repeat startup recovery, or inspect the dedicated bucket and ledger before manual recovery.

## Cloudflare setup

Use a dedicated private R2 Standard bucket. Do not enable an `r2.dev` public URL, Infrequent Access, R2 SQL or Data Catalog.

1. Create the bucket and an R2 API token restricted to object read/write access for that bucket.
2. Install Wrangler 4.36.0 or later, then copy `cloudflare/media-worker/wrangler.example.jsonc` to `cloudflare/media-worker/wrangler.jsonc`.
3. Replace the example hostname, zone and bucket name. Keep the 8,000,000,000-byte cap unless you intentionally choose a lower value. Keep the rate-limit namespace ID unique within the Cloudflare account.
4. Create two unrelated random ASCII secrets of at least 32 characters:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

5. Store them in the Worker without placing their values in files or shell history:

```bash
npx wrangler secret put MEDIA_TICKET_SECRET --config cloudflare/media-worker/wrangler.jsonc
npx wrangler secret put MEDIA_CONTROL_SECRET --config cloudflare/media-worker/wrangler.jsonc
```

6. Add the emergency lifecycle rule. Replace the bucket name if necessary:

```bash
npx wrangler r2 bucket lifecycle add ytload-media ytload-emergency media/ --expire-days 1 --abort-multipart-days 1
```

7. Deploy the Media Worker:

```bash
npx wrangler deploy --config cloudflare/media-worker/wrangler.jsonc
```

The media and media-control routes must be more specific than the existing site or reverse-proxy route. Cloudflare chooses the most specific matching Workers route. Confirm in the dashboard that both routes are set to fail closed.

## Backend setup

Install the optional S3 client only in the Hosted processing environment:

```bash
python3 -m pip install -r requirements-hosted-r2.txt
```

Set all variables from `.env.example` through the service manager, container secret store or protected machine environment. YTLoad does not load `.env` automatically. `YTLOAD_MEDIA_CONTROL_SECRET` must equal the Worker `MEDIA_CONTROL_SECRET`. The endpoint is the account-specific R2 S3 endpoint and the control URL ends in `/ytload/media-control`.

Start the backend normally:

```bash
python3 ytload.py --serve \
  --public-origin https://downloads.example.com \
  --base-path /ytload \
  --storage ./public-storage \
  --port 8765
```

All six required R2 variables must be present together. With none of them present, Hosted mode retains legacy local file delivery. With a partial or invalid configuration, startup fails instead of silently reverting. Local Web and CLI ignore these variables because they do not create `PublicState`.

Use only one active processing host. To move from macOS to Windows, stop the first host, copy the application configuration and secrets through a secure channel, install the optional requirement and media tools on the second host, then start it with a new empty `--storage` directory. R2 ready objects remain independent of the home host; queued and local processing jobs do not migrate.

## Architecture decisions

The selected design uses a private R2 Standard bucket, a dedicated Media Worker for signed streaming delivery and one SQLite-backed Durable Object for conservative capacity accounting. The home backend uploads completed files directly to R2 and never returns their bytes through its Tunnel. Ordinary media reads validate a short-lived HMAC ticket at the edge and stream from R2 without contacting the Durable Object or home host.

The following alternatives do not meet the same boundary:

- Presigned R2 S3 GET URLs expose an account-specific storage endpoint, provide less control over response headers and ticket shape, and cannot enforce the project media route.
- A public bucket or `r2.dev` URL removes the private-object boundary and makes accidental discovery or long-lived sharing harder to contain.
- Relaying downloads through a Tunnel or a direct home-host media name consumes home upload bandwidth and makes availability depend on the processing host. A DNS-only home endpoint also requires a reviewed public IP, CGNAT, firewall, TLS and abuse posture.
- Cloudflare Stream is a video delivery and transformation product rather than a generic temporary archive for original video, audio, subtitles and collection outputs.

The Worker and Durable Object remain a hosted-only component. Local Web and CLI workflows continue to use local files and do not require cloud credentials or cloud libraries.

## Production checks

Run these in order:

1. Start with an allowed small media file.
2. Confirm the job shows `waiting_delivery`, `uploading` and `ready`.
3. Confirm the final link is `/ytload/media/<ticket>` and `/api/files/...` cannot return it.
4. Download the full file and compare its size.
5. Send `HEAD`, `Range: bytes=0-1023`, a suffix range and an out-of-bounds range.
6. Stop the processing host and repeat a valid Range download.
7. Confirm the ticket expires after about one hour.
8. Confirm Worker Cron removes the object and releases capacity.
9. Cancel one upload and confirm the multipart upload is absent.
10. Confirm the included per-object Worker rate limit returns `429` for a sustained tiny Range loop while preserving normal browser resume.
11. Optionally add a zone WAF rate-limit rule as a second layer after testing shared-network and mobile behavior.
12. Test a larger permitted file only after these checks pass.

The existing site bootstrap can remain separate. If the processing host is offline, the current control route may show its existing offline response while already-issued R2 tickets continue to work. Serving a complete static Hosted UI while the home backend is offline is a separate frontend deployment change.

## Operations

Keep the R2 credentials off user devices and browsers. Do not publish the media-control secret, Worker ticket secret, backend proxy key, account identifier or generated Worker configuration. Rotate the control secret on the Worker and processing host together. Rotate the ticket secret only when invalidating all current links is acceptable.

Repeated small Range requests consume Worker and R2 operations. The implementation rejects multipart ranges, allows normal single-range resume, and calls the `MEDIA_RATE_LIMITER` binding before every R2 read. The sample limit is 30 requests per object per minute. Test it with desktop and mobile resume before public launch, and tune it only with measured evidence. A zone WAF rule can add an IP-based outer limit, but must account for shared networks and privacy relays. Do not cache media responses or bind tickets to IP addresses.

Monitor R2 stored bytes, Class A/Class B operations, Worker requests and Durable Object errors. Stop the processing host or remove the R2 environment variables to disable new publishing. Existing ready objects remain governed by their tickets, Worker Cron and lifecycle rule.
