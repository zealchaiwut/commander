/**
 * Behavioral tests for the Dev Report live auto-refresh (issue #2023).
 *
 * AC1: startDevReportAutoRefresh starts a visibility-aware interval that calls
 *      fetchFn() on each tick.
 * AC2: While the tab is hidden, fetchFn is never called.
 * AC3: The onUpdate callback fires after each successful fetchFn invocation.
 * AC4: The exported stop function halts the interval; REPORT_REFRESH_INTERVAL_MS
 *      is exported and in the sane range (15000-30000 ms).
 * AC4b: On visibility restore, the auto-refresh resumes (via visibilityInterval
 *       catch-up behavior).
 *
 * Run with: node --test tests/frontend/devreport-live-2023.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Stubs ─────────────────────────────────────────────────────────────────────

function makeDocStub() {
  let _hidden = false;
  const _listeners = [];

  return {
    get hidden() { return _hidden; },
    addEventListener(event, fn) {
      if (event === 'visibilitychange') _listeners.push(fn);
    },
    removeEventListener(event, fn) {
      if (event !== 'visibilitychange') return;
      const i = _listeners.indexOf(fn);
      if (i !== -1) _listeners.splice(i, 1);
    },
    listenerCount() { return _listeners.length; },
    hide() {
      _hidden = true;
      for (const fn of [..._listeners]) fn();
    },
    show() {
      _hidden = false;
      for (const fn of [..._listeners]) fn();
    },
  };
}

function makeTimers() {
  let _nextId = 1;
  const _active = new Map();

  return {
    setInterval(fn, _delay) {
      const id = _nextId++;
      _active.set(id, fn);
      return id;
    },
    clearInterval(id) {
      _active.delete(id);
    },
    has(id) {
      return _active.has(id);
    },
    activeCount() { return _active.size; },
    async tickAll() {
      const calls = [];
      for (const fn of [..._active.values()]) {
        calls.push(Promise.resolve(fn()));
      }
      await Promise.all(calls);
    },
  };
}

// ── Set up stubs BEFORE import so the module picks them up ───────────────────

let _docStub = makeDocStub();
if (typeof globalThis.window === 'undefined') globalThis.window = globalThis;
globalThis.document = _docStub;

// Import visibility.js and install the guard BEFORE importing live-refresh.js
const { installVisibilityGuard } = await import(
  '../../apps/dashboard/static/src/shell/visibility.js'
);
installVisibilityGuard();

// Import the module under test (ESM is cached — module captures globals at
// call time, not import time, so per-test overrides are picked up inside fn bodies).
const { startDevReportAutoRefresh, REPORT_REFRESH_INTERVAL_MS } = await import(
  '../../apps/dashboard/static/src/home/live-refresh.js'
);

// ── Per-test setup ────────────────────────────────────────────────────────────

function setup() {
  _docStub = makeDocStub();
  globalThis.document = _docStub;
  const timers = makeTimers();

  // Keep a reference to the original (patched) clearInterval so we can delegate to it
  const origClearInterval = globalThis.clearInterval;

  globalThis.setInterval = timers.setInterval.bind(timers);
  globalThis.clearInterval = (id) => {
    // If it's one of our timer IDs, clear it; otherwise delegate to the patched version
    if (timers.has && timers.has(id)) {
      timers.clearInterval(id);
    } else {
      origClearInterval(id);
    }
  };

  return { doc: _docStub, timers };
}

// ── AC1: auto-refresh triggers N fetches over N intervals ─────────────────────

test('AC1: startDevReportAutoRefresh calls fetchFn on first tick', async () => {
  const { timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => { fetchCount++; };

  startDevReportAutoRefresh({ fetchFn });
  assert.equal(fetchCount, 0, 'must not call fetchFn immediately on creation');

  await timers.tickAll();
  assert.equal(fetchCount, 1, 'must call fetchFn on first tick');
});

test('AC1: successive ticks call fetchFn each time', async () => {
  const { timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => { fetchCount++; };

  startDevReportAutoRefresh({ fetchFn });
  await timers.tickAll();  // tick 1
  await timers.tickAll();  // tick 2
  await timers.tickAll();  // tick 3
  assert.equal(fetchCount, 3, 'must call fetchFn on each tick');
});

// ── AC2: while hidden, no fetch; resumes on visibility ──────────────────────────

test('AC2: fetchFn does not fire while tab is hidden', async () => {
  const { doc, timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => { fetchCount++; };

  doc.hide();  // hide BEFORE creating the refresh
  startDevReportAutoRefresh({ fetchFn });
  await timers.tickAll();
  await timers.tickAll();
  assert.equal(fetchCount, 0, 'must not call fetchFn while hidden');
});

test('AC2: fetchFn stops firing when tab becomes hidden', async () => {
  const { doc, timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => { fetchCount++; };

  startDevReportAutoRefresh({ fetchFn });
  await timers.tickAll();  // tick 1 (visible)
  assert.equal(fetchCount, 1);

  doc.hide();
  await timers.tickAll();  // would be tick 2, but hidden
  await timers.tickAll();  // would be tick 3, but hidden
  assert.equal(fetchCount, 1, 'must not call fetchFn after tab is hidden');
});

test('AC4b: resumes fetching when tab becomes visible again', async () => {
  const { doc, timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => { fetchCount++; };

  startDevReportAutoRefresh({ fetchFn });
  doc.hide();
  fetchCount = 0;  // reset

  doc.show();  // triggers catch-up tick + resumes interval
  assert.equal(fetchCount, 1, 'must fire catch-up tick on visibility restore');

  await timers.tickAll();  // one more regular tick
  assert.equal(fetchCount, 2, 'must resume regular ticks after visibility restore');
});

// ── AC3: onUpdate callback fires after each successful tick ────────────────────

test('AC3: onUpdate callback is invoked after each tick', async () => {
  const { timers } = setup();
  let updateCount = 0;
  const fetchFn = async () => {};
  const onUpdate = () => { updateCount++; };

  startDevReportAutoRefresh({ fetchFn, onUpdate });
  await timers.tickAll();  // tick 1
  assert.equal(updateCount, 1, 'must call onUpdate after tick 1');

  await timers.tickAll();  // tick 2
  assert.equal(updateCount, 2, 'must call onUpdate after tick 2');
});

test('AC3: onUpdate receives an ISO timestamp string', async () => {
  const { timers } = setup();
  let receivedTs = null;
  const fetchFn = async () => {};
  const onUpdate = (ts) => { receivedTs = ts; };

  startDevReportAutoRefresh({ fetchFn, onUpdate });
  await timers.tickAll();

  assert.ok(receivedTs, 'onUpdate must be called with a timestamp');
  assert.ok(typeof receivedTs === 'string', 'timestamp must be a string');
  assert.ok(/^\d{4}-\d{2}-\d{2}T/.test(receivedTs), 'timestamp must be ISO format');
});

test('AC3: onUpdate is not called if fetchFn throws', async () => {
  const { timers } = setup();
  let updateCount = 0;
  const fetchFn = async () => { throw new Error('network error'); };
  const onUpdate = () => { updateCount++; };

  startDevReportAutoRefresh({ fetchFn, onUpdate });
  await timers.tickAll();
  assert.equal(updateCount, 0, 'onUpdate must not fire if fetchFn throws');
});

// ── AC4: stop function works; interval is sane ────────────────────────────────

test('AC4: stop function clears the interval', async () => {
  const { timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => { fetchCount++; };

  const stop = startDevReportAutoRefresh({ fetchFn });
  await timers.tickAll();
  assert.equal(fetchCount, 1, 'first tick should increment fetchCount');
  assert.equal(timers.activeCount(), 1, 'one interval should be active');

  stop();
  assert.equal(timers.activeCount(), 0, 'interval should be cleared after stop()');

  await timers.tickAll();
  await timers.tickAll();
  assert.equal(fetchCount, 1, 'must not call fetchFn after stop() is called');
});

test('AC4: REPORT_REFRESH_INTERVAL_MS is exported', () => {
  assert.ok(typeof REPORT_REFRESH_INTERVAL_MS === 'number', 'must export a number');
});

test('AC4: REPORT_REFRESH_INTERVAL_MS is in the sane range', () => {
  const MIN = 15000;
  const MAX = 30000;
  assert.ok(REPORT_REFRESH_INTERVAL_MS >= MIN && REPORT_REFRESH_INTERVAL_MS <= MAX,
    `must be between ${MIN}ms and ${MAX}ms, got ${REPORT_REFRESH_INTERVAL_MS}ms`);
});

test('AC4: custom interval can be passed to startDevReportAutoRefresh', async () => {
  const { timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => { fetchCount++; };

  startDevReportAutoRefresh({ fetchFn, interval: 5000 });
  await timers.tickAll();
  assert.equal(fetchCount, 1, 'custom interval should work');
});

// ── Error handling ─────────────────────────────────────────────────────────────

test('Errors in fetchFn do not break subsequent ticks', async () => {
  const { timers } = setup();
  let fetchCount = 0;
  const fetchFn = async () => {
    fetchCount++;
    if (fetchCount === 1) throw new Error('oops');
  };

  startDevReportAutoRefresh({ fetchFn });
  await timers.tickAll();  // tick 1 throws
  assert.equal(fetchCount, 1);

  await timers.tickAll();  // tick 2 should still fire
  assert.equal(fetchCount, 2, 'must continue ticking even after error');
});
