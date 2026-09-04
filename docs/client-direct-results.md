# Client-direct measurements

## Confirmed observations

These observations establish the production architecture decision. They do not represent a statistically complete corpus and must not be converted into an overall success percentage.

### Cross-IP iPhone Safari

The yt-dlp resolver and iPhone Safari used different public IP addresses.

| Operation | Observed result |
| --- | --- |
| Resolver | Successful |
| Progressive native open | Successful |
| Progressive native playback | Successful |
| Complete native file save | Not confirmed |
| Progressive JavaScript Range reads | Fetch rejected, 0 bytes |
| Best video JavaScript read | Fetch rejected, 0 bytes |
| Best audio JavaScript read | Fetch rejected, 0 bytes |

The progressive signed source could be opened and played through native Safari navigation. The test did not establish that Safari saved a complete file. JavaScript could not read progressive or separate media bodies.

### Same-IP desktop Chrome on macOS

Chrome and the resolver used the same public IP address.

| Operation | Observed result |
| --- | --- |
| Resolver | Successful |
| Best video Range request | Fetch rejected, 0 bytes |
| Best video plain GET | Fetch rejected, 0 bytes |
| Best video full Fetch | Fetch rejected, 0 bytes |
| Best audio Range request | Fetch rejected, 0 bytes |
| Best audio plain GET | Fetch rejected, 0 bytes |
| DevTools diagnosis | CORS confirmed: the response had no `Access-Control-Allow-Origin` header |

Same-IP placement did not make the selected googlevideo responses readable to JavaScript.

### Cloudflare Worker and R2 transport PoC

A fresh signed googlevideo URL returned HTTP 206 from the Mac and HTTP 403 from a Cloudflare Worker. Adding browser-like User-Agent, Accept, Accept-Language and Referer headers did not change the Worker result.

## Architecture decision

Production YTLoad does not use JavaScript media Fetch, OPFS or ffmpeg.wasm as its download and merge path. It also does not use a Cloudflare Worker media proxy or Worker-to-R2 transfer.

A safe HTTPS progressive stream containing both video and audio may be offered through explicit native browser navigation as a best-effort Direct Web option. Browser save behavior is not guaranteed.

The public hosted backend remains the compatibility path for yt-dlp download and FFmpeg merge/remux. YTLoad Local remains available for files beyond the public limit, large collections, browser sign-in and unrestricted local operation.

The development probe remains available for regression checks. Its sanitized historical embedded-browser control is stored in [client-direct-control.json](client-direct-control.json). Representative-corpus percentages remain unmeasured.
