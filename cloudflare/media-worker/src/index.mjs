import { CapacityError, CapacityModel, HARD_CAP_BYTES, createTicket, handleMedia } from './core.mjs';

const encoder = new TextEncoder();

function json(value, status = 200, extra = {}) {
  return new Response(JSON.stringify(value), { status, headers: { 'Cache-Control': 'private, no-store', 'Content-Type': 'application/json; charset=utf-8', ...extra } });
}

function statusFor(error) {
  if (error instanceof CapacityError) return error.code === 'missing' ? 404 : error.code === 'capacity' ? 409 : error.code === 'unreconciled' ? 503 : 400;
  return 503;
}

async function sha256(value) {
  const digest = await crypto.subtle.digest('SHA-256', value);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function signature(value, secret) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const signed = new Uint8Array(await crypto.subtle.sign('HMAC', key, encoder.encode(value)));
  let output = '';
  for (const byte of signed) output += String.fromCharCode(byte);
  return btoa(output).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}

function equal(first, second) {
  if (first.length !== second.length) return false;
  let difference = 0;
  for (let index = 0; index < first.length; index += 1) difference |= first.charCodeAt(index) ^ second.charCodeAt(index);
  return difference === 0;
}

export async function createControlAuthorization(method, path, body, secret, timestamp, nonce) {
  const canonical = `${method}\n${path}\n${timestamp}\n${nonce}\n${await sha256(body)}`;
  return `YTLoad-HMAC ${await signature(canonical, secret)}`;
}

export async function authenticateControl(request, body, env, now) {
  const secret = env.MEDIA_CONTROL_SECRET;
  const timestamp = request.headers.get('X-YTLoad-Timestamp') || '';
  const nonce = request.headers.get('X-YTLoad-Nonce') || '';
  const authorization = request.headers.get('Authorization') || '';
  if (typeof secret !== 'string' || secret.length < 32 || !/^\d{10}$/.test(timestamp) || !/^[a-f0-9]{32}$/.test(nonce) || Math.abs(now - Number(timestamp)) > 300 || !authorization.startsWith('YTLoad-HMAC ')) return false;
  const path = new URL(request.url).pathname;
  return equal(authorization, await createControlAuthorization(request.method, path, body, secret, timestamp, nonce));
}

async function readControlBody(request) {
  const length = Number(request.headers.get('Content-Length') || '0');
  if (!Number.isSafeInteger(length) || length < 2 || length > 65_536) throw new CapacityError('Invalid control body.');
  const body = new Uint8Array(await request.arrayBuffer());
  if (body.byteLength !== length) throw new CapacityError('Invalid control body.');
  return body;
}

async function ledger(env, path, payload = null, method = 'POST') {
  if (!env.CAPACITY_LEDGER) throw new Error('Capacity ledger unavailable.');
  const id = env.CAPACITY_LEDGER.idFromName('global');
  const response = await env.CAPACITY_LEDGER.get(id).fetch(`https://ledger.internal${path}`, {
    method,
    headers: payload == null ? {} : { 'Content-Type': 'application/json' },
    body: payload == null ? null : JSON.stringify(payload),
  });
  const value = await response.json();
  if (!response.ok) throw new CapacityError(value.error || 'Capacity ledger rejected the request.', value.code);
  return value;
}

async function listObjects(bucket) {
  const objects = [];
  let cursor;
  for (let page = 0; page < 10; page += 1) {
    const result = await bucket.list({ prefix: 'media/', limit: 1_000, ...(cursor ? { cursor } : {}) });
    for (const object of result.objects || []) objects.push({ key: object.key, size: object.size, uploaded: Math.floor(new Date(object.uploaded).getTime() / 1000) });
    if (!result.truncated) return { objects, complete: true };
    cursor = result.cursor;
    if (!cursor) break;
  }
  return { objects, complete: false };
}

export async function deleteBatchObjects(bucket, batch, safeUnregisteredKeys = []) {
  const safe = new Set(safeUnregisteredKeys);
  for (const file of batch.files) {
    if (file.completed) {
      await bucket.delete(file.key);
    } else if (file.uploadId) {
      try {
        await bucket.resumeMultipartUpload(file.key, file.uploadId).abort();
      } catch {
        const object = await bucket.head(file.key);
        if (object) await bucket.delete(file.key);
        else if (!safe.has(file.key)) return false;
      }
    } else {
      const object = await bucket.head(file.key);
      if (object) await bucket.delete(file.key);
      else if (!safe.has(file.key)) return false;
    }
    const remaining = await bucket.head(file.key);
    if (remaining) {
      await bucket.delete(file.key);
      if (await bucket.head(file.key)) return false;
    }
  }
  return true;
}

async function deleteOrphanObject(bucket, object) {
  await bucket.delete(object.key);
  if (await bucket.head(object.key)) return false;
  return true;
}


async function handleControl(request, env, now) {
  if (!env.MEDIA_BUCKET || !env.CAPACITY_LEDGER || typeof env.MEDIA_TICKET_SECRET !== 'string' || env.MEDIA_TICKET_SECRET.length < 32 || typeof env.MEDIA_CONTROL_SECRET !== 'string' || env.MEDIA_CONTROL_SECRET.length < 32) return json({ error: 'R2 delivery unavailable.' }, 503);
  if (request.method !== 'POST') return json({ error: 'Method not allowed.' }, 405, { Allow: 'POST' });
  let body;
  try {
    body = await readControlBody(request);
  } catch (error) {
    return json({ error: error.message }, 400);
  }
  if (!await authenticateControl(request, body, env, now)) return json({ error: 'Invalid control authorization.' }, 403);
  let payload;
  try {
    payload = JSON.parse(new TextDecoder().decode(body));
    if (!payload || Array.isArray(payload) || typeof payload !== 'object') throw new Error();
  } catch {
    return json({ error: 'Invalid JSON body.' }, 400);
  }
  const action = new URL(request.url).pathname.slice('/ytload/media-control/'.length);
  try {
    if (action === 'reconcile') return json(await ledger(env, '/reconcile', { now }));
    if (action === 'reserve') return json(await ledger(env, '/reserve', { ...payload, now }), 201);
    if (action === 'multipart') return json(await ledger(env, '/multipart', { ...payload, now }));
    if (action === 'touch') return json(await ledger(env, '/touch', { ...payload, now }));
    if (action === 'complete') {
      const object = await env.MEDIA_BUCKET.head(payload.key);
      if (!object || object.size !== payload.size) throw new CapacityError('Completed object is unavailable or has the wrong size.');
      return json(await ledger(env, '/complete', { ...payload, now }));
    }
    if (action === 'commit') {
      const batch = await ledger(env, '/commit', { ...payload, now, lifetime: Math.min(3_600, Number(env.RESULT_TTL_SECONDS || 3_600)) });
      const files = [];
      for (const file of batch.files) files.push({ name: file.name, size: file.size, url: `/ytload/media/${await createTicket({ key: file.key, size: file.size, expiresAt: batch.expiresAt }, env.MEDIA_TICKET_SECRET)}` });
      return json({ ready_at: batch.readyAt, expires_at: batch.expiresAt, files });
    }
    if (action === 'cancel') {
      let batch;
      try {
        batch = await ledger(env, '/begin-cleanup', { id: payload.id });
      } catch (error) {
        if (error.code === 'missing') return json({ ok: true });
        throw error;
      }
      const safeKeys = Array.isArray(payload.safe_unregistered_keys) ? payload.safe_unregistered_keys : [];
      if (safeKeys.some((key) => typeof key !== 'string' || !batch.files.some((file) => file.key === key))) throw new CapacityError('Invalid cleanup confirmation.');
      const safeAll = payload.safe_unregistered === true;
      const confirmed = safeAll ? batch.files.filter((file) => !file.completed).map((file) => file.key) : safeKeys;
      if (!await deleteBatchObjects(env.MEDIA_BUCKET, batch, confirmed)) throw new Error('R2 cleanup was not confirmed.');
      await ledger(env, '/release-batch', { id: batch.id });
      return json({ ok: true });
    }
    if (action === 'usage') return json(await ledger(env, '/usage', null, 'GET'));
    return json({ error: 'Control action not found.' }, 404);
  } catch (error) {
    return json({ error: error.message, code: error.code || 'unavailable' }, statusFor(error));
  }
}

export class CapacityLedger {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.model = null;
    this.serial = Promise.resolve();
    ctx.blockConcurrencyWhile(async () => {
      const sql = ctx.storage.sql;
      sql.exec('CREATE TABLE IF NOT EXISTS ledger_state (id INTEGER PRIMARY KEY, value TEXT NOT NULL)');
      const rows = [...sql.exec('SELECT value FROM ledger_state WHERE id = 1')];
      const state = rows.length ? JSON.parse(rows[0].value) : null;
      const cap = env.R2_HARD_CAP_BYTES === undefined ? HARD_CAP_BYTES : Number(env.R2_HARD_CAP_BYTES);
      const reconcileMaxAge = env.RECONCILE_MAX_AGE_SECONDS === undefined ? 1_800 : Number(env.RECONCILE_MAX_AGE_SECONDS);
      this.model = new CapacityModel(cap, state, reconcileMaxAge);
    });
  }

  persist() {
    this.ctx.storage.sql.exec('INSERT INTO ledger_state (id, value) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET value = excluded.value', JSON.stringify(this.model.toJSON()));
  }

  async maintenance(now) {
    if (!this.env.MEDIA_BUCKET || !Number.isSafeInteger(now)) throw new CapacityError('Maintenance configuration is invalid.');
    const activeStaleSeconds = this.env.ACTIVE_UPLOAD_STALE_SECONDS === undefined ? 3_600 : Number(this.env.ACTIVE_UPLOAD_STALE_SECONDS);
    const orphanGraceSeconds = this.env.ORPHAN_GRACE_SECONDS === undefined ? 1_800 : Number(this.env.ORPHAN_GRACE_SECONDS);
    if (![activeStaleSeconds, orphanGraceSeconds].every((value) => Number.isSafeInteger(value) && value > 0)) throw new CapacityError('Maintenance configuration is invalid.');
    const listing = await listObjects(this.env.MEDIA_BUCKET);
    this.model.reconcile(listing.objects, now, listing.complete);
    this.persist();
    const plans = this.model.cleanupPlans(now, activeStaleSeconds, orphanGraceSeconds);
    this.persist();
    for (const batch of plans.batches) {
      try {
        if (await deleteBatchObjects(this.env.MEDIA_BUCKET, batch)) {
          this.model.releaseBatch(batch.id);
          this.persist();
        }
      } catch (error) {
        console.error(JSON.stringify({ event: 'r2_cleanup_failed', kind: 'batch', id: batch.id, error: error?.name || 'Error' }));
      }
    }
    for (const object of plans.orphans) {
      try {
        if (await deleteOrphanObject(this.env.MEDIA_BUCKET, object)) {
          this.model.releaseOrphan(object.key);
          this.persist();
        }
      } catch (error) {
        console.error(JSON.stringify({ event: 'r2_cleanup_failed', kind: 'orphan', error: error?.name || 'Error' }));
      }
    }
    return { ok: listing.complete && !this.model.state.blocked, bytes: this.model.usage() };
  }

  fetch(request) {
    const operation = this.serial.then(() => this._fetch(request));
    this.serial = operation.catch(() => {});
    return operation;
  }

  async _fetch(request) {
    try {
      const url = new URL(request.url);
      let result;
      if (request.method === 'GET' && url.pathname === '/usage') {
        result = { bytes: this.model.usage(), cap: this.model.cap };
      } else {
        const payload = await request.json();
        if (url.pathname === '/maintenance') return json(await this.maintenance(payload.now));
        if (url.pathname === '/reserve') result = this.model.reserve(payload.id, payload.files, payload.now);
        else if (url.pathname === '/multipart') result = this.model.registerMultipart(payload.id, payload.key, payload.upload_id, payload.now);
        else if (url.pathname === '/touch') result = this.model.touch(payload.id, payload.now);
        else if (url.pathname === '/complete') result = this.model.completeFile(payload.id, payload.key, payload.size, payload.now);
        else if (url.pathname === '/commit') result = this.model.commit(payload.id, payload.now, payload.lifetime);
        else if (url.pathname === '/begin-cleanup') result = this.model.beginCleanup(payload.id);
        else if (url.pathname === '/release-batch') {
          this.model.releaseBatch(payload.id);
          result = { ok: true };
        } else if (url.pathname === '/release-orphan') {
          this.model.releaseOrphan(payload.key);
          result = { ok: true };
        } else if (url.pathname === '/cleanup-plans') {
          result = this.model.cleanupPlans(payload.now, payload.activeStaleSeconds, payload.orphanGraceSeconds);
        } else if (url.pathname === '/reconcile') {
          if (!this.env.MEDIA_BUCKET || !Number.isSafeInteger(payload.now)) throw new CapacityError('Reconciliation configuration is invalid.');
          const listing = await listObjects(this.env.MEDIA_BUCKET);
          this.model.reconcile(listing.objects, payload.now, listing.complete);
          result = { ok: listing.complete, bytes: this.model.usage() };
        } else {
          return json({ error: 'Action not found.', code: 'missing' }, 404);
        }
        this.persist();
      }
      return json(result);
    } catch (error) {
      return json({ error: error.message, code: error.code || 'invalid' }, statusFor(error));
    }
  }
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path.startsWith('/ytload/media/')) return handleMedia(request, env);
    if (path.startsWith('/ytload/media-control/')) return handleControl(request, env, Math.floor(Date.now() / 1000));
    return json({ error: 'Not found.' }, 404);
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil((async () => {
      const now = Math.floor(Date.now() / 1000);
      try {
        await ledger(env, '/maintenance', { now });
      } catch (error) {
        console.error(JSON.stringify({ event: 'r2_scheduled_cleanup_failed', error: error?.name || 'Error' }));
      }
    })());
  },
};
