const encoder = new TextEncoder();
const decoder = new TextDecoder();

export const HARD_CAP_BYTES = 8_000_000_000;
export const MAX_MEDIA_BYTES = 5 * 1024 * 1024 * 1024;

export class CapacityError extends Error {
  constructor(message, code = 'invalid') {
    super(message);
    this.code = code;
  }
}

function encodeBase64Url(bytes) {
  let value = '';
  for (const byte of bytes) value += String.fromCharCode(byte);
  return btoa(value).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

function decodeBase64Url(value) {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('Invalid ticket.');
  const padded = value.replaceAll('-', '+').replaceAll('_', '/') + '='.repeat((4 - value.length % 4) % 4);
  return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}

async function hmac(value, secret) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(value)));
}

function equalBytes(first, second) {
  if (first.length !== second.length) return false;
  let difference = 0;
  for (let index = 0; index < first.length; index += 1) difference |= first[index] ^ second[index];
  return difference === 0;
}

export async function createTicket({ key, size, expiresAt }, secret) {
  if (!validObjectKey(key) || !Number.isSafeInteger(size) || size < 0 || !Number.isSafeInteger(expiresAt)) throw new Error('Invalid ticket payload.');
  const payload = encodeBase64Url(encoder.encode(JSON.stringify({ v: 1, k: key, s: size, e: expiresAt })));
  return `${payload}.${encodeBase64Url(await hmac(payload, secret))}`;
}

export async function verifyTicket(ticket, secret, now = Math.floor(Date.now() / 1000)) {
  if (typeof secret !== 'string' || secret.length < 32) throw new Error('Ticket service unavailable.');
  const parts = ticket.split('.');
  if (parts.length !== 2) throw new Error('Invalid ticket.');
  const expected = await hmac(parts[0], secret);
  const supplied = decodeBase64Url(parts[1]);
  if (!equalBytes(expected, supplied)) throw new Error('Invalid ticket.');
  let payload;
  try {
    payload = JSON.parse(decoder.decode(decodeBase64Url(parts[0])));
  } catch {
    throw new Error('Invalid ticket.');
  }
  if (payload?.v !== 1 || !validObjectKey(payload.k) || !Number.isSafeInteger(payload.s) || payload.s < 0 || !Number.isSafeInteger(payload.e)) throw new Error('Invalid ticket.');
  if (payload.e <= now) throw new Error('Ticket expired.');
  return { key: payload.k, size: payload.s, expiresAt: payload.e };
}

export function parseRange(value, size) {
  if (value == null) return null;
  if (!Number.isSafeInteger(size) || size < 0 || size === 0) throw new Error('Invalid range.');
  const match = /^bytes=(\d*)-(\d*)$/.exec(value);
  if (!match || (!match[1] && !match[2])) throw new Error('Invalid range.');
  let start;
  let end;
  if (match[1]) {
    start = Number(match[1]);
    end = match[2] ? Math.min(Number(match[2]), size - 1) : size - 1;
  } else {
    const suffix = Number(match[2]);
    if (!Number.isSafeInteger(suffix) || suffix <= 0) throw new Error('Invalid range.');
    start = Math.max(0, size - suffix);
    end = size - 1;
  }
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= size || start > end) throw new Error('Invalid range.');
  return { offset: start, length: end - start + 1, start, end };
}

export function contentDisposition(filename) {
  const clean = String(filename || 'download.bin').replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 240) || 'download.bin';
  const fallback = clean.normalize('NFKD').replace(/[^\x20-\x7e]/g, '_').replace(/["\\]/g, '_').trim() || 'download.bin';
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(clean)}`;
}

function validObjectKey(value) {
  return typeof value === 'string' && /^media\/[a-f0-9]{32}\/[a-f0-9]{32}$/.test(value);
}

function metadataFilename(object) {
  const encoded = object?.customMetadata?.filenameEncoded || object?.customMetadata?.['filename-encoded'];
  if (typeof encoded === 'string') {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return 'download.bin';
    }
  }
  return object?.customMetadata?.filename || 'download.bin';
}

function responseHeaders(object, size, range) {
  const headers = new Headers({
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'private, no-store',
    'Content-Disposition': contentDisposition(metadataFilename(object)),
    'Content-Length': String(range ? range.length : size),
    'Content-Type': object?.httpMetadata?.contentType || 'application/octet-stream',
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
  });
  if (range) headers.set('Content-Range', `bytes ${range.start}-${range.end}/${size}`);
  return headers;
}

function simple(status, message, extra = {}) {
  return new Response(message, { status, headers: { 'Cache-Control': 'private, no-store', 'Content-Type': 'text/plain; charset=utf-8', ...extra } });
}

export async function handleMedia(request, env, now = Math.floor(Date.now() / 1000)) {
  if (!env.MEDIA_BUCKET || !env.MEDIA_RATE_LIMITER || typeof env.MEDIA_RATE_LIMITER.limit !== 'function' || typeof env.MEDIA_TICKET_SECRET !== 'string' || env.MEDIA_TICKET_SECRET.length < 32) return simple(503, 'Media delivery unavailable.');
  if (!['GET', 'HEAD'].includes(request.method)) return simple(405, 'Method not allowed.', { Allow: 'GET, HEAD' });
  const marker = '/ytload/media/';
  const pathname = new URL(request.url).pathname;
  if (!pathname.startsWith(marker) || pathname.length === marker.length) return simple(404, 'File not found.');
  const ticket = pathname.slice(marker.length);
  let payload;
  try {
    payload = await verifyTicket(ticket, env.MEDIA_TICKET_SECRET, now);
  } catch (error) {
    return simple(String(error.message).includes('expired') ? 410 : 403, String(error.message));
  }
  try {
    const allowance = await env.MEDIA_RATE_LIMITER.limit({ key: payload.key });
    if (!allowance?.success) return simple(429, 'Media request limit exceeded.', { 'Retry-After': '60' });
  } catch {
    return simple(503, 'Media delivery unavailable.');
  }
  let range;
  try {
    range = parseRange(request.headers.get('Range'), payload.size);
  } catch {
    return simple(416, 'Invalid range.', { 'Content-Range': `bytes */${payload.size}` });
  }
  try {
    const object = request.method === 'HEAD' ? await env.MEDIA_BUCKET.head(payload.key) : await env.MEDIA_BUCKET.get(payload.key, range ? { range: { offset: range.offset, length: range.length } } : {});
    if (!object || object.size !== payload.size) return simple(404, 'File not found.');
    const headers = responseHeaders(object, payload.size, range);
    return new Response(request.method === 'HEAD' ? null : object.body, { status: range ? 206 : 200, headers });
  } catch {
    return simple(503, 'Media delivery unavailable.');
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function validateFile(file, batchId, cap) {
  if (!file || !validObjectKey(file.key) || !file.key.startsWith(`media/${batchId}/`) || typeof file.name !== 'string' || !file.name || file.name.length > 240 || !Number.isSafeInteger(file.size) || file.size < 0 || file.size > Math.min(cap, MAX_MEDIA_BYTES) || typeof file.contentType !== 'string' || file.contentType.length > 120) throw new CapacityError('Invalid file reservation.');
  return { key: file.key, name: file.name, size: file.size, contentType: file.contentType, uploadId: null, completed: false };
}

export class CapacityModel {
  constructor(cap, state = null, reconcileMaxAge = 1_800) {
    if (!Number.isSafeInteger(cap) || cap <= 0 || cap > HARD_CAP_BYTES) throw new Error('Invalid capacity.');
    if (!Number.isSafeInteger(reconcileMaxAge) || reconcileMaxAge <= 0) throw new Error('Invalid reconciliation age.');
    this.cap = cap;
    this.reconcileMaxAge = reconcileMaxAge;
    this.state = state || { version: 1, reconciledAt: 0, blocked: 'Storage has not been reconciled.', batches: {}, orphans: {} };
  }

  toJSON() {
    return clone(this.state);
  }

  usage() {
    const batches = Object.values(this.state.batches).reduce((total, batch) => total + batch.reservedBytes, 0);
    const orphans = Object.values(this.state.orphans).reduce((total, object) => total + object.size, 0);
    return batches + orphans;
  }

  reserve(id, files, now) {
    if (!/^[a-f0-9]{32}$/.test(id) || !Array.isArray(files) || files.length < 1 || files.length > 50 || !Number.isSafeInteger(now)) throw new CapacityError('Invalid reservation.');
    if (this.state.blocked || !this.state.reconciledAt || now - this.state.reconciledAt > this.reconcileMaxAge) throw new CapacityError('R2 state must be reconciled before publishing.', 'unreconciled');
    const prepared = files.map((file) => validateFile(file, id, this.cap));
    if (new Set(prepared.map((file) => file.key)).size !== prepared.length) throw new CapacityError('Duplicate object reservation.');
    const existing = this.state.batches[id];
    if (existing) {
      if (JSON.stringify(existing.files.map(({ uploadId, completed, ...file }) => file)) !== JSON.stringify(prepared.map(({ uploadId, completed, ...file }) => file))) throw new CapacityError('Reservation identifier already exists.');
      if (existing.status === 'cleanup_pending') throw new CapacityError('Reservation cleanup is in progress.');
      return clone(existing);
    }
    const reservedBytes = prepared.reduce((total, file) => total + file.size, 0);
    if (!Number.isSafeInteger(reservedBytes) || this.usage() + reservedBytes > this.cap) throw new CapacityError('R2 delivery is at capacity.', 'capacity');
    const batch = { id, status: 'reserved', reservedBytes, createdAt: now, updatedAt: now, readyAt: null, expiresAt: null, files: prepared };
    this.state.batches[id] = batch;
    return clone(batch);
  }

  batch(id) {
    const batch = this.state.batches[id];
    if (!batch) throw new CapacityError('Unknown reservation.', 'missing');
    return batch;
  }

  file(batch, key) {
    const file = batch.files.find((item) => item.key === key);
    if (!file) throw new CapacityError('Unknown reserved object.', 'missing');
    return file;
  }

  registerMultipart(id, key, uploadId, now) {
    if (typeof uploadId !== 'string' || !uploadId || uploadId.length > 1_024) throw new CapacityError('Invalid multipart upload.');
    const batch = this.batch(id);
    if (batch.status === 'cleanup_pending' || batch.status === 'ready') throw new CapacityError('Reservation cleanup is in progress.');
    const file = this.file(batch, key);
    if (file.uploadId && file.uploadId !== uploadId) throw new CapacityError('Multipart upload is already registered.');
    file.uploadId = uploadId;
    batch.status = 'uploading';
    batch.updatedAt = now;
    return clone(batch);
  }

  touch(id, now) {
    const batch = this.batch(id);
    if (batch.status === 'cleanup_pending') throw new CapacityError('Reservation cleanup is in progress.');
    if (batch.status === 'ready') return clone(batch);
    batch.updatedAt = now;
    return clone(batch);
  }

  completeFile(id, key, size, now) {
    const batch = this.batch(id);
    if (batch.status === 'cleanup_pending') throw new CapacityError('Reservation cleanup is in progress.');
    const file = this.file(batch, key);
    if (!file.uploadId || file.size !== size) throw new CapacityError('Completed object does not match its reservation.');
    file.completed = true;
    batch.updatedAt = now;
    return clone(batch);
  }

  commit(id, now, lifetime) {
    const batch = this.batch(id);
    if (batch.status === 'ready') return clone(batch);
    if (batch.status === 'cleanup_pending') throw new CapacityError('Reservation cleanup is in progress.');
    if (!Number.isSafeInteger(lifetime) || lifetime < 60 || lifetime > 3_600 || !batch.files.every((file) => file.completed)) throw new CapacityError('Reservation is not ready.');
    batch.status = 'ready';
    batch.readyAt = now;
    batch.expiresAt = now + lifetime;
    batch.updatedAt = now;
    return clone(batch);
  }

  beginCleanup(id) {
    const batch = this.batch(id);
    batch.status = 'cleanup_pending';
    return clone(batch);
  }

  releaseBatch(id) {
    delete this.state.batches[id];
  }

  releaseOrphan(key) {
    delete this.state.orphans[key];
  }

  cleanupPlans(now, activeStaleSeconds, orphanGraceSeconds) {
    const batches = Object.values(this.state.batches).filter((batch) => batch.status === 'cleanup_pending' || (batch.status === 'ready' ? batch.expiresAt <= now : batch.updatedAt + activeStaleSeconds <= now));
    for (const batch of batches) batch.status = 'cleanup_pending';
    const orphans = Object.values(this.state.orphans).filter((object) => object.firstSeen + orphanGraceSeconds <= now);
    return { batches: clone(batches), orphans: clone(orphans) };
  }

  reconcile(objects, now, complete) {
    if (!complete) {
      this.state.reconciledAt = 0;
      this.state.blocked = 'R2 listing was incomplete.';
      return;
    }
    const known = new Map();
    for (const batch of Object.values(this.state.batches)) for (const file of batch.files) known.set(file.key, { batch, file });
    const observed = new Map(objects.map((object) => [object.key, object]));
    let blocked = null;
    const orphans = {};
    for (const object of objects) {
      if (!validObjectKey(object.key) || !Number.isSafeInteger(object.size) || object.size < 0) {
        blocked = 'R2 contains an invalid object record.';
        continue;
      }
      const entry = known.get(object.key);
      if (!entry) {
        const previous = this.state.orphans[object.key];
        orphans[object.key] = { key: object.key, size: object.size, firstSeen: previous?.firstSeen || object.uploaded || now };
      } else if (entry.file.size !== object.size) {
        blocked = 'An R2 object does not match its reservation.';
      }
    }
    for (const { batch, file } of known.values()) if (file.completed && !observed.has(file.key)) batch.status = 'cleanup_pending';
    this.state.orphans = orphans;
    this.state.reconciledAt = now;
    this.state.blocked = blocked;
  }
}
