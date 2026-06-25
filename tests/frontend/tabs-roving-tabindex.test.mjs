/* Roving tabindex tests for issue #1175.
 *
 * Verifies that computeRovingTabindex() always assigns tabIndex=0 to exactly
 * one top-level tab element, regardless of whether the active tab is a
 * top-level tab or a child of the manage/planning dropdown groups.
 *
 * Run with: node --test tests/frontend/tabs-roving-tabindex.test.mjs
 *
 * Covered ACs:
 *   AC1 — top-level tab active → that tab gets 0, all others get -1
 *   AC2 — manage child active → manage trigger gets 0, all others get -1
 *   AC3 — planning child active → planning trigger gets 0, all others get -1
 *   AC4 — never zero elements with tabIndex=0 across all known tabs
 *   AC6 — switching between top-level and group-child produces correct result each time
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// tabs.js has top-level document/window references — stub them before import.
globalThis.document = {
  addEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => ({ forEach: () => {} }),
};
globalThis.window = { addEventListener: () => {} };

const { computeRovingTabindex } = await import('../../apps/dashboard/static/src/shell/tabs.js');

const TOP_LEVEL = ['sprint-mgmt', 'tickets', 'manage', 'planning', 'settings'];

function assertExactlyOneZero(map, context) {
  const zeros = Object.entries(map).filter(([, v]) => v === 0);
  assert.equal(
    zeros.length, 1,
    `Expected exactly one tabIndex=0 in ${context}, got ${zeros.length}: ${JSON.stringify(map)}`
  );
}

function assertAllMinusOne(map, except) {
  for (const [key, val] of Object.entries(map)) {
    if (key === except) continue;
    assert.equal(val, -1, `Expected ${key} to be -1 when ${except} owns the active tab`);
  }
}

// ── AC1: top-level tab active ────────────────────────────────────────────────

test('AC1: sprint-mgmt active → sprint-mgmt gets 0, others get -1', () => {
  const map = computeRovingTabindex('sprint-mgmt', false);
  assert.equal(map['sprint-mgmt'], 0);
  assertAllMinusOne(map, 'sprint-mgmt');
  assertExactlyOneZero(map, 'tab=sprint-mgmt');
});

test('AC1: tickets active → tickets gets 0, others get -1', () => {
  const map = computeRovingTabindex('tickets', false);
  assert.equal(map['tickets'], 0);
  assertAllMinusOne(map, 'tickets');
  assertExactlyOneZero(map, 'tab=tickets');
});

test('AC1: settings active → settings gets 0, others get -1', () => {
  const map = computeRovingTabindex('settings', false);
  assert.equal(map['settings'], 0);
  assertAllMinusOne(map, 'settings');
  assertExactlyOneZero(map, 'tab=settings');
});

// ── AC2: manage group child active ──────────────────────────────────────────

test('AC2: logs active → manage trigger gets 0, others get -1', () => {
  const map = computeRovingTabindex('logs', false);
  assert.equal(map['manage'], 0, 'manage should be 0 when logs is active');
  assertAllMinusOne(map, 'manage');
  assertExactlyOneZero(map, 'tab=logs');
});

test('AC2: deploy active → manage trigger gets 0, others get -1', () => {
  const map = computeRovingTabindex('deploy', false);
  assert.equal(map['manage'], 0, 'manage should be 0 when deploy is active');
  assertAllMinusOne(map, 'manage');
  assertExactlyOneZero(map, 'tab=deploy');
});

test('AC2: metrics active → manage trigger gets 0, others get -1', () => {
  const map = computeRovingTabindex('metrics', false);
  assert.equal(map['manage'], 0, 'manage should be 0 when metrics is active');
  assertAllMinusOne(map, 'manage');
  assertExactlyOneZero(map, 'tab=metrics');
});

// ── AC3: planning group child active ────────────────────────────────────────

test('AC3: roadmap active → planning trigger gets 0, others get -1', () => {
  const map = computeRovingTabindex('roadmap', false);
  assert.equal(map['planning'], 0, 'planning should be 0 when roadmap is active');
  assertAllMinusOne(map, 'planning');
  assertExactlyOneZero(map, 'tab=roadmap');
});

test('AC3: advisor active → planning trigger gets 0, others get -1', () => {
  const map = computeRovingTabindex('advisor', false);
  assert.equal(map['planning'], 0, 'planning should be 0 when advisor is active');
  assertAllMinusOne(map, 'planning');
  assertExactlyOneZero(map, 'tab=advisor');
});

// ── AC4: never zero tabIndex=0 elements ─────────────────────────────────────

const ALL_TABS = [
  'sprint-mgmt', 'tickets', 'settings',
  'logs', 'deploy', 'metrics', 'bulk-create',
  'timeline', 'compare', 'est-vs-actual', 'calibration', 'notes', 'roadmap', 'advisor',
];

test('AC4: every known tab produces exactly one tabIndex=0 entry', () => {
  for (const tab of ALL_TABS) {
    const map = computeRovingTabindex(tab, false);
    assertExactlyOneZero(map, `tab=${tab}`);
  }
});

test('AC4: returns all -1 when onGlobalSettings=true (strip is hidden)', () => {
  for (const tab of ALL_TABS) {
    const map = computeRovingTabindex(tab, true);
    const zeros = Object.values(map).filter(v => v === 0);
    assert.equal(zeros.length, 0, `Expected no tabIndex=0 in global-settings mode, got: ${JSON.stringify(map)}`);
  }
});

// ── AC6: switching between group-child and top-level stays correct ──────────

test('AC6: switching from logs to sprint-mgmt produces correct result', () => {
  const fromLogs = computeRovingTabindex('logs', false);
  assert.equal(fromLogs['manage'], 0);

  const toSprintMgmt = computeRovingTabindex('sprint-mgmt', false);
  assert.equal(toSprintMgmt['sprint-mgmt'], 0);
  assert.equal(toSprintMgmt['manage'], -1);
  assertExactlyOneZero(toSprintMgmt, 'after switching to sprint-mgmt');
});

test('AC6: switching from roadmap to tickets produces correct result', () => {
  const fromRoadmap = computeRovingTabindex('roadmap', false);
  assert.equal(fromRoadmap['planning'], 0);

  const toTickets = computeRovingTabindex('tickets', false);
  assert.equal(toTickets['tickets'], 0);
  assert.equal(toTickets['planning'], -1);
  assertExactlyOneZero(toTickets, 'after switching to tickets');
});

test('AC6: switching from top-level to group-child and back stays consistent', () => {
  const states = ['sprint-mgmt', 'logs', 'sprint-mgmt', 'roadmap', 'tickets', 'deploy'];
  for (const tab of states) {
    const map = computeRovingTabindex(tab, false);
    assertExactlyOneZero(map, `during switch sequence at tab=${tab}`);
  }
});
