import { classifications, probeFormat, safeMediaURL, safeReport, summarize } from './direct-core.mjs';

const $ = id => document.getElementById(id);
const runs = [];
let current;
let busy = false;
let controller;
const diagnosisLabels = {
  'cors-rejected': 'CORS rejected · confirmed in DevTools',
  'http-403': 'HTTP 403 · confirmed',
  'http-429': 'HTTP 429 · confirmed',
  'redirect-failure': 'Redirect failure · confirmed',
  'network-dns-tls-failure': 'Network / DNS / TLS failure · confirmed',
  'ip-session-header-failure': 'IP / session / header failure · controlled comparison',
  'unsupported-protocol': 'Unsupported protocol',
  unknown: 'Unknown',
};
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('key')) {
  $('key').value = fragment.get('key');
  history.replaceState(null, '', location.pathname);
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function button(text, action, disabled = false) {
  const node = element('button', text);
  node.type = 'button';
  node.addEventListener('click', action);
  node.disabled = busy || disabled;
  return node;
}

function condition() {
  const checked = $('ip-verified').checked;
  return {
    browser: $('browser').value,
    browser_version: /^[\d.]{1,24}$/.test($('browser-version').value) ? $('browser-version').value : 'unknown',
    os_device: $('os-device').value,
    network: $('network').value,
    network_relation: checked ? $('network-relation').value : 'unknown',
    ip_verification: checked ? 'operator-confirmed' : 'not-verified',
    corpus_type: $('corpus-type').value,
  };
}

function conditionsMatch() {
  const selected = condition();
  return Object.keys(selected).every(key => selected[key] === current?.[key]);
}

function rateText(rate) {
  return `${rate.successes}/${rate.total} · ${rate.percent === null ? 'not measured' : `${rate.percent}%`}`;
}

function updateSummary() {
  const report = safeReport(runs);
  const summary = summarize(report.runs);
  $('report-preview').textContent = JSON.stringify(report, null, 2);
  $('metrics').replaceChildren();
  for (const relation of ['same-ip', 'different-ip']) {
    const metric = summary.cohorts[relation];
    const row = document.createElement('tr');
    for (const value of [relation === 'same-ip' ? 'Same IP' : 'Cross IP', rateText(metric.resolver), rateText(metric.progressive_native), rateText(metric.progressive_fetch), rateText(metric.dash_pair_fetch)]) {
      row.append(element('td', value));
    }
    $('metrics').append(row);
  }
  const cross = summary.cohorts['different-ip'];
  const native = cross.native_observations;
  const attempts = `${runs.length} resolution ${runs.length === 1 ? 'attempt' : 'attempts'}`;
  $('summary').textContent = `${attempts} · ${cross.operations} eligible cross-IP operations · ${cross.unique_cases} unique cross-IP cases · ${cross.finalized_cases} finalized. Native cross-IP observations: ${native.opened} opened, ${native.playback} playback, ${native.downloaded} downloaded, ${native.failed} failed, ${native.unknown} unknown, ${native.pending} pending.`;
}

function setBusy(value) {
  busy = value;
  $('resolve').disabled = value;
  $('clear').disabled = value;
  $('cancel').disabled = !value;
  for (const node of document.querySelectorAll('#results button, select, input')) node.disabled = value;
}

function checkbox(text, checked, onChange, disabled = false) {
  const label = element('label', undefined, 'check');
  const input = document.createElement('input');
  input.type = 'checkbox';
  input.checked = checked || false;
  input.disabled = busy || disabled;
  input.addEventListener('change', () => {
    onChange(input.checked);
    updateSummary();
  });
  label.append(input, element('span', text));
  return label;
}

function selectedDashVideo(formats) {
  return formats.find(format => format.role === 'dash-video' && format.available)
    || formats.find(format => format.role === 'best-video' && format.available && ['http', 'https'].includes(format.protocol));
}

function selectedDashAudio(formats) {
  return formats.find(format => format.role === 'dash-audio' && format.available)
    || formats.find(format => format.role === 'best-audio' && format.available && ['http', 'https'].includes(format.protocol));
}

function hasTest(format, mode) {
  return Boolean(format?.tests?.some(test => test.request_mode === mode));
}

function finalizeCase() {
  if (!conditionsMatch()) {
    $('status').textContent = 'Test conditions changed. Resolve this case again before finalizing it.';
    return;
  }
  const missing = [];
  const progressive = current.formats.find(format => format.role === 'progressive' && format.available);
  if (progressive) {
    if (!hasTest(progressive, 'range-start')) missing.push('progressive start Range');
    if (!hasTest(progressive, 'range-offset')) missing.push('progressive offset Range');
    const nativeAttempts = progressive.native_attempts || [];
    if (nativeAttempts.some(attempt => attempt.final_status === 'pending')) missing.push('resolve the pending progressive native observation');
    else if (!nativeAttempts.some(attempt => ['observed', 'failed', 'unknown'].includes(attempt.final_status))) missing.push('finalized progressive native observation');
  }
  const video = selectedDashVideo(current.formats);
  const audio = selectedDashAudio(current.formats);
  if (video && !hasTest(video, 'range-start')) missing.push('DASH video Range');
  if (audio && !hasTest(audio, 'range-start')) missing.push('DASH audio Range');
  if (missing.length) {
    $('status').textContent = `Complete: ${missing.join(', ')}.`;
    return;
  }
  current.case_finalized = true;
  $('status').textContent = 'Case finalized. Eligible confirmed browser/network cohorts include it; control and unverified runs remain excluded.';
  renderFormats();
  updateSummary();
}

function renderFormats() {
  $('results').replaceChildren();
  if (!current?.resolution_ok) {
    $('results').append(element('p', current ? current.resolution_reason ? `Resolution failed: ${current.resolution_reason}. This finalized failure remains in the report.` : 'Resolution in progress.' : 'No formats resolved yet.'));
    return;
  }
  $('results').append(element('p', `${current.case_id} · ${current.network_relation} · ${current.browser} · ${current.os_device} · ${current.case_finalized ? 'finalized' : 'collecting evidence'}`, 'meta'));
  for (const format of current.formats) {
    const card = element('article', undefined, 'format');
    card.append(element('h3', format.role));
    if (!format.available) {
      card.append(element('p', 'Format unavailable in this resolution.'));
      $('results').append(card);
      continue;
    }
    const size = format.filesize || format.filesize_approx;
    card.append(element('p', `${format.format_id || 'unknown'} · ${format.protocol || 'unknown protocol'} · ${format.container || 'unknown container'} · ${format.vcodec || 'none'} / ${format.acodec || 'none'} · ${format.width || '—'} × ${format.height || '—'} · ${size ? `${(size / 1048576).toFixed(1)} MiB${format.filesize ? '' : ' estimated'}` : 'size unknown'}`, 'meta'));
    card.append(element('p', `IP-binding hint: ${format.ip_binding_hint ? 'present, enforcement unverified' : 'not present'} · PO-token hint: ${format.po_token_hint ? 'present' : 'not present'} · expiry: ${format.expires_at ? new Date(format.expires_at * 1000).toISOString() : 'not supplied'}`, 'meta'));
    card.append(element('p', `Header names: ${Object.keys(format.http_headers || {}).join(', ') || 'none'}. Browser-controlled or omitted: ${(format.unreplayable_headers || []).join(', ') || 'none'}. Private headers required: ${Boolean(format.requires_private_headers)}.`, 'meta'));
    const result = format.result;
    card.append(element('p', result ? `${result.request_mode} · ${result.transport_outcome} · ${result.reason} · ${result.bytes_read} bytes${result.manual_classification ? ` · manual: ${result.manual_classification}` : ''}` : 'No Fetch evidence yet.', 'result'));
    const actions = element('div', undefined, 'actions');
    actions.append(button('Range 0–65535', () => testFormat(format, 'range-start'), current.case_finalized));
    actions.append(button('Range 65536–131071', () => testFormat(format, 'range-offset'), current.case_finalized));
    actions.append(button('Plain GET · 64 KiB cap', () => testFormat(format, 'plain'), current.case_finalized));
    actions.append(button('Read full stream · 256 MiB cap', () => testFormat(format, 'full'), current.case_finalized));
    if (format.role === 'progressive' && safeMediaURL(format.url)) {
      actions.append(button('Open source / try saving', () => {
        if (!conditionsMatch()) {
          $('status').textContent = 'Test conditions changed. Resolve this case again before opening media.';
          return;
        }
        if ((format.native_attempts || []).some(attempt => attempt.final_status === 'pending')) {
          $('status').textContent = 'Finish the current native observation before opening another.';
          return;
        }
        if ((format.native_attempts || []).length >= 20) {
          $('status').textContent = 'Twenty native attempts recorded. Export before another trial.';
          return;
        }
        window.open(safeMediaURL(format.url), '_blank', 'noopener,noreferrer');
        (format.native_attempts ||= []).push({ tested_at: Math.floor(Date.now() / 1000), open_request: 'requested', opened_confirmed: false,
          playback_confirmed: false, download_confirmed: false, failed: false, final_status: 'pending' });
        $('status').textContent = 'Native navigation requested. Confirm only what you actually observed below.';
        renderFormats();
        updateSummary();
      }, current.case_finalized));
    }
    card.append(actions);
    const evidence = element('div', undefined, 'fields');
    const diagnosis = element('label', 'Manual diagnosis for latest Fetch attempt');
    const select = document.createElement('select');
    select.append(new Option('No confirmed diagnosis', ''));
    for (const value of classifications) select.append(new Option(diagnosisLabels[value], value));
    select.value = format.result?.manual_classification || '';
    select.disabled = busy || !format.result || current.case_finalized;
    select.addEventListener('change', () => {
      if (!format.result) return;
      format.result.manual_classification = select.value;
      format.result.manual_evidence = select.value ? 'operator-devtools-or-controlled-observation' : '';
      updateSummary();
    });
    diagnosis.append(select);
    evidence.append(diagnosis, element('p', 'A generic Fetch rejection stays unobservable. Choose CORS, redirect, network, 403 or 429 only when DevTools or a controlled comparison proves it.', 'muted'));
    card.append(evidence);
    const native = format.native_attempts?.at(-1);
    if (native) {
      card.append(element('p', `Native attempt ${format.native_attempts.length}: ${native.final_status}. Requested navigation is not confirmation.`, 'result'));
      if (native.final_status === 'pending' && !current.case_finalized) {
        const mark = (name, value) => {
          native[name] = value;
          native.failed = false;
          renderFormats();
        };
        card.append(checkbox('Source opened in the browser.', native.opened_confirmed, value => mark('opened_confirmed', value)));
        card.append(checkbox('Playback worked.', native.playback_confirmed, value => mark('playback_confirmed', value)));
        card.append(checkbox('A complete file was downloaded.', native.download_confirmed, value => mark('download_confirmed', value)));
        card.append(button('Finalize observation', () => {
          if (!native.opened_confirmed && !native.playback_confirmed && !native.download_confirmed) {
            $('status').textContent = 'Confirm an observation, record failure, or finalize as unknown.';
            return;
          }
          native.final_status = 'observed';
          renderFormats();
          updateSummary();
        }));
        card.append(button('Record failed', () => {
          native.download_confirmed = false;
          native.failed = true;
          native.final_status = 'failed';
          renderFormats();
          updateSummary();
        }));
        card.append(button('Finalize as unknown', () => {
          native.opened_confirmed = false;
          native.playback_confirmed = false;
          native.download_confirmed = false;
          native.failed = false;
          native.final_status = 'unknown';
          renderFormats();
          updateSummary();
        }));
      }
    }
    if ((format.tests || []).length) {
      const list = element('ul', undefined, 'history');
      for (const test of format.tests.slice(-8)) {
        list.append(element('li', `${test.request_mode}: ${test.transport_outcome} · ${test.bytes_read} bytes${test.manual_classification ? ` · ${test.manual_classification}` : ''}`));
      }
      card.append(list);
    }
    $('results').append(card);
  }
  $('results').append(button(current.case_finalized ? 'Case finalized' : 'Finalize case for metrics', finalizeCase, current.case_finalized));
}

async function testFormat(format, mode) {
  if ((format.tests || []).length >= 30) {
    $('status').textContent = 'Thirty Fetch checks recorded for this format. Export before another trial.';
    return;
  }
  if (current.case_finalized || !conditionsMatch()) {
    $('status').textContent = current.case_finalized ? 'This case is finalized. Resolve a new trial for more tests.' : 'Test conditions changed. Resolve this case again.';
    return;
  }
  const full = mode === 'full';
  const rangeStart = mode === 'range-offset' ? 65536 : 0;
  controller = new AbortController();
  setBusy(true);
  $('status').textContent = full ? 'Reading and discarding the stream on this device.' : `Testing ${mode} directly from the source.`;
  try {
    const result = await probeFormat(format, {
      full,
      limit: full ? 268435456 : 65536,
      rangeStart,
      timeout: full ? 120000 : 20000,
      requestMode: mode,
      signal: controller.signal,
      onProgress: bytes => {
        $('status').textContent = `Read ${(bytes / 1048576).toFixed(2)} MiB directly from the source.`;
      },
    });
    format.result = { ...result, tested_at: Math.floor(Date.now() / 1000) };
    (format.tests ||= []).push(format.result);
    $('status').textContent = `${result.transport_outcome}: ${result.reason}. Unobservable failures require separate evidence before classification.`;
  } finally {
    setBusy(false);
    renderFormats();
    updateSummary();
  }
}

$('resolve-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  if (!$('key').value) {
    $('status').textContent = 'Enter the private connection key printed by the development server.';
    return;
  }
  if (runs.length >= 200) {
    $('status').textContent = 'Export and clear this report before adding more cases.';
    return;
  }
  current = { ...condition(), trial: runs.length + 1, case_id: `case-${String(Number($('case-number').value)).padStart(3, '0')}`,
    resolved_at: Math.floor(Date.now() / 1000), resolution_ok: false, case_finalized: false, formats: [], youtube_client: $('youtube-client').value };
  runs.push(current);
  controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 55000);
  setBusy(true);
  $('status').textContent = 'Resolving metadata. No media is downloaded by this server.';
  renderFormats();
  try {
    const response = await fetch('/api/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${$('key').value}` },
      body: JSON.stringify({ url: $('url').value, youtube_client: current.youtube_client, user_agent: navigator.userAgent }),
      credentials: 'omit',
      cache: 'no-store',
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      current.resolution_reason = typeof data.error === 'string' && /^[a-z0-9_-]{1,80}$/.test(data.error) ? data.error : 'resolution-failed';
      current.case_finalized = true;
      $('status').textContent = `Resolution failed: ${current.resolution_reason}. The input is preserved and this attempt remains in resolver metrics.`;
    } else {
      Object.assign(current, data, { resolution_ok: true, case_finalized: false });
      $('status').textContent = 'Metadata ready. Record native, progressive Fetch and DASH evidence before finalizing this case.';
    }
  } catch {
    current.resolution_reason = controller.signal.aborted ? 'cancelled-or-timeout' : 'resolver-unreachable';
    current.case_finalized = true;
    $('status').textContent = 'The resolver request did not complete. The input is preserved and the attempt remains in resolver metrics.';
  } finally {
    clearTimeout(timer);
    setBusy(false);
    renderFormats();
    updateSummary();
  }
});

$('cancel').addEventListener('click', () => controller?.abort());
$('clear').addEventListener('click', () => {
  runs.length = 0;
  current = null;
  renderFormats();
  updateSummary();
  $('status').textContent = 'Results cleared from this page. The source input is preserved.';
});
$('export').addEventListener('click', () => {
  const report = safeReport(runs);
  report.summary = summarize(report.runs);
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
  const anchor = element('a');
  anchor.href = url;
  anchor.download = 'ytload-direct-report.json';
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

renderFormats();
updateSummary();
