import { createTranslator, translateText } from './i18n.mjs';
import { parseLinks } from './links.mjs';
import { directLink, estimateState, selectedEstimate } from './hosted-ui.mjs';

const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="workspace-token"]').content;
const modeNames = { video: 'Video + audio', audio: 'Audio only', subs: 'Transcript only', metadata: 'Video details' };
const symbols = { video: '▷', audio: '♫', subs: 'Tt', metadata: 'ⓘ' };
const jobs = new Map();
const optionLabels = new Map([...document.querySelectorAll('select')].map((select) => [select.id, new Map([...select.options].map((option) => [option.value, option.textContent]))]));
let locale = document.documentElement.lang || 'en';
const translatePage = createTranslator(document, () => locale);
let ready = false;
let submitting = false;
let inspecting = false;
let preferencesTimer;
let toastTimer;
let currentEnvironment;
let hosted = false;
let lastInspection;
let lastInspectionKey;
const basePath = document.querySelector('meta[name="workspace-base"]').content;
let lastQueueAnnouncement = '';

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function urls() {
  return parseLinks($('urls').value);
}

function textFormats() {
  return [...document.querySelectorAll('input[name="transcript-format"]:checked')].map((input) => input.value);
}

function hasTranscript() {
  return selectedMode() === 'subs' || (selectedMode() !== 'metadata' && $('with-transcript').checked);
}

function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}

function showError(message) {
  $('form-error').textContent = message;
  $('form-error').hidden = !message;
  if (message) $('form-error').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function api(path, payload, timeout = 15000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(basePath + path, {
      method: payload === undefined ? 'GET' : 'POST',
      headers: { 'X-YTLoad-Token': token, ...(payload === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: controller.signal,
      cache: 'no-store',
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('This request took too long. Check Downloads before trying again.');
    if (error instanceof TypeError) throw new Error('The workspace is disconnected. Keep the terminal open and reload this page.');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function formatBytes(bytes) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  while (bytes >= 1024 && index < units.length - 1) { bytes /= 1024; index += 1; }
  return `${bytes.toFixed(index && bytes < 100 ? 1 : 0)} ${units[index]}`;
}

function formatTime(seconds) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${total % 60}s` : `${total}s`;
}

function inspectionKey() {
  return JSON.stringify([urls()[0] || '', $('browser').value || '', $('user-agent').value.trim(), $('youtube-client').value]);
}

function clearInspection() {
  lastInspection = null;
  lastInspectionKey = null;
  $('preview').replaceChildren();
  $('preview').hidden = true;
  $('preview').classList.remove('error');
}

function estimateSize(estimate) {
  if (!estimate || typeof estimate.bytes !== 'number') return 'Size unknown';
  return `${estimate.approximate ? '~' : ''}${formatBytes(estimate.bytes)}`;
}

function renderHostedInspection(data) {
  if (data.collection_size_notice) {
    const notice = document.createElement('p');
    notice.textContent = 'File sizes vary by item. Each public web result is limited to 5 GB.';
    $('preview').append(notice);
    return;
  }
  const policy = $('container').value === 'mp4' || $('quality').value === 'compatible' ? 'compatible' : 'source';
  const estimates = data.size_estimates?.[policy] || {};
  const heading = document.createElement('h3');
  heading.className = 'inspection-heading';
  heading.textContent = 'Available downloads';
  const list = document.createElement('div');
  list.className = 'estimate-list';
  for (const quality of ['best', '2160p', '1440p', '1080p', '720p', '480p', 'small', ...(policy === 'compatible' ? ['compatible'] : [])]) {
    const estimate = estimates[quality];
    if (!estimate) continue;
    const row = document.createElement('div');
    const state = estimateState(estimate);
    row.className = 'estimate-row';
    row.dataset.state = state;
    const name = document.createElement('strong');
    name.textContent = optionLabels.get('quality').get(quality) || quality;
    const size = document.createElement('span');
    size.textContent = estimateSize(estimate);
    const status = document.createElement('span');
    status.textContent = state === 'over' ? 'Public limit exceeded' : state === 'near' ? 'Close to public limit' : state === 'unknown' ? 'YTLoad will enforce the 5 GB limit while processing.' : 'Available';
    row.append(name, size, status);
    list.append(row);
  }
  $('preview').append(heading, list);
  const attributes = directLink(data.direct);
  if (attributes) {
    const direct = document.createElement('div');
    direct.className = 'direct-source';
    const title = document.createElement('strong');
    title.textContent = 'Direct source available';
    const detail = document.createElement('p');
    detail.textContent = [data.direct.height ? `${data.direct.height}p` : '', data.direct.container?.toUpperCase(), estimateSize(data.direct)].filter(Boolean).join(' · ');
    const link = document.createElement('a');
    link.className = 'secondary';
    Object.assign(link, attributes);
    link.textContent = 'Open direct source';
    const note = document.createElement('p');
    note.textContent = 'Opens directly from the source and does not use YTLoad server bandwidth. Browser download behavior may vary. The 5 GB public server limit does not apply to this direct link.';
    direct.append(title, detail, link, note);
    $('preview').append(direct);
  }
}

function renderInspection(data) {
  const title = document.createElement('strong');
  title.textContent = data.title;
  const details = document.createElement('p');
  details.textContent = [data.channel, formatTime(data.duration), data.is_collection ? (data.count ? `${data.count} items` : 'Playlist or channel') : data.heights.length ? `Available: ${data.heights.map((height) => height + 'p').join(', ')}` : 'Source quality'].filter(Boolean).join(' · ');
  const captions = document.createElement('p');
  captions.textContent = data.is_collection ? 'Caption availability varies by video. The link preview samples up to 5 items.' : data.languages.length ? `Caption tracks: ${data.languages.slice(0, 14).map((language) => language.code).join(', ')}${data.languages.length > 14 ? ` + ${data.languages.length - 14} more` : ''}` : 'No caption tracks were found for this video.';
  $('preview').replaceChildren(title, details, captions);
  if (hosted) renderHostedInspection(data);
  translatePage();
}

function updateSummary() {
  const mode = selectedMode();
  const includeText = hasTranscript();
  const count = urls().length;
  $('video-options').hidden = mode !== 'video';
  $('audio-options').hidden = mode !== 'audio';
  $('metadata-hint').hidden = mode !== 'metadata';
  $('transcript-area').hidden = mode === 'metadata';
  $('transcript-toggle-row').hidden = mode === 'subs';
  $('transcript-options').hidden = !includeText;
  $('transcript-only-hint').hidden = mode !== 'subs';
  $('embed-row').hidden = mode !== 'video';
  $('custom-language-row').hidden = $('sub-langs').value !== 'custom';
  $('custom-language').required = includeText && $('sub-langs').value === 'custom';
  $('timestamps').disabled = !textFormats().includes('txt');
  $('link-count').textContent = count ? `${count} ${count === 1 ? 'link' : 'links'} added` : hosted ? 'YouTube videos, playlists & channels' : 'YouTube & other supported sites';
  $('inspect-button').disabled = !ready || !count || inspecting;
  $('summary-links').textContent = count ? `${count} ${count === 1 ? 'link' : 'links'}` : 'Add your first link';
  $('summary-main').textContent = modeNames[mode];
  $('summary-symbol').textContent = symbols[mode];
  $('summary-tag').textContent = { video: 'VIDEO', audio: 'AUDIO', subs: 'TEXT', metadata: 'JSON' }[mode];
  $('summary-transcript').textContent = includeText ? textFormats().map((value) => value.toUpperCase()).join(' + ') || 'Choose a format' : 'Not included';
  const folder = $('output').value.trim().replace(/[\\/]+$/, '');
  $('summary-folder').textContent = folder.split(/[\\/]/).slice(-2).join(' / ') || 'Choose a folder';
  $('summary-folder').title = folder;
  const quality = optionLabels.get('quality').get($('quality').value);
  const container = $('container').value;
  const compatible = container === 'mp4' || $('quality').value === 'compatible';
  $('container-help').textContent = compatible ? 'H.264 video + AAC audio for players and editors.' : container === 'mkv' ? 'Keeps source codecs in a flexible MKV container.' : 'Keeps original codecs. Merged video uses MKV.';
  $('summary-detail').textContent = mode === 'video' ? `${quality} · ${compatible ? (container === 'mkv' ? 'MKV / H.264' : 'MP4 / H.264') : container === 'mkv' ? 'MKV' : 'original codecs'}`
    : mode === 'audio' ? optionLabels.get('audio-format').get($('audio-format').value)
      : mode === 'subs' ? `${textFormats().map((value) => value.toUpperCase()).join(' + ') || 'Choose a format'} · no media download` : 'JSON · no media download';
  const tips = {
    video: ['A little quality advice', '1080p is a great everyday choice. Choose Best available to keep the highest source quality.'],
    audio: ['Sound, without the picture', 'Choose Original to keep the source audio. MP3 works in most players; M4A is a compact everyday choice.'],
    subs: ['Words you can work with', 'TXT is for reading. SRT and VTT keep timing for editors and players. JSON is useful for processing text.'],
    metadata: ['All the useful details', 'Add the thumbnail or description under More control to keep them alongside the video information.'],
  };
  $('tip-title').textContent = tips[mode][0];
  $('tip-text').textContent = tips[mode][1];
  const inspection = lastInspectionKey === inspectionKey() ? lastInspection : null;
  const estimate = hosted && mode === 'video' ? selectedEstimate(inspection, $('quality').value, $('container').value) : null;
  const state = estimateState(estimate);
  const blocked = Boolean(inspection && state === 'over');
  const selectionNote = $('public-selection-note');
  selectionNote.hidden = !inspection || mode !== 'video' || state === 'available';
  selectionNote.dataset.state = state;
  selectionNote.textContent = state === 'over' ? 'This quality is over the 5 GB public limit. Choose a lower quality or use YTLoad Local.'
    : state === 'near' ? 'Close to the public limit. The final processed file may still exceed 5 GB.'
      : state === 'unknown' && inspection ? 'Size unknown. YTLoad will enforce the 5 GB limit while processing.' : '';
  $('submit-button').disabled = !ready || submitting || blocked;
  document.querySelector('.mobile-submit').disabled = !ready || submitting || blocked;
}

function payload(list = urls()) {
  const mode = selectedMode();
  const includeText = hasTranscript();
  if (!list.length || list.length > 100) throw new Error('Add between 1 and 100 links per batch.');
  for (const value of list) {
    let parsed;
    try { parsed = new URL(value); } catch { throw new Error('Use complete links beginning with https:// or http://.'); }
    if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error('Use HTTP or HTTPS links without embedded credentials.');
  }
  if (includeText && !textFormats().length) throw new Error('Choose at least one transcript format: TXT, SRT, VTT or JSON.');
  return {
    urls: list, mode, quality: $('quality').value, video_container: $('container').value,
    audio_format: $('audio-format').value, ...(hosted ? {} : {output_root: $('output').value.trim()}),
    subtitles: includeText ? $('subtitles').value : 'none',
    sub_langs: $('sub-langs').value === 'custom' ? $('custom-language').value.trim() : $('sub-langs').value,
    transcript_formats: includeText ? textFormats() : [],
    transcript_timestamps: includeText && $('timestamps').checked,
    embed_subs: mode === 'video' && includeText && $('embed-subs').checked,
    channel_scope: $('channel-scope').value, playlist_items: $('items').value.trim() || null,
    date_after: $('date-after').value || null, date_before: $('date-before').value || null,
    max_filesize: $('max-filesize').value.trim() || null, ...(hosted ? {} : {limit_rate: $('limit-rate').value.trim() || null}),
    user_agent: $('user-agent').value.trim() || null, youtube_client: $('youtube-client').value,
    browser: $('browser').value || null, sponsorblock: ['video', 'audio'].includes(mode) ? $('sponsorblock').value : 'off',
    write_thumbnail: $('thumbnail').checked, write_description: $('description').checked,
    write_info_json: $('info-json').checked || mode === 'metadata', write_comments: $('comments').checked,
    archive: $('archive').checked ? null : false,
  };
}

const preferenceIds = ['quality', 'container', 'audio-format', 'with-transcript', 'sub-langs', 'custom-language', 'subtitles', 'timestamps', 'embed-subs'];
function savePreferences() {
  const data = { formats: textFormats(), mode: selectedMode() };
  for (const id of preferenceIds) data[id] = $(id).type === 'checkbox' ? $(id).checked : $(id).value;
  try { localStorage.setItem('ytload-preferences', JSON.stringify(data)); } catch { }
}

function restorePreferences() {
  try {
    const data = JSON.parse(localStorage.getItem('ytload-preferences'));
    if (!data || typeof data !== 'object') return;
    for (const id of preferenceIds) {
      const input = $(id);
      if (input.type === 'checkbox' && typeof data[id] === 'boolean') input.checked = data[id];
      else if (typeof data[id] === 'string' && (input.tagName !== 'SELECT' || [...input.options].some((option) => option.value === data[id]))) input.value = data[id];
    }
    if (Object.hasOwn(modeNames, data.mode)) document.querySelector(`input[name="mode"][value="${data.mode}"]`).checked = true;
    if (Array.isArray(data.formats)) document.querySelectorAll('input[name="transcript-format"]').forEach((input) => { input.checked = data.formats.includes(input.value); });
  } catch { }
}

$('download-form').addEventListener('input', () => {
  updateSummary();
  clearTimeout(preferencesTimer);
  preferencesTimer = setTimeout(savePreferences, 300);
});
$('download-form').addEventListener('change', updateSummary);
$('urls').addEventListener('input', clearInspection);
for (const id of ['browser', 'user-agent', 'youtube-client']) $(id).addEventListener('input', () => {
  if (lastInspectionKey && lastInspectionKey !== inspectionKey()) clearInspection();
});
for (const id of ['quality', 'container']) $(id).addEventListener('change', () => {
  if (lastInspection && lastInspectionKey === inspectionKey()) renderInspection(lastInspection);
});
$('clear-links').addEventListener('click', () => {
  $('urls').value = '';
  clearInspection();
  updateSummary();
  translatePage();
  $('urls').focus();
});

$('download-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submitting || !ready) return;
  showError('');
  try {
    const request = payload();
    submitting = true;
    updateSummary();
    const result = await api('/api/jobs', request);
    clearInspection();
    toast(`${result.ids.length} ${result.ids.length === 1 ? 'download' : 'downloads'} added. Your files will save automatically.`);
    await refreshJobs();
    $('queue-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) { showError(error.message); }
  finally { submitting = false; updateSummary(); }
});

$('paste-button').addEventListener('click', async () => {
  try {
    const value = await navigator.clipboard.readText();
    $('urls').value += ($('urls').value.trim() ? '\n' : '') + value.trim();
    clearInspection();
    updateSummary();
  } catch { toast('Click the link field and press Ctrl+V or ⌘V to paste.'); $('urls').focus(); }
});
$('import-button').addEventListener('click', () => $('import-file').click());
$('import-file').addEventListener('change', async () => {
  const file = $('import-file').files[0];
  if (!file) return;
  if (file.size > 1024 * 1024) { toast('Choose a text file smaller than 1 MB.'); return; }
  try {
    const lines = (await file.text()).split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !line.startsWith('#'));
    $('urls').value += ($('urls').value.trim() ? '\n' : '') + lines.join('\n');
    clearInspection();
    updateSummary();
  } catch { toast('This file could not be read. Paste its links directly into the field.'); }
  $('import-file').value = '';
});

$('inspect-button').addEventListener('click', async () => {
  const url = urls()[0];
  if (!url || inspecting) return;
  const key = inspectionKey();
  inspecting = true;
  $('preview').hidden = false;
  $('preview').classList.remove('error');
  $('preview').textContent = 'Checking your first link. This may take a moment…';
  updateSummary();
  try {
    const data = await api('/api/inspect', { url, browser: $('browser').value || null, user_agent: $('user-agent').value.trim() || null, youtube_client: $('youtube-client').value }, 50000);
    if (inspectionKey() !== key) return;
    lastInspection = data;
    lastInspectionKey = key;
    renderInspection(data);
  } catch (error) {
    lastInspection = null;
    lastInspectionKey = null;
    $('preview').classList.add('error');
    $('preview').textContent = error.message;
  } finally { inspecting = false; updateSummary(); }
});

$('choose-folder').addEventListener('click', async () => {
  $('choose-folder').disabled = true;
  try {
    const result = await api('/api/choose-folder', {}, 130000);
    if (result.path) { $('output').value = result.path; updateSummary(); }
  } catch (error) { toast(error.message); }
  finally { $('choose-folder').disabled = false; }
});
$('open-folder').addEventListener('click', async () => {
  try { await api('/api/open-folder', { path: $('output').value.trim() }); }
  catch (error) { toast(error.message); }
});

for (const id of ['help-button', 'footer-help']) $(id).addEventListener('click', () => $('help-dialog').showModal());
for (const id of ['close-help', 'help-done']) $(id).addEventListener('click', () => $('help-dialog').close());
for (const id of ['close-setup', 'setup-done']) $(id).addEventListener('click', () => $('setup-dialog').close());
$('copy-setup-command').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('setup-command').textContent);
    toast('Setup commands copied.');
  } catch { toast('Select and copy the commands manually.'); }
});
$('help-dialog').addEventListener('click', (event) => { if (event.target === $('help-dialog')) { const rect = $('help-dialog').getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) $('help-dialog').close(); } });

function reviewSetting(action) {
  if (action === 'setup') {
    $('setup-dialog').showModal();
    return;
  }
  let target;
  if (action === 'browser') {
    target = $('browser');
    target.closest('details').open = true;
  } else if (action === 'captions') {
    if (selectedMode() === 'metadata') document.querySelector('input[name="mode"][value="subs"]').checked = true;
    $('with-transcript').checked = true;
    target = $('sub-langs');
  } else if (action === 'format') {
    $('quality').value = 'best';
    $('container').value = 'auto';
    target = $('quality');
  }
  updateSummary();
  translatePage();
  if (target) {
    target.scrollIntoView({behavior: 'smooth', block: 'center'});
    target.focus({preventScroll: true});
  }
}

function renderRecovery(job, node) {
  const panel = node.querySelector('.job-recovery');
  const retryable = ['failed', 'cancelled'].includes(job.status);
  const advice = job.recovery || [];
  panel.hidden = !retryable && !advice.length;
  panel.replaceChildren();
  if (panel.hidden) return;
  const heading = document.createElement('h4');
  heading.textContent = 'Suggested next steps';
  panel.append(heading);
  const actions = new Set();
  if (advice.length) {
    const list = document.createElement('ul');
    for (const item of advice) {
      const entry = document.createElement('li');
      entry.textContent = item.text;
      list.append(entry);
      if (item.action !== 'none' && !(hosted && ['browser', 'setup'].includes(item.action))) actions.add(item.action);
    }
    panel.append(list);
  }
  const controls = document.createElement('div');
  controls.className = 'recovery-actions';
  const labels = {browser: 'Review browser sign-in', captions: 'Choose caption language', setup: 'Tool setup help', format: 'Use Best + Auto'};
  for (const action of actions) {
    if (!Object.hasOwn(labels, action)) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = labels[action];
    button.addEventListener('click', () => reviewSetting(action));
    controls.append(button);
  }
  if (retryable) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = 'Retry with current settings';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const request = payload([job.url]);
        request.channel_scope = 'auto';
        await api('/api/jobs', request);
        toast('Retry added using the settings above. Your links are unchanged.');
        await refreshJobs();
      } catch (error) { toast(error.message); }
      finally { button.disabled = false; }
    });
    controls.append(button);
  }
  panel.append(controls);
  if (retryable) {
    const note = document.createElement('p');
    note.className = 'field-note';
    note.textContent = 'Retry with current settings uses the form above for this link only. Retry same settings repeats the original request.';
    panel.append(note);
  }
}

function createJob(job) {
  const node = document.createElement('article');
  node.className = 'job';
  node.innerHTML = '<div class="job-head"><span class="job-icon" aria-hidden="true"></span><div class="job-text"><h3 class="job-title"></h3><span class="job-url"></span></div><div class="job-actions"><span class="job-status"></span><button class="job-button" type="button"></button></div></div><progress class="job-progress" max="100" aria-label="Current file progress"></progress><div class="job-progress-text"><span></span><span></span></div><p class="job-error" hidden><span class="job-error-message"></span><small class="job-error-detail"></small></p><div class="job-warnings" hidden></div><div class="job-files"></div><section class="job-recovery" aria-label="Download recovery" hidden></section><details class="job-log"><summary>Activity log</summary><pre></pre></details>';
  node.querySelector('.job-button').addEventListener('click', async () => {
    const button = node.querySelector('.job-button');
    button.disabled = true;
    try {
      await api(['queued', 'running'].includes(node.dataset.status) ? '/api/cancel' : '/api/retry', { id: job.id });
      await refreshJobs();
    } catch (error) { toast(error.message); }
    finally { button.disabled = false; }
  });
  $('queue').append(node);
  return { node, last: '', fileKey: '' };
}

function renderJob(job, entry) {
  const serialized = JSON.stringify(job);
  if (entry.last === serialized) return;
  entry.last = serialized;
  const node = entry.node;
  node.dataset.status = job.status;
  node.querySelector('.job-title').textContent = job.title;
  node.querySelector('.job-title').title = job.title;
  node.querySelector('.job-url').textContent = job.url;
  node.querySelector('.job-icon').textContent = symbols[job.mode];
  node.querySelector('.job-status').textContent = job.result_expired ? 'Expired' : { queued: 'Queued', running: 'Downloading', cancelling: 'Stopping…', completed: job.files.length ? 'Saved' : 'Finished', failed: 'Needs attention', cancelled: 'Cancelled' }[job.status];
  const button = node.querySelector('.job-button');
  button.hidden = job.result_expired || ['completed', 'cancelling'].includes(job.status);
  button.textContent = ['queued', 'running'].includes(job.status) ? 'Cancel' : 'Retry same settings';
  button.setAttribute('aria-label', `${button.textContent} ${job.title}`);
  const progress = node.querySelector('.job-progress');
  const total = job.progress.total;
  const downloaded = job.progress.downloaded;
  progress.hidden = !['running', 'cancelling'].includes(job.status);
  if (typeof total === 'number' && total > 0 && typeof downloaded === 'number') progress.value = Math.min(100, downloaded / total * 100);
  else progress.removeAttribute('value');
  const progressText = node.querySelector('.job-progress-text');
  const parts = [];
  if (job.status === 'running') {
    if (job.progress.status === 'finished') parts.push('Finalizing files…');
    else if (typeof downloaded === 'number') parts.push(`${formatBytes(downloaded)}${typeof total === 'number' ? ' / ' + formatBytes(total) : ''}`);
    else parts.push('Connecting and reading video information…');
    if (typeof job.progress.index === 'number') parts.push(`Item ${job.progress.index}${typeof job.progress.count === 'number' ? ' / ' + job.progress.count : ''}`);
  } else if (job.result_expired) parts.push('Temporary file expired. Run the download again.');
  else if (job.status === 'completed') parts.push(job.files.length ? `${job.files.length} ${job.files.length === 1 ? 'file' : 'files'} saved to your folder` : 'No new files. Items may already exist or be excluded by filters.');
  else if (job.status === 'queued') parts.push('Waiting for the previous download');
  else if (job.status === 'cancelled') parts.push('Partial media files are kept for a retry');
  progressText.children[0].textContent = parts.join(' · ');
  progressText.children[1].textContent = job.status === 'running' && job.progress.status !== 'finished' ? [formatBytes(job.progress.speed) ? formatBytes(job.progress.speed) + '/s' : '', formatTime(job.progress.eta) ? formatTime(job.progress.eta) + ' left' : ''].filter(Boolean).join(' · ') : '';
  node.querySelector('.job-error').hidden = !job.error || (job.status === 'cancelled' && !job.result_expired);
  node.querySelector('.job-error-message').textContent = job.error || '';
  node.querySelector('.job-error-detail').textContent = job.error_detail || '';
  renderRecovery(job, node);
  node.querySelector('.job-warnings').hidden = !job.warnings.length;
  node.querySelector('.job-warnings').textContent = job.warnings.join('\n');
  node.querySelector('.job-log pre').textContent = job.logs.join('\n') || 'No activity yet.';
  const fileKey = JSON.stringify(job.files);
  if (fileKey !== entry.fileKey) {
    entry.fileKey = fileKey;
    node.querySelector('.job-files').replaceChildren(...job.files.map((path, index) => {
      const link = document.createElement('a');
      const name = path.split(/[\\/]/).at(-1);
      link.textContent = `↓ ${name}`;
      link.title = 'Save another copy of ' + name;
      link.href = `${basePath}/api/files/${job.id}/${index}?token=${encodeURIComponent(token)}`;
      link.download = name;
      return link;
    }));
  }
}

async function refreshJobs() {
  const data = await api('/api/jobs');
  if (data.length && $('queue').querySelector('.queue-empty')) $('queue').replaceChildren();
  const ids = new Set(data.map((job) => job.id));
  for (const [id, entry] of jobs) if (!ids.has(id)) { entry.node.remove(); jobs.delete(id); }
  for (const job of data) {
    if (!jobs.has(job.id)) jobs.set(job.id, createJob(job));
    renderJob(job, jobs.get(job.id));
  }
  $('queue-count').textContent = data.length;
  const active = data.filter((job) => ['queued', 'running', 'cancelling'].includes(job.status)).length;
  $('nav-count').textContent = active || data.length;
  const announcement = `${active} queued or running, ${data.filter((job) => job.status === 'completed').length} completed, ${data.filter((job) => job.status === 'failed').length} need attention.`;
  if (lastQueueAnnouncement !== announcement) { $('queue-status').textContent = announcement; lastQueueAnnouncement = announcement; }
  $('connection').classList.remove('offline');
  $('connection').lastChild.textContent = hosted ? 'Public workspace' : 'Local workspace';
  return active;
}

async function poll() {
  let active = 0;
  try { active = await refreshJobs(); }
  catch {
    $('connection').classList.add('offline');
    $('connection').lastChild.textContent = 'Disconnected';
  }
  setTimeout(poll, document.hidden ? 5000 : active ? 1000 : 2500);
}

async function initialize() {
  updateSummary();
  try {
    const data = await api('/api/config');
    currentEnvironment = data.environment;
    hosted = Boolean(data.hosted);
    if (hosted) {
      $('public-policy').hidden = false;
      $('hosted-storage-note').hidden = false;
      document.querySelector('.local-note').hidden = true;
      document.querySelector('label[for="output"]').textContent = 'Storage';
      $('output').readOnly = true;
      $('choose-folder').hidden = true;
      $('open-folder').hidden = true;
      $('browser').value = '';
      $('browser').closest('label').hidden = true;
      $('sponsorblock').closest('label').hidden = true;
      $('comments').closest('label').hidden = true;
      $('max-filesize').placeholder = 'Up to 5G';
      $('limit-rate').value = '';
      $('limit-rate-row').hidden = true;
      $('items').placeholder = '1:50 · or 51:100';
      $('connection').lastChild.textContent = 'Public workspace';
    }
    $('remote-note').hidden = !data.remote;
    $('remote-folder-note').hidden = !data.remote || hosted;
    $('output').value = data.config.output_root;
    $('quality').value = data.config.quality;
    if ([...$('sub-langs').options].some((option) => option.value === data.config.sub_langs)) $('sub-langs').value = data.config.sub_langs;
    else { $('sub-langs').value = 'custom'; $('custom-language').value = data.config.sub_langs; }
    $('browser').value = data.config.browser || '';
    $('archive').checked = data.config.archive;
    $('sponsorblock').value = data.config.sponsorblock;
    $('version').textContent = 'v' + data.version;
    restorePreferences();
    ready = true;
    const missing = ['yt_dlp', 'ffmpeg'].filter((key) => !currentEnvironment[key].installed);
    if (missing.length) {
      $('setup-warning').hidden = false;
      $('setup-warning').textContent = `${missing.map((key) => key === 'yt_dlp' ? 'yt-dlp' : 'FFmpeg').join(' and ')} not found. Run install-macos.sh, install-linux.sh or install-windows.ps1 for your system, then restart this app.${missing.length === 1 && missing[0] === 'ffmpeg' ? ' Transcript and Details modes still work without FFmpeg.' : ''}`;
    } else if (!currentEnvironment.deno.installed) {
      $('setup-warning').hidden = false;
      $('setup-warning').textContent = 'Deno was not found. Some YouTube videos need it to resolve playback challenges. The installer can add it.';
    }
    updateSummary();
    poll();
  } catch (error) { showError(error.message); }
}

function updateAppearance() {
  const dark = document.documentElement.dataset.theme === 'dark';
  const label = dark ? 'Switch to light theme' : 'Switch to dark theme';
  $('theme-toggle').setAttribute('aria-label', label);
  $('theme-toggle').title = label;
  $('theme-toggle').firstElementChild.textContent = dark ? '☼' : '◐';
  document.querySelector('meta[name="theme-color"]').content = dark ? '#171b17' : '#e7e0d1';
  document.querySelectorAll('[data-language]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.language === locale)));
  translatePage();
}

$('theme-toggle').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('ytload-theme', theme); } catch { }
  updateAppearance();
});
for (const button of document.querySelectorAll('[data-language]')) button.addEventListener('click', () => {
  locale = button.dataset.language;
  try { localStorage.setItem('ytload-language', locale); } catch { }
  updateAppearance();
});
updateAppearance();
initialize();
