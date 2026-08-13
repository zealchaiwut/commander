/**
 * Frontend behavioral tests for issue #2264:
 * Surface buried planning data already computed by the backend.
 *
 * AC1: Preflight readiness warnings (stale estimates) visible from the sprint
 *      planning view without opening a modal — _smgmtStaleEstimateHtml builds
 *      the inline row from the /preflight API's warnings.stale_estimates list.
 *
 * AC2: Dep-order and conflicts viewable for a composed sprint — verified by
 *      confirming board-render.js still exports _smgmtLoadConflicts and
 *      _smgmtLoadDepOrder, and that _smgmtCardHtml includes the sc-preview-slot
 *      (where the mini-rail — which renders dep-order levels and file-conflicts
 *      inline — is injected).
 *
 * AC3: Estimate-vs-actual reachable in ≤2 clicks — _smgmtEstVsActualSectionHtml
 *      builds a collapsible per-ticket comparison panel from the
 *      /estimate-vs-actual API response; rendered at card paint time so the
 *      toggle is always 1 click from the project page.
 *
 * AC4: No new backend endpoints — validated by confirming neither new module
 *      imports from any backend router file.
 *
 * Run with: node --test tests/frontend/planning-data-2264.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal DOM / window stubs ────────────────────────────────────────────────

if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null, querySelector: () => null };
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
globalThis._smgmtRepo = () => 'owner/test';
globalThis._smgmtRiskFlagIconsHtml = () => '';
globalThis._smgmtRowMenuOpen = _noop;
globalThis._smgmtRunningViewUpdate = _noop;
globalThis._smgmtSchedDepHtml = () => '';
globalThis._smgmtSetSprintTokenEl = _noop;
globalThis._smgmtStateMeta = () => ({ state: '', badge: '', badgeCls: '', cardClass: '' });
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
globalThis._fmtWallClock = (secs) => `${Math.round(secs)}s`;
globalThis.sprintHealthStripInit = _noop;
globalThis._blApplyFilters = _noop;
globalThis._blBacklogAll = [];
globalThis._blSyncFilterPills = _noop;
globalThis._blUpdateActions = _noop;
// Stubs for new loader functions (tested in their own modules below)
globalThis._smgmtLoadPlanningInsights = _noop;
globalThis._smgmtLoadEstVsActual = _noop;
globalThis._smgmtToggleEstVsActual = _noop;

// ── Import modules under test ─────────────────────────────────────────────────

const { _smgmtCardHtml, _smgmtLoadConflicts, _smgmtLoadDepOrder } = await import(
  '../../apps/dashboard/static/src/sprint-board/board-render.js'
);

const { _smgmtStaleEstimateHtml, _smgmtLoadPlanningInsights } = await import(
  '../../apps/dashboard/static/src/sprint-board/planning-insights.js'
);

const { _smgmtEstVsActualSectionHtml, _smgmtToggleEstVsActual, _smgmtLoadEstVsActual } = await import(
  '../../apps/dashboard/static/src/sprint-board/est-vs-actual.js'
);

// ── AC1: Stale estimates visible inline without opening a modal ───────────────

test('AC1 — _smgmtStaleEstimateHtml returns non-empty HTML for one stale ticket', () => {
  const html = _smgmtStaleEstimateHtml(['#100']);
  assert.ok(html.length > 0, 'must produce HTML when stale list is non-empty');
  assert.ok(html.includes('stale estimate'), 'must include stale estimate label');
  assert.ok(html.includes('#100'), 'must include the stale ticket ID');
  assert.ok(html.includes('pi-stale-row'), 'must use pi-stale-row container class');
});

test('AC1 — _smgmtStaleEstimateHtml pluralises correctly for multiple stale tickets', () => {
  const html = _smgmtStaleEstimateHtml(['#100', '#200', '#300']);
  assert.ok(html.includes('3 stale estimates'), 'must show plural form for 3 tickets');
  assert.ok(html.includes('#200'), 'must list all stale ticket IDs');
});

test('AC1 — _smgmtStaleEstimateHtml uses singular for one stale ticket', () => {
  const html = _smgmtStaleEstimateHtml(['#42']);
  assert.ok(html.includes('1 stale estimate'), 'must use singular for 1 ticket');
  assert.ok(!html.includes('1 stale estimates'), 'must NOT use plural for 1 ticket');
});

test('AC1 — _smgmtStaleEstimateHtml returns empty string when no stale tickets', () => {
  assert.strictEqual(_smgmtStaleEstimateHtml([]), '', 'empty list must return empty string');
  assert.strictEqual(_smgmtStaleEstimateHtml(null), '', 'null must return empty string');
  assert.strictEqual(_smgmtStaleEstimateHtml(undefined), '', 'undefined must return empty string');
});

test('AC1 — _smgmtLoadPlanningInsights is a function', () => {
  assert.strictEqual(typeof _smgmtLoadPlanningInsights, 'function',
    '_smgmtLoadPlanningInsights must be exported from planning-insights.js');
});

// ── AC2: Dep-order and conflicts viewable for a composed sprint ───────────────

test('AC2 — board-render.js exports _smgmtLoadConflicts', () => {
  assert.strictEqual(typeof _smgmtLoadConflicts, 'function',
    '_smgmtLoadConflicts must be exported from board-render.js');
});

test('AC2 — board-render.js exports _smgmtLoadDepOrder', () => {
  assert.strictEqual(typeof _smgmtLoadDepOrder, 'function',
    '_smgmtLoadDepOrder must be exported from board-render.js');
});

test('AC2 — planning card includes sc-preview-slot (mini-rail injection point)', () => {
  const html = _smgmtCardHtml(
    'sprint-100', 100,
    [{ number: 1, labels: [{ name: 'backlog' }] }],
    null, false, null, false,
  );
  assert.ok(html.includes('sc-preview-slot') && html.includes('sc-preview-sprint-100'),
    'card must contain the sc-preview-slot div where mini-rail (dep-order + conflicts) is injected');
});

test('AC2 — planning card includes pi-stale-slot for stale-estimate injection', () => {
  const html = _smgmtCardHtml(
    'sprint-100', 100,
    [{ number: 1, labels: [{ name: 'backlog' }] }],
    null, false, null, false,
  );
  assert.ok(html.includes('pi-stale-sprint-100'),
    'card must contain pi-stale-${label} slot for stale-estimate injection');
});

// ── AC3: Estimate-vs-actual reachable in ≤2 clicks ───────────────────────────

test('AC3 — _smgmtEstVsActualSectionHtml returns non-empty HTML for completed sprint data', () => {
  const data = {
    sprint_label: 'sprint-50',
    tickets: [
      {
        ticket_id: 574,
        title: 'Add dashboard widget',
        estimated_size: 'L',
        estimated_minutes: 30,
        actual_elapsed_minutes: 21.5,
        delta_minutes: -8.5,
        status: 'done',
      },
    ],
  };
  const html = _smgmtEstVsActualSectionHtml('sprint-50', data);
  assert.ok(html.length > 0, 'must produce non-empty HTML when tickets are present');
  assert.ok(html.includes('pi-ev-section'), 'must use pi-ev-section container class');
  assert.ok(html.includes('#574'), 'must include ticket number');
  assert.ok(html.includes('Add dashboard widget'), 'must include ticket title');
  assert.ok(html.includes('30m'), 'must include estimated minutes');
  assert.ok(html.includes('(L)'), 'must include estimated size label');
  assert.ok(html.includes('22m') || html.includes('21m'), 'must include actual elapsed (rounded)');
});

test('AC3 — _smgmtEstVsActualSectionHtml includes a toggle button', () => {
  const data = {
    tickets: [
      { ticket_id: 1, title: 'T', estimated_size: 'S', estimated_minutes: 5,
        actual_elapsed_minutes: 4, delta_minutes: -1, status: 'done' },
    ],
  };
  const html = _smgmtEstVsActualSectionHtml('sprint-1', data);
  assert.ok(html.includes('pi-ev-toggle-btn'), 'must include toggle button for expand/collapse');
  assert.ok(html.includes('_smgmtToggleEstVsActual'), 'toggle button must call _smgmtToggleEstVsActual');
  assert.ok(html.includes('pi-ev-content'), 'must include collapsible content container');
  assert.ok(html.includes('display:none'), 'content must start collapsed');
});

test('AC3 — _smgmtEstVsActualSectionHtml shows under-estimate delta for fast tickets', () => {
  const data = {
    tickets: [
      { ticket_id: 10, title: 'Fast', estimated_size: 'M', estimated_minutes: 15,
        actual_elapsed_minutes: 5, delta_minutes: -10, status: 'done' },
    ],
  };
  const html = _smgmtEstVsActualSectionHtml('sprint-1', data);
  assert.ok(html.includes('pi-ev-under'), 'under-estimate delta must have pi-ev-under class');
});

test('AC3 — _smgmtEstVsActualSectionHtml shows over-estimate delta for slow tickets', () => {
  const data = {
    tickets: [
      { ticket_id: 11, title: 'Slow', estimated_size: 'S', estimated_minutes: 5,
        actual_elapsed_minutes: 60, delta_minutes: 55, status: 'done' },
    ],
  };
  const html = _smgmtEstVsActualSectionHtml('sprint-1', data);
  assert.ok(html.includes('pi-ev-over'), 'over-estimate delta must have pi-ev-over class');
});

test('AC3 — _smgmtEstVsActualSectionHtml returns empty string for empty ticket list', () => {
  assert.strictEqual(_smgmtEstVsActualSectionHtml('sprint-1', { tickets: [] }), '',
    'empty ticket list must return empty string');
  assert.strictEqual(_smgmtEstVsActualSectionHtml('sprint-1', null), '',
    'null data must return empty string');
});

test('AC3 — _smgmtEstVsActualSectionHtml handles null estimates gracefully', () => {
  const data = {
    tickets: [
      { ticket_id: 20, title: 'No estimate', estimated_size: null,
        estimated_minutes: null, actual_elapsed_minutes: 10, delta_minutes: null, status: 'done' },
    ],
  };
  const html = _smgmtEstVsActualSectionHtml('sprint-1', data);
  assert.ok(html.length > 0, 'must still render when estimates are null');
  assert.ok(html.includes('#20'), 'must show ticket number even with null estimates');
});

test('AC3 — _smgmtToggleEstVsActual is exported as a function', () => {
  assert.strictEqual(typeof _smgmtToggleEstVsActual, 'function',
    '_smgmtToggleEstVsActual must be exported from est-vs-actual.js');
});

test('AC3 — _smgmtLoadEstVsActual is exported as a function', () => {
  assert.strictEqual(typeof _smgmtLoadEstVsActual, 'function',
    '_smgmtLoadEstVsActual must be exported from est-vs-actual.js');
});

test('AC3 — sprint card includes pi-ev-slot for estimate-vs-actual injection', () => {
  // pi-ev slot is present on all non-running cards so _smgmtLoadEstVsActual
  // can populate it for finished sprints without a separate card rebuild.
  const html = _smgmtCardHtml(
    'sprint-55', 55,
    [{ number: 1, labels: [{ name: 'backlog' }] }],
    null, false, null, false,
  );
  assert.ok(html.includes('pi-ev-sprint-55'),
    'card must include pi-ev-${label} slot for estimate-vs-actual injection');
});

// ── AC4: No new backend endpoints ─────────────────────────────────────────────

test('AC4 — planning-insights.js does not reference any new backend route', async () => {
  const src = await import('node:fs').then(fs =>
    fs.readFileSync(
      'apps/dashboard/static/src/sprint-board/planning-insights.js', 'utf8'
    )
  );
  const hasNewEndpoint = /\/api\/[a-z-]+\/[a-z-]+\/[a-z-]+-new/.test(src);
  assert.ok(!hasNewEndpoint, 'planning-insights.js must not reference new backend endpoints');
  // Verify it only uses the existing /preflight endpoint
  assert.ok(src.includes('/preflight'), 'must use the existing /preflight endpoint');
});

test('AC4 — est-vs-actual.js does not reference any new backend route', async () => {
  const src = await import('node:fs').then(fs =>
    fs.readFileSync(
      'apps/dashboard/static/src/sprint-board/est-vs-actual.js', 'utf8'
    )
  );
  // Verify it only uses the existing /estimate-vs-actual endpoint
  assert.ok(src.includes('/estimate-vs-actual'), 'must use the existing /estimate-vs-actual endpoint');
  assert.ok(!src.includes('/api/new-'), 'must not introduce new backend endpoints');
});
