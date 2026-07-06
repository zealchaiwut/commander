/**
 * Behavioral tests for issue #1748: backlog selection state persists through
 * auto-refresh re-renders.
 *
 * AC1 — selection Set survives _smgmtRenderBacklog; bar stays visible.
 * AC2 — innerHTML is NOT replaced when selection is active (suppressed).
 * AC4 — simulate select → render tick → assert selection + bar survive.
 *
 * Run with: node --test tests/frontend/backlog-selection-persist.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal fake DOM element factory ─────────────────────────────────────────

function makeFakeEl(id) {
  const classes = new Set();
  return {
    id,
    innerHTML: '',
    textContent: '',
    hidden: false,
    classList: {
      toggle(cls, force) {
        if (force === undefined) {
          classes.has(cls) ? classes.delete(cls) : classes.add(cls);
        } else {
          force ? classes.add(cls) : classes.delete(cls);
        }
      },
      contains: (cls) => classes.has(cls),
      add:      (cls) => classes.add(cls),
      remove:   (cls) => classes.delete(cls),
    },
  };
}

// ── Shared selection set (mutated by tests) ───────────────────────────────────

const _selectionSet = new Set();

// ── DOM stubs ─────────────────────────────────────────────────────────────────

const _ticketsEl  = makeFakeEl('smgmt-backlog-tickets');
const _countEl    = makeFakeEl('smgmt-backlog-count');
const _eyebrowEl  = makeFakeEl('bl-eyebrow');
const _bulkEstBtn = makeFakeEl('smgmt-backlog-bulk-est-btn');
const _barEl      = makeFakeEl('proj-selection-bar');
const _selCount   = makeFakeEl('smgmt-sel-count');

const _domMap = {
  'smgmt-backlog-tickets':     _ticketsEl,
  'smgmt-backlog-count':       _countEl,
  'bl-eyebrow':                _eyebrowEl,
  'smgmt-backlog-bulk-est-btn': _bulkEstBtn,
  'proj-selection-bar':        _barEl,
  'smgmt-sel-count':           _selCount,
};

// ── Globals required by board-render.js at import time ────────────────────────

const _noop    = () => {};
const _noopSet = new Set();

globalThis.document = {
  getElementById:  (id) => _domMap[id] ?? null,
  querySelector:   ()  => null,
  querySelectorAll: () => [],
};
globalThis.window = globalThis;

// Shared selection set wired into the module's global lookup
globalThis._smgmtSelectedIssues = _selectionSet;

// _smgmtUpdateSelectionUI — mirrors the real implementation from project.html.
// Called by _smgmtRenderBacklog after the AC1/AC2 fix so the bar is synced.
globalThis._smgmtUpdateSelectionUI = () => {
  const n = globalThis._smgmtSelectedIssues
    ? globalThis._smgmtSelectedIssues.size : 0;
  const bar = globalThis.document.getElementById('proj-selection-bar');
  if (bar) bar.classList.toggle('hidden', n === 0);
  const cnt = globalThis.document.getElementById('smgmt-sel-count');
  if (cnt) cnt.textContent = `${n} issue${n !== 1 ? 's' : ''} selected`;
  if (typeof globalThis._blUpdateActions === 'function') globalThis._blUpdateActions();
};

// Required board-render.js globals
globalThis._blBacklogAll      = [];
globalThis._blApplyFilters    = (tickets) => tickets;
globalThis._blSyncFilterPills = _noop;
globalThis._blUpdateActions   = _noop;
globalThis._smgmtTicketToSprint = {};
globalThis._smgmtOrderedLabels  = [];
globalThis._smgmtRunningLabels  = _noopSet;
globalThis._smgmtData           = null;
globalThis._smgmtSchedDepHtml   = () => '';
globalThis._smgmtSchedToggleHtml = () => '';
globalThis._estDataCache        = {};
globalThis._cachedFullRepo      = {};
globalThis._slug                = 'test';
globalThis.escHtml              = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
globalThis.sprintLabelDisplay   = (l) => l;
globalThis.colorizeLogLine      = (s) => s;
globalThis._smgmtCtxMenuOpen    = _noop;
globalThis._smgmtToggleSelect   = _noop;
globalThis._smgmtRowMenuOpen    = _noop;
globalThis._smgmtRowClickSelect = _noop;

// Globals needed by other board-render.js functions (loaded at module parse)
globalThis._smgmtEnsureCapData       = _noop;
globalThis._smgmtLoadMiniRail        = _noop;
globalThis._smgmtMiniRailRestoreCached = _noop;
globalThis._smgmtRenderAllCapBars    = _noop;
globalThis._smgmtUpdateSubnav        = _noop;
globalThis._smgmtActiveAgentsHtml    = () => '';
globalThis._smgmtAgentTagClass       = () => '';
globalThis._smgmtApplySort           = _noop;
globalThis._smgmtBulkEstimate        = _noop;
globalThis._smgmtBySprint            = {};
globalThis._smgmtCancelBannerHtml    = () => '';
globalThis._smgmtCapacityInputHtml   = () => '';
globalThis._smgmtCheckEstimatorHealth = _noop;
globalThis._smgmtCloseIssueOpen      = _noop;
globalThis._smgmtConflictsByIssue    = {};
globalThis._smgmtDagDataCache        = {};
globalThis._smgmtDeactivatedLabels   = _noopSet;
globalThis._smgmtDepOrderByIssue     = {};
globalThis._smgmtEstimateBadgeHtml   = () => '';
globalThis._smgmtEstimatorAvailable  = false;
globalThis._smgmtFilterApply         = _noop;
globalThis._smgmtFinishCards         = {};
globalThis._smgmtFinishedLabels      = _noopSet;
globalThis._smgmtHasCompletedTickets = _noop;
globalThis._smgmtInitCapacityGauges  = _noop;
globalThis._smgmtInjectOutcomeBand   = _noop;
globalThis._smgmtIsCancelled         = () => false;
globalThis._smgmtKbRestoreFocus      = _noop;
globalThis._smgmtLabelColors         = {};
globalThis._smgmtLabelFilterToggle   = _noop;
globalThis._smgmtLabelFilterToggleExpand = _noop;
globalThis._smgmtLastLabelIssues     = {};
globalThis._smgmtLevelsHtml          = () => '';
globalThis._smgmtLiveAgentBadgesHtml = () => '';
globalThis._smgmtLiveCache           = {};
globalThis._smgmtLiveCacheRepo       = null;
globalThis._smgmtLiveLogLinesHtml    = () => '';
globalThis._smgmtLivePollRestart     = _noop;
globalThis._smgmtLingerRestore       = _noop;
globalThis._smgmtLingerStart         = _noop;
globalThis._smgmtIsLinger            = () => false;
globalThis._smgmtLingerLive          = () => null;
globalThis._smgmtNextChildLabel      = _noop;
globalThis._smgmtOutcomeCache        = {};
globalThis._smgmtOutcomeLogHtml      = () => '';
globalThis._smgmtPrimaryRunningLabel = _noop;
globalThis._smgmtReEstimate          = _noop;
globalThis._smgmtRepo                = () => 'owner/repo';
globalThis._smgmtRiskFlagIconsHtml   = () => '';
globalThis._smgmtRunningViewUpdate   = _noop;
globalThis._smgmtSetSprintTokenEl    = _noop;
globalThis._smgmtStateMeta           = _noop;
globalThis._smgmtUpdateCapacityGauge = _noop;
globalThis._smgmtUpdateCleanupBtn    = _noop;
globalThis._smgmtUpdateConflictBadge = _noop;
globalThis._smgmtUpdateDepOrderBadge = _noop;
globalThis._smgmtHydrateSchedToggles = _noop;
globalThis._smgmtAnySprintRunning    = false;
globalThis._smgmtRowActivity         = {};

// Import AFTER globals are set
const { _smgmtRenderBacklog } = await import(
  '../../apps/dashboard/static/src/sprint-board/board-render.js'
);

// ── Helpers ───────────────────────────────────────────────────────────────────

function _makeTicket(number) {
  return { number, title: `Ticket ${number}`, url: `#${number}`, labels: [], body: '', status: 'backlog' };
}

function _reset() {
  _selectionSet.clear();
  _barEl.classList.add('hidden');
  _ticketsEl.innerHTML = '';
  _selCount.textContent = '';
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test('AC1: _smgmtSelectedIssues (JS Set) persists through _smgmtRenderBacklog', () => {
  _reset();
  const tickets = [_makeTicket(101), _makeTicket(102), _makeTicket(103)];

  _smgmtRenderBacklog(tickets);
  assert.equal(_selectionSet.size, 0, 'no selection initially');

  _selectionSet.add(101);
  _selectionSet.add(102);

  // Simulate auto-refresh render tick
  _smgmtRenderBacklog(tickets);

  assert.ok(_selectionSet.has(101), 'issue 101 still selected after render');
  assert.ok(_selectionSet.has(102), 'issue 102 still selected after render');
  assert.ok(!_selectionSet.has(103), 'issue 103 not selected');
  assert.equal(_selectionSet.size, 2, 'selection count unchanged');
});

test('AC1: proj-selection-bar stays visible after render with active selection', () => {
  _reset();
  const tickets = [_makeTicket(201), _makeTicket(202)];

  _selectionSet.add(201);
  _barEl.classList.remove('hidden');

  _smgmtRenderBacklog(tickets);

  assert.ok(!_barEl.classList.contains('hidden'), 'bar not hidden after render with active selection');
  assert.equal(_selCount.textContent, '1 issue selected', 'bar count text correct');
});

test('AC1: proj-selection-bar is hidden when no issues are selected', () => {
  _reset();
  const tickets = [_makeTicket(301)];

  _smgmtRenderBacklog(tickets);

  assert.ok(_barEl.classList.contains('hidden'), 'bar hidden when no issues selected');
});

test('AC2: backlog innerHTML NOT replaced during active selection', () => {
  _reset();
  const tickets = [_makeTicket(401), _makeTicket(402)];

  // First render (no selection) — writes HTML
  _smgmtRenderBacklog(tickets);
  const htmlBefore = _ticketsEl.innerHTML;
  assert.ok(htmlBefore.length > 0, 'initial render wrote HTML');

  // Select an issue — re-render should suppress innerHTML replacement
  _selectionSet.add(401);
  _smgmtRenderBacklog(tickets);

  assert.equal(_ticketsEl.innerHTML, htmlBefore, 'innerHTML unchanged while selection active (AC2)');
});

test('AC2: backlog innerHTML IS replaced when no selection (normal auto-refresh)', () => {
  _reset();
  const ticketsV1 = [_makeTicket(501)];
  const ticketsV2 = [_makeTicket(501), _makeTicket(502)];

  _smgmtRenderBacklog(ticketsV1);
  const html1 = _ticketsEl.innerHTML;
  assert.ok(html1.includes('501'), 'first render has ticket 501');

  _smgmtRenderBacklog(ticketsV2);
  const html2 = _ticketsEl.innerHTML;
  assert.ok(html2.includes('502'), 'second render has new ticket 502');
  assert.notEqual(html1, html2, 'innerHTML updated when no selection active');
});

test('AC4: select then two render ticks — selection and bar survive', () => {
  _reset();
  const tickets = [_makeTicket(601), _makeTicket(602), _makeTicket(603)];

  _smgmtRenderBacklog(tickets);

  _selectionSet.add(601);
  _selectionSet.add(602);
  _selectionSet.add(603);
  _barEl.classList.remove('hidden');

  // Two auto-refresh render ticks
  _smgmtRenderBacklog(tickets);
  _smgmtRenderBacklog(tickets);

  assert.ok(_selectionSet.has(601), '601 selected after 2 render ticks');
  assert.ok(_selectionSet.has(602), '602 selected after 2 render ticks');
  assert.ok(_selectionSet.has(603), '603 selected after 2 render ticks');
  assert.equal(_selectionSet.size, 3, 'all 3 issues still selected');
  assert.ok(!_barEl.classList.contains('hidden'), 'bar visible after render ticks');
  assert.equal(_selCount.textContent, '3 issues selected', 'bar count correct');
});
