import test from 'node:test';
import assert from 'node:assert/strict';
import { parseLinks } from '../ytloadlib/static/links.mjs';

test('pasted Markdown and angle links become valid URL inputs', () => {
  assert.deepEqual(parseLinks('[A shared video](https://youtube.com/shorts/AbCdEf123_-?si=share)\n<https://youtu.be/AbCdEf123_->'), [
    'https://youtube.com/shorts/AbCdEf123_-?si=share',
    'https://youtu.be/AbCdEf123_-',
  ]);
});

test('link lists are deduplicated without changing signed query parameters', () => {
  const url = 'https://example.org/video?si=signature&token=value';
  assert.deepEqual(parseLinks(`  ${url}\n${url}  `), [url]);
});

test('invalid tokens are retained for validation instead of silently discarded', () => {
  assert.deepEqual(parseLinks('bad-input https://example.org/video'), ['bad-input', 'https://example.org/video']);
});
