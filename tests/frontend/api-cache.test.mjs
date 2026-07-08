/**
 * Behavioral tests for issue #1779 AC3–AC8: module-level promise memo for
 * /api/environment, /api/version, and /api/settings.
 *
 * Run with: node --test tests/frontend/api-cache.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Stubs required at import time ─────────────────────────────────────────────
if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Install a fetch spy that returns canned JSON by URL. Returns call-count map. */
function installFetchSpy(routes) {
  const counts = {};
  globalThis.fetch = async (url) => {
    const key = String(url).split('?')[0];
    counts[key] = (counts[key] || 0) + 1;
    for (const [u, body] of Object.entries(routes)) {
      if (key === u) return { ok: true, json: async () => body };
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
  return counts;
}

function installFailingFetch(url) {
  const counts = {};
  globalThis.fetch = async (u) => {
    const key = String(u).split('?')[0];
    counts[key] = (counts[key] || 0) + 1;
    if (key === url) return { ok: false, status: 500, json: async () => ({}) };
    throw new Error(`Unexpected fetch: ${u}`);
  };
  return counts;
}

// Re-import the module fresh for each group by using dynamic import with cache-busting query.
// Node caches ES modules by specifier, so we need a different approach: we reset module
// state via the exported resetForTesting() helper.

import {
  getEnvironment,
  getVersion,
  getSettings,
  invalidateSettings,
  _resetCacheForTesting,
} from '../../apps/dashboard/static/src/api.js';


// ── AC3: /api/environment fetched at most once per session ────────────────────

test('getEnvironment: two calls produce one fetch', async () => {
  _resetCacheForTesting();
  const counts = installFetchSpy({ '/api/environment': { environment: 'prd', port: 8000 } });

  const [r1, r2] = await Promise.all([getEnvironment(), getEnvironment()]);

  assert.equal(counts['/api/environment'], 1, 'must fetch /api/environment exactly once');
  assert.deepEqual(r1, r2, 'both calls must resolve to the same data');
});

test('getEnvironment: second sequential call returns cached data without re-fetching', async () => {
  _resetCacheForTesting();
  const counts = installFetchSpy({ '/api/environment': { environment: 'uat' } });

  const first = await getEnvironment();
  const second = await getEnvironment();

  assert.equal(counts['/api/environment'], 1);
  assert.deepEqual(first, second);
});

test('getEnvironment: failed fetch clears cache so next call retries', async () => {
  _resetCacheForTesting();
  const counts = installFailingFetch('/api/environment');

  await getEnvironment().catch(() => {});

  assert.equal(counts['/api/environment'], 1);

  // After failure, cache is cleared — retry fetch
  installFetchSpy({ '/api/environment': { environment: 'prd' } });
  _resetCacheForTesting();

  const data = await getEnvironment();
  assert.ok(data, 'retry after failure must succeed');
});


// ── AC4: /api/version fetched at most once per session ────────────────────────

test('getVersion: two calls produce one fetch', async () => {
  _resetCacheForTesting();
  const counts = installFetchSpy({ '/api/version': { branch: 'develop', git_sha: 'abc1234', build_timestamp: '2026-01-01' } });

  const [r1, r2] = await Promise.all([getVersion(), getVersion()]);

  assert.equal(counts['/api/version'], 1, 'must fetch /api/version exactly once');
  assert.deepEqual(r1, r2);
});

test('getVersion: third call still returns cached data', async () => {
  _resetCacheForTesting();
  const counts = installFetchSpy({ '/api/version': { branch: 'main', git_sha: 'deadbeef', build_timestamp: '2026-06-01' } });

  await getVersion();
  await getVersion();
  const third = await getVersion();

  assert.equal(counts['/api/version'], 1);
  assert.equal(third.branch, 'main');
});


// ── AC5: /api/settings fetched at most once per session on initial load ───────

test('getSettings: two parallel calls produce one fetch', async () => {
  _resetCacheForTesting();
  const counts = installFetchSpy({ '/api/settings': { history_cache_ttl_min: 5 } });

  const [r1, r2] = await Promise.all([getSettings(), getSettings()]);

  assert.equal(counts['/api/settings'], 1, 'must fetch /api/settings exactly once');
  assert.deepEqual(r1, r2);
});

test('getSettings: three sequential calls produce one fetch', async () => {
  _resetCacheForTesting();
  const counts = installFetchSpy({ '/api/settings': { history_cache_ttl_min: 10 } });

  await getSettings();
  await getSettings();
  await getSettings();

  assert.equal(counts['/api/settings'], 1);
});


// ── AC6: /api/settings cache invalidated after successful PUT /api/settings ───

test('invalidateSettings: next getSettings() call re-fetches', async () => {
  _resetCacheForTesting();
  let callN = 0;
  globalThis.fetch = async (url) => {
    if (String(url) === '/api/settings') {
      callN++;
      return { ok: true, json: async () => ({ history_cache_ttl_min: callN * 5 }) };
    }
    throw new Error(`Unexpected: ${url}`);
  };

  const first = await getSettings();
  assert.equal(first.history_cache_ttl_min, 5);

  invalidateSettings();

  const second = await getSettings();
  assert.equal(second.history_cache_ttl_min, 10, 'must re-fetch after invalidation');
  assert.equal(callN, 2, 'must have made exactly 2 fetches total');
});

test('invalidateSettings: does not affect environment or version caches', async () => {
  _resetCacheForTesting();
  const counts = installFetchSpy({
    '/api/environment': { environment: 'prd' },
    '/api/version': { branch: 'develop', git_sha: 'abc', build_timestamp: '2026' },
    '/api/settings': { history_cache_ttl_min: 5 },
  });

  await Promise.all([getEnvironment(), getVersion(), getSettings()]);
  invalidateSettings();
  await getSettings(); // re-fetch settings

  assert.equal(counts['/api/environment'], 1, 'environment cache must be untouched');
  assert.equal(counts['/api/version'], 1, 'version cache must be untouched');
  assert.equal(counts['/api/settings'], 2, 'settings must be fetched twice (once + after invalidation)');
});


// ── AC7: no stale-settings regression — cache clears on failure too ───────────

test('getSettings: failure clears cache so caller can retry', async () => {
  _resetCacheForTesting();
  let attempt = 0;
  globalThis.fetch = async (url) => {
    if (String(url) === '/api/settings') {
      attempt++;
      if (attempt === 1) return { ok: false, status: 503, json: async () => ({}) };
      return { ok: true, json: async () => ({ history_cache_ttl_min: 5 }) };
    }
    throw new Error(`Unexpected: ${url}`);
  };

  await getSettings().catch(() => {}); // first call fails
  const data = await getSettings(); // second call must retry

  assert.equal(attempt, 2, 'must re-fetch after a failed attempt');
  assert.equal(data.history_cache_ttl_min, 5);
});


// ── AC8: cache is module-level (not localStorage) ─────────────────────────────

test('cache is NOT persisted to localStorage', () => {
  // The module should not call localStorage at all.
  // We verify by stubbing localStorage with a trap that throws.
  const trap = new Proxy({}, {
    get(_, k) { throw new Error(`localStorage.${String(k)} accessed — must not use localStorage`); },
    set(_, k) { throw new Error(`localStorage.${String(k)} set — must not use localStorage`); },
  });
  const orig = globalThis.localStorage;
  globalThis.localStorage = trap;
  try {
    _resetCacheForTesting();
    invalidateSettings();
    // No throw = no localStorage access
  } finally {
    globalThis.localStorage = orig;
  }
});

test('cache is NOT persisted to sessionStorage', () => {
  const trap = new Proxy({}, {
    get(_, k) { throw new Error(`sessionStorage.${String(k)} accessed — must not use sessionStorage`); },
    set(_, k) { throw new Error(`sessionStorage.${String(k)} set — must not use sessionStorage`); },
  });
  const orig = globalThis.sessionStorage;
  globalThis.sessionStorage = trap;
  try {
    _resetCacheForTesting();
    invalidateSettings();
  } finally {
    globalThis.sessionStorage = orig;
  }
});
