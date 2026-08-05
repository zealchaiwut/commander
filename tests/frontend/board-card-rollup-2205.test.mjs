/**
 * Frontend behavioral test for issue #2205.
 *
 * _smgmtCardHtml's needs_rework branch defaulted `rollupItems` to the live
 * open-GitHub-ticket list (`tickets`), which is empty for a sprint whose
 * tickets are all closed without being fixed (e.g. perf-coach sprint-121:
 * end_reason="ticket-failures", but every ticket eventually merged after
 * exhausting its fix-loop). The header rollup then read "0 tickets" right
 * next to a "NEEDS REWORK" badge. The fix falls back to `outcome.issues`
 * (the historical ticket list) when the live list is empty, matching what
 * the completed-state branch already did.
 *
 * Run with: node --test tests/frontend/board-card-rollup-2205.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}
if (typeof globalThis.localStorage === 'undefined') {
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
}

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
globalThis._smgmtRepo = () => 'owner/perf-coach';
globalThis._smgmtRiskFlagIconsHtml = () => '';
globalThis._smgmtRowMenuOpen = _noop;
globalThis._smgmtRunningViewUpdate = _noop;
globalThis._smgmtSchedDepHtml = () => '';
globalThis._smgmtSetSprintTokenEl = _noop;
// The behavior under test hinges on this returning state: 'needs_rework',
// matching _smgmtCardHtml's `meta.state === "needs_rework"` branch.
globalThis._smgmtStateMeta = (outcome) => ({
  state: 'needs_rework',
  badge: 'NEEDS REWORK',
  badgeCls: 'state-needs-rework',
  cardClass: 'sc-needs-rework',
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
globalThis.escHtml = (s) => String(s);
globalThis.sprintLabelDisplay = (s) => String(s);
globalThis.colorizeLogLine = (s) => String(s);
globalThis._smgmtAnySprintRunning = false;
globalThis._smgmtOrderedLabels = [];
globalThis._smgmtRunningLabels = new Set();
globalThis._fmtRunningTime = (secs) => `${Math.round(secs)}s`;
globalThis._fmtStoppedAt = (s) => String(s);

const { _smgmtCardHtml } = await import(
  '../../apps/dashboard/static/src/sprint-board/board-render.js'
);

function _outcomeIssue(number) {
  return { number, title: `Ticket #${number}`, outcome: 'done' };
}

test('needs_rework card with 0 open tickets shows the historical ticket count, not "0 tickets"', () => {
  const outcome = {
    state: 'has_rework',
    lifecycle: 'needs_rework',
    sprint_status: 'stopped',
    end_reason: 'ticket-failures',
    wall_clock_secs: 6432,
    ended_at: null,
    issues: [849, 901, 1306, 1399, 1420, 1473, 1525].map(_outcomeIssue),
  };

  const html = _smgmtCardHtml(
    'sprint-121',           // label
    121,                    // n
    [],                     // tickets — live open GitHub tickets, empty (all closed)
    outcome,                // outcome
    false,                  // isNext
    null,                   // parent
    false,                  // finished
  );

  assert.ok(
    html.includes('7 ticket'),
    'Expected the header rollup to reflect the 7 historical tickets from outcome.issues'
  );
  assert.ok(
    !html.includes('>0 tickets<'),
    'Header rollup must not read "0 tickets" for a needs_rework sprint that had real ticket activity'
  );
});

test('needs_rework card WITH open tickets still shows the live actionable list (regression guard)', () => {
  const outcome = {
    state: 'has_rework',
    lifecycle: 'needs_rework',
    sprint_status: 'stopped',
    end_reason: 'ticket-failures',
    wall_clock_secs: 100,
    ended_at: null,
    issues: [1, 2].map(_outcomeIssue),
  };
  const liveTickets = [
    { number: 1, title: 'Open rework ticket', status: 'open' },
  ];

  const html = _smgmtCardHtml('sprint-3', 3, liveTickets, outcome, false, null, false);

  assert.ok(
    html.includes('1 ticket'),
    'A needs_rework sprint WITH an open ticket must still roll up from the live list'
  );
});
