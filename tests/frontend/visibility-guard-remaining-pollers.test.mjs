/**
 * Behavioral tests for issue #1796: route three remaining raw-setInterval
 * network pollers through visibilityInterval so they pause while the tab is hidden.
 *
 * AC1: _smgmtSseStartFallback uses visibilityInterval (not raw setInterval)
 *      for the ≥15 s SSE-outage fallback poll.
 * AC2: _smgmtLivePollRestart uses visibilityInterval for the 10-min timeline
 *      refresh timer (_smgmtTimelineRefreshId).
 * AC3: _bcStartPostPoll uses visibilityInterval (not raw setInterval) for the
 *      2 s bulk-post progress poll.
 *
 * Run with: node --test tests/frontend/visibility-guard-remaining-pollers.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dir = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(
  join(__dir, '../../apps/dashboard/static/project.html'),
  'utf8'
);

// ── AC1: SSE Fallback Poller ─────────────────────────────────────────────────

test('AC1: _smgmtSseStartFallback uses _vi defensive pattern for visibility guarding', () => {
  // Within _smgmtSseStartFallback: the defensive _vi pattern is defined,
  // then _smgmtSseFallbackId.set is called with _vi (not raw setInterval)
  const fnStart = html.indexOf('function _smgmtSseStartFallback(label, repo) {');

  assert.ok(fnStart !== -1, '_smgmtSseStartFallback function not found');

  // Extract ~1000 chars after fn start for pattern matching
  const fnBody = html.slice(fnStart, fnStart + 1000);

  // Verify: _vi pattern defined AND _smgmtSseFallbackId.set uses _vi (not raw setInterval)
  assert.ok(fnBody.includes('const _vi = typeof visibilityInterval === \'function\''),
    'AC1: _vi defensive pattern must be defined');
  assert.ok(fnBody.includes('_smgmtSseFallbackId.set(label, _vi('),
    'AC1: Must call _vi for the SSE fallback timer');
  assert.ok(!fnBody.includes('_smgmtSseFallbackId.set(label, setInterval('),
    'AC1: Must NOT call raw setInterval');
});

test('AC1: _smgmtSseFallbackStop properly clears the timer handle', () => {
  const fnStart = html.indexOf('function _smgmtSseFallbackStop(');
  const fnEnd = html.indexOf('/** Start the ≥15 s fallback poll');

  assert.ok(fnStart !== -1, '_smgmtSseFallbackStop function not found');
  const fnBody = html.slice(fnStart, fnEnd);

  // Verify clearInterval is called
  assert.ok(fnBody.includes('clearInterval'),
    'AC1: _smgmtSseFallbackStop must call clearInterval');
});

// ── AC2: Timeline Refresh Poller ─────────────────────────────────────────────

test('AC2: _smgmtLivePollRestart uses _vi defensive pattern for the timeline timer', () => {
  const fnStart = html.indexOf('function _smgmtLivePollRestart() {');
  const fnEnd = html.indexOf('/** First-paint for the Running view');

  assert.ok(fnStart !== -1, '_smgmtLivePollRestart function not found');
  const fnBody = html.slice(fnStart, fnEnd);

  // Verify: _vi pattern defined AND _smgmtTimelineRefreshId assignment uses _vi
  assert.match(fnBody, /const _vi = typeof visibilityInterval.*?setInterval/,
    'AC2: _vi defensive pattern must be defined');
  assert.ok(fnBody.includes('_smgmtTimelineRefreshId = _vi(_smgmtTimelineRefreshTick'),
    'AC2: Must call _vi for the timeline refresh timer');
  assert.ok(!fnBody.includes('_smgmtTimelineRefreshId = setInterval(_smgmtTimelineRefreshTick'),
    'AC2: Must NOT call raw setInterval');
});

// ── AC3: Bulk-Post Poll Poller ───────────────────────────────────────────────

test('AC3: _bcStartPostPoll uses _vi defensive pattern for bulk-post progress polling', () => {
  const fnStart = html.indexOf('function _bcStartPostPoll() {');

  assert.ok(fnStart !== -1, '_bcStartPostPoll function not found');
  const fnBody = html.slice(fnStart, fnStart + 1000);

  // Verify: _vi pattern defined AND _bcPostPollId assignment uses _vi
  assert.ok(fnBody.includes('const _vi = typeof visibilityInterval === \'function\''),
    'AC3: _vi defensive pattern must be defined');
  assert.ok(fnBody.includes('_bcPostPollId = _vi('),
    'AC3: Must call _vi for the bulk-post poll timer');
  assert.ok(!fnBody.includes('_bcPostPollId = setInterval('),
    'AC3: Must NOT call raw setInterval');
});

test('AC3: _bcStopPostPoll properly clears the bulk-post timer handle', () => {
  const fnStart = html.indexOf('function _bcStopPostPoll()');
  const fnEnd = html.indexOf('function _bcStartPostPoll()');

  assert.ok(fnStart !== -1, '_bcStopPostPoll function not found');
  const fnBody = html.slice(fnStart, fnEnd);

  // Verify clearInterval is called
  assert.ok(fnBody.includes('clearInterval'),
    'AC3: _bcStopPostPoll must call clearInterval');
});

// ── Integration: Defensive pattern used consistently ──────────────────────────

test('Integration: All three pollers use _vi defensive pattern', () => {
  // Verify that all three functions use the defensive pattern
  const ssePattern = /function _smgmtSseStartFallback[\s\S]*?const _vi = typeof visibilityInterval[\s\S]*?_smgmtSseFallbackId\.set\(label, _vi\(/;
  const pollPattern = /function _smgmtLivePollRestart[\s\S]*?const _vi = typeof visibilityInterval[\s\S]*?_smgmtTimelineRefreshId = _vi\(/;
  const bcPattern = /function _bcStartPostPoll[\s\S]*?const _vi = typeof visibilityInterval[\s\S]*?_bcPostPollId = _vi\(/;

  assert.ok(ssePattern.test(html),
    'SSE fallback must use _vi defensive pattern');
  assert.ok(pollPattern.test(html),
    'Timeline refresh must use _vi defensive pattern');
  assert.ok(bcPattern.test(html),
    'Bulk-post poll must use _vi defensive pattern');
});

test('Integration: No raw setInterval calls in the guarded timer assignments', () => {
  // The three timer assignments must not use raw setInterval
  assert.ok(!html.includes('_smgmtSseFallbackId.set(label, setInterval('),
    'SSE fallback must not use raw setInterval');
  assert.ok(!html.includes('_smgmtTimelineRefreshId = setInterval('),
    'Timeline refresh must not use raw setInterval');
  assert.ok(!html.includes('_bcPostPollId = setInterval('),
    'Bulk-post poll must not use raw setInterval');
});
