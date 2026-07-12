/**
 * Behavioral tests for issue #1853: group Activity feed by sprint run.
 *
 * Tests the pure evlGroupEventsByRun() helper exported from activity-grouping.js.
 * No DOM required — pure data-transformation function.
 *
 * Run: node --test tests/frontend/activity-grouping.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { evlGroupEventsByRun } from '../../apps/dashboard/static/src/activity-grouping.js';

// ── Fixtures ─────────────────────────────────────────────────────────────────

function ev(overrides = {}) {
  return {
    timestamp: '2026-07-12T10:00:00',
    source: 'agent',
    type: 'ticket_dispatched',
    target: '',
    detail: {},
    ...overrides,
  };
}

// ── AC1: events grouped by sprint run ────────────────────────────────────────

test('AC1: sprint events grouped under their sprint label', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'ticket_dispatched' }),
    ev({ sprint_label: 'sprint-112', type: 'ticket_failed' }),
    ev({ source: 'github', type: 'pr_opened', target: '#1', detail: {} }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups.length, 2, 'one sprint group + one other');
  assert.equal(groups[0].sprint_label, 'sprint-112');
  assert.equal(groups[0].isOther, false);
  assert.equal(groups[0].events.length, 2);
  assert.equal(groups[1].isOther, true);
});

test('AC1: unassociated events fall into Dashboard/other per day', () => {
  const events = [
    ev({ timestamp: '2026-07-12T10:00:00', source: 'github', type: 'pr_opened', target: '#1', detail: {} }),
    ev({ timestamp: '2026-07-12T09:00:00', source: 'github', type: 'label_added', detail: {} }),
    ev({ timestamp: '2026-07-11T10:00:00', source: 'github', type: 'pr_merged', detail: {} }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups.length, 2, 'two different days → two other groups');
  assert.equal(groups[0].isOther, true);
  assert.equal(groups[0].dayKey, '2026-07-12');
  assert.equal(groups[0].events.length, 2, 'both same-day events in first group');
  assert.equal(groups[1].dayKey, '2026-07-11');
  assert.equal(groups[1].events.length, 1);
});

test('AC1: newest sprint run group appears first (events sorted newest-first)', () => {
  const events = [
    ev({ sprint_label: 'sprint-113', timestamp: '2026-07-12T10:00:00', type: 'sprint_started' }),
    ev({ sprint_label: 'sprint-112', timestamp: '2026-07-11T10:00:00', type: 'sprint_started' }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].sprint_label, 'sprint-113', 'most recent sprint first');
  assert.equal(groups[1].sprint_label, 'sprint-112');
});

test('AC1: multiple events for same sprint go into one group', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'sprint_started' }),
    ev({ sprint_label: 'sprint-112', type: 'ticket_dispatched' }),
    ev({ sprint_label: 'sprint-112', type: 'sprint_finished' }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].events.length, 3);
});

test('AC1: sprint label extracted from detail.sprint_label', () => {
  const events = [
    ev({ type: 'label_added', source: 'github', detail: { sprint_label: 'sprint-112', label_name: 'in-progress' } }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].sprint_label, 'sprint-112');
  assert.equal(groups[0].isOther, false);
});

test('AC1: sprint label extracted from detail.sprint_id', () => {
  const events = [
    ev({ type: 'sprint_started', source: 'agent', detail: { sprint_id: 'sprint-112', ticket_count: 5 } }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].sprint_label, 'sprint-112');
});

test('AC1: sprint label from ev.target for sprint_run lifecycle events', () => {
  const events = [
    ev({ type: 'sprint_run', source: 'agent', target: 'sprint-112', detail: {} }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].sprint_label, 'sprint-112');
  assert.equal(groups[0].isOther, false);
});

test('AC1: ev.target NOT used as sprint label for non-sprint events (pr_merged)', () => {
  // PR number '#123' must not be treated as a sprint label
  const events = [
    ev({ type: 'pr_merged', source: 'github', target: '#123', detail: {} }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].isOther, true, 'pr_merged without sprint detail → other group');
  assert.equal(groups[0].sprint_label, '');
});

test('AC1: ev.target NOT used as sprint label for label_added events', () => {
  const events = [
    ev({ type: 'label_added', source: 'github', target: '#42', detail: {} }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].isOther, true);
});

// ── AC2: groups with errors expand by default ─────────────────────────────────

test('AC2: hasErrors=true when group contains ticket_failed', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'ticket_dispatched' }),
    ev({ sprint_label: 'sprint-112', type: 'ticket_failed' }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].hasErrors, true);
});

test('AC2: hasErrors=false when group has no error events', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'sprint_started' }),
    ev({ sprint_label: 'sprint-112', type: 'sprint_finished', detail: { done: 3, failed: 0 } }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].hasErrors, false);
});

test('AC2: agent_finished with error status is an error', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'agent_finished', detail: { status: 'error' } }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].hasErrors, true);
});

test('AC2: agent_finished with timed_out status is an error', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'agent_finished', detail: { status: 'timed_out' } }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].hasErrors, true);
});

test('AC2: agent_finished with completed status is NOT an error', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'agent_finished', detail: { status: 'completed' } }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].hasErrors, false);
});

// ── AC4: group header shows event count and error count ───────────────────────

test('AC4: errorCount reflects number of error events', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'ticket_failed' }),
    ev({ sprint_label: 'sprint-112', type: 'ticket_failed' }),
    ev({ sprint_label: 'sprint-112', type: 'sprint_started' }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].errorCount, 2);
  assert.equal(groups[0].events.length, 3, 'all 3 events in group');
});

test('AC4: errorCount=0 for successful sprint', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'sprint_started' }),
    ev({ sprint_label: 'sprint-112', type: 'sprint_finished', detail: { done: 5, failed: 0 } }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups[0].errorCount, 0);
  assert.equal(groups[0].events.length, 2);
});

// ── Outcome detection ─────────────────────────────────────────────────────────

test('outcome: "completed" when sprint_finished with no failures', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'sprint_finished', detail: { done: 3, failed: 0 } }),
  ];
  const [g] = evlGroupEventsByRun(events);
  assert.equal(g.outcome, 'completed');
});

test('outcome: "failed" when sprint_finished with failures', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'sprint_finished', detail: { done: 2, failed: 1 } }),
  ];
  const [g] = evlGroupEventsByRun(events);
  assert.equal(g.outcome, 'failed');
});

test('outcome: null when no sprint_finished event (running or unknown)', () => {
  const events = [
    ev({ sprint_label: 'sprint-112', type: 'sprint_started' }),
    ev({ sprint_label: 'sprint-112', type: 'ticket_dispatched' }),
  ];
  const [g] = evlGroupEventsByRun(events);
  assert.equal(g.outcome, null);
});

// ── Edge cases ────────────────────────────────────────────────────────────────

test('empty input returns empty array', () => {
  assert.deepEqual(evlGroupEventsByRun([]), []);
  assert.deepEqual(evlGroupEventsByRun(null), []);
  assert.deepEqual(evlGroupEventsByRun(undefined), []);
});

test('mixed sprint and other events maintain separate groups', () => {
  const events = [
    ev({ sprint_label: 'sprint-113', type: 'sprint_started', timestamp: '2026-07-12T10:00:00' }),
    ev({ source: 'github', type: 'issue_opened', target: '#200', detail: {}, timestamp: '2026-07-12T09:00:00' }),
    ev({ sprint_label: 'sprint-112', type: 'sprint_finished', detail: { done: 2, failed: 0 }, timestamp: '2026-07-11T10:00:00' }),
  ];
  const groups = evlGroupEventsByRun(events);
  assert.equal(groups.length, 3);
  assert.equal(groups[0].sprint_label, 'sprint-113');
  assert.equal(groups[1].isOther, true);
  assert.equal(groups[2].sprint_label, 'sprint-112');
});
