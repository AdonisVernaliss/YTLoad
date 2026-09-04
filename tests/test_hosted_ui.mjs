import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { directLink, estimateState, selectedEstimate } from '../ytloadlib/static/hosted-ui.mjs';

const inspection = {
  size_estimates: {
    source: { '1080p': { bytes: 100, approximate: false, over_limit: false, near_limit: false } },
    compatible: { '1080p': { bytes: 120, approximate: true, over_limit: true, near_limit: false }, compatible: { bytes: null, approximate: false, over_limit: false, near_limit: false } },
  },
};

test('selected estimate follows the same source or compatible policy as downloads', () => {
  assert.equal(selectedEstimate(inspection, '1080p', 'auto').bytes, 100);
  assert.equal(selectedEstimate(inspection, '1080p', 'mkv').bytes, 100);
  assert.equal(selectedEstimate(inspection, '1080p', 'mp4').bytes, 120);
  assert.equal(selectedEstimate(inspection, 'compatible', 'mkv').bytes, null);
});

test('over-limit, near-limit and unknown estimates remain distinct', () => {
  assert.equal(estimateState({ bytes: 6, over_limit: true, near_limit: false }), 'over');
  assert.equal(estimateState({ bytes: 5, over_limit: false, near_limit: true }), 'near');
  assert.equal(estimateState({ bytes: null, over_limit: false, near_limit: false }), 'unknown');
  assert.equal(estimateState({ bytes: 4, over_limit: false, near_limit: false }), 'available');
});

test('direct action is a native no-referrer navigation', () => {
  const link = directLink({ url: 'https://r1.googlevideo.com/videoplayback?id=18' });
  assert.deepEqual(link, { href: 'https://r1.googlevideo.com/videoplayback?id=18', target: '_blank', rel: 'noopener noreferrer', referrerPolicy: 'no-referrer' });
  assert.equal(directLink(null), null);
});

test('public interface contains production limits without client media fetch or wasm', () => {
  const html = fs.readFileSync(new URL('../ytloadlib/static/index.html', import.meta.url), 'utf8');
  const app = fs.readFileSync(new URL('../ytloadlib/static/app.js', import.meta.url), 'utf8');
  assert.match(html, /5 GB maximum final file/);
  assert.match(html, /12 GB temporary session capacity/);
  assert.match(html, /files expire about 1 hour/);
  assert.doesNotMatch(app, /fetch\([^\n]*direct/i);
  assert.doesNotMatch(app, /ffmpeg\.wasm|OPFS/i);
});
