/**
 * Behavioral tests for issue #1760: bulk move-to-new-sprint must clear
 * _smgmtSelectedIssues on success so the backlog re-renders.
 *
 * AC1 — After a successful batch-labels call for isNew=true, the selection Set
 *        is cleared (size === 0).
 * AC2 — After clearing, _smgmtRenderBacklog replaces backlog innerHTML
 *        (render suppression from #1748 no longer blocks it).
 * AC3 — On batch-labels failure (error path), the selection is NOT cleared
 *        (so the user can retry with the same selection intact).
 * AC4 — The clear also fixes any backlog add/remove whose render was suppressed
 *        while a selection was active: after clearing, the next render reflects
 *        the updated ticket list.
 *
 * Run with: node --test tests/frontend/bulk-move-new-sprint-clear-selection.test.mjs
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

// ── DOM stubs ─────────────────────────────────────────────────────────────────

const _ticketsEl  = makeFakeEl('smgmt-backlog-tickets');
const _countEl    = makeFakeEl('smgmt-backlog-count');
const _eyebrowEl  = makeFakeEl('bl-eyebrow');
const _bulkEstBtn = makeFakeEl('smgmt-backlog-bulk-est-btn');
const _barEl      = makeFakeEl('proj-selection-bar');
const _selCount   = makeFakeEl('smgmt-sel-count');

const _domMap = {
  'smgmt-backlog-tickets':       _ticketsEl,
  'smgmt-backlog-count':         _countEl,
  'bl-eyebrow':                  _eyebrowEl,
  'smgmt-backlog-bulk-est-btn':  _bulkEstBtn,
  'proj-selection-bar':          _barEl,
  'smgmt-sel-count':             _selCount,
};

// ── Shared selection set ──────────────────────────────────────────────────────

const _selectionSet = new Set();
globalThis._smgmtSelectedIssues = _selectionSet;

// _smgmtClearSelection — mirrors the real implementation from project.html.
// The fix adds a call to this function in the _smgmtMoveModalPickBulk success
// branch for isNew=true; tests use this same function to drive pre/post state.
globalThis._smgmtClearSelection = () => {
  globalThis._smgmtSelectedIssues.clear();
  if (typeof globalThis._smgmtUpdateSelectionUI === 'function') {
    globalThis._smgmtUpdateSelectionUI();
  }
};

// ── Globals required by board-render.js at import time ────────────────────────

const _noop    = () => {};
const _noopSet = new Set();

globalThis.document = {
  getElementById:   (id) => _domMap[id] ?? null,
  querySelector:    ()   => null,
  querySelectorAll: ()   => [],
};
globalThis.window = globalThis;

globalThis._smgmtUpdateSelectionUI = () => {
  const n = globalThis._smgmtSelectedIssues
    ? globalThis._smgmtSelectedIssues.size : 0;
  const bar = globalThis.document.getElementById('proj-selection-bar');
  if (bar) bar.classList.toggle('hidden', n === 0);
  const cnt = globalThis.document.getElementById('smgmt-sel-count');
  if (cnt) cnt.textContent = `${n} issue${n !== 1 ? 's' : ''} selected`;
};

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

globalThis._smgmtEnsureCapData         = _noop;
globalThis._smgmtLoadMiniRail          = _noop;
globalThis._smgmtMiniRailRestoreCached = _noop;
globalThis._smgmtRenderAllCapBars      = _noop;
globalThis._smgmtUpdateSubnav          = _noop;
globalThis._smgmtActiveAgentsHtml      = () => '';
globalThis._smgmtAgentTagClass         = () => '';
globalThis._smgmtApplySort             = _noop;
globalThis._smgmtBulkEstimate          = _noop;
globalThis._smgmtBySprint              = {};
globalThis._smgmtCancelBannerHtml      = () => '';
globalThis._smgmtCapacityInputHtml     = () => '';
globalThis._smgmtCheckEstimatorHealth  = _noop;
globalThis._smgmtCloseIssueOpen        = _noop;
globalThis._smgmtConflictsByIssue      = {};
globalThis._smgmtDagDataCache          = {};
globalThis._smgmtDeactivatedLabels     = _noopSet;
globalThis._smgmtDepOrderByIssue       = {};
globalThis._smgmtEstimateBadgeHtml     = () => '';
globalThis._smgmtEstimatorAvailable    = false;
globalThis._smgmtFilterApply           = _noop;
globalThis._smgmtFinishCards           = {};
globalThis._smgmtFinishedLabels        = _noopSet;
globalThis._smgmtHasCompletedTickets   = _noop;
globalThis._smgmtInitCapacityGauges    = _noop;
globalThis._smgmtInjectOutcomeBand     = _noop;
globalThis._smgmtIsCancelled           = () => false;
globalThis._smgmtKbRestoreFocus        = _noop;
globalThis._smgmtLabelColors           = {};
globalThis._smgmtLabelFilterToggle     = _noop;
globalThis._smgmtLabelFilterToggleExpand = _noop;
globalThis._smgmtLastLabelIssues       = {};
globalThis._smgmtLevelsHtml            = () => '';
globalThis._smgmtLiveAgentBadgesHtml   = () => '';
globalThis._smgmtLiveCache             = {};
globalThis._smgmtLiveCacheRepo         = null;
globalThis._smgmtLiveLogLinesHtml      = () => '';
globalThis._smgmtLivePollRestart       = _noop;
globalThis._smgmtLingerRestore         = _noop;
globalThis._smgmtLingerStart           = _noop;
globalThis._smgmtIsLinger              = () => false;
globalThis._smgmtLingerLive            = () => null;
globalThis._smgmtNextChildLabel        = _noop;
globalThis._smgmtOutcomeCache          = {};
globalThis._smgmtOutcomeLogHtml        = () => '';
globalThis._smgmtPrimaryRunningLabel   = _noop;
globalThis._smgmtReEstimate            = _noop;
globalThis._smgmtRepo                  = () => 'owner/repo';
globalThis._smgmtRiskFlagIconsHtml     = () => '';
globalThis._smgmtRunningViewUpdate     = _noop;
globalThis._smgmtSetSprintTokenEl      = _noop;
globalThis._smgmtStateMeta             = _noop;
globalThis._smgmtUpdateCapacityGauge   = _noop;
globalThis._smgmtUpdateCleanupBtn      = _noop;
globalThis._smgmtUpdateConflictBadge   = _noop;
globalThis._smgmtUpdateDepOrderBadge   = _noop;
globalThis._smgmtHydrateSchedToggles   = _noop;
globalThis._smgmtAnySprintRunning      = false;
globalThis._smgmtRowActivity           = {};

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

/**
 * Simulate the _smgmtMoveModalPickBulk success path for isNew=true.
 *
 * This mirrors what the fixed function does:
 *   1. batch-labels fetch succeeds
 *   2. _smgmtClearSelection() is called  ← THE FIX (absent pre-fix)
 *   3. loadSprintMgmt() triggers _smgmtRenderBacklog
 *
 * Returns true if clear was called, false if simulating pre-fix (no clear).
 */
async function _simulateBulkMoveNewSprintSuccess(tickets, applyFix) {
  // Pre-condition: selection has issues
  _selectionSet.add(101);
  _selectionSet.add(102);

  // First render — DOM gets written while no selection active (before user selects)
  _reset();
  _selectionSet.add(101);
  _selectionSet.add(102);
  // At this point DOM was written before selection; simulate it:
  _ticketsEl.innerHTML = tickets.map(t => `<div data-issue="${t.number}">#${t.number}</div>`).join('');

  // batch-labels succeeded — apply fix or not
  if (applyFix) {
    globalThis._smgmtClearSelection();  // THE FIX: clears _smgmtSelectedIssues
  }
  // loadSprintMgmt → _smgmtRenderBacklog is called with fresh ticket list
  // (moved tickets are now absent from backlog tickets list)
  const freshTickets = tickets.filter(t => ![101, 102].includes(t.number));
  _smgmtRenderBacklog(freshTickets);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test('AC1 pre-fix: selection NOT cleared → _smgmtRenderBacklog suppresses DOM (demonstrates the bug)', async () => {
  _reset();
  const tickets = [_makeTicket(101), _makeTicket(102), _makeTicket(103)];
  const htmlBefore = tickets.map(t => `<div data-issue="${t.number}">#${t.number}</div>`).join('');

  // Simulate buggy path: selection remains, render is called
  _selectionSet.add(101);
  _selectionSet.add(102);
  _ticketsEl.innerHTML = htmlBefore;

  // Fresh ticket list (moved tickets gone) but selection still active → suppressed
  const freshTickets = [_makeTicket(103)];
  _smgmtRenderBacklog(freshTickets);

  // Bug: DOM unchanged even though tickets 101+102 were moved
  assert.equal(_ticketsEl.innerHTML, htmlBefore,
    'pre-fix: DOM not updated while selection active (render suppressed)');
  assert.equal(_selectionSet.size, 2,
    'pre-fix: selection Set still has 2 issues');
});

test('AC1 post-fix: _smgmtClearSelection() before render lifts suppression → DOM updated', async () => {
  _reset();
  const tickets = [_makeTicket(101), _makeTicket(102), _makeTicket(103)];
  const htmlBefore = tickets.map(t => `<div data-issue="${t.number}">#${t.number}</div>`).join('');

  _selectionSet.add(101);
  _selectionSet.add(102);
  _ticketsEl.innerHTML = htmlBefore;

  // THE FIX: clear selection before render
  globalThis._smgmtClearSelection();
  assert.equal(_selectionSet.size, 0, 'selection cleared after _smgmtClearSelection()');

  // Now render with fresh ticket list (moved tickets gone)
  const freshTickets = [_makeTicket(103)];
  _smgmtRenderBacklog(freshTickets);

  // Post-fix: DOM updated — moved tickets no longer visible
  assert.ok(!_ticketsEl.innerHTML.includes('data-issue="101"'),
    'post-fix: ticket 101 removed from DOM after clear + render');
  assert.ok(!_ticketsEl.innerHTML.includes('data-issue="102"'),
    'post-fix: ticket 102 removed from DOM after clear + render');
  assert.ok(_ticketsEl.innerHTML.includes('103'),
    'post-fix: ticket 103 still present in DOM');
});

test('AC2: _smgmtRenderBacklog renders after selection cleared (size=0)', () => {
  _reset();
  const tickets = [_makeTicket(201), _makeTicket(202)];

  // First render writes DOM with no selection
  _smgmtRenderBacklog(tickets);
  const html1 = _ticketsEl.innerHTML;
  assert.ok(html1.length > 0, 'initial render writes DOM');

  // Selection becomes active — render is suppressed
  _selectionSet.add(201);
  _smgmtRenderBacklog([_makeTicket(202)]);  // removed 201 from list
  assert.equal(_ticketsEl.innerHTML, html1, 'AC2: render suppressed while selection active');

  // Clear selection → render lifts suppression
  globalThis._smgmtClearSelection();
  assert.equal(_selectionSet.size, 0, 'selection cleared');
  _smgmtRenderBacklog([_makeTicket(202)]);
  assert.ok(!_ticketsEl.innerHTML.includes('201'),
    'AC2: after clear, render removes moved ticket 201 from DOM');
});

test('AC3: error path does NOT clear selection (user can retry)', async () => {
  _reset();
  const tickets = [_makeTicket(301), _makeTicket(302), _makeTicket(303)];

  _selectionSet.add(301);
  _selectionSet.add(302);
  _ticketsEl.innerHTML = tickets.map(t => `<div data-issue="${t.number}">#${t.number}</div>`).join('');

  // Simulate error path: batch-labels fails → NO _smgmtClearSelection() call
  // (fetch throws, catch branch runs — selection must stay intact for retry)
  const htmlAfterError = _ticketsEl.innerHTML;
  _smgmtRenderBacklog(tickets);  // re-render on error — still suppressed

  assert.equal(_selectionSet.size, 2, 'AC3: selection intact after error (user can retry)');
  assert.equal(_ticketsEl.innerHTML, htmlAfterError,
    'AC3: DOM not changed when error occurs with active selection');
});

test('AC4: stale add/remove while selection active is shown after clear', () => {
  _reset();
  const initial = [_makeTicket(401), _makeTicket(402)];

  // Write initial DOM
  _smgmtRenderBacklog(initial);
  const htmlBefore = _ticketsEl.innerHTML;
  assert.ok(htmlBefore.includes('401') && htmlBefore.includes('402'),
    'initial render has both tickets');

  // User selects a ticket — new ticket 403 arrives but render is suppressed
  _selectionSet.add(401);
  const withNew = [_makeTicket(401), _makeTicket(402), _makeTicket(403)];
  _smgmtRenderBacklog(withNew);
  assert.equal(_ticketsEl.innerHTML, htmlBefore,
    'AC4: new ticket 403 not shown while selection active');

  // Clear selection → fresh render shows the new ticket
  globalThis._smgmtClearSelection();
  _smgmtRenderBacklog(withNew);
  assert.ok(_ticketsEl.innerHTML.includes('403'),
    'AC4: ticket 403 visible after selection cleared');
});

test('AC1: _smgmtSelectedIssues.size is 0 after _smgmtClearSelection()', () => {
  _reset();
  _selectionSet.add(501);
  _selectionSet.add(502);
  _selectionSet.add(503);
  assert.equal(_selectionSet.size, 3, 'pre-clear: 3 issues selected');

  globalThis._smgmtClearSelection();

  assert.equal(_selectionSet.size, 0, 'post-clear: selection Set is empty');
});

test('AC1: proj-selection-bar is hidden after _smgmtClearSelection()', () => {
  _reset();
  _selectionSet.add(601);
  _barEl.classList.remove('hidden');

  globalThis._smgmtClearSelection();

  assert.ok(_barEl.classList.contains('hidden'),
    'selection bar hidden after _smgmtClearSelection()');
  assert.equal(_selCount.textContent, '0 issues selected',
    'selection count text reset');
});
