/**
 * Frontend unit tests for issue #1638: aggregate board flag helpers.
 *
 * Tests the pure helper functions exported from board-render.js that power
 * the aggregate path:
 *   - _smgmtBuildAggCards(agg)  → {label → card} index
 *   - _smgmtAggToRenderData(agg) → render data shape
 *   - _smgmtCardBucket behaviour via _aggregateBuckets
 *
 * No DOM or fetch required — these are pure data-transformation functions.
 * Run with: node --test tests/frontend/board-aggregate-flag.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Module import with minimal global stubs ───────────────────────────────────
// board-render.js references globals that the bundle supplies; we stub the
// ones needed at import time (none for pure helpers — function bodies are safe).

// Stub globals used at import time (none currently, but guard for safety)
if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

// Stub globals declared in /* global */ directive (called in function bodies)
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
globalThis.escHtml = (s) => String(s);
globalThis.sprintLabelDisplay = (s) => String(s);
globalThis.colorizeLogLine = (s) => String(s);
globalThis._smgmtAnySprintRunning = false;
globalThis._smgmtOrderedLabels = [];
globalThis._smgmtRunningLabels = new Set();

import {
  _smgmtBuildAggCards,
  _smgmtAggToRenderData,
  _smgmtCardBucket,
  loadSprintMgmt,
} from '../../apps/dashboard/static/src/sprint-board/board-render.js';


// ── Test fixtures ─────────────────────────────────────────────────────────────

function _makeTicket(number, sprint_label, status = 'backlog') {
  return { number, title: `Ticket #${number}`, sprint_label, status, labels: [], body: '' };
}

function _makeCard(label, section, lifecycle_state, tickets = [], chain = null) {
  const card = {
    label,
    lifecycle_state,
    tickets,
    mini_rail: { levels: [[`#${tickets[0]?.number || 1}`]], conflicts: [], unestimated: [] },
    dep_order: { has_cycle: false, cycles: [], in_cycle_tickets: [], dep_hints: {} },
    estimate_hours: 0.5,
    run_stats: { label, has_runs: false, split: [], tickets: [] },
  };
  if (chain) card.chain = chain;
  return card;
}

function _makeAgg(options = {}) {
  return {
    project: 'owner/repo',
    generated_at: new Date().toISOString(),
    sections: {
      running: options.running || [],
      needs_rework: options.needs_rework || [],
      ready_to_merge: options.ready_to_merge || [],
      draft: options.draft || [],
      lineage: options.lineage || [],
      backlog: { count: 0, tickets: [] },
    },
    capacity: { budget_minutes: 180, size_minutes: { S: 5, M: 15, L: 30, XL: 60 } },
    summaries: {},
  };
}


// ── _smgmtBuildAggCards tests ─────────────────────────────────────────────────

test('_smgmtBuildAggCards: indexes a draft card by label', () => {
  const card = _makeCard('sprint-1', 'draft', 'draft', [_makeTicket(10, 'sprint-1')]);
  const agg = _makeAgg({ draft: [card] });
  const idx = _smgmtBuildAggCards(agg);
  assert.ok('sprint-1' in idx, 'sprint-1 must be in index');
  assert.equal(idx['sprint-1'], card);
});

test('_smgmtBuildAggCards: indexes a running card by label', () => {
  const card = _makeCard('sprint-2', 'running', 'running');
  const agg = _makeAgg({ running: [card] });
  const idx = _smgmtBuildAggCards(agg);
  assert.ok('sprint-2' in idx);
});

test('_smgmtBuildAggCards: indexes a needs_rework card', () => {
  const card = _makeCard('sprint-3', 'needs_rework', 'needs_rework');
  const agg = _makeAgg({ needs_rework: [card] });
  const idx = _smgmtBuildAggCards(agg);
  assert.ok('sprint-3' in idx);
});

test('_smgmtBuildAggCards: indexes a ready_to_merge card', () => {
  const card = _makeCard('sprint-4', 'ready_to_merge', 'ready_to_merge');
  const agg = _makeAgg({ ready_to_merge: [card] });
  const idx = _smgmtBuildAggCards(agg);
  assert.ok('sprint-4' in idx);
});

test('_smgmtBuildAggCards: registers lineage chain ancestors', () => {
  const card = _makeCard('sprint-5.1', 'lineage', 'running',
    [_makeTicket(51, 'sprint-5.1')], ['sprint-5', 'sprint-5.1']);
  const agg = _makeAgg({ lineage: [card] });
  const idx = _smgmtBuildAggCards(agg);
  assert.ok('sprint-5' in idx, 'ancestor sprint-5 must be in index');
  assert.ok('sprint-5.1' in idx, 'latest sprint-5.1 must be in index');
});

test('_smgmtBuildAggCards: empty aggregate returns empty index', () => {
  const idx = _smgmtBuildAggCards(_makeAgg());
  assert.deepEqual(idx, {});
});


// ── _smgmtAggToRenderData tests ───────────────────────────────────────────────

test('_smgmtAggToRenderData: returns required render data keys', () => {
  const agg = _makeAgg({ draft: [_makeCard('sprint-1', 'draft', 'draft')] });
  const data = _smgmtAggToRenderData(agg);
  const required = ['sprints', 'order', 'issues', 'finished_sprints',
    'merged_sprints', 'sprint_parents', 'sprint_rerun_into',
    'sprint_plan_states', 'sprint_has_run', 'sprint_signoff', '_aggregateBuckets'];
  for (const k of required) {
    assert.ok(k in data, `missing key: ${k}`);
  }
});

test('_smgmtAggToRenderData: sprint-1 draft → sprint_has_run false', () => {
  const card = _makeCard('sprint-1', 'draft', 'draft', [_makeTicket(10, 'sprint-1')]);
  const data = _smgmtAggToRenderData(_makeAgg({ draft: [card] }));
  assert.equal(data.sprint_has_run['sprint-1'], false);
});

test('_smgmtAggToRenderData: sprint-3 needs_rework → sprint_has_run true', () => {
  const card = _makeCard('sprint-3', 'needs_rework', 'needs_rework');
  const data = _smgmtAggToRenderData(_makeAgg({ needs_rework: [card] }));
  assert.equal(data.sprint_has_run['sprint-3'], true);
});

test('_smgmtAggToRenderData: sprint-4 ready_to_merge → sprint_has_run true', () => {
  const card = _makeCard('sprint-4', 'ready_to_merge', 'ready_to_merge');
  const data = _smgmtAggToRenderData(_makeAgg({ ready_to_merge: [card] }));
  assert.equal(data.sprint_has_run['sprint-4'], true);
});

test('_smgmtAggToRenderData: tickets are flattened with sprint_label set', () => {
  const t1 = _makeTicket(10, 'sprint-1');
  const t2 = _makeTicket(11, 'sprint-1');
  const card = _makeCard('sprint-1', 'draft', 'draft', [t1, t2]);
  const data = _smgmtAggToRenderData(_makeAgg({ draft: [card] }));
  assert.equal(data.issues.length, 2);
  assert.ok(data.issues.every(i => i.sprint_label === 'sprint-1'));
});

test('_smgmtAggToRenderData: backlog tickets have sprint_label null', () => {
  const agg = _makeAgg();
  agg.sections.backlog = { count: 1, tickets: [_makeTicket(60, null)] };
  const data = _smgmtAggToRenderData(agg);
  const backlogIssues = data.issues.filter(i => i.sprint_label === null);
  assert.equal(backlogIssues.length, 1);
  assert.equal(backlogIssues[0].number, 60);
});

test('_smgmtAggToRenderData: order is ascending by sprint number', () => {
  const card1 = _makeCard('sprint-3', 'draft', 'draft');
  const card2 = _makeCard('sprint-1', 'draft', 'draft');
  const card3 = _makeCard('sprint-2', 'draft', 'draft');
  const data = _smgmtAggToRenderData(_makeAgg({ draft: [card1, card2, card3] }));
  assert.deepEqual(data.order, ['sprint-1', 'sprint-2', 'sprint-3']);
});

test('_smgmtAggToRenderData: sub-sprint ordering (sprint-5 before sprint-5.1)', () => {
  const chainCard = _makeCard('sprint-5.1', 'lineage', 'running',
    [], ['sprint-5', 'sprint-5.1']);
  const data = _smgmtAggToRenderData(_makeAgg({ lineage: [chainCard] }));
  const idx5 = data.order.indexOf('sprint-5');
  const idx51 = data.order.indexOf('sprint-5.1');
  assert.ok(idx5 >= 0, 'sprint-5 must be in order');
  assert.ok(idx51 >= 0, 'sprint-5.1 must be in order');
  assert.ok(idx5 < idx51, 'sprint-5 must precede sprint-5.1');
});

test('_smgmtAggToRenderData: lineage chain sets sprint_parents', () => {
  const chainCard = _makeCard('sprint-5.1', 'lineage', 'running',
    [], ['sprint-5', 'sprint-5.1']);
  const data = _smgmtAggToRenderData(_makeAgg({ lineage: [chainCard] }));
  assert.equal(data.sprint_parents['sprint-5.1'], 'sprint-5');
});

test('_smgmtAggToRenderData: _aggregateBuckets maps each label to its section', () => {
  const draft = _makeCard('sprint-1', 'draft', 'draft');
  const running = _makeCard('sprint-2', 'running', 'running');
  const rework = _makeCard('sprint-3', 'needs_rework', 'needs_rework');
  const rtm = _makeCard('sprint-4', 'ready_to_merge', 'ready_to_merge');
  const data = _smgmtAggToRenderData(_makeAgg({
    draft: [draft], running: [running], needs_rework: [rework], ready_to_merge: [rtm],
  }));
  assert.equal(data._aggregateBuckets['sprint-1'], 'draft');
  assert.equal(data._aggregateBuckets['sprint-2'], 'running');
  assert.equal(data._aggregateBuckets['sprint-3'], 'needs_rework');
  assert.equal(data._aggregateBuckets['sprint-4'], 'ready_to_merge');
});

test('_smgmtAggToRenderData: finished_sprints only for completed lifecycle', () => {
  const completed = _makeCard('sprint-10', 'ready_to_merge', 'completed');
  const active = _makeCard('sprint-11', 'ready_to_merge', 'ready_to_merge');
  const data = _smgmtAggToRenderData(_makeAgg({ ready_to_merge: [completed, active] }));
  assert.ok(data.finished_sprints.includes('sprint-10'));
  assert.ok(!data.finished_sprints.includes('sprint-11'));
});


// ── _smgmtCardBucket uses _aggregateBuckets when _smgmtData._aggregateBuckets present ──

test('_smgmtCardBucket: returns ready_to_merge from _aggregateBuckets', () => {
  // Temporarily set _smgmtData with _aggregateBuckets
  const orig = globalThis._smgmtData;
  globalThis._smgmtData = { _aggregateBuckets: { 'sprint-4': 'ready_to_merge' }, sprint_has_run: {} };
  globalThis._smgmtRunningLabels = new Set();
  try {
    const bucket = _smgmtCardBucket('sprint-4', {});
    assert.equal(bucket, 'ready_to_merge');
  } finally {
    globalThis._smgmtData = orig;
  }
});

test('_smgmtCardBucket: returns needs_rework from _aggregateBuckets', () => {
  const orig = globalThis._smgmtData;
  globalThis._smgmtData = { _aggregateBuckets: { 'sprint-3': 'needs_rework' }, sprint_has_run: {} };
  globalThis._smgmtRunningLabels = new Set();
  try {
    const bucket = _smgmtCardBucket('sprint-3', {});
    assert.equal(bucket, 'needs_rework');
  } finally {
    globalThis._smgmtData = orig;
  }
});

test('_smgmtCardBucket: returns running when label is in _smgmtRunningLabels (aggregate overrides)', () => {
  const orig = globalThis._smgmtData;
  globalThis._smgmtData = { _aggregateBuckets: { 'sprint-2': 'running' }, sprint_has_run: {} };
  globalThis._smgmtRunningLabels = new Set(['sprint-2']);
  try {
    const bucket = _smgmtCardBucket('sprint-2', {});
    assert.equal(bucket, 'running');
  } finally {
    globalThis._smgmtData = orig;
    globalThis._smgmtRunningLabels = new Set();
  }
});

test('_smgmtCardBucket: returns draft from _aggregateBuckets', () => {
  const orig = globalThis._smgmtData;
  globalThis._smgmtData = { _aggregateBuckets: { 'sprint-1': 'draft' }, sprint_has_run: {} };
  globalThis._smgmtRunningLabels = new Set();
  try {
    const bucket = _smgmtCardBucket('sprint-1', {});
    assert.equal(bucket, 'draft');
  } finally {
    globalThis._smgmtData = orig;
  }
});

test('_smgmtCardBucket: falls through to legacy path when no _aggregateBuckets', () => {
  const orig = globalThis._smgmtData;
  globalThis._smgmtData = { sprint_has_run: {}, sprint_plan_states: {} };
  globalThis._smgmtRunningLabels = new Set();
  globalThis._smgmtFinishedLabels = new Set();
  try {
    // Legacy path returns "draft" when no run/outcome data
    const bucket = _smgmtCardBucket('sprint-99', {});
    assert.equal(bucket, 'draft');
  } finally {
    globalThis._smgmtData = orig;
  }
});


// ── AC3 (issue #1746): fetch-spy tests — aggregate path is the only path ───────
// The prior AC7/AC8 tests were source-regex checks; these exercise the actual
// loadSprintMgmt() fetch path so a flag-bypass bug will fail the suite.

// Stub _smgmtRender so DOM write doesn't blow up in Node
globalThis._smgmtRender = _noop;

// Provide a fake smgmt-sprint-list element so loadSprintMgmt doesn't early-return
const _fakeListEl = { innerHTML: '' };
globalThis.document = {
  getElementById: (id) => id === 'smgmt-sprint-list' ? _fakeListEl : null,
  querySelector: () => null,
};

/** Build a minimal fake aggregate response */
function _fakeAggResponse() {
  return {
    project: 'owner/repo',
    generated_at: new Date().toISOString(),
    sections: { running: [], needs_rework: [], ready_to_merge: [], draft: [], lineage: [], backlog: { count: 0, tickets: [] } },
    capacity: { budget_minutes: 180, size_minutes: { S: 5, M: 15, L: 30, XL: 60 } },
    summaries: {},
  };
}

/** Build a minimal fake legacy response (sprint-management/issues shape) */
function _fakeLegacyResponse() {
  return {
    sprints: {}, order: [], issues: [], finished_sprints: [], merged_sprints: [],
    sprint_parents: {}, sprint_rerun_into: {}, sprint_plan_states: {}, sprint_has_run: {}, sprint_signoff: {},
  };
}

/** Install a fetch spy that returns canned responses by URL prefix */
function _installFetchSpy(routes) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    for (const [prefix, body] of routes) {
      if (String(url).startsWith(prefix)) {
        return {
          ok: true,
          json: async () => body,
          body: { getReader: () => ({ read: async () => ({ done: true }) }) },
        };
      }
    }
    throw new Error(`Unexpected fetch: ${url}`);
  };
  return calls;
}

test('fetch-spy: loadSprintMgmt calls only /api/board (single fetch)', async () => {
  // Set up repo
  const repo = 'owner/fetchtest';
  globalThis._slug = 'fetchtest';
  globalThis._cachedFullRepo = { fetchtest: repo };
  // Stub loaders called after render (they check _smgmtAggregateCards and bail)
  globalThis._smgmtLoadEstimates = _noop;
  globalThis._smgmtLoadConflicts = _noop;
  globalThis._smgmtLoadDepOrder = _noop;
  globalThis._smgmtLoadMiniRail = _noop;

  const fetchCalls = _installFetchSpy([
    ['/api/board', _fakeAggResponse()],
  ]);

  await loadSprintMgmt(true, null);

  const boardCalls = fetchCalls.filter(u => u.startsWith('/api/board'));
  const legacyCalls = fetchCalls.filter(u =>
    u.includes('/api/sprint-management') || u.includes('/api/sprints/running-all')
  );

  assert.equal(boardCalls.length, 1, 'must make exactly one /api/board call');
  assert.equal(legacyCalls.length, 0, 'must make zero legacy per-sprint calls');
});

