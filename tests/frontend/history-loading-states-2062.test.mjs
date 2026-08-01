/**
 * Frontend behavioral tests for issue #2062: History tab loading states.
 *
 * AC5 (issue #1746 pattern): exercises _histLoadLedger with a deferred fetch
 * to assert the correct UI state at each phase of the load lifecycle:
 *   - Loading skeleton is present in #hist-ledger before the fetch resolves.
 *   - Empty state renders after a successful fetch returning zero sprints.
 *   - Error state with a retry affordance renders after a fetch rejection.
 *
 * Run with: node --test tests/frontend/history-loading-states-2062.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal global stubs ───────────────────────────────────────────────────────

if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}
// Initial document stub; each test overwrites getElementById as needed.
globalThis.document = { getElementById: () => null, querySelector: () => null };

const _noop = () => {};

// Globals declared in history.js /* global */ directive
globalThis.escHtml = (s) => String(s);
globalThis.sprintLabelDisplay = (s) => String(s);
globalThis._slug = 'test-project';
globalThis._cachedFullRepo = { 'test-project': 'owner/test' };
globalThis._smgmtAnySprintRunning = false;
globalThis._smgmtBySprint = {};
globalThis._smgmtUpdateSubnav = _noop;
globalThis._nextSprintSublabel = (l) => l + '.1';
globalThis.finishSprintAndWait = _noop;
globalThis.bulkCompleteLineageAndWait = _noop;
globalThis._smgmtBoardLock = _noop;
globalThis._smgmtBoardProgress = _noop;
globalThis._smgmtBoardLog = _noop;
globalThis._smgmtBoardFinish = _noop;
globalThis.loadSprintMgmt = _noop;
globalThis.CSS = { escape: (s) => s };
globalThis.requestAnimationFrame = _noop;

import {
  _histLoadLedger,
  _histResetLedgerCache,
} from '../../apps/dashboard/static/src/sprint-board/history.js';

// ── Helpers ────────────────────────────────────────────────────────────────────

/** Build a fake #hist-ledger DOM node that tracks innerHTML. */
function makeLedgerEl() {
  return { innerHTML: '' };
}

/** Install a fetch spy that routes calls by URL substring. */
function installFetch(routes) {
  globalThis.fetch = (url) => {
    for (const [pattern, handler] of routes) {
      if (String(url).includes(pattern)) return handler(url);
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
}

/** Wire document.getElementById to return ledger for 'hist-ledger', null otherwise. */
function patchDocument(ledger) {
  globalThis.document = {
    getElementById: (id) => (id === 'hist-ledger' ? ledger : null),
    querySelector: () => null,
  };
}

/** Reset module-level cache state between tests. */
function resetState() {
  _histResetLedgerCache();
}

// ── AC1: loading skeleton present while fetch is pending ───────────────────────

test('AC1: skeleton renders in #hist-ledger synchronously before the fetch resolves', async () => {
  resetState();

  let resolveHistory;
  const historyPending = new Promise((resolve) => {
    resolveHistory = resolve;
  });

  installFetch([
    ['/settings', () => Promise.resolve({ ok: false })],
    ['/history', () => historyPending],
  ]);

  const ledger = makeLedgerEl();
  patchDocument(ledger);

  // Do NOT await — skeleton is injected synchronously before the first await
  const loadPromise = _histLoadLedger('owner/test', {});

  assert.ok(
    ledger.innerHTML.includes('hist-ledger-skeleton'),
    'skeleton class must be present in #hist-ledger while fetch is in flight',
  );
  assert.ok(
    ledger.innerHTML.includes('aria-busy="true"'),
    'skeleton must carry aria-busy="true" for screen-reader accessibility',
  );
  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-empty'),
    'empty state must not appear while the fetch is still pending',
  );

  // Unblock the fetch so the test does not leak a dangling promise.
  resolveHistory({ ok: true, json: async () => ({ sprints: [] }) });
  await loadPromise;
});

// ── AC3: distinct empty state after resolving with no sprints ──────────────────

test('AC3: empty state with icon renders after a fetch returning zero sprints', async () => {
  resetState();

  installFetch([
    ['/settings', () => Promise.resolve({ ok: false })],
    ['/history', () => Promise.resolve({ ok: true, json: async () => ({ sprints: [] }) })],
  ]);

  const ledger = makeLedgerEl();
  patchDocument(ledger);

  await _histLoadLedger('owner/test', { force: true });

  assert.ok(
    ledger.innerHTML.includes('hist-ledger-empty'),
    'empty-state class must be present after a zero-sprint response',
  );
  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-skeleton'),
    'skeleton must not remain after the fetch resolves',
  );
  // The icon makes the empty state visually distinct from the skeleton (AC3).
  assert.ok(
    ledger.innerHTML.includes('ti-inbox-off'),
    'empty state must include a tabler inbox-off icon to differ from the loading skeleton',
  );
});

// ── AC4: error state with retry affordance after a fetch rejection ─────────────

test('AC4: error state with retry button renders after a fetch failure', async () => {
  resetState();

  installFetch([
    ['/settings', () => Promise.resolve({ ok: false })],
    ['/history', () => Promise.reject(new Error('simulated network failure'))],
  ]);

  const ledger = makeLedgerEl();
  patchDocument(ledger);

  await _histLoadLedger('owner/test', { force: true });

  assert.ok(
    ledger.innerHTML.includes('hist-ledger-error'),
    'error-state class must be present after a fetch failure',
  );
  assert.ok(
    ledger.innerHTML.includes('hist-ledger-retry'),
    'a retry affordance (.hist-ledger-retry) must be present in the error state',
  );
  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-skeleton'),
    'skeleton must not remain after a fetch failure',
  );
  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-empty'),
    'error state must be distinct from empty state (different class)',
  );
});
