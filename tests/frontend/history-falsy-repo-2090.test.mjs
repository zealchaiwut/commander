/**
 * Frontend behavioral tests for issue #2090:
 * History ledger must not show a permanent skeleton when repo is falsy.
 *
 * AC: When _histLoadLedger is called with a falsy repo and background=false,
 *     the #hist-ledger element must show the empty/error state (hist-ledger-empty),
 *     NOT a loading skeleton (hist-ledger-skeleton) that never resolves.
 *
 * Run with: node --test tests/frontend/history-falsy-repo-2090.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal global stubs ───────────────────────────────────────────────────────

if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}
globalThis.document = { getElementById: () => null, querySelector: () => null };

const _noop = () => {};

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

// fetch is not expected to be called for falsy-repo paths
globalThis.fetch = () => {
  throw new Error('fetch must not be called when repo is falsy');
};

import {
  _histLoadLedger,
  _histResetLedgerCache,
} from '../../apps/dashboard/static/src/sprint-board/history.js';

function makeLedgerEl() {
  return { innerHTML: '' };
}

function patchDocument(ledger) {
  globalThis.document = {
    getElementById: (id) => (id === 'hist-ledger' ? ledger : null),
    querySelector: () => null,
  };
}

function resetState() {
  _histResetLedgerCache();
}

// ── AC: falsy repo renders empty state, not a permanent skeleton ───────────────

test('AC: falsy repo (null) renders empty state, not a skeleton that never resolves', async () => {
  resetState();

  const ledger = makeLedgerEl();
  patchDocument(ledger);

  await _histLoadLedger(null, {});

  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-skeleton'),
    'skeleton must NOT appear for falsy repo — it would never be cleared',
  );
  assert.ok(
    ledger.innerHTML.includes('hist-ledger-empty'),
    'empty state must render for falsy repo so the pane does not hang on a skeleton',
  );
});

test('AC: falsy repo (empty string) renders empty state, not a skeleton', async () => {
  resetState();

  const ledger = makeLedgerEl();
  patchDocument(ledger);

  await _histLoadLedger('', {});

  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-skeleton'),
    'skeleton must NOT appear for empty-string repo',
  );
  assert.ok(
    ledger.innerHTML.includes('hist-ledger-empty'),
    'empty state must render for empty-string repo',
  );
});

test('AC: falsy repo with background=true leaves ledger content unchanged', async () => {
  resetState();

  const ledger = makeLedgerEl();
  ledger.innerHTML = '<div class="existing-content">existing</div>';
  patchDocument(ledger);

  await _histLoadLedger(null, { background: true });

  // background=true should never touch the DOM (same as before the fix)
  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-skeleton'),
    'background call must not paint a skeleton for falsy repo',
  );
  assert.ok(
    !ledger.innerHTML.includes('hist-ledger-empty'),
    'background call must not modify the ledger DOM for falsy repo',
  );
  assert.ok(
    ledger.innerHTML.includes('existing-content'),
    'background call must leave existing DOM content intact',
  );
});
