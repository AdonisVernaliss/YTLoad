import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const core = await import('../cloudflare/media-worker/src/core.mjs');
const { CapacityModel, MAX_MEDIA_BYTES, createTicket, verifyTicket, parseRange, contentDisposition, handleMedia } = core;
const workerModule = await import('../cloudflare/media-worker/src/index.mjs');
const { CapacityLedger, authenticateControl, createControlAuthorization, deleteBatchObjects } = workerModule;
const mediaWorker = workerModule.default;

test('capacity configuration enforces the compiled cap and reconciliation age', () => {
  assert.doesNotThrow(() => new CapacityModel(8_000_000_000, null, 1_800));
  assert.doesNotThrow(() => new CapacityModel(7_000_000_000, null, 1));
  assert.throws(() => new CapacityModel(8_000_000_001, null, 1_800), /capacity/i);
  for (const value of [0, -1, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => new CapacityModel(100, null, value), /reconciliation/i);
  }
  const model = new CapacityModel(8_000_000_000);
  const id = '0'.repeat(32);
  const key = 'media/' + id + '/' + '1'.repeat(32);
  model.reconcile([], 1, true);
  assert.throws(() => model.reserve(id, [{ key, name: 'too-large.mp4', size: MAX_MEDIA_BYTES + 1, contentType: 'video/mp4' }], 2), /reservation/i);
});

test('capacity admission includes ready objects and full active reservations', () => {
  const model = new CapacityModel(8_000_000_000);
  model.reconcile([], 1_000, true);
  const firstKey = `media/${'a'.repeat(32)}/${'1'.repeat(32)}`;
  const secondKey = `media/${'b'.repeat(32)}/${'2'.repeat(32)}`;
  model.reserve('a'.repeat(32), [{ key: firstKey, name: 'one.mp4', size: 5_000_000_000, contentType: 'video/mp4' }], 1_001);
  model.registerMultipart('a'.repeat(32), firstKey, 'upload-a', 1_002);
  assert.equal(model.usage(), 5_000_000_000);
  assert.throws(() => model.reserve('b'.repeat(32), [{ key: secondKey, name: 'two.mp4', size: 3_000_000_001, contentType: 'video/mp4' }], 1_003), /capacity/i);
  model.completeFile('a'.repeat(32), firstKey, 5_000_000_000, 1_004);
  model.commit('a'.repeat(32), 1_005, 3_600);
  assert.equal(model.usage(), 5_000_000_000);
  model.reserve('b'.repeat(32), [{ key: secondKey, name: 'two.mp4', size: 3_000_000_000, contentType: 'video/mp4' }], 1_006);
  assert.equal(model.usage(), 8_000_000_000);
});

test('capacity fails closed before a fresh complete reconciliation', () => {
  const model = new CapacityModel(8_000_000_000);
  const key = `media/${'a'.repeat(32)}/${'1'.repeat(32)}`;
  assert.throws(() => model.reserve('a'.repeat(32), [{ key, name: 'one', size: 1, contentType: 'application/octet-stream' }], 1), /reconcile/i);
  model.reconcile([], 1, false);
  assert.throws(() => model.reserve('a'.repeat(32), [{ key, name: 'one', size: 1, contentType: 'application/octet-stream' }], 2), /reconcile/i);
});

test('reconciliation accounts for unknown objects and stale cleanup plans', () => {
  const model = new CapacityModel(100);
  const orphan = `media/${'9'.repeat(32)}/${'8'.repeat(32)}`;
  const key = `media/${'c'.repeat(32)}/${'3'.repeat(32)}`;
  model.reconcile([{ key: orphan, size: 40, uploaded: 100 }], 200, true);
  assert.equal(model.usage(), 40);
  assert.throws(() => model.reserve('c'.repeat(32), [{ key, name: 'file', size: 61, contentType: 'application/octet-stream' }], 201), /capacity/i);
  assert.deepEqual(model.cleanupPlans(2_001, 1_000, 1_000).orphans.map((item) => item.key), [orphan]);
  model.releaseOrphan(orphan);
  assert.equal(model.usage(), 0);
});

test('ready lifetime starts at commit and expired ready batches are cleaned', () => {
  const model = new CapacityModel(100);
  const id = 'd'.repeat(32);
  const key = `media/${id}/${'4'.repeat(32)}`;
  model.reconcile([], 1, true);
  model.reserve(id, [{ key, name: 'file.mp4', size: 10, contentType: 'video/mp4' }], 100);
  model.registerMultipart(id, key, 'upload-d', 200);
  model.completeFile(id, key, 10, 300);
  const ready = model.commit(id, 500, 3_600);
  assert.equal(ready.readyAt, 500);
  assert.equal(ready.expiresAt, 4_100);
  assert.equal(model.cleanupPlans(4_099, 900, 900).batches.length, 0);
  assert.equal(model.cleanupPlans(4_101, 900, 900).batches[0].id, id);
});

test('tickets are object-bound, signed and expire', async () => {
  const secret = 's'.repeat(48);
  const ticket = await createTicket({ key: `media/${'e'.repeat(32)}/${'5'.repeat(32)}`, size: 10, expiresAt: 2_000 }, secret);
  assert.equal((await verifyTicket(ticket, secret, 1_999)).size, 10);
  await assert.rejects(() => verifyTicket(ticket.slice(0, -1) + 'x', secret, 1_999), /ticket/i);
  await assert.rejects(() => verifyTicket(ticket, secret, 2_000), /expired/i);
  assert.doesNotMatch(ticket, /Users|\\\\|\.\./);
});

test('single browser ranges produce 206 and invalid or multiple ranges produce 416', () => {
  assert.deepEqual(parseRange(null, 10), null);
  assert.deepEqual(parseRange('bytes=2-5', 10), { offset: 2, length: 4, start: 2, end: 5 });
  assert.deepEqual(parseRange('bytes=-3', 10), { offset: 7, length: 3, start: 7, end: 9 });
  assert.throws(() => parseRange('bytes=99-', 10), /range/i);
  assert.throws(() => parseRange('bytes=0-1,4-5', 10), /range/i);
});

test('content disposition is safe for unicode and control characters', () => {
  const value = contentDisposition('A\r\nфайл.mp4');
  assert.doesNotMatch(value, /\r|\n/);
  assert.match(value, /filename\*=UTF-8''/);
  assert.match(value, /^attachment;/);
});

test('media handler streams valid tickets, supports HEAD and ranges, and fails closed', async () => {
  const secret = 't'.repeat(48);
  const key = `media/${'f'.repeat(32)}/${'6'.repeat(32)}`;
  const ticket = await createTicket({ key, size: 10, expiresAt: 2_000 }, secret);
  const body = new Uint8Array(Buffer.from('0123456789'));
  const bucket = {
    async head(requested) {
      if (requested !== key) return null;
      return { size: 10, httpMetadata: { contentType: 'video/mp4' }, customMetadata: { filename: 'sample.mp4' } };
    },
    async get(requested, options = {}) {
      if (requested !== key) return null;
      const range = options.range;
      const bytes = range ? body.slice(range.offset, range.offset + range.length) : body;
      return { size: 10, body: new Blob([bytes]).stream(), httpMetadata: { contentType: 'video/mp4' }, customMetadata: { filename: 'sample.mp4' } };
    },
  };
  const base = 'https://downloads.example.com/ytload/media/' + ticket;
  const rateLimiter = { async limit() { return { success: true }; } };
  const mediaEnv = { MEDIA_BUCKET: bucket, MEDIA_TICKET_SECRET: secret, MEDIA_RATE_LIMITER: rateLimiter };
  const full = await handleMedia(new Request(base), mediaEnv, 1_999);
  assert.equal(full.status, 200);
  assert.equal(await full.text(), '0123456789');
  assert.equal(full.headers.get('Content-Length'), '10');
  assert.equal(full.headers.get('Cache-Control'), 'private, no-store');
  const head = await handleMedia(new Request(base, { method: 'HEAD' }), mediaEnv, 1_999);
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
  const partial = await handleMedia(new Request(base, { headers: { Range: 'bytes=2-5' } }), mediaEnv, 1_999);
  assert.equal(partial.status, 206);
  assert.equal(await partial.text(), '2345');
  assert.equal(partial.headers.get('Content-Range'), 'bytes 2-5/10');
  const invalid = await handleMedia(new Request(base, { headers: { Range: 'bytes=99-' } }), mediaEnv, 1_999);
  assert.equal(invalid.status, 416);
  assert.equal(invalid.headers.get('Content-Range'), 'bytes */10');
  const unavailable = await handleMedia(new Request(base), { MEDIA_TICKET_SECRET: secret }, 1_999);
  assert.equal(unavailable.status, 503);
});

test('media handler fails closed and returns 429 when the ticket request budget is exhausted', async () => {
  const secret = 'r'.repeat(48);
  const key = 'media/' + '4'.repeat(32) + '/' + '5'.repeat(32);
  const ticket = await createTicket({ key, size: 10, expiresAt: 2_000 }, secret);
  let reads = 0;
  const response = await handleMedia(new Request('https://downloads.example.com/ytload/media/' + ticket), {
    MEDIA_TICKET_SECRET: secret,
    MEDIA_RATE_LIMITER: { async limit() { return { success: false }; } },
    MEDIA_BUCKET: { async get() { reads += 1; return null; } },
  }, 1_999);
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('Retry-After'), '60');
  assert.equal(reads, 0);
  const unavailable = await handleMedia(new Request('https://downloads.example.com/ytload/media/' + ticket), {
    MEDIA_TICKET_SECRET: secret,
    MEDIA_BUCKET: { async get() { return null; } },
  }, 1_999);
  assert.equal(unavailable.status, 503);
});

test('active multipart reservations become cleanup work after their heartbeat expires', () => {
  const model = new CapacityModel(100);
  const id = '7'.repeat(32);
  const key = `media/${id}/${'6'.repeat(32)}`;
  model.reconcile([], 1, true);
  model.reserve(id, [{ key, name: 'file.mp4', size: 10, contentType: 'video/mp4' }], 100);
  model.registerMultipart(id, key, 'upload-stale', 200);
  assert.equal(model.cleanupPlans(1_199, 1_000, 1_000).batches.length, 0);
  const plan = model.cleanupPlans(1_201, 1_000, 1_000).batches[0];
  assert.equal(plan.files[0].uploadId, 'upload-stale');
  assert.throws(() => model.touch(id, 1_202), /cleanup/i);
});

test('manual cleanup closes the batch before external deletion', () => {
  const model = new CapacityModel(100);
  const id = '6'.repeat(32);
  const key = 'media/' + id + '/' + '5'.repeat(32);
  model.reconcile([], 1, true);
  model.reserve(id, [{ key, name: 'file.mp4', size: 10, contentType: 'video/mp4' }], 2);
  const plan = model.beginCleanup(id);
  assert.equal(plan.status, 'cleanup_pending');
  assert.throws(() => model.reserve(id, [{ key, name: 'file.mp4', size: 10, contentType: 'video/mp4' }], 3), /cleanup/i);
  assert.throws(() => model.touch(id, 3), /cleanup/i);
  assert.throws(() => model.registerMultipart(id, key, 'late-upload', 3), /cleanup/i);
  assert.throws(() => model.completeFile(id, key, 10, 3), /cleanup/i);
  assert.throws(() => model.commit(id, 3, 3_600), /cleanup/i);
});

test('control authentication binds method, path, timestamp, nonce and body', async () => {
  const secret = 'c'.repeat(48);
  const timestamp = '2000000000';
  const nonce = 'a'.repeat(32);
  const path = '/ytload/media-control/reserve';
  const body = new TextEncoder().encode('{"id":"value"}');
  const authorization = await createControlAuthorization('POST', path, body, secret, timestamp, nonce);
  const request = new Request(`https://downloads.example.com${path}`, {
    method: 'POST',
    headers: {
      Authorization: authorization,
      'X-YTLoad-Timestamp': timestamp,
      'X-YTLoad-Nonce': nonce,
    },
  });
  assert.equal(await authenticateControl(request, body, { MEDIA_CONTROL_SECRET: secret }, Number(timestamp)), true);
  assert.equal(await authenticateControl(request, new TextEncoder().encode('{}'), { MEDIA_CONTROL_SECRET: secret }, Number(timestamp)), false);
  assert.equal(await authenticateControl(request, body, { MEDIA_CONTROL_SECRET: secret }, Number(timestamp) + 301), false);
});

test('scheduled cleanup delegates expensive work to the capacity Durable Object', async () => {
  const calls = [];
  const env = {
    CAPACITY_LEDGER: {
      idFromName(name) {
        assert.equal(name, 'global');
        return 'ledger-id';
      },
      get(id) {
        assert.equal(id, 'ledger-id');
        return {
          async fetch(url, options) {
            calls.push({ url, options });
            return new Response(JSON.stringify({ ok: true }), { headers: { 'Content-Type': 'application/json' } });
          },
        };
      },
    },
  };
  let work;
  mediaWorker.scheduled({}, env, { waitUntil(promise) { work = promise; } });
  await work;
  assert.equal(calls.length, 1);
  assert.equal(new URL(calls[0].url).pathname, '/maintenance');
  assert.equal(calls[0].options.method, 'POST');
});

test('unregistered multipart uncertainty retains capacity until explicitly confirmed', async () => {
  const id = '8'.repeat(32);
  const key = 'media/' + id + '/' + '9'.repeat(32);
  const batch = { id, createdAt: 100, files: [{ key, completed: false, uploadId: null }] };
  const bucket = {
    async head() { return null; },
    async delete() { throw new Error('nothing should be deleted'); },
  };
  assert.equal(await deleteBatchObjects(bucket, batch, []), false);
  assert.equal(await deleteBatchObjects(bucket, batch, [key]), true);
  assert.equal(await deleteBatchObjects(bucket, batch, []), false);
});

test('failed multipart abort retains capacity without authoritative confirmation', async () => {
  const id = '9'.repeat(32);
  const key = 'media/' + id + '/' + 'a'.repeat(32);
  const batch = { id, files: [{ key, completed: false, uploadId: 'upload-1' }] };
  const bucket = {
    resumeMultipartUpload() { return { async abort() { throw new Error('temporary failure'); } }; },
    async head() { return null; },
    async delete() { throw new Error('nothing should be deleted'); },
  };
  assert.equal(await deleteBatchObjects(bucket, batch, []), false);
  assert.equal(await deleteBatchObjects(bucket, batch, [key]), true);
});

test('durable-object reconciliation serializes concurrent completion', async () => {
  let persisted = null;
  let initialize;
  let resolveListing;
  const bucket = {
    list() {
      return new Promise((resolve) => {
        resolveListing = resolve;
      });
    },
  };
  const sql = {
    exec(statement, ...values) {
      if (statement.startsWith('SELECT')) return persisted == null ? [] : [{ value: persisted }];
      if (statement.startsWith('INSERT')) persisted = values[0];
      return [];
    },
  };
  const ctx = {
    storage: { sql },
    blockConcurrencyWhile(operation) {
      initialize = Promise.resolve().then(operation);
      return initialize;
    },
  };
  const ledger = new CapacityLedger(ctx, {
    MEDIA_BUCKET: bucket,
    R2_HARD_CAP_BYTES: '100',
    RECONCILE_MAX_AGE_SECONDS: '1800',
  });
  await initialize;
  const id = 'a'.repeat(32);
  const key = 'media/' + id + '/' + 'b'.repeat(32);
  ledger.model.reconcile([], 1, true);
  ledger.model.reserve(id, [{ key, name: 'file.mp4', size: 10, contentType: 'video/mp4' }], 2);
  ledger.model.registerMultipart(id, key, 'upload-1', 3);
  const reconcile = ledger.fetch(new Request('https://ledger.internal/reconcile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ now: 4 }),
  }));
  while (!resolveListing) await new Promise((resolve) => setTimeout(resolve, 0));
  let completionFinished = false;
  const completion = ledger.fetch(new Request('https://ledger.internal/complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, key, size: 10, now: 5 }),
  })).then((response) => {
    completionFinished = true;
    return response;
  });
  await Promise.resolve();
  assert.equal(completionFinished, false);
  resolveListing({ objects: [], truncated: false });
  assert.equal((await reconcile).status, 200);
  assert.equal((await completion).status, 200);
  assert.equal(ledger.model.batch(id).files[0].completed, true);
  assert.notEqual(ledger.model.batch(id).status, 'cleanup_pending');
});
