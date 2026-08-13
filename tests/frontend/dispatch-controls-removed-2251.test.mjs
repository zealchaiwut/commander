/**
 * Behavioral tests for issue #2251: Remove dispatch controls, keep status rendering.
 *
 * AC1: Listed controls (run/cancel/approve/reject buttons) no longer appear in
 *      _smgmtCardHtml output; cards, badges, counts, and separators still render.
 * AC2: _pfBuildWarningsHtml from preflight-warnings.js produces correct warning
 *      chips for unestimated, stale-estimates, and missing-AC data.
 * AC3: Finish wizard (smgmtFinishSprint) is still exported from finish-modal.js.
 * AC4: _smgmtCardActionBtnHtml is exported from board-render.js as a callable
 *      function (proves the card renderer was split).
 *
 * Run with: node --test tests/frontend/dispatch-controls-removed-2251.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal DOM / window stubs ────────────────────────────────────────────────
if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}
if (typeof globalThis.localStorage === 'undefined') {
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
}

// ── Global stubs required by board-render.js ──────────────────────────────────
const _noop = () => {};
const _noopSet = new Set();
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
globalThis._smgmtData = {};
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
globalThis._smgmtRepo = () => 'owner/test';
globalThis._smgmtRiskFlagIconsHtml = () => '';
globalThis._smgmtRowMenuOpen = _noop;
globalThis._smgmtRunningViewUpdate = _noop;
globalThis._smgmtSchedDepHtml = () => '';
globalThis._smgmtSetSprintTokenEl = _noop;
globalThis._smgmtStateMeta = () => ({
  state: '', badge: '', badgeCls: '', cardClass: '',
});
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
globalThis._smgmtUpdateSelectionUI = _noop;
globalThis.escHtml = (s) => String(s);
globalThis.sprintLabelDisplay = (s) => String(s);
globalThis.colorizeLogLine = (s) => String(s);
globalThis._smgmtAnySprintRunning = false;
globalThis._smgmtOrderedLabels = [];
globalThis._smgmtRunningLabels = new Set();
globalThis._fmtRunningTime = (secs) => `${Math.round(secs)}s`;
globalThis._fmtStoppedAt = (s) => String(s);
globalThis.sprintHealthStripInit = _noop;
globalThis._blApplyFilters = _noop;
globalThis._blBacklogAll = [];
globalThis._blSyncFilterPills = _noop;
globalThis._blUpdateActions = _noop;

// ── Import modules under test ─────────────────────────────────────────────────
const { _smgmtCardHtml, _smgmtCardActionBtnHtml } = await import(
  '../../apps/dashboard/static/src/sprint-board/board-render.js'
);

const { _pfBuildWarningsHtml } = await import(
  '../../apps/dashboard/static/src/sprint-board/preflight-warnings.js'
);

// finish-modal.js uses document/globals only in function bodies, safe to import
const finishModal = await import(
  '../../apps/dashboard/static/src/sprint-board/finish-modal.js'
);

// ── AC1: No dispatch buttons in card HTML ─────────────────────────────────────

test('AC1 — planning card has no run-btn', () => {
  const html = _smgmtCardHtml(
    'sprint-100', 100,
    [{ number: 1, labels: [{ name: 'backlog' }] }],
    null, false, null, false,
  );
  assert.ok(!html.includes('smgmt-run-btn'),
    'planning card must not contain smgmt-run-btn');
});

test('AC1 — running card has no cancel-btn', () => {
  globalThis._smgmtRunningLabels = new Set(['sprint-200']);
  const html = _smgmtCardHtml(
    'sprint-200', 200,
    [{ number: 2, labels: [{ name: 'in-progress' }] }],
    null, false, null, false,
  );
  assert.ok(!html.includes('smgmt-cancel-btn'),
    'running card must not contain smgmt-cancel-btn');
  globalThis._smgmtRunningLabels = new Set();
});

test('AC1 — planning card has no approve/reject signoff buttons', () => {
  // Simulate signoff pending state via _smgmtData
  globalThis._smgmtData = {
    sprint_signoff: { 'sprint-300': 'pending' },
    _commanderFeatures: { signoff: true },
  };
  globalThis._commanderFeatures = { signoff: true };
  const html = _smgmtCardHtml(
    'sprint-300', 300,
    [{ number: 3, labels: [{ name: 'backlog' }] }],
    null, false, null, false,
  );
  assert.ok(!html.includes('smgmt-approve-btn'),
    'planning card must not contain smgmt-approve-btn even when signoff pending');
  assert.ok(!html.includes('smgmt-reject-btn'),
    'planning card must not contain smgmt-reject-btn even when signoff pending');
  globalThis._smgmtData = {};
  globalThis._commanderFeatures = undefined;
});

test('AC1 — planning card still renders status badge area and ticket count', () => {
  const html = _smgmtCardHtml(
    'sprint-101', 101,
    [{ number: 10, labels: [{ name: 'backlog' }] }, { number: 11, labels: [{ name: 'backlog' }] }],
    null, false, null, false,
  );
  assert.ok(html.includes('smgmt-sprint-card'), 'card container must render');
  assert.ok(html.includes('smgmt-sprint-count'), 'ticket rollup count must render');
});

// ── AC2: Preflight warnings view still accessible ─────────────────────────────

test('AC2 — _pfBuildWarningsHtml renders unestimated chips', () => {
  const html = _pfBuildWarningsHtml({
    unestimated: ['#100', '#200'],
    stale_estimates: [],
    missing_ac: [],
  });
  assert.ok(html.includes('unestimated'), 'must include unestimated label');
  assert.ok(html.includes('#100'), 'must list the unestimated issue number');
  assert.ok(html.includes('pf-warning-chip'), 'must use the pf-warning-chip class');
});

test('AC2 — _pfBuildWarningsHtml renders stale-estimate chips', () => {
  const html = _pfBuildWarningsHtml({
    unestimated: [],
    stale_estimates: ['#300'],
    missing_ac: [],
  });
  assert.ok(html.includes('stale estimate'), 'must include stale estimate label');
  assert.ok(html.includes('#300'), 'must list the stale estimate issue number');
});

test('AC2 — _pfBuildWarningsHtml renders missing-AC chips', () => {
  const html = _pfBuildWarningsHtml({
    unestimated: [],
    stale_estimates: [],
    missing_ac: ['#400', '#500'],
  });
  assert.ok(html.includes('missing AC'), 'must include missing AC label');
  assert.ok(html.includes('#400'), 'must list the first missing AC issue');
  assert.ok(html.includes('#500'), 'must list the second missing AC issue');
});

test('AC2 — _pfBuildWarningsHtml returns non-empty string for any warning', () => {
  const html = _pfBuildWarningsHtml({
    unestimated: ['#1'],
    stale_estimates: ['#2'],
    missing_ac: ['#3'],
  });
  assert.ok(html.length > 0, 'must produce non-empty HTML when warnings exist');
});

test('AC2 — _pfBuildWarningsHtml returns empty/no-warnings text when no warnings', () => {
  const html = _pfBuildWarningsHtml({ unestimated: [], stale_estimates: [], missing_ac: [] });
  assert.ok(!html.includes('pf-warning-chip'),
    'must not produce warning chips when there are no warnings');
});

// ── AC3: Finish wizard untouched ──────────────────────────────────────────────

test('AC3 — smgmtFinishSprint is still exported from finish-modal.js', () => {
  assert.strictEqual(typeof finishModal.smgmtFinishSprint, 'function',
    'smgmtFinishSprint must still be exported from finish-modal.js');
});

test('AC3 — _fsOpen is still exported from finish-modal.js', () => {
  assert.strictEqual(typeof finishModal._fsOpen, 'function',
    '_fsOpen must still be exported from finish-modal.js');
});

// ── AC4: Card renderer split — _smgmtCardActionBtnHtml is a separate export ──

test('AC4 — _smgmtCardActionBtnHtml is exported from board-render.js', () => {
  assert.strictEqual(typeof _smgmtCardActionBtnHtml, 'function',
    '_smgmtCardActionBtnHtml must be exported from board-render.js');
});

test('AC4 — _smgmtCardActionBtnHtml returns a string', () => {
  const result = _smgmtCardActionBtnHtml('sprint-999', {
    isRunning: false,
    isLinger: false,
    isHasRework: false,
    isPostRun: false,
    rerunInto: null,
    rerunChildDisplay: '',
    canRun: true,
    tickets: [{ number: 1 }],
  });
  assert.strictEqual(typeof result, 'string',
    '_smgmtCardActionBtnHtml must return a string');
});
