/**
 * Behavioral tests for the visibility-aware interval guard (issue #1775).
 *
 * AC1: visibilityInterval starts a normal interval when the tab is visible
 *      and clears it (stops calling fn) when the tab is hidden.
 * AC2: On becoming visible, fn fires immediately (catch-up) before the next
 *      regular tick.
 * AC3: fn is never called while the tab is hidden.
 * AC7: No duplicate intervals are created on rapid show/hide toggling.
 *
 * Run with: node --test tests/frontend/visibility.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Stubs ─────────────────────────────────────────────────────────────────────

function makeDocStub() {
  let _hidden = false;
  const _listeners = [];

  return {
    get hidden() { return _hidden; },
    addEventListener(event, fn) {
      if (event === 'visibilitychange') _listeners.push(fn);
    },
    removeEventListener(event, fn) {
      if (event !== 'visibilitychange') return;
      const i = _listeners.indexOf(fn);
      if (i !== -1) _listeners.splice(i, 1);
    },
    listenerCount() { return _listeners.length; },
    hide() {
      _hidden = true;
      for (const fn of [..._listeners]) fn();
    },
    show() {
      _hidden = false;
      for (const fn of [..._listeners]) fn();
    },
  };
}

function makeTimers() {
  let _nextId = 1;
  const _active = new Map();

  return {
    setInterval(fn, _delay) {
      const id = _nextId++;
      _active.set(id, fn);
      return id;
    },
    clearInterval(id) {
      _active.delete(id);
    },
    activeCount() { return _active.size; },
    tickAll() { for (const fn of [..._active.values()]) fn(); },
  };
}

// ── Set up stubs BEFORE import so the module picks them up ───────────────────

let _docStub = makeDocStub();
if (typeof globalThis.window === 'undefined') globalThis.window = globalThis;
globalThis.document = _docStub;

// Import the module under test (ESM is cached — module captures globals at
// call time, not import time, so per-test overrides of globalThis.setInterval
// and globalThis.document are picked up inside function bodies).
const { visibilityInterval } = await import(
  '../../apps/dashboard/static/src/shell/visibility.js'
);

// ── Per-test setup ────────────────────────────────────────────────────────────

function setup() {
  _docStub = makeDocStub();
  globalThis.document = _docStub;
  const timers = makeTimers();
  globalThis.setInterval = timers.setInterval.bind(timers);
  globalThis.clearInterval = timers.clearInterval.bind(timers);
  return { doc: _docStub, timers };
}

// ── AC1: starts/stops the interval based on tab visibility ───────────────────

test('AC1: creates one active interval when tab is visible', () => {
  const { timers } = setup();
  visibilityInterval(() => {}, 1000);
  assert.equal(timers.activeCount(), 1, 'must have exactly one active interval');
});

test('AC1: does not start an interval when tab is hidden at creation time', () => {
  const { doc, timers } = setup();
  doc.hide();
  visibilityInterval(() => {}, 1000);
  assert.equal(timers.activeCount(), 0, 'must not start interval when tab is hidden');
});

test('AC1: clears the interval when the tab becomes hidden', () => {
  const { doc, timers } = setup();
  visibilityInterval(() => {}, 1000);
  assert.equal(timers.activeCount(), 1);
  doc.hide();
  assert.equal(timers.activeCount(), 0, 'interval must be cleared on hide');
});

test('AC1: restarts the interval when the tab becomes visible again', () => {
  const { doc, timers } = setup();
  visibilityInterval(() => {}, 1000);
  doc.hide();
  doc.show();
  assert.equal(timers.activeCount(), 1, 'interval must resume after show');
});

// ── AC2: catch-up tick fires immediately on restore ───────────────────────────

test('AC2: fn fires once immediately when tab becomes visible after being hidden', () => {
  const { doc } = setup();
  let calls = 0;
  visibilityInterval(() => { calls++; }, 1000);
  doc.hide();
  calls = 0;   // reset — only care about the restore call
  doc.show();
  assert.equal(calls, 1, 'fn must fire exactly once on visibility restore (catch-up)');
});

test('AC2: interval resumes ticking after the catch-up tick', () => {
  const { doc, timers } = setup();
  let calls = 0;
  visibilityInterval(() => { calls++; }, 1000);
  doc.hide();
  calls = 0;
  doc.show();
  timers.tickAll();  // simulate one regular tick
  assert.equal(calls, 2, 'fn must fire catch-up (1) + one interval tick (2)');
});

// ── AC3: zero fn calls while the tab is hidden ────────────────────────────────

test('AC3: fn is never called while tab is hidden', () => {
  const { doc, timers } = setup();
  let calls = 0;
  visibilityInterval(() => { calls++; }, 1000);
  doc.hide();
  // Attempt ticks — no active intervals so tick is a no-op, but let's be explicit
  timers.tickAll();
  timers.tickAll();
  assert.equal(calls, 0, 'fn must not be called while hidden');
});

test('AC3: fn does not fire if tab was hidden before visibilityInterval was called', () => {
  const { doc, timers } = setup();
  doc.hide();
  let calls = 0;
  visibilityInterval(() => { calls++; }, 1000);
  timers.tickAll();
  assert.equal(calls, 0, 'fn must not fire if created while tab was hidden');
});

// ── AC7: no duplicate intervals on rapid show/hide toggling ──────────────────

test('AC7: exactly one interval active after repeated show/hide/show', () => {
  const { doc, timers } = setup();
  visibilityInterval(() => {}, 1000);
  doc.hide();
  doc.show();
  doc.hide();
  doc.show();
  doc.hide();
  doc.show();
  assert.equal(timers.activeCount(), 1, 'must have exactly one interval after rapid toggle');
});

test('AC7: zero intervals active after repeated show/hide ending hidden', () => {
  const { doc, timers } = setup();
  visibilityInterval(() => {}, 1000);
  doc.show();
  doc.hide();
  doc.show();
  doc.hide();
  assert.equal(timers.activeCount(), 0, 'must have zero intervals when ending hidden');
});

test('AC7: rapid toggle does not accumulate duplicate catch-up calls per show', () => {
  const { doc } = setup();
  let calls = 0;
  visibilityInterval(() => { calls++; }, 1000);
  doc.hide();
  calls = 0;
  doc.show();
  assert.equal(calls, 1, 'exactly one catch-up per show event');
  doc.hide();
  doc.show();
  assert.equal(calls, 2, 'exactly two catch-ups after two show events');
});
