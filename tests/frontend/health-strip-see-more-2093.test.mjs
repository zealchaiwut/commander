/**
 * Behavioral tests for issue #2093 — health-strip "See more →" link fix.
 *
 * AC coverage:
 *   AC1 — health-strip no longer renders a "See more →" / shs-see-more link
 *          that routes operators to the failures inbox (removed/relabeled)
 *   AC2 — no surviving switchTab('metrics') call in the rendered health-strip HTML
 *
 * Run with: node --test tests/frontend/health-strip-see-more-2093.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal stubs required by health-strip.js ─────────────────────────────
globalThis.window = { _anlHealthData: null, _anlHealthPromise: null };
globalThis.document = {
  getElementById: () => null,
};

import {
  _sHealthBuildHtml,
} from '../../apps/dashboard/static/src/sprint-board/health-strip.js';

const DATA_WITH_SPRINTS = {
  first_pass_rate: { rate: 0.75, total_completed: 8 },
  rework_rate: { rate: 0.25 },
  throughput: { avg_sprint_length_minutes: 90 },
};


// ── AC1: "See more →" link removed ────────────────────────────────────────

test('AC1: _sHealthBuildHtml returns non-null HTML when sprints exist', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html !== null, 'strip must render when completed sprints exist');
});

test('AC1: health-strip HTML must NOT contain "See more" link text', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html !== null, 'pre-condition: strip renders');
  assert.ok(
    !html.includes('See more'),
    'health-strip must not render "See more" — it was pointing to the failures inbox'
  );
});

test('AC1: health-strip HTML must NOT contain the shs-see-more CSS class', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html !== null, 'pre-condition: strip renders');
  assert.ok(
    !html.includes('shs-see-more'),
    'shs-see-more element must be removed from the health-strip'
  );
});

test('AC1: _sHealthBuildHtml returns null when no completed sprints (guard unchanged)', () => {
  const html = _sHealthBuildHtml({ first_pass_rate: { total_completed: 0 } });
  assert.strictEqual(html, null, 'strip must be hidden when no sprints');
});


// ── AC2: no surviving switchTab("metrics") caller ─────────────────────────

test('AC2: health-strip HTML must NOT call switchTab("metrics")', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html !== null, 'pre-condition: strip renders');
  assert.ok(
    !html.includes("switchTab('metrics')") && !html.includes('switchTab("metrics")'),
    'health-strip must not call switchTab("metrics") — that tab is removed and coerces to failures'
  );
});

test('AC2: health-strip HTML must NOT reference the removed metrics tab in any onclick', () => {
  const html = _sHealthBuildHtml(DATA_WITH_SPRINTS);
  assert.ok(html !== null, 'pre-condition: strip renders');
  // The old onclick called anlShowTab('metrics') too — ensure both are gone
  assert.ok(
    !html.includes("'metrics'") && !html.includes('"metrics"'),
    'health-strip HTML must not contain any reference to the removed "metrics" tab'
  );
});
