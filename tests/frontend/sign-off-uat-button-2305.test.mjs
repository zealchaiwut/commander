/**
 * Frontend behavioral test for issue #2305 (AC7 — UI exposes the per-sprint
 * UAT sign-off action from the sprint card).
 *
 * _smgmtCardHtml renders a "Sign off UAT" button when the sprint is not
 * running and has at least one ticket with status "uat". Feeds real ticket
 * arrays through the exported render function and asserts on the emitted
 * HTML (per CLAUDE.md #1746 — no source-regex checks).
 *
 * Run with: node --test tests/frontend/sign-off-uat-button-2305.test.mjs
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
globalThis._smgmtStateMeta = () => ({
  state: '',
  badge: '',
  badgeCls: '',
  cardClass: '',
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

test('sprint card with an open UAT ticket shows the Sign off UAT button, wired to the sprint label', () => {
  const tickets = [{ number: 1, title: 'Ticket #1', status: 'uat' }];
  const html = _smgmtCardHtml('sprint-10', 10, tickets, null, false, null, false);

  assert.ok(
    html.includes('smgmt-uat-signoff-btn'),
    'Expected the Sign off UAT button to render for a sprint with an open UAT ticket'
  );
  assert.ok(
    html.includes("smgmtUatSignoffSprint('sprint-10')"),
    'Expected the button to call smgmtUatSignoffSprint with this card\'s own sprint label'
  );
});

test('sprint card with no UAT tickets omits the Sign off UAT button', () => {
  const tickets = [{ number: 1, title: 'Ticket #1', status: 'sit' }];
  const html = _smgmtCardHtml('sprint-11', 11, tickets, null, false, null, false);

  assert.ok(
    !html.includes('smgmt-uat-signoff-btn'),
    'Sign off UAT button must not render when the sprint has no open UAT tickets'
  );
});

test('a running sprint never shows the Sign off UAT button, even with UAT tickets', () => {
  globalThis._smgmtRunningLabels = new Set(['sprint-12']);
  try {
    const tickets = [{ number: 1, title: 'Ticket #1', status: 'uat' }];
    const html = _smgmtCardHtml('sprint-12', 12, tickets, null, false, null, false);

    assert.ok(
      !html.includes('smgmt-uat-signoff-btn'),
      'Sign off UAT button must not render for a currently-running sprint'
    );
  } finally {
    globalThis._smgmtRunningLabels = new Set();
  }
});
