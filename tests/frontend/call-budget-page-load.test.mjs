/**
 * Behavioral page-load call-budget tests (issue #1831).
 *
 * Wires the shared fetch-spy harness (installFetchSpy / assertFetchBudget
 * from call-budget-helpers.mjs) into real loader calls so a budget violation
 * in the actual code fails the suite — source-regex checks cannot catch that
 * (per CLAUDE.md #1746, the class of miss that caused the #982 SSE regression).
 *
 * Arc targets enforced here (per issue #1788 documentation):
 *   Board load          = 1 /api/board call        (loadSprintMgmt)
 *   History feed load   = 1 /api/sprints/history   (_histLoadLedger)
 *   Running first paint = 1 /api/running call       (_smgmtRunningFirstPaint, via vm)
 *   AC6: driving 2 board loads causes assertFetchBudget to throw (spy-based)
 *
 * Run with: node --test tests/frontend/call-budget-page-load.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  installFetchSpy,
  assertFetchBudget,
  assertFetchBudgetMax,
} from './call-budget-helpers.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Global stubs required by board-render.js at import time ──────────────────

const _noop = () => {};
const _noopSet = new Set();

if (typeof globalThis.document === 'undefined') {
  globalThis.document = {
    getElementById: () => null,
    querySelector: () => null,
  };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

// Stubs for globals referenced in board-render.js function bodies
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
globalThis._smgmtRender = _noop;
globalThis._smgmtLoadEstimates = _noop;
globalThis._smgmtLoadConflicts = _noop;
globalThis._smgmtLoadDepOrder = _noop;
globalThis.sprintHealthStripInit = _noop;

// Provide a fake smgmt-sprint-list element so loadSprintMgmt() doesn't early-return
const _fakeListEl = { innerHTML: '' };
globalThis.document = {
  getElementById: (id) => id === 'smgmt-sprint-list' ? _fakeListEl : null,
  querySelector: () => null,
};

import { loadSprintMgmt } from '../../apps/dashboard/static/src/sprint-board/board-render.js';
import { _histLoadLedger, _histResetLedgerCache } from '../../apps/dashboard/static/src/sprint-board/history.js';

// ── Canned aggregate response ─────────────────────────────────────────────────

function _fakeAggResponse() {
  return {
    project: 'owner/repo',
    generated_at: '2026-01-01T00:00:00Z',
    sections: {
      running: [],
      needs_rework: [],
      ready_to_merge: [],
      draft: [],
      lineage: [],
      backlog: { count: 0, tickets: [] },
    },
    capacity: { budget_minutes: 180, size_minutes: { S: 5, M: 15, L: 30, XL: 60 } },
    summaries: {},
  };
}

// ── Canned history response ───────────────────────────────────────────────────

function _fakeHistoryResponse() {
  return {
    sprints: [
      {
        label: 'sprint-100',
        lifecycle_state: 'ready_to_merge',
        tickets: [],
        run_stats: { label: 'sprint-100', has_runs: true, split: [], tickets: [] },
      },
    ],
  };
}


// ═══════════════════════════════════════════════════════════════════════════════
// Board load budget: exactly 1 /api/board call via installFetchSpy
// ═══════════════════════════════════════════════════════════════════════════════

test('board budget: loadSprintMgmt makes exactly 1 /api/board fetch (installFetchSpy)', async () => {
  const repo = 'owner/budget-test';
  globalThis._slug = 'budget-test';
  globalThis._cachedFullRepo = { 'budget-test': repo };

  const spy = installFetchSpy({
    '/api/board': _fakeAggResponse(),
  });

  await loadSprintMgmt(true, null);

  assertFetchBudget(spy, '/api/board', 1);
});

test('board budget: loadSprintMgmt makes zero legacy per-sprint calls', async () => {
  const repo = 'owner/budget-test-2';
  globalThis._slug = 'budget-test-2';
  globalThis._cachedFullRepo = { 'budget-test-2': repo };

  const spy = installFetchSpy({
    '/api/board': _fakeAggResponse(),
  });

  await loadSprintMgmt(true, null);

  assertFetchBudget(spy, '/api/sprint-management', 0);
  assertFetchBudget(spy, '/api/sprints/running-all', 0);
});


// ═══════════════════════════════════════════════════════════════════════════════
// History feed budget: exactly 1 /api/sprints/history call via installFetchSpy
// ═══════════════════════════════════════════════════════════════════════════════

test('history budget: _histLoadLedger makes exactly 1 /api/sprints/history fetch', async () => {
  globalThis._slug = 'hist-test';

  // Reset ledger cache so the function doesn't serve from cache
  _histResetLedgerCache();

  const spy = installFetchSpy({
    '/api/sprints/history': _fakeHistoryResponse(),
    '/api/projects/': { history_fold_size: 10, history_cache_ttl_min: 5 },
  });

  await _histLoadLedger('owner/hist-test', {});

  assertFetchBudget(spy, '/api/sprints/history', 1);
});

test('history budget: _histLoadLedger makes at most 1 /api/projects settings fetch', async () => {
  globalThis._slug = 'hist-test-2';

  _histResetLedgerCache();

  const spy = installFetchSpy({
    '/api/sprints/history': _fakeHistoryResponse(),
    '/api/projects/': { history_fold_size: 10, history_cache_ttl_min: 5 },
  });

  await _histLoadLedger('owner/hist-test-2', {});

  assertFetchBudgetMax(spy, '/api/projects/', 1);
});

test('history budget: _histLoadLedger total fetch count ≤ 2 (1 history + 1 settings)', async () => {
  globalThis._slug = 'hist-test-3';

  _histResetLedgerCache();

  const spy = installFetchSpy({
    '/api/sprints/history': _fakeHistoryResponse(),
    '/api/projects/': { history_fold_size: 10, history_cache_ttl_min: 5 },
  });

  await _histLoadLedger('owner/hist-test-3', {});

  const total = spy.calls.length;
  assert.ok(
    total <= 2,
    `History load budget: expected ≤ 2 total fetches, got ${total}: ${JSON.stringify(spy.calls)}`,
  );
});

test('history budget: cached second call makes 0 fetches (served from module cache)', async () => {
  globalThis._slug = 'hist-cache-test';

  _histResetLedgerCache();

  // First load populates the cache
  const spy1 = installFetchSpy({
    '/api/sprints/history': _fakeHistoryResponse(),
    '/api/projects/': {},
  });
  await _histLoadLedger('owner/hist-cache-test', {});
  assertFetchBudget(spy1, '/api/sprints/history', 1);

  // Second load within TTL must serve from cache (0 new fetches)
  const spy2 = installFetchSpy({
    '/api/sprints/history': _fakeHistoryResponse(),
    '/api/projects/': {},
  });
  await _histLoadLedger('owner/hist-cache-test', {});
  assertFetchBudget(spy2, '/api/sprints/history', 0);
});


// ═══════════════════════════════════════════════════════════════════════════════
// Running first paint budget: exactly 1 /api/running call (vm sandbox)
// ═══════════════════════════════════════════════════════════════════════════════

// Extract _smgmtRunningFirstPaint from project.html and run it in a vm sandbox
// so we don't need to import the 20k-line HTML page.
const _projectHtml = readFileSync(
  path.join(__dirname, '../../apps/dashboard/static/project.html'),
  'utf-8',
);

function _extractFunction(name, src) {
  const re = new RegExp(`(?:async )?function ${name}\\([^)]*\\)\\s*\\{`, 'm');
  const m = re.exec(src);
  if (!m) throw new Error(`${name} not found in project.html`);
  let i = m.index + m[0].length;
  let depth = 1;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(m.index, i);
}

const _runningFirstPaintSrc = _extractFunction('_smgmtRunningFirstPaint', _projectHtml);
const _lingerActiveSrc = _extractFunction('_smgmtLingerActive', _projectHtml);
const _isLingerSrc = _extractFunction('_smgmtIsLinger', _projectHtml);

assert.ok(
  _runningFirstPaintSrc.includes('/api/running'),
  'sanity: extracted _smgmtRunningFirstPaint must reference /api/running',
);

function _buildRunningSandbox(fetchImpl) {
  const calls = { livePollTick: 0, runningViewUpdate: [], runHeadPatch: [] };
  const sandbox = {
    console,
    fetch: fetchImpl,
    _smgmtRepo: () => 'owner/running-test',
    _smgmtLiveCacheSeq: 0,
    _smgmtRunningLabels: new Set(),
    _smgmtLiveCache: {},
    _smgmtLiveCacheWriteSeq: {},
    _smgmtLiveHeartbeatAt: null,
    _smgmtData: null,
    _smgmtRender: () => {},
    _smgmtLivePatch: () => {},
    _sprintProgressRender: () => {},
    _snavPanelOpen: false,
    _snavRenderPanel: () => {},
    _smgmtRunningViewUpdate: (label, data) => { calls.runningViewUpdate.push([label, data]); },
    _smgmtRunHeadPatch: (label, data) => { calls.runHeadPatch.push([label, data]); },
    _smgmtLivePollTick: () => { calls.livePollTick++; },
    _smgmtLingerLabels: new Map(),
    _SMGMT_LINGER_MS: 60 * 60 * 1000,
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(_lingerActiveSrc, ctx);
  vm.runInContext(_isLingerSrc, ctx);
  vm.runInContext(_runningFirstPaintSrc, ctx);
  return { sandbox, ctx, calls };
}

test('running budget: _smgmtRunningFirstPaint makes exactly 1 /api/running fetch (installFetchSpy + vm)', async () => {
  const spyCalls = [];
  const fetchImpl = async (url) => {
    spyCalls.push(String(url));
    if (String(url).includes('/api/running')) {
      return { ok: true, json: async () => ({ sprint_label: 'sprint-101', issues: [] }) };
    }
    throw new Error(`Unexpected fetch in running-budget test: ${url}`);
  };

  const { ctx } = _buildRunningSandbox(fetchImpl);
  await vm.runInContext('_smgmtRunningFirstPaint()', ctx);

  const runningCalls = spyCalls.filter(u => u.includes('/api/running'));
  assert.equal(
    runningCalls.length,
    1,
    `Running first paint budget: expected exactly 1 /api/running fetch, got ${runningCalls.length}: ${JSON.stringify(spyCalls)}`,
  );
});

test('running budget: _smgmtRunningFirstPaint makes 0 /api/running fetches when repo is null', async () => {
  const spyCalls = [];
  const fetchImpl = async (url) => {
    spyCalls.push(String(url));
    return { ok: false };
  };

  const { sandbox, ctx } = _buildRunningSandbox(fetchImpl);
  // Override _smgmtRepo to return null (no project selected)
  sandbox._smgmtRepo = () => null;

  await vm.runInContext('_smgmtRunningFirstPaint()', ctx);

  assertFetchBudget({ calls: spyCalls }, '/api/running', 0);
});


// ═══════════════════════════════════════════════════════════════════════════════
// AC6: driving a double board-load via spy causes assertFetchBudget to throw
// ═══════════════════════════════════════════════════════════════════════════════

test('AC6: two consecutive loadSprintMgmt calls accumulate 2 /api/board fetches — assertFetchBudget detects the violation', async () => {
  const repo = 'owner/ac6-test';
  globalThis._slug = 'ac6-test';
  globalThis._cachedFullRepo = { 'ac6-test': repo };

  const spy = installFetchSpy({
    '/api/board': _fakeAggResponse(),
  });

  // Simulate a scenario where loadSprintMgmt is called twice (e.g. a double-render
  // triggered by a bug). The spy records both fetches so a budget-1 assertion fails.
  await loadSprintMgmt(true, null);
  await loadSprintMgmt(true, null);

  assert.equal(
    spy.calls.filter(u => u.includes('/api/board')).length,
    2,
    'sanity: spy must record 2 /api/board calls from the double invocation',
  );

  // assertFetchBudget must detect the violation and throw
  assert.throws(
    () => assertFetchBudget(spy, '/api/board', 1),
    (err) => {
      assert.ok(err instanceof assert.AssertionError || err.message.includes('/api/board'),
        'must throw an assertion about /api/board');
      return true;
    },
    'assertFetchBudget must throw when 2 calls exceed the budget of 1',
  );
});

test('AC6: assertFetchBudget passes immediately after a single loadSprintMgmt call', async () => {
  const repo = 'owner/ac6-ok-test';
  globalThis._slug = 'ac6-ok-test';
  globalThis._cachedFullRepo = { 'ac6-ok-test': repo };

  const spy = installFetchSpy({
    '/api/board': _fakeAggResponse(),
  });

  await loadSprintMgmt(true, null);

  // Must not throw — single call is within budget
  assertFetchBudget(spy, '/api/board', 1);
});
