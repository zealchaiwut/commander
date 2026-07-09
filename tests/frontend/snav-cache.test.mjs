/**
 * Behavioral tests for snavNavStatusFetch (issue #1776 AC4).
 *
 * Verifies that /api/sprint-nav-status responses are cached within a short TTL
 * so snavRefresh() and statusRefresh() firing in the same 30s window do not
 * make duplicate network calls for the current repo.
 *
 * Run with: node --test tests/frontend/snav-cache.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { snavNavStatusFetch, snavNavStatusCacheClear } from
  '../../apps/dashboard/static/src/shell/snav-cache.js';

// Reset cache between tests so results are independent
function setup() {
  snavNavStatusCacheClear();
}

// ── AC4: responses are cached within the TTL ──────────────────────────────────

test('AC4: first call hits the network', async () => {
  setup();
  let hits = 0;
  globalThis.fetch = async (url) => {
    hits++;
    return { ok: true, json: async () => ({ has_sprint: true, sprint: 42, url }) };
  };
  await snavNavStatusFetch('/api/sprint-nav-status?repo=owner%2Frepo');
  assert.equal(hits, 1, 'first call must hit the network');
});

test('AC4: second call within TTL returns cached data without a network round-trip', async () => {
  setup();
  let hits = 0;
  const payload = { has_sprint: true, sprint: 42 };
  globalThis.fetch = async () => {
    hits++;
    return { ok: true, json: async () => payload };
  };
  const url = '/api/sprint-nav-status?repo=owner%2Frepo';
  const r1 = await snavNavStatusFetch(url);
  const r2 = await snavNavStatusFetch(url);
  assert.equal(hits, 1, 'second call within TTL must not hit the network');
  assert.deepEqual(r2, payload, 'cached response must equal the first response');
});

test('AC4: different URLs are cached independently', async () => {
  setup();
  let hits = 0;
  globalThis.fetch = async (url) => {
    hits++;
    return { ok: true, json: async () => ({ url }) };
  };
  const url1 = '/api/sprint-nav-status?repo=owner%2Frepo1';
  const url2 = '/api/sprint-nav-status?repo=owner%2Frepo2';
  await snavNavStatusFetch(url1);
  await snavNavStatusFetch(url2);
  assert.equal(hits, 2, 'two distinct URLs must each hit the network once');
  // Cached second call for each
  await snavNavStatusFetch(url1);
  await snavNavStatusFetch(url2);
  assert.equal(hits, 2, 'subsequent calls for both URLs must use cache (still 2 hits)');
});

test('AC4: snavNavStatusCacheClear() with URL clears only that entry', async () => {
  setup();
  let hits = 0;
  globalThis.fetch = async (url) => {
    hits++;
    return { ok: true, json: async () => ({ url }) };
  };
  const url1 = '/api/sprint-nav-status?repo=a';
  const url2 = '/api/sprint-nav-status?repo=b';
  await snavNavStatusFetch(url1);
  await snavNavStatusFetch(url2);
  snavNavStatusCacheClear(url1);
  await snavNavStatusFetch(url1); // should re-fetch
  await snavNavStatusFetch(url2); // still cached
  assert.equal(hits, 3, 'clearing url1 forces a re-fetch; url2 stays cached');
});

test('AC4: snavNavStatusCacheClear() with no args clears all entries', async () => {
  setup();
  let hits = 0;
  globalThis.fetch = async (url) => {
    hits++;
    return { ok: true, json: async () => ({ url }) };
  };
  const url1 = '/api/sprint-nav-status?repo=x';
  const url2 = '/api/sprint-nav-status?repo=y';
  await snavNavStatusFetch(url1);
  await snavNavStatusFetch(url2);
  snavNavStatusCacheClear();
  await snavNavStatusFetch(url1);
  await snavNavStatusFetch(url2);
  assert.equal(hits, 4, 'clearing all entries forces re-fetch for both URLs');
});

test('AC4: a non-ok response throws and does not cache', async () => {
  setup();
  let hits = 0;
  globalThis.fetch = async () => {
    hits++;
    return { ok: false, status: 503, json: async () => ({}) };
  };
  const url = '/api/sprint-nav-status?repo=err';
  await assert.rejects(() => snavNavStatusFetch(url), /HTTP 503/);
  // Second call must also hit network (error not cached)
  await assert.rejects(() => snavNavStatusFetch(url), /HTTP 503/);
  assert.equal(hits, 2, 'error responses must not be cached');
});
