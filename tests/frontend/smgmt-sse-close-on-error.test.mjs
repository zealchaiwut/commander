/**
 * Behavioral tests for issue #1817: _smgmtSseConnect must call es.close()
 * in both the `complete` and `onerror` handlers before nulling _smgmtSseEs.
 *
 * Without the fix the orphaned EventSource auto-reconnects in the background
 * and a second connect call (from the ≥15 s fallback poll) opens a duplicate
 * stream — leading to double-appended log_line events.
 *
 * Run with: node --test tests/frontend/smgmt-sse-close-on-error.test.mjs
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

// Extract the SSE connect/fallback block by pattern.
// Starts at the first `let _smgmtSseEs` declaration and ends at the closing `}`
// of `_smgmtSseStartFallback`, identified by the sentinel comment that follows.
const startMarker = 'const _smgmtSseEs = new Map();';
const endMarker = '/** Handle a \'snapshot\' event from the SSE stream.';
const startIdx = html.indexOf(startMarker);
const endIdx = html.indexOf(endMarker);
assert.ok(startIdx !== -1, 'start marker not found in project.html');
assert.ok(endIdx !== -1, 'end marker not found in project.html');
const sseBlock = html.slice(startIdx, endIdx).trimEnd();

/** Create a fresh VM context with mocked browser globals. */
function makeContext() {
  let closeCalls = 0;
  const esInstances = [];

  function MockEventSource(url) {
    this.url = url;
    this._listeners = {};
    this.onerror = null;
    this.close = () => { closeCalls++; };
    esInstances.push(this);
  }
  MockEventSource.prototype.addEventListener = function(type, fn) {
    this._listeners[type] = fn;
  };

  const ctx = vm.createContext({
    EventSource: MockEventSource,
    _smgmtSseHandleSnapshot: () => {},
    _smgmtSseHandleLogLine: () => {},
    visibilityInterval: (fn, delay) => null,
    setInterval: () => null,
    clearInterval: () => {},
    fetch: async () => ({ ok: false }),
  });

  vm.runInContext(sseBlock, ctx);

  return {
    ctx,
    esInstances,
    getCloseCalls: () => closeCalls,
  };
}

// ── AC1: complete handler must close the EventSource ─────────────────────────

test('complete handler: es.close() is called before _smgmtSseEs is nulled', () => {
  const { ctx, esInstances, getCloseCalls } = makeContext();

  vm.runInContext('_smgmtSseConnect("sprint-117", "owner/repo")', ctx);
  assert.equal(esInstances.length, 1, 'one EventSource created after connect');

  const es = esInstances[0];

  // Trigger the 'complete' SSE event
  es._listeners['complete']({});

  assert.equal(getCloseCalls(), 1,
    'es.close() must be called when the complete event fires (AC1)');
});

// ── AC2: onerror handler must close the EventSource ──────────────────────────

test('onerror handler: es.close() is called before _smgmtSseEs is nulled', () => {
  const { ctx, esInstances, getCloseCalls } = makeContext();

  vm.runInContext('_smgmtSseConnect("sprint-117", "owner/repo")', ctx);
  assert.equal(esInstances.length, 1, 'one EventSource created after connect');

  const es = esInstances[0];

  // Trigger the onerror handler
  es.onerror({});

  assert.equal(getCloseCalls(), 1,
    'es.close() must be called when onerror fires (AC2)');
});

// ── AC3: a second connect after complete does not reuse the orphaned stream ──

test('second connect after complete: orphan is closed, new stream opened', () => {
  const { ctx, esInstances, getCloseCalls } = makeContext();

  // First connect
  vm.runInContext('_smgmtSseConnect("sprint-117", "owner/repo")', ctx);
  const es1 = esInstances[0];

  // complete fires → should close es1 and null _smgmtSseEs
  es1._listeners['complete']({});

  assert.equal(getCloseCalls(), 1, 'es1.close() called by complete handler');

  // Second connect (e.g. from fallback poll)
  vm.runInContext('_smgmtSseConnect("sprint-117", "owner/repo")', ctx);
  assert.equal(esInstances.length, 2, 'second EventSource created');

  const es2 = esInstances[1];
  assert.ok(es2 !== es1, 'second connect must open a new EventSource, not reuse the orphan');
});
