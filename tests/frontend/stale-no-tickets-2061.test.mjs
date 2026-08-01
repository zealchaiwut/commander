/**
 * Frontend unit tests for issue #2061: stale_no_tickets suppression.
 *
 * Tests that _smgmtAggToRenderData() correctly propagates the server-set
 * stale_no_tickets flag into the render data's _staleNoTicketLabels Set,
 * so the frontend can suppress action buttons on zombie sprint cards.
 *
 * Run with: node --test tests/frontend/stale-no-tickets-2061.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal global stubs (mirrors board-aggregate-flag.test.mjs) ──────────────

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

import { _smgmtAggToRenderData } from '../../apps/dashboard/static/src/sprint-board/board-render.js';


// ── Helpers ───────────────────────────────────────────────────────────────────

function _makeCard(label, lifecycle_state, tickets = [], extra = {}) {
  return {
    label,
    lifecycle_state,
    tickets,
    mini_rail: { levels: [], conflicts: [], unestimated: [] },
    dep_order: { has_cycle: false, cycles: [], in_cycle_tickets: [], dep_hints: {} },
    estimate_hours: 0,
    run_stats: { label, has_runs: false, split: [], tickets: [] },
    ...extra,
  };
}

function _makeAgg(sections = {}) {
  return {
    project: 'owner/repo',
    generated_at: new Date().toISOString(),
    sections: {
      running: [],
      needs_rework: [],
      ready_to_merge: [],
      draft: [],
      lineage: [],
      backlog: { count: 0, tickets: [] },
      ...sections,
    },
    capacity: { budget_minutes: 180, size_minutes: { S: 5, M: 15, L: 30, XL: 60 } },
    summaries: {},
  };
}


// ── Tests ─────────────────────────────────────────────────────────────────────

test('_staleNoTicketLabels: key present in render data', () => {
  const agg = _makeAgg({ draft: [_makeCard('sprint-1', 'draft')] });
  const data = _smgmtAggToRenderData(agg);
  assert.ok('_staleNoTicketLabels' in data, '_staleNoTicketLabels must be present in render data');
  assert.ok(data._staleNoTicketLabels instanceof Set, '_staleNoTicketLabels must be a Set');
});

test('_staleNoTicketLabels: ready_to_merge with stale_no_tickets=true is included', () => {
  const card = _makeCard('sprint-4', 'ready_to_merge', [], { stale_no_tickets: true });
  const data = _smgmtAggToRenderData(_makeAgg({ ready_to_merge: [card] }));
  assert.ok(
    data._staleNoTicketLabels.has('sprint-4'),
    'sprint-4 (ready_to_merge, stale_no_tickets=true) must be in _staleNoTicketLabels'
  );
});

test('_staleNoTicketLabels: needs_rework with stale_no_tickets=true is included', () => {
  const card = _makeCard('sprint-3', 'needs_rework', [], { stale_no_tickets: true });
  const data = _smgmtAggToRenderData(_makeAgg({ needs_rework: [card] }));
  assert.ok(
    data._staleNoTicketLabels.has('sprint-3'),
    'sprint-3 (needs_rework, stale_no_tickets=true) must be in _staleNoTicketLabels'
  );
});

test('_staleNoTicketLabels: ready_to_merge WITHOUT stale_no_tickets is excluded', () => {
  // A real ready-to-merge sprint (has tickets, no stale flag) must NOT be suppressed
  const card = _makeCard('sprint-5', 'ready_to_merge', [{ number: 40 }]);
  const data = _smgmtAggToRenderData(_makeAgg({ ready_to_merge: [card] }));
  assert.ok(
    !data._staleNoTicketLabels.has('sprint-5'),
    'sprint-5 (ready_to_merge, no stale flag) must NOT be in _staleNoTicketLabels'
  );
});

test('_staleNoTicketLabels: running sprint with stale_no_tickets=true is excluded', () => {
  // AC5: running sprint with 0 tickets must NOT be suppressed
  const card = _makeCard('sprint-2', 'running', [], { stale_no_tickets: true });
  const data = _smgmtAggToRenderData(_makeAgg({ running: [card] }));
  assert.ok(
    !data._staleNoTicketLabels.has('sprint-2'),
    'Running sprint must NOT be in _staleNoTicketLabels even if server sets stale_no_tickets=true'
  );
});

test('_staleNoTicketLabels: draft sprint is never stale regardless of flag', () => {
  // AC4: empty runnable draft must remain creatable and runnable
  const card = _makeCard('sprint-1', 'draft', [], { stale_no_tickets: true });
  const data = _smgmtAggToRenderData(_makeAgg({ draft: [card] }));
  assert.ok(
    !data._staleNoTicketLabels.has('sprint-1'),
    'Draft sprint must NOT be in _staleNoTicketLabels — it is runnable'
  );
});

test('_staleNoTicketLabels: empty agg returns empty set', () => {
  const data = _smgmtAggToRenderData(_makeAgg());
  assert.equal(data._staleNoTicketLabels.size, 0, 'Empty aggregate must yield empty stale set');
});

test('_staleNoTicketLabels: mix of stale and healthy cards', () => {
  // zombie (0 tickets, stale=true) + real ready-to-merge (tickets, no flag) co-exist
  const zombie = _makeCard('sprint-67', 'ready_to_merge', [], { stale_no_tickets: true });
  const healthy = _makeCard('sprint-68', 'ready_to_merge', [{ number: 40 }]);
  const data = _smgmtAggToRenderData(_makeAgg({ ready_to_merge: [zombie, healthy] }));
  assert.ok(data._staleNoTicketLabels.has('sprint-67'), 'zombie sprint-67 must be stale');
  assert.ok(!data._staleNoTicketLabels.has('sprint-68'), 'healthy sprint-68 must NOT be stale');
});
