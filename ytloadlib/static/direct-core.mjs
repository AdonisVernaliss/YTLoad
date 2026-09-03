export const classifications = ['cors-rejected', 'http-403', 'http-429', 'redirect-failure', 'network-dns-tls-failure', 'ip-session-header-failure', 'unsupported-protocol', 'unknown'];

export function safeMediaURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.hash && (!url.port || url.port === '443') && /^(?:[a-z0-9-]+\.)+googlevideo\.com$/.test(url.hostname) ? url.href : null;
  } catch { return null; }
}

export async function probeFormat(format, { fetcher = fetch, limit = 65536, rangeStart = 0, timeout = 20000, requestMode = 'range-start', full = false, signal, onProgress } = {}) {
  const started = performance.now();
  const isRange = !full && requestMode !== 'plain';
  const result = { classification: 'unknown failure', transport_outcome: 'unobservable-failure', reason: '', http_status: null, bytes_read: 0, complete: false,
    scope: full ? 'full-transfer' : 'sample', request_mode: full ? 'full' : requestMode, range_start: isRange ? rangeStart : null,
    range_end: isRange ? rangeStart + limit - 1 : null, range_honored: null, elapsed_ms: 0 };
  const finish = (classification, transportOutcome, reason) => ({ ...result, classification, transport_outcome: transportOutcome, reason, elapsed_ms: Math.round(performance.now() - started) });
  if (!format.available) return finish('unknown failure', 'format-unavailable', 'format-unavailable');
  if (format.requires_private_headers) return finish('IP/session/header related failure', 'private-headers-required', 'private-headers-required');
  if (!['http', 'https'].includes(format.protocol)) return finish('unsupported protocol', 'unsupported-protocol', 'manifest-or-segmented-stream');
  const url = safeMediaURL(format.url);
  if (!url) return finish('unknown failure', 'destination-rejected', 'destination-rejected');
  if (format.expires_at && format.expires_at * 1000 <= Date.now()) return finish('IP/session/header related failure', 'url-expired', 'url-expired');
  if (!Number.isInteger(limit) || limit < 1 || limit > 268435456 || !Number.isSafeInteger(rangeStart) || rangeStart < 0 || rangeStart + limit > Number.MAX_SAFE_INTEGER) throw new Error('Invalid probe range');
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (signal?.aborted) return finish('unknown failure', 'cancelled', 'cancelled');
  signal?.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, timeout);
  let reader;
  try {
    const headers = {};
    for (const key of ['accept', 'accept-language']) {
      const value = format.http_headers?.[key];
      if (typeof value === 'string' && value.length <= 1024 && !/[\x00-\x1f\x7f]/.test(value)) headers[key] = value;
    }
    if (isRange) headers.Range = `bytes=${rangeStart}-${rangeStart + limit - 1}`;
    const response = await fetcher(url, { mode: 'cors', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer', cache: 'no-store', headers, signal: controller.signal });
    result.http_status = response.status || null;
    if (response.status === 403) { await response.body?.cancel(); return finish('403', 'http-403', 'visible-http-403'); }
    if (response.status === 429) { await response.body?.cancel(); return finish('429', 'http-429', 'visible-http-429'); }
    if (!response.ok || !response.body || response.type === 'opaque') {
      await response.body?.cancel();
      return finish('unknown failure', response.status ? `http-${response.status}` : 'unreadable-response', response.status ? `visible-http-${response.status}` : 'unreadable-response');
    }
    const contentType = response.headers.get('Content-Type')?.split(';')[0]?.toLowerCase();
    if (contentType && !/^(?:video\/|audio\/|application\/octet-stream$)/.test(contentType)) {
      await response.body.cancel();
      return finish('unknown failure', 'non-media-response', 'non-media-response');
    }
    if (response.status === 206) {
      const match = /^bytes (\d+)-(\d+)\/(?:\d+|\*)$/i.exec(response.headers.get('Content-Range') || '');
      if (!match || Number(match[1]) !== rangeStart || Number(match[2]) > rangeStart + limit - 1 || Number(match[2]) < Number(match[1])) {
        await response.body.cancel();
        return finish('unknown failure', 'invalid-partial-response', 'content-range-mismatch');
      }
      result.range_honored = true;
    } else if (isRange) {
      result.range_honored = false;
    }
    const lengthText = response.headers.get('Content-Length');
    const length = lengthText !== null && /^\d+$/.test(lengthText) ? Number(lengthText) : null;
    if (full && length !== null && length > limit) {
      await response.body.cancel();
      return finish('unknown failure', 'size-limit', 'full-transfer-size-limit');
    }
    reader = response.body.getReader();
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        if (!result.bytes_read) return finish('unknown failure', 'empty-response', 'empty-response');
        if (length !== null && result.bytes_read !== length) return finish('unknown failure', 'length-mismatch', 'length-mismatch');
        result.complete = full && response.status === 200;
        return finish('direct-readable', response.status === 206 ? 'readable-206' : 'readable-200', result.complete ? 'full-body-read' : 'sample-read');
      }
      result.bytes_read += value.byteLength;
      onProgress?.(result.bytes_read);
      if (result.bytes_read >= limit) {
        await reader.cancel();
        result.complete = full && response.status === 200 && length !== null && result.bytes_read === length && length <= limit;
        if (full && !result.complete) return finish('unknown failure', 'size-limit', 'full-transfer-size-limit');
        return finish('direct-readable', response.status === 206 ? 'readable-206' : 'readable-200', result.complete ? 'full-body-read' : 'sample-read');
      }
    }
  } catch {
    return finish('unknown failure', signal?.aborted ? 'cancelled' : controller.signal.aborted ? 'timeout' : 'unobservable-failure',
      signal?.aborted ? 'cancelled' : controller.signal.aborted ? 'timeout' : 'fetch-rejected');
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancel);
    controller.abort();
    try { await reader?.cancel(); } catch {}
  }
}

function pick(source, names) {
  return Object.fromEntries(names.filter(name => source?.[name] !== undefined).map(name => [name, source[name]]));
}

const resultFields = ['classification', 'transport_outcome', 'reason', 'http_status', 'bytes_read', 'complete', 'scope', 'request_mode', 'range_start', 'range_end', 'range_honored', 'elapsed_ms', 'tested_at', 'manual_classification', 'manual_evidence'];
const nativeFields = ['tested_at', 'open_request', 'opened_confirmed', 'playback_confirmed', 'download_confirmed', 'failed', 'final_status', 'outcome', 'saved', 'playback'];

export function safeReport(runs) {
  return { schema_version: 2, experiment: 'client-direct', runs: runs.map(run => ({
    ...pick(run, ['resolution_id', 'case_id', 'trial', 'resolved_at', 'resolution_ok', 'resolution_reason', 'resolution_ms', 'youtube_client', 'browser', 'browser_version', 'os_device', 'network', 'network_relation', 'ip_verification', 'corpus_type', 'case_finalized']),
    formats: (run.formats || []).map(format => ({
      ...pick(format, ['role', 'available', 'format_id', 'protocol', 'container', 'vcodec', 'acodec', 'height', 'width', 'fps', 'filesize', 'filesize_approx', 'requires_private_headers', 'ip_binding_hint', 'po_token_hint']),
      result: pick(format.result, resultFields),
      tests: (format.tests || []).map(result => pick(result, resultFields)),
      native_attempts: (format.native_attempts || []).map(attempt => pick(attempt, nativeFields)),
    })),
  })) };
}

const supportedBrowsers = ['desktop-chrome', 'desktop-safari', 'desktop-firefox', 'android-chrome', 'iphone-safari'];

function rate(successes, total) {
  return { successes, total, percent: total ? Math.round(1000 * successes / total) / 10 : null };
}

function testSucceeded(format, mode) {
  return Boolean(format?.tests?.some(test => test.request_mode === mode && test.classification === 'direct-readable' && test.transport_outcome === 'readable-206' && test.range_honored !== false));
}

function directVideo(formats) {
  return formats.find(format => format.role === 'dash-video' && format.available)
    || formats.find(format => format.role === 'https-video' && format.available)
    || formats.find(format => format.role === 'best-video' && format.available && ['http', 'https'].includes(format.protocol));
}

function directAudio(formats) {
  return formats.find(format => format.role === 'dash-audio' && format.available)
    || formats.find(format => format.role === 'best-audio' && format.available && ['http', 'https'].includes(format.protocol));
}

function nativeDownloadSucceeded(format) {
  return Boolean(format?.native_attempts?.some(attempt => (attempt.final_status === 'observed' && attempt.download_confirmed === true) || (attempt.outcome === 'verified' && attempt.saved === true && attempt.playback === true)));
}

function cohort(runs, relation) {
  const eligible = runs.filter(run => run.network_relation === relation && run.ip_verification === 'operator-confirmed' && supportedBrowsers.includes(run.browser));
  const finalized = eligible.filter(run => run.resolution_ok && run.case_finalized);
  const progressiveNative = finalized.filter(run => nativeDownloadSucceeded(run.formats?.find(format => format.role === 'progressive'))).length;
  const progressiveFetch = finalized.filter(run => {
    const format = run.formats?.find(item => item.role === 'progressive' && item.available);
    return testSucceeded(format, 'range-start') && testSucceeded(format, 'range-offset');
  }).length;
  const dashPair = finalized.filter(run => {
    const formats = run.formats || [];
    return testSucceeded(directVideo(formats), 'range-start') && testSucceeded(directAudio(formats), 'range-start');
  }).length;
  const nativeAttempts = eligible.flatMap(run => (run.formats || []).filter(format => format.role === 'progressive').flatMap(format => format.native_attempts || []));
  return {
    operations: eligible.length,
    unique_cases: new Set(eligible.map(run => run.case_id).filter(Boolean)).size,
    finalized_cases: finalized.length,
    resolver: rate(eligible.filter(run => run.resolution_ok).length, eligible.length),
    progressive_native: rate(progressiveNative, finalized.length),
    progressive_fetch: rate(progressiveFetch, finalized.length),
    dash_pair_fetch: rate(dashPair, finalized.length),
    native_observations: {
      opened: nativeAttempts.filter(attempt => attempt.opened_confirmed).length,
      playback: nativeAttempts.filter(attempt => attempt.playback_confirmed || attempt.playback).length,
      downloaded: nativeAttempts.filter(attempt => (attempt.final_status === 'observed' && attempt.download_confirmed) || (attempt.outcome === 'verified' && attempt.saved && attempt.playback)).length,
      failed: nativeAttempts.filter(attempt => attempt.failed || attempt.final_status === 'failed' || attempt.outcome === 'failed').length,
      unknown: nativeAttempts.filter(attempt => attempt.final_status === 'unknown').length,
      pending: nativeAttempts.filter(attempt => ((!attempt.final_status && attempt.outcome !== 'failed' && attempt.outcome !== 'verified') || attempt.final_status === 'pending')).length,
    },
  };
}

export function summarize(runs) {
  const cohorts = { 'same-ip': cohort(runs, 'same-ip'), 'different-ip': cohort(runs, 'different-ip') };
  const cross = runs.filter(run => run.network_relation === 'different-ip' && run.ip_verification === 'operator-confirmed' && supportedBrowsers.includes(run.browser));
  const native = cross.flatMap(run => (run.formats || []).filter(format => format.role === 'progressive').flatMap(format => format.native_attempts || []));
  const attempted = native.filter(attempt => attempt.outcome === 'failed' || (attempt.outcome === 'verified' && attempt.saved && attempt.playback)
    || ['observed', 'failed', 'unknown'].includes(attempt.final_status));
  const downloads = attempted.filter(attempt => attempt.download_confirmed === true || (attempt.outcome === 'verified' && attempt.saved && attempt.playback));
  const checks = cross.flatMap(run => run.formats || []).flatMap(format => format.tests || []);
  return { cohorts, cross_ip_operations: cross.length, cross_ip_unique_cases: cohorts['different-ip'].unique_cases,
    resolved_operations: cross.filter(run => run.resolution_ok).length,
    readable_format_samples: checks.filter(result => result.classification === 'direct-readable').length,
    tested_formats: checks.length, full_readable_formats: checks.filter(result => result.complete).length,
    download_attempts: attempted.length, pending_download_attempts: native.length - attempted.length, verified_downloads: downloads.length,
    download_success_percent: attempted.length ? Math.round(1000 * downloads.length / attempted.length) / 10 : null,
    resolution_success_percent: cross.length ? Math.round(1000 * cross.filter(run => run.resolution_ok).length / cross.length) / 10 : null };
}
