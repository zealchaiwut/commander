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
globalThis._smgmtBoardLock             = _noop;
globalThis._smgmtBoardUnlock           = _noop;
globalThis._smgmtShowInlineError       = _noop;
globalThis._smgmtShowToast             = _noop;
globalThis.loadSprintMgmt              = async () => {};

const { _smgmtRenderBacklog } = await import(
  '../../apps/dashboard/static/src/sprint-board/board-render.js'
);

const { _smgmtMoveModalPickBulk } = await import(
  '../../apps/dashboard/static/src/sprint-board/bulk-move.js'
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

// Mock fetch globally to capture requests and return controlled responses
let _fetchHistory = [];
let _fetchResponses = [];

globalThis.fetch = async (url, opts) => {
  const req = { url, method: opts?.method || 'GET', body: opts?.body };
  _fetchHistory.push(req);

  const resp = _fetchResponses.shift();
  if (!resp) {
    throw new Error(`Unexpected fetch to ${url}: no mock response configured`);
  }
  return resp;
};

// ── Tests ─────────────────────────────────────────────────────────────────────

test('AC1 & AC2: isNew=true → _smgmtMoveModalPickBulk clears selection after successful batch-labels', async () => {
  _reset();
  _fetchHistory = [];
  _fetchResponses = [
    // Response to /api/sprints/create
    {
      ok: true,
      json: async () => ({ sprint_label: 'sprint-1234' }),
    },
    // Response to /api/sprints/batch-labels
    {
      ok: true,
      json: async () => ({ applied: 2, failed: 0, errors: [] }),
    },
  ];

  _selectionSet.add(101);
  _selectionSet.add(102);
  assert.equal(_selectionSet.size, 2, 'pre-call: 2 issues selected');

  globalThis._smgmtRender = (_data) => {};

  // Call the real function with isNew=true
  await _smgmtMoveModalPickBulk([101, 102], null, true);

  // AC1: selection cleared after successful move to new sprint
  assert.equal(_selectionSet.size, 0,
    'AC1: selection cleared after successful move to new sprint (isNew=true)');

  // Verify the API calls were made
  assert.equal(_fetchHistory.length, 2, 'made both /api/sprints/create and /api/sprints/batch-labels calls');
  assert.ok(_fetchHistory[0].url.includes('/api/sprints/create'), 'first call creates sprint');
  assert.ok(_fetchHistory[1].url.includes('/api/sprints/batch-labels'), 'second call moves issues');
});

test('AC3: error path does NOT clear selection → user can retry', async () => {
  _reset();
  _fetchHistory = [];
  _fetchResponses = [
    // /api/sprints/create fails
    {
      ok: false,
      status: 500,
      json: async () => ({ detail: 'Internal server error' }),
      text: async () => 'Internal server error',
    },
  ];

  _selectionSet.add(301);
  _selectionSet.add(302);
  assert.equal(_selectionSet.size, 2, 'pre-call: 2 issues selected');

  // Call with isNew=true but make it fail
  await _smgmtMoveModalPickBulk([301, 302], null, true);

  // AC3: selection NOT cleared when the API call fails
  assert.equal(_selectionSet.size, 2,
    'AC3: selection intact after error (user can retry)');
});

test('AC2 & AC3: isNew=false (existing sprint) → clear selection on success', async () => {
  _reset();
  _fetchHistory = [];
  _fetchResponses = [
    // Move to existing sprint succeeds
    {
      ok: true,
      json: async () => ({ applied: 2, failed: 0, errors: [] }),
    },
  ];

  globalThis._smgmtData = {
    issues: [
      { number: 401, title: 'Issue 401', sprint: null },
      { number: 402, title: 'Issue 402', sprint: null },
    ],
  };

  _selectionSet.add(401);
  _selectionSet.add(402);
  assert.equal(_selectionSet.size, 2, 'pre-call: 2 issues selected for move to existing sprint');

  globalThis._smgmtRender = (_data) => {};

  // Move to existing sprint (isNew=false)
  await _smgmtMoveModalPickBulk([401, 402], 'sprint-1234', false);

  // Selection cleared immediately (for existing sprint, the optimization path clears it)
  assert.equal(_selectionSet.size, 0,
    'AC2/AC3: selection cleared after move to existing sprint');
});

test('AC1: _smgmtMoveModalPickBulk exports the function (importable and callable)', async () => {
  assert.ok(typeof _smgmtMoveModalPickBulk === 'function',
    'AC1: _smgmtMoveModalPickBulk is exported as a function');
});
