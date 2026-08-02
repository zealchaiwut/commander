/**
 * Frontend unit tests for issue #2089: document (and guard) the intentional
 * exclusion of sub-sprint labels from the "Clean up empty sprints" bulk action.
 *
 * AC: sub-sprint labels (sprint-N.M) must NOT appear in the leading-empty
 * result computed by _smgmtComputeLeadingEmpty — those zombie cards are
 * handled per-card via the Delete button (approach b, issue #2061).
 *
 * Run with: node --test tests/frontend/cleanup-empty-sprints-2089.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal global stubs needed for board-render.js import ───────────────────

if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
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
globalThis._smgmtUpdateSelectionUI = _noop;
globalThis.escHtml = (s) => String(s);
globalThis.sprintLabelDisplay = (s) => String(s);
globalThis.colorizeLogLine = (s) => String(s);
globalThis._smgmtAnySprintRunning = false;
globalThis._smgmtOrderedLabels = [];
globalThis._smgmtRunningLabels = new Set();

import { _smgmtComputeLeadingEmpty } from '../../apps/dashboard/static/src/sprint-board/board-render.js';


// ── Tests: sub-sprint labels excluded from bulk cleanup (AC — issue #2089) ────

test('sub-sprint label excluded even when it has 0 tickets', () => {
  // sprint-10 has 0 tickets, sprint-10.1 has 0 tickets, sprint-11 is active
  const orderedLabels = ['sprint-10', 'sprint-10.1', 'sprint-11'];
  const issues = [{ sprint_label: 'sprint-11' }];
  const result = _smgmtComputeLeadingEmpty(orderedLabels, issues);
  assert.ok(!result.includes('sprint-10.1'),
    'sprint-10.1 (sub-sprint) must NOT appear in bulk cleanup list');
  assert.ok(result.includes('sprint-10'),
    'sprint-10 (empty plain sprint before active sprint-11) must appear');
  assert.equal(result.length, 1, 'only the leading empty plain sprint expected');
});

test('sub-sprint label excluded even when it appears before the active sprint', () => {
  // sprint-N.M must never enter the leading-empty accumulator regardless of order
  const orderedLabels = ['sprint-1', 'sprint-1.1', 'sprint-2', 'sprint-3'];
  const issues = [{ sprint_label: 'sprint-3' }];
  const result = _smgmtComputeLeadingEmpty(orderedLabels, issues);
  assert.ok(!result.includes('sprint-1.1'), 'sub-sprint must be skipped');
  assert.ok(result.includes('sprint-1'), 'sprint-1 is leading empty');
  assert.ok(result.includes('sprint-2'), 'sprint-2 is leading empty');
});

test('plain sprint whose base is active via a sub-sprint ticket is not eligible', () => {
  // sprint-10.1 has a ticket → base 10 is active → sprint-10 must not be in result
  const orderedLabels = ['sprint-9', 'sprint-10', 'sprint-10.1'];
  const issues = [{ sprint_label: 'sprint-10.1' }];
  const result = _smgmtComputeLeadingEmpty(orderedLabels, issues);
  assert.ok(result.includes('sprint-9'), 'sprint-9 (empty, before active base) is in result');
  assert.ok(!result.includes('sprint-10'), 'sprint-10 base is active (via sprint-10.1 ticket)');
  assert.ok(!result.includes('sprint-10.1'), 'sub-sprint never in bulk cleanup');
});

test('no active sprint found → result is [] (AC3 of #2061 re-stated)', () => {
  const orderedLabels = ['sprint-1', 'sprint-2', 'sprint-1.1'];
  const issues = [];  // no tickets anywhere
  const result = _smgmtComputeLeadingEmpty(orderedLabels, issues);
  assert.equal(result.length, 0,
    'when no active sprint found nothing is eligible for cleanup');
});

test('all-plain sprints before active: correct leading run', () => {
  // sanity check: plain sprint logic unaffected by this change
  const orderedLabels = ['sprint-5', 'sprint-6', 'sprint-7'];
  const issues = [{ sprint_label: 'sprint-7' }];
  const result = _smgmtComputeLeadingEmpty(orderedLabels, issues);
  assert.deepEqual(result, ['sprint-5', 'sprint-6']);
});

test('only sub-sprints in order with active ticket: no plain sprints eligible', () => {
  // Edge case: order has only sub-sprints and active sprint
  const orderedLabels = ['sprint-100.1', 'sprint-101'];
  const issues = [{ sprint_label: 'sprint-101' }];
  const result = _smgmtComputeLeadingEmpty(orderedLabels, issues);
  assert.equal(result.length, 0, 'sub-sprint only in leading position → nothing eligible');
});
