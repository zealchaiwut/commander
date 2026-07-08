/**
 * Behavioral tests for issue #1779 AC1–AC2: gh-auth poll interval.
 *
 * Verifies that startGhAuthPoll() calls setInterval with 2000ms (not 600ms),
 * and that the poll function is invoked immediately on start.
 *
 * Run with: node --test tests/frontend/device-login-poll.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal stubs required at import time ─────────────────────────────────────
if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

import {
  GH_AUTH_POLL_INTERVAL_MS,
  startGhAuthPoll,
  stopGhAuthPoll,
} from '../../apps/dashboard/static/src/device-login.js';


// ── AC1: poll interval is 2000ms ──────────────────────────────────────────────

test('GH_AUTH_POLL_INTERVAL_MS is 2000', () => {
  assert.equal(GH_AUTH_POLL_INTERVAL_MS, 2000, 'poll interval must be 2000ms (was 600ms)');
});

test('startGhAuthPoll: passes 2000ms to setInterval', () => {
  const origSetInterval = globalThis.setInterval;
  const origClearInterval = globalThis.clearInterval;
  let capturedMs = null;
  let timerId = 0;

  globalThis.clearInterval = () => {};
  globalThis.setInterval = (fn, ms) => {
    capturedMs = ms;
    return ++timerId;
  };

  try {
    startGhAuthPoll(() => {});
    assert.equal(capturedMs, 2000, 'setInterval must be called with 2000ms interval');
  } finally {
    stopGhAuthPoll();
    globalThis.setInterval = origSetInterval;
    globalThis.clearInterval = origClearInterval;
  }
});


// ── AC2: device login still completes — poll function called immediately ───────

test('startGhAuthPoll: invokes pollFn immediately (first poll without waiting for interval)', () => {
  const origSetInterval = globalThis.setInterval;
  const origClearInterval = globalThis.clearInterval;
  globalThis.clearInterval = () => {};
  globalThis.setInterval = () => 99;

  let immediateCalls = 0;
  const pollFn = () => { immediateCalls++; };

  try {
    startGhAuthPoll(pollFn);
    assert.equal(immediateCalls, 1, 'pollFn must be called once immediately on startGhAuthPoll');
  } finally {
    stopGhAuthPoll();
    globalThis.setInterval = origSetInterval;
    globalThis.clearInterval = origClearInterval;
  }
});

test('startGhAuthPoll: passes pollFn to setInterval for periodic calls', () => {
  const origSetInterval = globalThis.setInterval;
  const origClearInterval = globalThis.clearInterval;
  let capturedFn = null;
  globalThis.clearInterval = () => {};
  globalThis.setInterval = (fn) => { capturedFn = fn; return 100; };

  const pollFn = () => {};

  try {
    startGhAuthPoll(pollFn);
    assert.equal(capturedFn, pollFn, 'setInterval must be given the same pollFn');
  } finally {
    stopGhAuthPoll();
    globalThis.setInterval = origSetInterval;
    globalThis.clearInterval = origClearInterval;
  }
});


// ── stopGhAuthPoll: clears timer ──────────────────────────────────────────────

test('stopGhAuthPoll: calls clearInterval on the active timer', () => {
  const origSetInterval = globalThis.setInterval;
  const origClearInterval = globalThis.clearInterval;
  let clearedId = null;
  const fakeTimerId = 42;

  globalThis.clearInterval = (id) => { clearedId = id; };
  globalThis.setInterval = () => fakeTimerId;

  try {
    startGhAuthPoll(() => {});
    stopGhAuthPoll();
    assert.equal(clearedId, fakeTimerId, 'clearInterval must be called with the timer id from setInterval');
  } finally {
    globalThis.setInterval = origSetInterval;
    globalThis.clearInterval = origClearInterval;
  }
});

test('stopGhAuthPoll: safe to call when no poll is running', () => {
  const origClearInterval = globalThis.clearInterval;
  let clearCalled = false;
  globalThis.clearInterval = () => { clearCalled = true; };

  try {
    stopGhAuthPoll(); // must not throw even if no timer is active
    // clearInterval may or may not be called when timer is null — either is fine
    assert.ok(true, 'stopGhAuthPoll when idle must not throw');
  } finally {
    globalThis.clearInterval = origClearInterval;
  }
});

test('startGhAuthPoll: calling twice stops previous interval first', () => {
  const origSetInterval = globalThis.setInterval;
  const origClearInterval = globalThis.clearInterval;
  let timerId = 0;
  const clearedIds = [];

  globalThis.clearInterval = (id) => { if (id != null) clearedIds.push(id); };
  globalThis.setInterval = () => ++timerId;

  try {
    startGhAuthPoll(() => {});
    const firstId = timerId;
    startGhAuthPoll(() => {});
    assert.ok(clearedIds.includes(firstId), 'second startGhAuthPoll must clear first timer');
  } finally {
    stopGhAuthPoll();
    globalThis.setInterval = origSetInterval;
    globalThis.clearInterval = origClearInterval;
  }
});
