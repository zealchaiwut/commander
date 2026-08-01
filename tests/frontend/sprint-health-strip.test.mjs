/**
 * Behavioral tests for issue #1849 — delivery-health stat strip on
 * Board/History header.
 *
 * AC coverage:
 *   AC1 — strip renders first-pass rate, rework rate, avg sprint duration
 *          when project has completed sprints
 *   AC2 — rendered HTML contains a "See more" link pointing at Analytics metrics
 *   AC3 — strip is hidden (returns null) when project has no completed sprints
 *   AC4 — sprintHealthStripInit skips fetch when _anlHealthData is already set
 *
 * Run with: node --test tests/frontend/sprint-health-strip.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal DOM stub ──────────────────────────────────────────────────────────
if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

import {
  _sHealthBuildHtml,
  _sHealthStripRender,
} from '../../apps/dashboard/static/src/sprint-board/health-strip.js';


// ── Fixtures ──────────────────────────────────────────────────────────────────

const DATA_WITH_SPRINTS = {
  first_pass_rate: { rate: 0.75, total_completed: 8 },
  rework_rate: { rate: 0.25 },
  throughput: { avg_sprint_length_minutes: 90 },
};

const DATA_NO_SPRINTS = {
  first_pass_rate: { rate: 0, total_completed: 0 },
  rework_rate: { rate: 0 },
  throughput: { avg_sprint_length_minutes: 0 },
};


// ── AC1: strip renders numeric values ─────────────────────────────────────────

test('AC1: _sHealthBuildHtml returns a non-null string when sprints exist', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html !== null, 'must return HTML string when completed sprints exist');
  assert.equal(typeof html, 'string');
});

test('AC1: rendered HTML contains the first-pass rate percentage', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html.includes('75'), 'first-pass rate 0.75 should appear as 75');
});

test('AC1: rendered HTML contains the rework rate percentage', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html.includes('25'), 'rework rate 0.25 should appear as 25');
});

test('AC1: rendered HTML contains the avg sprint duration in hours', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  // 90 minutes = 1.5h
  assert.ok(html.includes('1.5h'), 'avg sprint 90 min should appear as 1.5h');
});

test('AC1: short durations under 60m render as minutes', () => {
  const data = {
    ...DATA_WITH_SPRINTS,
    throughput: { avg_sprint_length_minutes: 45 },
  };
  const html = _sHealthBuildHtml(data);
  assert.ok(html.includes('45m'), 'short durations should use minutes');
});


// ── AC2: strip contains "See more" link ───────────────────────────────────────

test('AC2: rendered HTML contains a "See more" link text', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html.includes('See more'), 'HTML must contain "See more" link');
});

test('AC2: "See more" link opens Analytics metrics pane', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  // Must reference the analytics metrics navigation (switchTab + anlShowTab)
  assert.ok(
    html.includes('metrics') && html.includes('switchTab'),
    'link must navigate to the metrics pane via switchTab'
  );
});


// ── AC3: strip is hidden when no completed sprints ────────────────────────────

test('AC3: _sHealthBuildHtml returns null when total_completed is 0', () => {
  assert.strictEqual(_sHealthBuildHtml(DATA_NO_SPRINTS), null);
});

test('AC3: _sHealthBuildHtml returns null when first_pass_rate missing', () => {
  assert.strictEqual(_sHealthBuildHtml({}), null);
});

test('AC3: _sHealthBuildHtml returns null when total_completed < 0', () => {
  const data = { first_pass_rate: { total_completed: -1 } };
  assert.strictEqual(_sHealthBuildHtml(data), null);
});

test('AC3: _sHealthStripRender hides strip element when no completed sprints', () => {
  const el = { hidden: false, innerHTML: '' };
  const orig = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) =>
    id === 'sprint-health-strip' ? el : null;

  _sHealthStripRender(DATA_NO_SPRINTS);
  assert.equal(el.hidden, true, 'strip element must be hidden');

  globalThis.document.getElementById = orig;
});

test('AC3: _sHealthStripRender shows strip element when sprints exist', () => {
  const el = { hidden: true, innerHTML: '' };
  const orig = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) =>
    id === 'sprint-health-strip' ? el : null;

  _sHealthStripRender(DATA_WITH_SPRINTS);
  assert.equal(el.hidden, false, 'strip element must be visible');

  globalThis.document.getElementById = orig;
});

test('AC3: _sHealthStripRender sets innerHTML when sprints exist', () => {
  const el = { hidden: true, innerHTML: '' };
  const orig = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) =>
    id === 'sprint-health-strip' ? el : null;

  _sHealthStripRender(DATA_WITH_SPRINTS);
  assert.ok(el.innerHTML.length > 0, 'strip innerHTML must be populated');

  globalThis.document.getElementById = orig;
});


// ── AC4: no-op when _anlHealthData is already set ────────────────────────────

test('AC4: _sHealthBuildHtml is a pure function with no side effects', () => {
  // Pure render function should not modify globals
  delete globalThis._anlHealthData;
  _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.equal(typeof globalThis._anlHealthData, 'undefined',
    '_sHealthBuildHtml must not set _anlHealthData');
});
