import test from 'node:test';
import assert from 'node:assert/strict';
import { probeFormat, safeReport, summarize } from '../ytloadlib/static/direct-core.mjs';

const format = { role: 'progressive', available: true, protocol: 'https', url: 'https://rr1.googlevideo.com/videoplayback?sig=secret', format_id: '18', container: 'mp4', vcodec: 'avc1', acodec: 'aac', http_headers: {}, download_options: {} };

test('a readable byte range is evidence of a sample, not a completed download', async () => {
  const result = await probeFormat(format, { fetcher: async (_, options) => {
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    return new Response(new Uint8Array(32), { status: 206, headers: { 'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-31/1000' } });
  }, limit: 32 });
  assert.equal(result.classification, 'direct-readable');
  assert.equal(result.complete, false);
  assert.equal(result.bytes_read, 32);
});

test('fetch exceptions cannot distinguish CORS, hidden 403, DNS and session failures', async () => {
  const result = await probeFormat(format, { fetcher: async () => { throw new TypeError('secret URL'); } });
  assert.equal(result.classification, 'unknown failure');
  assert.equal(result.reason, 'fetch-rejected');
  assert.ok(!JSON.stringify(result).includes('secret'));
});

test('only visible HTTP 403 is classified as 403', async () => {
  const result = await probeFormat(format, { fetcher: async () => new Response('', { status: 403 }) });
  assert.equal(result.classification, '403');
});

test('unsupported protocols and credential-dependent formats are not requested', async () => {
  const fetcher = async () => { throw Error('should not fetch'); };
  assert.equal((await probeFormat({ ...format, protocol: 'm3u8_native' }, { fetcher })).classification, 'unsupported protocol');
  assert.equal((await probeFormat({ ...format, requires_private_headers: true }, { fetcher })).classification, 'IP/session/header related failure');
});

test('a 200 HTML body and an empty media response are not successful samples', async () => {
  assert.equal((await probeFormat(format, { fetcher: async () => new Response('error', { headers: { 'Content-Type': 'text/html' } }) })).classification, 'unknown failure');
  assert.equal((await probeFormat(format, { fetcher: async () => new Response(new Uint8Array(), { headers: { 'Content-Type': 'video/mp4' } }) })).classification, 'unknown failure');
});

test('reports strip media URLs, headers and test input', () => {
  const result = safeReport([{ resolution_id: 'test-1', input: 'secret', formats: [{ ...format, result: { classification: 'direct-readable', bytes_read: 32, complete: false, raw: 'secret' } }], network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'desktop-chrome' }]);
  assert.ok(!JSON.stringify(result).includes('secret'));
  assert.ok(!JSON.stringify(result).includes('googlevideo'));
});

test('same-IP samples and unverified link opens cannot inflate cross-IP download success', () => {
  const result = summarize([{ network_relation: 'same-ip', resolution_ok: true, formats: [{ role: 'progressive', result: { classification: 'direct-readable', complete: true } }] },
    { network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'desktop-chrome', resolution_ok: true, formats: [{ role: 'progressive', result: { classification: 'direct-open-only', complete: false } }] }]);
  assert.equal(result.cross_ip_operations, 1);
  assert.equal(result.verified_downloads, 0);
  assert.equal(result.download_success_percent, null);
});

test('an unconfirmed native save stays pending, not a measured failure', () => {
  const result = summarize([{ network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'desktop-chrome', resolution_ok: true, formats: [{ role: 'progressive', download_attempted: true }] }]);
  assert.equal(result.download_success_percent, null);
});

test('the byte reader is cancelled when a server ignores the range', async () => {
  let cancelled = false;
  const body = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(64)); }, cancel() { cancelled = true; } });
  const result = await probeFormat(format, { limit: 64, fetcher: async () => new Response(body, { headers: { 'Content-Type': 'video/mp4' } }) });
  assert.equal(result.bytes_read, 64);
  assert.equal(cancelled, true);
  assert.equal(result.complete, false);
});

test('full stream success requires a complete body and full-read mode', async () => {
  const result = await probeFormat(format, { full: true, limit: 100, fetcher: async () => new Response(new Uint8Array(32), { headers: { 'Content-Type': 'video/mp4', 'Content-Length': '32' } }) });
  assert.equal(result.classification, 'direct-readable');
  assert.equal(result.complete, true);
});

test('report export retains failed attempts before a later readable attempt', () => {
  const report = safeReport([{ formats: [{ ...format, tests: [{ classification: 'unknown failure', reason: 'fetch-rejected', raw: 'secret' }, { classification: 'direct-readable' }] }] }]);
  assert.equal(report.runs[0].formats[0].tests.length, 2);
  assert.ok(!JSON.stringify(report).includes('secret'));
});

test('native retry history keeps the first failure in the denominator', () => {
  const result = summarize([{ network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'desktop-chrome', resolution_ok: true, formats: [{ role: 'progressive', native_attempts: [{ outcome: 'failed' }, { outcome: 'verified', saved: true, playback: true }] }] }]);
  assert.equal(result.download_attempts, 2);
  assert.equal(result.verified_downloads, 1);
  assert.equal(result.download_success_percent, 50);
});

test('summaries retain complete reads and failed checks after later samples', () => {
  const result = summarize([{ network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'desktop-safari',
    formats: [{ tests: [{ classification: 'unknown failure' }, { classification: 'direct-readable', complete: true }, { classification: 'direct-readable', complete: false }] }] }]);
  assert.equal(result.tested_formats, 3);
  assert.equal(result.readable_format_samples, 2);
  assert.equal(result.full_readable_formats, 1);
});

test('unverified egress and embedded controls are excluded from primary rates', () => {
  const result = summarize([{ network_relation: 'different-ip', browser: 'desktop-chrome' },
    { network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'embedded-chromium' }]);
  assert.equal(result.cross_ip_operations, 0);
  assert.equal(result.download_success_percent, null);
});

test('manual diagnosis is retained for each attempt without raw diagnostics', () => {
  const report = safeReport([{ formats: [{ tests: [{ classification: 'unknown failure', manual_classification: 'CORS blocked', manual_evidence: 'operator-devtools-or-native-observation', raw: 'secret' }, { classification: '403' }] }] }]);
  assert.equal(report.runs[0].formats[0].tests[0].manual_classification, 'CORS blocked');
  assert.ok(!JSON.stringify(report).includes('secret'));
});

test('range probes request exactly 64 KiB from the selected position', async () => {
  const result = await probeFormat(format, { rangeStart: 1048576, limit: 65536, fetcher: async (_, options) => {
    assert.equal(options.headers.Range, 'bytes=1048576-1114111');
    return new Response(new Uint8Array(65536), { status: 206, headers: { 'Content-Type': 'video/mp4', 'Content-Range': 'bytes 1048576-1114111/2000000', 'Content-Length': '65536' } });
  } });
  assert.equal(result.transport_outcome, 'readable-206');
  assert.equal(result.range_start, 1048576);
  assert.equal(result.range_end, 1114111);
});

test('visible 200, 403 and 429 outcomes remain distinct', async () => {
  const readable = await probeFormat(format, { limit: 4, fetcher: async () => new Response(new Uint8Array(4), { status: 200, headers: { 'Content-Type': 'video/mp4', 'Content-Length': '4' } }) });
  const forbidden = await probeFormat(format, { fetcher: async () => new Response('', { status: 403 }) });
  const limited = await probeFormat(format, { fetcher: async () => new Response('', { status: 429 }) });
  assert.equal(readable.transport_outcome, 'readable-200');
  assert.equal(forbidden.transport_outcome, 'http-403');
  assert.equal(limited.transport_outcome, 'http-429');
});

test('a mismatched partial response is not readable evidence', async () => {
  const result = await probeFormat(format, { rangeStart: 65536, limit: 65536, fetcher: async () => new Response(new Uint8Array(16), {
    status: 206, headers: { 'Content-Type': 'video/mp4', 'Content-Range': 'bytes 0-15/1000000', 'Content-Length': '16' }
  }) });
  assert.equal(result.classification, 'unknown failure');
  assert.equal(result.reason, 'content-range-mismatch');
});

test('four practical rates are separated by network relation and finalized cases', () => {
  const readable = position => ({ classification: 'direct-readable', transport_outcome: 'readable-206', request_mode: position });
  const formats = [
    { role: 'progressive', available: true, tests: [readable('range-start'), readable('range-offset')], native_attempts: [{ final_status: 'observed', opened_confirmed: true, playback_confirmed: true, download_confirmed: true }] },
    { role: 'dash-video', available: true, protocol: 'https', tests: [readable('range-start')] },
    { role: 'best-audio', available: true, protocol: 'https', tests: [readable('range-start')] },
  ];
  const summary = summarize([
    { network_relation: 'same-ip', ip_verification: 'operator-confirmed', browser: 'desktop-chrome', resolution_ok: true, case_finalized: true, case_id: 'case-001', formats },
    { network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'iphone-safari', resolution_ok: false, case_finalized: true, case_id: 'case-002', formats: [] },
    { network_relation: 'different-ip', ip_verification: 'operator-confirmed', browser: 'iphone-safari', resolution_ok: true, case_finalized: false, case_id: 'case-003', formats },
  ]);
  assert.deepEqual(summary.cohorts['same-ip'].resolver, { successes: 1, total: 1, percent: 100 });
  assert.deepEqual(summary.cohorts['same-ip'].progressive_native, { successes: 1, total: 1, percent: 100 });
  assert.deepEqual(summary.cohorts['same-ip'].progressive_fetch, { successes: 1, total: 1, percent: 100 });
  assert.deepEqual(summary.cohorts['same-ip'].dash_pair_fetch, { successes: 1, total: 1, percent: 100 });
  assert.deepEqual(summary.cohorts['different-ip'].resolver, { successes: 1, total: 2, percent: 50 });
  assert.deepEqual(summary.cohorts['different-ip'].progressive_native, { successes: 0, total: 0, percent: null });
});

test('sanitized reports retain OS, range and native observations without diagnostics text', () => {
  const report = safeReport([{ os_device: 'iphone', case_finalized: true, formats: [{
    ...format,
    tests: [{ transport_outcome: 'unobservable-failure', request_mode: 'range-offset', range_start: 1048576, range_end: 1114111, manual_classification: 'cors-rejected', raw: 'private diagnostics' }],
    native_attempts: [{ open_request: 'window-returned', opened_confirmed: true, playback_confirmed: true, download_confirmed: false, failed: false, final_status: 'observed' }]
  }] }]);
  const encoded = JSON.stringify(report);
  assert.equal(report.runs[0].os_device, 'iphone');
  assert.equal(report.runs[0].formats[0].tests[0].range_start, 1048576);
  assert.equal(report.runs[0].formats[0].native_attempts[0].opened_confirmed, true);
  assert.ok(!encoded.includes('private diagnostics'));
});

test('a server that ignores both Range positions cannot pass progressive Fetch', async () => {
  const ignored = position => ({ classification: 'direct-readable', transport_outcome: 'readable-200', request_mode: position, range_honored: false });
  const summary = summarize([{ network_relation: 'same-ip', ip_verification: 'operator-confirmed', browser: 'desktop-chrome', resolution_ok: true, case_finalized: true,
    formats: [{ role: 'progressive', available: true, tests: [ignored('range-start'), ignored('range-offset')] }] }]);
  assert.deepEqual(summary.cohorts['same-ip'].progressive_fetch, { successes: 0, total: 1, percent: 0 });
});

test('pending and contradictory native attempts cannot count as downloads', () => {
  const summary = summarize([{ network_relation: 'same-ip', ip_verification: 'operator-confirmed', browser: 'desktop-chrome', resolution_ok: true, case_finalized: true,
    formats: [{ role: 'progressive', available: true, native_attempts: [
      { final_status: 'pending', download_confirmed: true },
      { final_status: 'failed', download_confirmed: true, failed: true },
      { final_status: 'unknown', download_confirmed: true },
    ] }] }]);
  assert.deepEqual(summary.cohorts['same-ip'].progressive_native, { successes: 0, total: 1, percent: 0 });
});
