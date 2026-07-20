/**
 * Behavioral tests for issue #1818: _smgmtLivePollRestart must connect one SSE
 * stream per running label, not just Array.from(_smgmtRunningLabels)[0].
 *
 * AC1: When _smgmtRunningLabels has N labels, _smgmtLivePollRestart opens N
 *      distinct EventSource connections (one per label).
 * AC2: An SSE error/complete on one label does not disconnect the other labels'
 *      streams — each label's connection is independent.
 * AC3: _smgmtSseDisconnect closes all open EventSource connections and empties
 *      the internal _smgmtSseEs map.
 *
 * Run with: node --test tests/frontend/smgmt-sse-all-labels.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dir = dirname(fileURLToPath(import.meta.url));
const htmlPath = join(__dir, '../../apps/dashboard/static/project.html');
const html = readFileSync(htmlPath, 'utf8');

// ── Extract SSE infrastructure block ─────────────────────────────────────────
// Starts at the Map-based state declaration (issue #1818) and ends just before
// the snapshot handler (which uses globals not available in this test context).
const sseStartMarker = 'const _smgmtSseEs = new Map();';
const sseEndMarker = "/** Handle a 'snapshot' event from the SSE stream.";
const sseStartIdx = html.indexOf(sseStartMarker);
const sseEndIdx = html.indexOf(sseEndMarker);
assert.ok(sseStartIdx !== -1, 'SSE start marker not found — was _smgmtSseEs changed from Map() init?');
assert.ok(sseEndIdx !== -1, 'SSE end marker not found in project.html');
const sseBlock = html.slice(sseStartIdx, sseEndIdx).trimEnd();

// ── Extract _smgmtLivePollRestart function ────────────────────────────────────
const pollStartMarker = 'function _smgmtLivePollRestart() {';
const pollEndMarker = '/** First-paint for the Running view via single /api/running snapshot';
const pollStartIdx = html.indexOf(pollStartMarker);
const pollEndIdx = html.indexOf(pollEndMarker);
assert.ok(pollStartIdx !== -1, 'poll restart start marker not found in project.html');
assert.ok(pollEndIdx !== -1, 'poll restart end marker not found in project.html');
const pollBlock = html.slice(pollStartIdx, pollEndIdx).trimEnd();

/** Build a fresh VM context with the given running labels. */
function makeContext(runningLabels) {
  const esInstances = [];
  const closedUrls = [];

  function MockEventSource(url) {
    this.url = url;
    this._listeners = {};
    this.onerror = null;
    this.close = () => { closedUrls.push(url); };
    esInstances.push(this);
  }
  MockEventSource.prototype.addEventListener = function(type, fn) {
    this._listeners[type] = fn;
  };

  const ctx = vm.createContext({
    EventSource: MockEventSource,
    _smgmtSseHandleSnapshot: () => {},
    _smgmtSseHandleLogLine: () => {},
    visibilityInterval: (fn, delay) => 999,
    setInterval: () => 999,
    clearInterval: () => {},
    fetch: async () => ({ ok: false }),
    // Globals consumed by _smgmtLivePollRestart
    _smgmtRunningLabels: new Set(runningLabels),
    _smgmtLivePollId: null,
    _smgmtLogPollId: null,
    _smgmtTimelineRefreshId: null,
    _smgmtRepo: () => 'owner/repo',
    _SMGMT_TIMELINE_REFRESH_MS: 600000,
    _smgmtTimelineRefreshTick: () => {},
    _smgmtLingerPollRestart: () => {},
    _smgmtRunningFirstPaint: () => {},
  });

  vm.runInContext(sseBlock, ctx);
  vm.runInContext(pollBlock, ctx);

  return { ctx, esInstances, closedUrls };
}

// ── AC1: one SSE connection per running label ─────────────────────────────────

test('_smgmtLivePollRestart: opens one EventSource per running label (AC1)', () => {
  const { ctx, esInstances } = makeContext(['sprint-117', 'sprint-118']);

  vm.runInContext('_smgmtLivePollRestart()', ctx);

  assert.equal(esInstances.length, 2,
    'must open two EventSource connections for two running labels');

  const urls = esInstances.map(e => e.url);
  assert.ok(urls.some(u => u.includes('sprint-117')),
    'sprint-117 must have its own SSE connection');
  assert.ok(urls.some(u => u.includes('sprint-118')),
    'sprint-118 must have its own SSE connection');
});

test('_smgmtLivePollRestart: single running label still opens exactly one connection (AC1)', () => {
  const { ctx, esInstances } = makeContext(['sprint-100']);

  vm.runInContext('_smgmtLivePollRestart()', ctx);

  assert.equal(esInstances.length, 1,
    'single running label must open exactly one connection');
  assert.ok(esInstances[0].url.includes('sprint-100'));
});

// ── AC2: one label's error does not disconnect the other label's stream ────────

test('SSE onerror on one label leaves the other label connected (AC2)', () => {
  const { ctx, esInstances } = makeContext(['sprint-117', 'sprint-118']);

  vm.runInContext('_smgmtLivePollRestart()', ctx);
  assert.equal(esInstances.length, 2);

  const es117 = esInstances.find(e => e.url.includes('sprint-117'));
  assert.ok(es117, 'EventSource for sprint-117 must exist');

  // Trigger the error handler for sprint-117
  es117.onerror({});

  // sprint-118 must still be in the active map
  const has118 = vm.runInContext('_smgmtSseEs.has("sprint-118")', ctx);
  assert.ok(has118, 'sprint-118 stream must remain connected after sprint-117 errors (AC2)');

  const mapSize = vm.runInContext('_smgmtSseEs.size', ctx);
  assert.equal(mapSize, 1, 'exactly one label must remain after the other errors');
});

test('SSE complete on one label leaves the other label connected (AC2)', () => {
  const { ctx, esInstances } = makeContext(['sprint-117', 'sprint-118']);

  vm.runInContext('_smgmtLivePollRestart()', ctx);

  const es118 = esInstances.find(e => e.url.includes('sprint-118'));
  assert.ok(es118, 'EventSource for sprint-118 must exist');

  // Trigger the complete event for sprint-118
  es118._listeners['complete']({});

  const has117 = vm.runInContext('_smgmtSseEs.has("sprint-117")', ctx);
  assert.ok(has117, 'sprint-117 stream must remain connected after sprint-118 completes (AC2)');
});

// ── AC3: _smgmtSseDisconnect closes all open connections ─────────────────────

test('_smgmtSseDisconnect closes all EventSource connections (AC3)', () => {
  const { ctx, esInstances, closedUrls } = makeContext(['sprint-117', 'sprint-118']);

  vm.runInContext('_smgmtLivePollRestart()', ctx);
  assert.equal(esInstances.length, 2);

  vm.runInContext('_smgmtSseDisconnect()', ctx);

  assert.equal(closedUrls.length, 2,
    'both EventSources must be closed by _smgmtSseDisconnect (AC3)');

  const mapSize = vm.runInContext('_smgmtSseEs.size', ctx);
  assert.equal(mapSize, 0, '_smgmtSseEs map must be empty after disconnect');
});
