/**
 * Frontend behavioral tests for board_invalidated SSE handler (issue #1785).
 *
 * AC4: tab visible → board_invalidated event triggers debounce timer ≥ 2 s
 * AC5: visibility guard — no timer while tab hidden; fires once on tab-show
 * AC6: rapid events within debounce window cancel the previous timer (no storm)
 *
 * Run with: node --test tests/frontend/board-invalidation.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal stubs required by board-render.js at import time ─────────────────

if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null, hidden: false };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

const _noop = () => {};
const _noopSet = new Set();

// All globals referenced in /* global */ directives (function bodies, not import time)
globalThis._smgmtEnsureCapData = _noop;
globalThis._smgmtLoadMiniRail = _noop;
globalThis._smgmtMiniRailRestoreCached = _noop;
globalThis._smgmtRenderAllCapBars = _noop;
globalThis._smgmtUpdateSubnav = _noop;
globalThis._cachedFullRepo = {};
globalThis._estDataCache = {};
globalThis._slug = 'test';
globalThis._smgmtActiveAgentsHtml = _noop;
globalThis._smgmtAgentTagClass = _noop;
globalThis._smgmtApplySort = _noop;
globalThis._smgmtBulkEstimate = _noop;
globalThis._smgmtBySprint = {};
globalThis._smgmtCancelBannerHtml = () => '';
globalThis._smgmtCapacityInputHtml = () => '';
globalThis._smgmtCheckEstimatorHealth = _noop;
globalThis._smgmtCloseIssueOpen = _noop;
globalThis._smgmtConflictsByIssue = {};
globalThis._smgmtCtxMenuOpen = _noop;
globalThis._smgmtDagDataCache = {};
globalThis._smgmtData = null;
globalThis._smgmtDeactivatedLabels = _noopSet;
globalThis._smgmtDepOrderByIssue = {};
globalThis._smgmtEstimateBadgeHtml = () => '';
globalThis._smgmtEstimatorAvailable = false;
globalThis._smgmtFilterApply = _noop;
globalThis._smgmtFinishCards = {};
globalThis._smgmtFinishedLabels = _noopSet;
globalThis._smgmtHasCompletedTickets = _noop;
globalThis._smgmtInitCapacityGauges = _noop;
globalThis._smgmtInjectOutcomeBand = _noop;
globalThis._smgmtIsCancelled = () => false;
globalThis._smgmtKbRestoreFocus = _noop;
globalThis._smgmtLabelColors = {};
globalThis._smgmtLabelFilterToggle = _noop;
globalThis._smgmtLabelFilterToggleExpand = _noop;
globalThis._smgmtLastLabelIssues = {};
globalThis._smgmtLevelsHtml = () => '';
globalThis._smgmtLiveAgentBadgesHtml = () => '';
globalThis._smgmtLiveCache = {};
globalThis._smgmtLiveCacheRepo = null;
globalThis._smgmtLiveLogLinesHtml = () => '';
globalThis._smgmtLivePollRestart = _noop;
globalThis._smgmtLingerRestore = _noop;
globalThis._smgmtLingerStart = _noop;
globalThis._smgmtIsLinger = () => false;
globalThis._smgmtLingerLive = () => null;
globalThis._smgmtNextChildLabel = _noop;
globalThis._smgmtOutcomeCache = {};
globalThis._smgmtOutcomeLogHtml = () => '';
globalThis._smgmtPrimaryRunningLabel = () => null;
globalThis._smgmtReEstimate = _noop;
globalThis._smgmtRepo = () => null;
globalThis._smgmtRiskFlagIconsHtml = () => '';
globalThis._smgmtRowMenuOpen = _noop;
globalThis._smgmtRunningViewUpdate = _noop;
globalThis._smgmtSchedDepHtml = () => '';
globalThis._smgmtSetSprintTokenEl = _noop;
globalThis._smgmtStateMeta = () => ({ state: 'unknown' });
globalThis._smgmtTicketToSprint = {};
globalThis._smgmtUpdateCapacityGauge = _noop;
globalThis._smgmtUpdateCleanupBtn = _noop;
globalThis._smgmtUpdateConflictBadge = _noop;
globalThis._smgmtUpdateDepOrderBadge = _noop;
globalThis._smgmtUpdateEstimateBadge = _noop;
globalThis._smgmtSchedToggleHtml = () => '';
globalThis._smgmtHydrateSchedToggles = _noop;
globalThis._smgmtSelectedIssues = _noopSet;
globalThis._smgmtRowClickSelect = _noop;
globalThis.escHtml = (s) => String(s);
globalThis.sprintLabelDisplay = (s) => String(s);
globalThis.colorizeLogLine = (s) => String(s);
globalThis._smgmtAnySprintRunning = false;
globalThis._smgmtOrderedLabels = [];
globalThis._smgmtRunningLabels = new Set();
globalThis._smgmtRender = _noop;

// For loadSprintMgmt (called by _boardSseFireRefetch)
const _fakeListEl = { innerHTML: '' };
globalThis.document = {
  getElementById: (id) => id === 'smgmt-sprint-list' ? _fakeListEl : null,
  querySelector: () => null,
  hidden: false,
};

import {
  _boardSseOnInvalidated,
  _boardSseOnVisible,
} from '../../apps/dashboard/static/src/sprint-board/board-render.js';


// ── Fake timer helpers ────────────────────────────────────────────────────────

let _realSetTimeout = globalThis.setTimeout;
let _realClearTimeout = globalThis.clearTimeout;

function _installFakeTimers() {
  const timers = [];
  let seq = 0;
  globalThis.setTimeout = (fn, delay) => {
    const id = ++seq;
    timers.push({ id, fn, delay, fired: false, cleared: false });
    return id;
  };
  globalThis.clearTimeout = (id) => {
    const t = timers.find(x => x.id === id);
    if (t) t.cleared = true;
  };
  return {
    timers,
    fireAll() {
      for (const t of timers) {
        if (!t.fired && !t.cleared) { t.fired = true; t.fn(); }
      }
    },
    restore() {
      globalThis.setTimeout = _realSetTimeout;
      globalThis.clearTimeout = _realClearTimeout;
    },
  };
}


// ── AC4: flag ON, tab visible → debounce timer set ≥ 2 s ────────────────────

test('AC4: board_invalidated with flag ON and visible tab sets debounce timer >= 2s', () => {
  const ft = _installFakeTimers();
  try {
    globalThis._commanderFeatures = { board_aggregate: true };
    globalThis.document.hidden = false;

    _boardSseOnInvalidated('owner/repo');

    const active = ft.timers.filter(t => !t.cleared);
    assert.equal(active.length, 1, 'should set exactly one debounce timer');
    assert.ok(active[0].delay >= 2000, `debounce delay must be >= 2000ms, got ${active[0].delay}`);
  } finally {
    ft.restore();
  }
});

test('AC4: debounce timer callback triggers board load', async () => {
  const ft = _installFakeTimers();
  let loadCalls = 0;
  // We'll detect the load via fetch (loadSprintMgmt calls fetch when flag is ON)
  globalThis._commanderFeatures = { board_aggregate: true };
  globalThis._slug = 'testrepo';
  globalThis._cachedFullRepo = { testrepo: 'owner/testrepo' };
  globalThis._smgmtLoadEstimates = _noop;
  globalThis._smgmtLoadConflicts = _noop;
  globalThis._smgmtLoadDepOrder = _noop;

  globalThis.fetch = async (url) => {
    if (String(url).startsWith('/api/board')) loadCalls++;
    return {
      ok: true,
      json: async () => ({
        project: 'owner/testrepo',
        generated_at: new Date().toISOString(),
        sections: { running: [], needs_rework: [], ready_to_merge: [], draft: [], lineage: [], backlog: { count: 0, tickets: [] } },
        capacity: { budget_minutes: 180, size_minutes: { S: 5, M: 15, L: 30, XL: 60 } },
        summaries: {},
      }),
    };
  };

  try {
    globalThis.document.hidden = false;
    _boardSseOnInvalidated('owner/testrepo');

    // Fire the debounce timer
    ft.fireAll();

    // Allow microtasks (async loadSprintMgmt) to settle
    await new Promise(r => _realSetTimeout(r, 20));

    assert.ok(loadCalls >= 1, `expected /api/board to be fetched after debounce, got ${loadCalls}`);
  } finally {
    ft.restore();
  }
});


// ── AC5: visibility guard ─────────────────────────────────────────────────────

test('AC5: board_invalidated while tab hidden does not set a timer', () => {
  const ft = _installFakeTimers();
  try {
    globalThis._commanderFeatures = { board_aggregate: true };
    globalThis.document.hidden = true;

    _boardSseOnInvalidated('owner/repo');

    const active = ft.timers.filter(t => !t.cleared);
    assert.equal(active.length, 0, 'no timer should be set while tab is hidden');
  } finally {
    ft.restore();
    globalThis.document.hidden = false;
  }
});

test('AC5: _boardSseOnVisible fires refetch after hidden-tab board_invalidated', () => {
  const ft = _installFakeTimers();
  try {
    globalThis._commanderFeatures = { board_aggregate: true };

    // Receive event while hidden
    globalThis.document.hidden = true;
    _boardSseOnInvalidated('owner/repo');

    // No timer should be set
    assert.equal(ft.timers.filter(t => !t.cleared).length, 0);

    // Tab becomes visible
    globalThis.document.hidden = false;
    _boardSseOnVisible();

    // Timer should now be set
    const active = ft.timers.filter(t => !t.cleared);
    assert.ok(active.length >= 1, 'timer must be set when tab becomes visible after hidden invalidation');
    assert.ok(active[0].delay >= 2000, `delay must be >= 2000ms, got ${active[0].delay}`);
  } finally {
    ft.restore();
    globalThis.document.hidden = false;
  }
});

test('AC5: _boardSseOnVisible is a no-op when no board_invalidated arrived while hidden', () => {
  const ft = _installFakeTimers();
  try {
    globalThis._commanderFeatures = { board_aggregate: true };
    globalThis.document.hidden = false;

    // Call visible without any prior hidden invalidation
    _boardSseOnVisible();

    assert.equal(ft.timers.length, 0, 'no timer should be set when no pending invalidation');
  } finally {
    ft.restore();
  }
});


// ── AC6: debounce collapses rapid events ──────────────────────────────────────

test('AC6: rapid board_invalidated events result in exactly one pending timer (debounce)', () => {
  const ft = _installFakeTimers();
  try {
    globalThis._commanderFeatures = { board_aggregate: true };
    globalThis.document.hidden = false;

    // Fire 5 rapid invalidations
    for (let i = 0; i < 5; i++) {
      _boardSseOnInvalidated('owner/repo');
    }

    // After 5 calls, only the last timer should be active (previous ones cleared)
    const active = ft.timers.filter(t => !t.cleared);
    assert.equal(active.length, 1, 'rapid events must collapse to exactly one pending timer');
    // 4 timers must have been cleared (debounce cancellation)
    const cleared = ft.timers.filter(t => t.cleared);
    assert.equal(cleared.length, 4, 'each new event must cancel the previous timer');
  } finally {
    ft.restore();
  }
});

