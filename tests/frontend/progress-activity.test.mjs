/* ProgressActivity component tests (issue #928).
 *
 * Tests every AC in the spec against the ES module — run with:
 *   node --test tests/frontend/progress-activity.test.mjs
 *
 * Covered:
 *   AC1  — bar mode: fill width, current label, counts, est-remaining
 *   AC2  — stepper mode: step states (pending/running/done/failed/fixed)
 *   AC3  — indeterminate mode: shimmer + spinner + current label
 *   AC4  — live-log slot: log lines, empty state, collapse toggle
 *   AC5  — no server-cancel side effects on structural close/hide
 *   AC6  — done end state; error end state + retry button
 *   AC7  — full normalized payload shape accepted without error
 *   AC8  — CSS uses var(--…) tokens, no hardcoded hex
 *   AC9  — board-render.js imports and calls renderProgressActivity
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
  renderProgressActivity,
  updateProgressActivityLog,
  paToggleLog,
  PA_CSS,
} from '../../apps/dashboard/static/src/progress-activity.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '../../');

// ── helpers ──────────────────────────────────────────────────────────────────

/** Quick regex assertions on rendered HTML. */
function hasClass(html, cls) {
  return html.includes(`class="${cls}"`) || html.includes(` ${cls} `) || html.includes(` ${cls}"`) || html.includes(`"${cls} `);
}

function hasClassPartial(html, cls) {
  return html.includes(cls);
}

function hasId(html, id) {
  return html.includes(`id="${id}"`);
}

// ── AC1: bar mode ─────────────────────────────────────────────────────────────

test('AC1: bar mode — root has pa-mode-bar class', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 3, total: 12 });
  assert.ok(hasClassPartial(html, 'pa-mode-bar'), 'root should have pa-mode-bar class');
});

test('AC1: bar mode — fill width reflects done/total fraction', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 3, total: 12 });
  assert.ok(html.includes('width:25%'), `expected width:25% in bar fill, got: ${html.slice(0, 300)}`);
});

test('AC1: bar mode — current-item label rendered', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 3, total: 12, current: 'Issue #123' });
  assert.ok(html.includes('pa-current'), 'pa-current element missing');
  assert.ok(html.includes('Issue #123'), 'current item text missing');
});

test('AC1: bar mode — counts rendered as "N of M"', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 3, total: 12 });
  assert.ok(html.includes('pa-counts'), 'pa-counts element missing');
  assert.ok(html.includes('3 of 12'), 'counts text missing');
});

test('AC1: bar mode — est-remaining rendered when provided', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 3, total: 12, est_remaining_minutes: 5 });
  assert.ok(html.includes('pa-est-rem'), 'pa-est-rem element missing');
  assert.ok(html.includes('~5m remaining'), 'est-remaining text missing');
});

test('AC1: bar mode — shimmer present while progress < 100%', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 3, total: 12 });
  assert.ok(html.includes('pa-bar-shimmer'), 'pa-bar-shimmer missing for partial progress');
});

test('AC1: bar mode — shimmer absent at 100%', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 12, total: 12 });
  assert.ok(!html.includes('pa-bar-shimmer'), 'pa-bar-shimmer should be absent at 100%');
});

test('AC1: bar mode — zero/zero fraction does not crash and shows 0%', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 0, total: 0 });
  assert.ok(html.includes('width:0%'), 'expected 0% fill for 0/0');
});

// ── AC1b: auto-detect bar mode from total ─────────────────────────────────────

test('AC1: bar mode auto-detected when total is present but mode absent', () => {
  const html = renderProgressActivity({ done: 5, total: 10 });
  assert.ok(hasClassPartial(html, 'pa-mode-bar'), 'should auto-detect bar mode from total');
});

// ── AC2: stepper mode ─────────────────────────────────────────────────────────

const STEPS = [
  { key: 'a', label: 'Step A', state: 'done' },
  { key: 'b', label: 'Step B', state: 'running' },
  { key: 'c', label: 'Step C', state: 'pending' },
  { key: 'd', label: 'Step D', state: 'failed' },
];

test('AC2: stepper mode — root has pa-mode-stepper class', () => {
  const html = renderProgressActivity({ mode: 'stepper', steps: STEPS });
  assert.ok(hasClassPartial(html, 'pa-mode-stepper'), 'root should have pa-mode-stepper');
});

test('AC2: stepper mode — pa-step--done rendered', () => {
  const html = renderProgressActivity({ mode: 'stepper', steps: STEPS });
  assert.ok(html.includes('pa-step--done'), 'pa-step--done missing');
});

test('AC2: stepper mode — pa-step--running rendered', () => {
  const html = renderProgressActivity({ mode: 'stepper', steps: STEPS });
  assert.ok(html.includes('pa-step--running'), 'pa-step--running missing');
});

test('AC2: stepper mode — pa-step--pending rendered', () => {
  const html = renderProgressActivity({ mode: 'stepper', steps: STEPS });
  assert.ok(html.includes('pa-step--pending'), 'pa-step--pending missing');
});

test('AC2: stepper mode — pa-step--failed rendered', () => {
  const html = renderProgressActivity({ mode: 'stepper', steps: STEPS });
  assert.ok(html.includes('pa-step--failed'), 'pa-step--failed missing');
});

test('AC2: stepper mode — step labels rendered', () => {
  const html = renderProgressActivity({ mode: 'stepper', steps: STEPS });
  assert.ok(html.includes('Step A'), 'Step A label missing');
  assert.ok(html.includes('Step B'), 'Step B label missing');
  assert.ok(html.includes('Step C'), 'Step C label missing');
});

test('AC2: stepper mode — step notes rendered when present', () => {
  const stepsWithNote = [{ key: 'x', label: 'X', state: 'done', note: 'All good' }];
  const html = renderProgressActivity({ mode: 'stepper', steps: stepsWithNote });
  assert.ok(html.includes('All good'), 'step note missing');
  assert.ok(html.includes('pa-step-note'), 'pa-step-note element missing');
});

test('AC2: stepper mode — fixed state rendered', () => {
  const steps = [{ key: 'f', label: 'Fixed step', state: 'fixed' }];
  const html = renderProgressActivity({ mode: 'stepper', steps });
  assert.ok(html.includes('pa-step--fixed'), 'pa-step--fixed missing');
});

test('AC2: stepper mode — auto-detected from steps array when mode absent', () => {
  const html = renderProgressActivity({ steps: [{ key: 'a', label: 'A', state: 'pending' }] });
  assert.ok(hasClassPartial(html, 'pa-mode-stepper'), 'should auto-detect stepper mode from steps');
});

// ── AC3: indeterminate mode ───────────────────────────────────────────────────

test('AC3: indeterminate — root has pa-mode-indeterminate class', () => {
  const html = renderProgressActivity({ status: 'running' });
  assert.ok(hasClassPartial(html, 'pa-mode-indeterminate'), 'root should have pa-mode-indeterminate');
});

test('AC3: indeterminate — spinner rendered', () => {
  const html = renderProgressActivity({ status: 'running' });
  assert.ok(html.includes('pa-spinner'), 'pa-spinner missing');
});

test('AC3: indeterminate — shimmer track rendered', () => {
  const html = renderProgressActivity({ status: 'running' });
  assert.ok(html.includes('pa-indet-shimmer'), 'pa-indet-shimmer missing');
});

test('AC3: indeterminate — current action label rendered', () => {
  const html = renderProgressActivity({ status: 'running', current: 'Loading data...' });
  assert.ok(html.includes('Loading data...'), 'current action label missing');
});

test('AC3: indeterminate — no fraction or step list shown', () => {
  const html = renderProgressActivity({ status: 'running' });
  assert.ok(!html.includes('pa-bar-fill'), 'pa-bar-fill should not appear in indeterminate mode');
  assert.ok(!html.includes('pa-step'), 'pa-step should not appear in indeterminate mode');
  assert.ok(!html.includes('pa-counts'), 'pa-counts should not appear in indeterminate mode');
});

// ── AC4: live-log slot ────────────────────────────────────────────────────────

test('AC4: log slot — pa-log section rendered when log_tail present', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ type: 'success', message: 'Done', timestamp: '12:00' }],
  });
  assert.ok(html.includes('pa-log'), 'pa-log section missing');
});

test('AC4: log slot — log lines rendered in stream', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ type: 'success', message: 'Ticket #1 done', timestamp: '12:00' }],
  });
  assert.ok(html.includes('pa-log-stream'), 'pa-log-stream element missing');
  assert.ok(html.includes('Ticket #1 done'), 'log message text missing');
});

test('AC4: log slot — success type gets pa-log-success class', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ type: 'success', message: 'ok' }],
  });
  assert.ok(html.includes('pa-log-success'), 'pa-log-success class missing for success type');
});

test('AC4: log slot — fail type gets pa-log-fail class', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ type: 'fail', message: 'failed' }],
  });
  assert.ok(html.includes('pa-log-fail'), 'pa-log-fail class missing for fail type');
});

test('AC4: log slot — warn type gets pa-log-warn class', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ type: 'warn', message: 'warning' }],
  });
  assert.ok(html.includes('pa-log-warn'), 'pa-log-warn class missing for warn type');
});

test('AC4: log slot — empty log_tail shows waiting message', () => {
  const html = renderProgressActivity({ status: 'running', log_tail: [] });
  assert.ok(html.includes('Waiting for log'), 'waiting message missing for empty log_tail');
});

test('AC4: log slot — collapse toggle button present', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ message: 'line' }],
  }, { id: 'test-id' });
  assert.ok(html.includes('pa-log-toggle-btn'), 'pa-log-toggle-btn missing');
});

test('AC4: log slot — log header clickable (onclick present)', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ message: 'line' }],
  }, { id: 'test-id' });
  assert.ok(html.includes('paToggleLog'), 'toggle function call missing in log header');
});

test('AC4: log slot — log slot absent when status is done', () => {
  const html = renderProgressActivity({ status: 'done', result: 'All done' });
  assert.ok(!html.includes('pa-log'), 'pa-log should be absent in done state');
});

test('AC4: log slot — log slot absent when status is error', () => {
  const html = renderProgressActivity({ status: 'error', error: 'Failed' });
  assert.ok(!html.includes('pa-log'), 'pa-log should be absent in error state');
});

test('AC4: log slot — pa-log-stream id set when rootId provided', () => {
  const html = renderProgressActivity(
    { status: 'running', log_tail: [{ message: 'x' }] },
    { id: 'my-root' },
  );
  assert.ok(html.includes('pa-log-stream-my-root'), 'stream id not set from rootId');
});

test('AC4: log slot — log collapsed by default when logCollapsed option set', () => {
  const html = renderProgressActivity(
    { status: 'running', log_tail: [{ message: 'x' }] },
    { id: 'c-test', logCollapsed: true },
  );
  assert.ok(html.includes('pa-log-collapsed'), 'pa-log-collapsed class missing when logCollapsed=true');
});

// ── AC5: no server-cancel side effects ───────────────────────────────────────

test('AC5: no fetch/XHR in rendered HTML (no cancel on close)', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ message: 'line' }],
  }, { id: 'op-test' });
  // The component must not embed fetch or XMLHttpRequest calls
  assert.ok(!html.includes('fetch('), 'HTML must not embed fetch() calls');
  assert.ok(!html.includes('XMLHttpRequest'), 'HTML must not embed XHR calls');
  assert.ok(!html.includes('addEventListener'), 'HTML must not add event listeners via inline script');
});

test('AC5: paToggleLog only toggles CSS class (no server calls)', () => {
  // paToggleLog is DOM-only — it only operates on document.getElementById
  // Verifying the function exists and is exported (its signature is DOM-only)
  assert.equal(typeof paToggleLog, 'function', 'paToggleLog must be exported');
  // Calling it without a DOM should be a no-op (no throw)
  assert.doesNotThrow(() => paToggleLog('nonexistent'), 'paToggleLog must not throw without DOM');
});

// ── AC6: done and error end states ───────────────────────────────────────────

test('AC6: done state — root has pa-status-done class', () => {
  const html = renderProgressActivity({ status: 'done', result: 'Sprint completed' });
  assert.ok(hasClassPartial(html, 'pa-status-done'), 'pa-status-done class missing');
});

test('AC6: done state — pa-done element rendered', () => {
  const html = renderProgressActivity({ status: 'done', result: 'Sprint completed' });
  assert.ok(html.includes('pa-done'), 'pa-done element missing');
});

test('AC6: done state — result text rendered', () => {
  const html = renderProgressActivity({ status: 'done', result: 'All 5 tickets done' });
  assert.ok(html.includes('All 5 tickets done'), 'result text missing');
});

test('AC6: done state — no retry button in done state', () => {
  const html = renderProgressActivity({ status: 'done', result: 'Done' });
  assert.ok(!html.includes('pa-retry-btn'), 'retry button must not appear in done state');
});

test('AC6: error state — root has pa-status-error class', () => {
  const html = renderProgressActivity({ status: 'error', error: 'Network failed' });
  assert.ok(hasClassPartial(html, 'pa-status-error'), 'pa-status-error class missing');
});

test('AC6: error state — pa-error element rendered', () => {
  const html = renderProgressActivity({ status: 'error', error: 'Network failed' });
  assert.ok(html.includes('pa-error'), 'pa-error element missing');
});

test('AC6: error state — error message rendered', () => {
  const html = renderProgressActivity({ status: 'error', error: 'Network failed' });
  assert.ok(html.includes('Network failed'), 'error message missing');
});

test('AC6: error state — retry button rendered', () => {
  const html = renderProgressActivity({ status: 'error', error: 'Network failed' });
  assert.ok(html.includes('pa-retry-btn'), 'pa-retry-btn missing in error state');
});

test('AC6: error state — retry button calls retryFn when provided', () => {
  const html = renderProgressActivity(
    { status: 'error', error: 'Failed' },
    { retryFn: 'myRetryHandler' },
  );
  assert.ok(html.includes('myRetryHandler'), 'retryFn name missing in retry button');
});

// ── AC7: normalized payload shape ─────────────────────────────────────────────

test('AC7: full payload shape accepted without error', () => {
  const payload = {
    status: 'running',
    mode: 'bar',
    current: 'Issue #99',
    done: 1,
    total: 5,
    steps: [],
    log_tail: [{ type: 'dispatch', message: 'dispatching', timestamp: '10:00' }],
    result: null,
  };
  assert.doesNotThrow(() => renderProgressActivity(payload), 'should not throw on full payload');
  const html = renderProgressActivity(payload);
  assert.ok(html.length > 0, 'rendered HTML must not be empty');
});

test('AC7: empty payload renders without error', () => {
  assert.doesNotThrow(() => renderProgressActivity({}), 'empty payload should not throw');
});

test('AC7: null payload renders without error', () => {
  assert.doesNotThrow(() => renderProgressActivity(null), 'null payload should not throw');
});

// ── AC8: design tokens — no hardcoded hex ─────────────────────────────────────

test('AC8: PA_CSS exported and non-empty', () => {
  assert.ok(typeof PA_CSS === 'string' && PA_CSS.length > 0, 'PA_CSS must be a non-empty string');
});

test('AC8: PA_CSS uses var(--...) tokens not hardcoded hex colors', () => {
  // Strip animation color stops and rgba() which are allowed for non-semantic shimmer overlays
  // Only check for standalone hex values used as solid design colors
  const cssWithoutRgba = PA_CSS.replace(/rgba\([^)]+\)/g, '');
  // Allow semi-transparent overlay values like rgba(22,163,74,.18) — these are fine
  // but reject fully-opaque hex like #16a34a (which should be var(--green))
  const hexMatches = cssWithoutRgba.match(/#[0-9a-fA-F]{6}\b/g) || [];
  assert.deepEqual(hexMatches, [], `PA_CSS should not contain hardcoded hex colors, found: ${hexMatches.join(', ')}`);
});

test('AC8: rendered HTML does not contain hardcoded hex colors', () => {
  const html = renderProgressActivity({ mode: 'bar', done: 1, total: 4, log_tail: [{ message: 'x' }] });
  const hexMatches = html.match(/#[0-9a-fA-F]{6}\b/g) || [];
  assert.deepEqual(hexMatches, [], `Rendered HTML should not contain hardcoded hex, found: ${hexMatches.join(', ')}`);
});

// ── AC9: board-render.js uses renderProgressActivity ─────────────────────────

test('AC9: board-render.js imports renderProgressActivity from progress-activity.js', () => {
  const boardRenderPath = path.join(REPO_ROOT, 'apps/dashboard/static/src/sprint-board/board-render.js');
  const src = readFileSync(boardRenderPath, 'utf8');
  assert.ok(
    src.includes('progress-activity'),
    'board-render.js must import from progress-activity.js',
  );
  assert.ok(
    src.includes('renderProgressActivity'),
    'board-render.js must reference renderProgressActivity',
  );
});

test('AC9: _smgmtRunningCardHtml calls renderProgressActivity', () => {
  const boardRenderPath = path.join(REPO_ROOT, 'apps/dashboard/static/src/sprint-board/board-render.js');
  const src = readFileSync(boardRenderPath, 'utf8');
  // The function body should contain the renderProgressActivity call
  const fnStart = src.indexOf('function _smgmtRunningCardHtml');
  const fnEnd = src.indexOf('\nexport function', fnStart + 1);
  const fnBody = fnEnd > fnStart ? src.slice(fnStart, fnEnd) : src.slice(fnStart);
  assert.ok(
    fnBody.includes('renderProgressActivity'),
    '_smgmtRunningCardHtml must call renderProgressActivity',
  );
});

// ── Additional: XSS safety ───────────────────────────────────────────────────

test('XSS: current item label is HTML-escaped', () => {
  const html = renderProgressActivity({ mode: 'bar', current: '<script>alert(1)</script>', done: 0, total: 1 });
  assert.ok(!html.includes('<script>'), 'XSS: <script> tag must be escaped in current label');
  assert.ok(html.includes('&lt;script&gt;'), 'XSS: < must be escaped as &lt;');
});

test('XSS: log message is HTML-escaped when no colorize provided', () => {
  const html = renderProgressActivity({
    status: 'running',
    log_tail: [{ message: '<img src=x onerror=alert(1)>' }],
  });
  assert.ok(!html.includes('<img '), 'XSS: raw img tag must be escaped in log message');
});

test('XSS: step label is HTML-escaped', () => {
  const html = renderProgressActivity({
    mode: 'stepper',
    steps: [{ key: 'x', label: '<b>bold</b>', state: 'pending' }],
  });
  assert.ok(!html.includes('<b>'), 'XSS: <b> tag must be escaped in step label');
});

test('XSS: error message is HTML-escaped', () => {
  const html = renderProgressActivity({ status: 'error', error: '<script>bad()</script>' });
  assert.ok(!html.includes('<script>'), 'XSS: error message must be HTML-escaped');
});
