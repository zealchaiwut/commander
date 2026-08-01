/**
 * Behavioral tests for issue #2060: backlog rows must render label chips for
 * stage and sprint labels so shipped/UAT tickets are visually distinct.
 *
 * AC1  — chips render for UAT, SIT, in-progress, needs-rework, blocked, sprint-*
 * AC3  — overflow labels are capped with "+N"; non-meaningful labels are omitted
 * AC4  — UAT chip carries the --uat modifier class
 * AC5  — (this file) backlog ticket with UAT + sprint label shows both chips
 *
 * Run with: node --test tests/frontend/backlog-label-chips-2060.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal global stubs required by board-render.js at import time ───────────

const _noop    = () => {};
const _noopSet = new Set();

globalThis.document  = { getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
globalThis.window    = globalThis;

globalThis.escHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
globalThis.sprintLabelDisplay   = (l) => l;
globalThis.colorizeLogLine      = (s) => s;

globalThis._smgmtSelectedIssues  = _noopSet;
globalThis._smgmtOrderedLabels   = [];
globalThis._smgmtRunningLabels   = _noopSet;
globalThis._smgmtResolvedAncestors = new Set();
globalThis._smgmtData            = null;
globalThis._smgmtTicketToSprint  = {};
globalThis._smgmtConflictsByIssue = {};
globalThis._smgmtDagDataCache    = {};
globalThis._smgmtDeactivatedLabels = _noopSet;
globalThis._smgmtDepOrderByIssue = {};
globalThis._estDataCache         = {};
globalThis._smgmtLabelColors     = {};
globalThis._smgmtLastLabelIssues = {};
globalThis._smgmtLiveCache       = {};
globalThis._smgmtLiveCacheRepo   = null;
globalThis._smgmtOutcomeCache    = {};
globalThis._smgmtFinishCards     = {};
globalThis._smgmtFinishedLabels  = _noopSet;
globalThis._smgmtBySprint        = {};
globalThis._cachedFullRepo       = {};
globalThis._slug                 = 'test';
globalThis._smgmtAnySprintRunning = false;
globalThis._smgmtEstimatorAvailable = false;
globalThis._smgmtRowActivity     = {};

// Function stubs
globalThis._smgmtEnsureCapData         = _noop;
globalThis._smgmtLoadMiniRail          = _noop;
globalThis._smgmtMiniRailRestoreCached = _noop;
globalThis._smgmtRenderAllCapBars      = _noop;
globalThis._smgmtUpdateSubnav          = _noop;
globalThis._smgmtActiveAgentsHtml      = () => '';
globalThis._smgmtAgentTagClass         = () => '';
globalThis._smgmtApplySort             = _noop;
globalThis._smgmtBulkEstimate          = _noop;
globalThis._smgmtCancelBannerHtml      = () => '';
globalThis._smgmtCapacityInputHtml     = () => '';
globalThis._smgmtCheckEstimatorHealth  = _noop;
globalThis._smgmtCloseIssueOpen        = _noop;
globalThis._smgmtCtxMenuOpen           = _noop;
globalThis._smgmtEstimateBadgeHtml     = () => '';
globalThis._smgmtFilterApply           = _noop;
globalThis._smgmtHasCompletedTickets   = _noop;
globalThis._smgmtInitCapacityGauges    = _noop;
globalThis._smgmtInjectOutcomeBand     = _noop;
globalThis._smgmtIsCancelled           = () => false;
globalThis._smgmtKbRestoreFocus        = _noop;
globalThis._smgmtLabelFilterToggle     = _noop;
globalThis._smgmtLabelFilterToggleExpand = _noop;
globalThis._smgmtLevelsHtml            = () => '';
globalThis._smgmtLiveAgentBadgesHtml   = () => '';
globalThis._smgmtLiveLogLinesHtml      = () => '';
globalThis._smgmtLivePollRestart       = _noop;
globalThis._smgmtLingerRestore         = _noop;
globalThis._smgmtLingerStart           = _noop;
globalThis._smgmtIsLinger              = () => false;
globalThis._smgmtLingerLive            = () => null;
globalThis._smgmtNextChildLabel        = _noop;
globalThis._smgmtOutcomeLogHtml        = () => '';
globalThis._smgmtPrimaryRunningLabel   = _noop;
globalThis._smgmtReEstimate            = _noop;
globalThis._smgmtRepo                  = () => 'owner/repo';
globalThis._smgmtRiskFlagIconsHtml     = () => '';
globalThis._smgmtRowMenuOpen           = _noop;
globalThis._smgmtRowClickSelect        = _noop;
globalThis._smgmtRunningViewUpdate     = _noop;
globalThis._smgmtSchedDepHtml          = () => '';
globalThis._smgmtSchedToggleHtml       = () => '';
globalThis._smgmtHydrateSchedToggles   = _noop;
globalThis._smgmtSetSprintTokenEl      = _noop;
globalThis._smgmtStateMeta             = () => ({ state: 'unknown' });
globalThis._smgmtToggleSelect          = _noop;
globalThis._smgmtUpdateCapacityGauge   = _noop;
globalThis._smgmtUpdateCleanupBtn      = _noop;
globalThis._smgmtUpdateConflictBadge   = _noop;
globalThis._smgmtUpdateDepOrderBadge   = _noop;
globalThis._smgmtUpdateEstimateBadge   = _noop;
globalThis._blBacklogAll               = [];
globalThis._blApplyFilters             = (t) => t;
globalThis._blSyncFilterPills          = _noop;
globalThis._blUpdateActions            = _noop;
globalThis._smgmtUpdateSelectionUI     = _noop;
globalThis._sizeMinutes                = (s) => ({ S: 5, M: 15, L: 30, XL: 60 }[s] || 15);

// Import AFTER globals
const {
  _smgmtBacklogLabelChipsHtml,
  _smgmtBacklogTicketHtml,
} = await import(
  '../../apps/dashboard/static/src/sprint-board/board-render.js'
);

// ── Helpers ───────────────────────────────────────────────────────────────────

function _makeLabel(name) { return { name, color: '0e8a16' }; }

function _makeTicket(number, labelNames = []) {
  return {
    number,
    title: `Ticket #${number}`,
    url: `https://github.com/owner/repo/issues/${number}`,
    labels: labelNames.map(_makeLabel),
    body: '',
    status: 'backlog',
  };
}

// ── _smgmtBacklogLabelChipsHtml unit tests ────────────────────────────────────

test('AC1: UAT chip appears in generated HTML', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('UAT')]);
  assert.ok(html.includes('UAT'), 'UAT chip must appear');
  assert.ok(html.includes('smgmt-bl-label-chip'), 'chip class must be present');
});

test('AC1: SIT chip appears in generated HTML', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('SIT')]);
  assert.ok(html.includes('SIT'), 'SIT chip must appear');
});

test('AC1: in-progress chip appears', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('in-progress')]);
  assert.ok(html.includes('in-progress'));
});

test('AC1: needs-rework chip appears', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('needs-rework')]);
  assert.ok(html.includes('needs-rework'));
});

test('AC1: blocked chip appears', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('blocked')]);
  assert.ok(html.includes('blocked'));
});

test('AC1: sprint-* chip appears', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('sprint-1008')]);
  assert.ok(html.includes('sprint-1008'));
});

test('AC1: non-meaningful label (e.g. "size-M") is not rendered', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('size-M')]);
  assert.equal(html, '', 'size-M must produce no chip output');
});

test('AC1: empty labels array returns empty string', () => {
  assert.equal(_smgmtBacklogLabelChipsHtml([]), '');
  assert.equal(_smgmtBacklogLabelChipsHtml(null), '');
});

test('AC3: overflow capped at 3 chips + "+N" indicator', () => {
  const labels = [
    _makeLabel('UAT'),
    _makeLabel('SIT'),
    _makeLabel('in-progress'),
    _makeLabel('blocked'),        // 4th — should overflow
    _makeLabel('sprint-1008'),    // 5th
  ];
  const html = _smgmtBacklogLabelChipsHtml(labels);
  // Exactly 3 visible + 1 overflow chip
  assert.ok(html.includes('+2'), 'overflow of 2 must be shown as +2');
  // UAT, SIT, in-progress are the first 3
  assert.ok(html.includes('UAT'));
  assert.ok(html.includes('SIT'));
  assert.ok(html.includes('in-progress'));
  // blocked and sprint-1008 collapsed into +2
  assert.ok(!html.includes('>blocked<'), 'blocked must be in overflow, not visible');
  assert.ok(!html.includes('>sprint-1008<'), 'sprint-1008 must be in overflow, not visible');
});

test('AC3: exactly 3 chips with no overflow shows no +N', () => {
  const labels = [_makeLabel('UAT'), _makeLabel('SIT'), _makeLabel('sprint-42')];
  const html = _smgmtBacklogLabelChipsHtml(labels);
  assert.ok(!html.includes('+'), 'no overflow indicator when ≤3 chips');
});

test('AC4: UAT chip carries --uat modifier class', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('UAT')]);
  assert.ok(html.includes('smgmt-bl-label-chip--uat'), 'UAT must have --uat modifier');
});

test('AC4: SIT chip carries --active modifier class', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('SIT')]);
  assert.ok(html.includes('smgmt-bl-label-chip--active'));
});

test('AC4: needs-rework chip carries --alert modifier class', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('needs-rework')]);
  assert.ok(html.includes('smgmt-bl-label-chip--alert'));
});

test('AC4: blocked chip carries --alert modifier class', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('blocked')]);
  assert.ok(html.includes('smgmt-bl-label-chip--alert'));
});

test('AC4: sprint-* chip has no special modifier (neutral styling)', () => {
  const html = _smgmtBacklogLabelChipsHtml([_makeLabel('sprint-1008')]);
  // Should be a plain chip with no --uat/--active/--alert/--overflow
  assert.ok(!html.includes('--uat'));
  assert.ok(!html.includes('--active'));
  assert.ok(!html.includes('--alert'));
  assert.ok(!html.includes('--overflow'));
});

// ── AC5 (issue #2060): full backlog row — UAT + sprint label both appear ──────

test('AC5: _smgmtBacklogTicketHtml with UAT + sprint label shows both chips', () => {
  const ticket = _makeTicket(2047, ['UAT', 'sprint-viz9003', 'size-M']);
  const html = _smgmtBacklogTicketHtml(ticket, []);

  assert.ok(html.includes('smgmt-bl-label-chip'), 'label chip container must appear in row HTML');
  assert.ok(html.includes('smgmt-bl-label-chip--uat'), 'UAT chip with --uat class must appear');
  assert.ok(html.includes('>UAT<'), 'UAT label text must be in the row');
  assert.ok(html.includes('>sprint-viz9003<'), 'sprint-viz9003 label text must be in the row');
  // size-M is not a meaningful stage/sprint label
  assert.ok(!html.includes('>size-M<'), 'size-M must not appear as a chip');
});

test('AC5: backlog row with no stage labels renders no chip container', () => {
  const ticket = _makeTicket(100, ['size-M', 'enhancement']);
  const html = _smgmtBacklogTicketHtml(ticket, []);
  assert.ok(!html.includes('smgmt-bl-label-chips'), 'chip container must be absent when no stage labels');
});
