/* Drag/drop smoke test (issue #797 — AC4).
 *
 * Runs against the modularized board code: imports the DOM-free decision
 * helpers from static/src/sprint-board/drag-drop.js and asserts the rules that
 * the live drop handlers rely on. `node --test tests/frontend/` runs this in CI
 * (the frontend-build job has Node); it needs no browser or DOM.
 *
 * Covered rules:
 *   - isDragBlocked(): a drop is refused while a move is in flight (#276) —
 *     mirrors the `if (_smgmtMoveLock) return;` guard wired into the handlers.
 *   - computeDropPlan(): single vs. multi-select move set (#660) and the
 *     same-column no-op.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  isDragBlocked,
  computeDropPlan,
} from '../../apps/dashboard/static/src/sprint-board/drag-drop.js';

test('isDragBlocked: blocked only while a move lock is held', () => {
  assert.equal(isDragBlocked({ moveLock: true }), true);
  assert.equal(isDragBlocked({ moveLock: false }), false);
  assert.equal(isDragBlocked({}), false);
  assert.equal(isDragBlocked(null), false);
  assert.equal(isDragBlocked(undefined), false);
});

test('computeDropPlan: single-ticket drag moves just that ticket', () => {
  const plan = computeDropPlan({ number: 42, fromSprint: 'sprint-1', multi: null }, 'sprint-2');
  assert.equal(plan.mode, 'single');
  assert.deepEqual(plan.tickets, [42]);
  assert.equal(plan.targetLabel, 'sprint-2');
  assert.equal(plan.noop, false);
});

test('computeDropPlan: dropping a single ticket on its own column is a no-op', () => {
  const plan = computeDropPlan({ number: 42, fromSprint: 'sprint-1', multi: null }, 'sprint-1');
  assert.equal(plan.noop, true);
  assert.deepEqual(plan.tickets, []);
});

test('computeDropPlan: multi-select drag (#660) moves the whole selection together', () => {
  const plan = computeDropPlan({ number: 7, fromSprint: 'sprint-1', multi: [7, 8, 9] }, 'sprint-3');
  assert.equal(plan.mode, 'multi');
  assert.deepEqual(plan.tickets, [7, 8, 9]);
  assert.equal(plan.targetLabel, 'sprint-3');
  assert.equal(plan.noop, false);
  // returns a copy — does not alias the caller's selection array
  plan.tickets.push(10);
  assert.deepEqual(computeDropPlan({ number: 7, multi: [7, 8, 9] }, 'sprint-3').tickets, [7, 8, 9]);
});

test('computeDropPlan: a single-element multi falls back to single-ticket semantics', () => {
  const plan = computeDropPlan({ number: 5, fromSprint: 'sprint-2', multi: [5] }, 'sprint-4');
  assert.equal(plan.mode, 'single');
  assert.deepEqual(plan.tickets, [5]);
});

test('computeDropPlan: null drag info is an inert no-op', () => {
  const plan = computeDropPlan(null, 'sprint-2');
  assert.equal(plan.noop, true);
  assert.deepEqual(plan.tickets, []);
});
