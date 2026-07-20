/**
 * Behavioral tests for issue #1796: route the three remaining raw-setInterval
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
import vm from 'node:vm';

const __dir = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(
  join(__dir, '../../apps/dashboard/static/project.html'),
  'utf8'
);

// ── Extract SSE fallback block (AC1) ─────────────────────────────────────────
const sseBlockStart = 'const _smgmtSseEs = new Map();';
const sseBlockEnd = "/** Handle a 'snapshot' event from the SSE stream.";
const sseBlockStartIdx = html.indexOf(sseBlockStart);
const sseBlockEndIdx = html.indexOf(sseBlockEnd);
assert.ok(sseBlockStartIdx !== -1, 'SSE block start marker not found in project.html');
assert.ok(sseBlockEndIdx !== -1, 'SSE block end marker not found in project.html');
const sseBlock = html.slice(sseBlockStartIdx, sseBlockEndIdx).trimEnd();

// ── Extract _smgmtLivePollRestart block (AC2) ─────────────────────────────────
const pollBlockStart = 'function _smgmtLivePollRestart() {';
const pollBlockEnd = '/** First-paint for the Running view via single /api/running snapshot';
const pollBlockStartIdx = html.indexOf(pollBlockStart);
const pollBlockEndIdx = html.indexOf(pollBlockEnd);
assert.ok(pollBlockStartIdx !== -1, '_smgmtLivePollRestart start marker not found in project.html');
assert.ok(pollBlockEndIdx !== -1, '_smgmtLivePollRestart end marker not found in project.html');
const pollBlock = html.slice(pollBlockStartIdx, pollBlockEndIdx).trimEnd();

// ── Extract bulk-post poll block (AC3) ───────────────────────────────────────
const bcBlockStart = 'let _bcPostPollId = null;';
const bcBlockEnd = 'async function bcPostSelected() {';
const bcBlockStartIdx = html.indexOf(bcBlockStart);
const bcBlockEndIdx = html.indexOf(bcBlockEnd);
assert.ok(bcBlockStartIdx !== -1, '_bcPostPollId declaration not found in project.html');
assert.ok(bcBlockEndIdx !== -1, 'bcPostSelected sentinel not found in project.html');
const bcBlock = html.slice(bcBlockStartIdx, bcBlockEndIdx).trimEnd();

// ── AC1: _smgmtSseStartFallback uses visibilityInterval ──────────────────────

function makeSseContext() {
  let viCalls = 0;
  let rawSetIntervalCalls = 0;

  const ctx = vm.createContext({
    visibilityInterval: (fn, delay) => { viCalls++; return ++makeSseContext._id; },
    setInterval: () => { rawSetIntervalCalls++; return ++makeSseContext._id; },
    clearInterval: () => {},
    fetch: async () => ({ ok: false }),
    _smgmtSseConnect: () => {},
    _smgmtSseHandleSnapshot: () => {},
  });

  vm.runInContext(sseBlock, ctx);

  return {
    ctx,
    viCallCount: () => viCalls,
    rawCallCount: () => rawSetIntervalCalls,
  };
}
makeSseContext._id = 0;

test('AC1: _smgmtSseStartFallback calls visibilityInterval (not raw setInterval)', () => {
  const { ctx, viCallCount, rawCallCount } = makeSseContext();

  vm.runInContext("_smgmtSseStartFallback('sprint-100', 'owner/repo')", ctx);

  assert.equal(viCallCount(), 1,
    'visibilityInterval must be called once to register the SSE fallback poll (AC1)');
  assert.equal(rawCallCount(), 0,
    'raw setInterval must NOT be called for the SSE fallback poll (AC1)');
});

test('AC1: _smgmtSseFallbackStop still clears the visibilityInterval handle via clearInterval', () => {
  let clearCalls = 0;
  const ctx = vm.createContext({
    visibilityInterval: (fn, delay) => 42,
    setInterval: () => 99,
    clearInterval: (id) => { clearCalls++; },
    fetch: async () => ({ ok: false }),
    _smgmtSseConnect: () => {},
    _smgmtSseHandleSnapshot: () => {},
  });
  vm.runInContext(sseBlock, ctx);

  // Start then stop the fallback for a label
  vm.runInContext("_smgmtSseStartFallback('sprint-100', 'owner/repo')", ctx);
  vm.runInContext("_smgmtSseFallbackStop('sprint-100')", ctx);

  assert.equal(clearCalls, 1,
    'clearInterval must be called once when stopping the SSE fallback (AC1)');
});

// ── AC2: _smgmtLivePollRestart uses visibilityInterval for timeline refresh ──

function makePollContext() {
  let viCalls = 0;
  let rawSetIntervalCalls = 0;

  const ctx = vm.createContext({
    visibilityInterval: (fn, delay) => { viCalls++; return ++makePollContext._id; },
    setInterval: () => { rawSetIntervalCalls++; return ++makePollContext._id; },
    clearInterval: () => {},
    _smgmtRunningLabels: new Set(['sprint-100']),
    _smgmtLivePollId: null,
    _smgmtLogPollId: null,
    _smgmtTimelineRefreshId: null,
    _smgmtRepo: () => 'owner/repo',
    _SMGMT_TIMELINE_REFRESH_MS: 600000,
    _smgmtTimelineRefreshTick: () => {},
    _smgmtLingerPollRestart: () => {},
    _smgmtRunningFirstPaint: () => {},
    _smgmtSseDisconnect: () => {},
    _smgmtSseConnect: () => {},
  });

  return {
    ctx,
    viCallCount: () => viCalls,
    rawCallCount: () => rawSetIntervalCalls,
  };
}
makePollContext._id = 0;

test('AC2: _smgmtLivePollRestart uses visibilityInterval for the 10-min timeline refresh', () => {
  const { ctx, viCallCount, rawCallCount } = makePollContext();

  vm.runInContext(pollBlock, ctx);
  vm.runInContext('_smgmtLivePollRestart()', ctx);

  assert.ok(viCallCount() >= 1,
    'visibilityInterval must be called at least once for the timeline refresh timer (AC2)');
  assert.equal(rawCallCount(), 0,
    'raw setInterval must NOT be called for the timeline refresh (AC2)');
});

test('AC2: _smgmtLivePollRestart sets _smgmtTimelineRefreshId from visibilityInterval return value', () => {
  let lastViId = null;
  const ctx = vm.createContext({
    visibilityInterval: (fn, delay) => { lastViId = ++makePollContext._id; return lastViId; },
    setInterval: () => 9999,
    clearInterval: () => {},
    _smgmtRunningLabels: new Set(['sprint-100']),
    _smgmtLivePollId: null,
    _smgmtLogPollId: null,
    _smgmtTimelineRefreshId: null,
    _smgmtRepo: () => 'owner/repo',
    _SMGMT_TIMELINE_REFRESH_MS: 600000,
    _smgmtTimelineRefreshTick: () => {},
    _smgmtLingerPollRestart: () => {},
    _smgmtRunningFirstPaint: () => {},
    _smgmtSseDisconnect: () => {},
    _smgmtSseConnect: () => {},
  });

  vm.runInContext(pollBlock, ctx);
  vm.runInContext('_smgmtLivePollRestart()', ctx);

  const storedId = vm.runInContext('_smgmtTimelineRefreshId', ctx);
  assert.equal(storedId, lastViId,
    '_smgmtTimelineRefreshId must be the handle returned by visibilityInterval (AC2)');
});

// ── AC3: _bcStartPostPoll uses visibilityInterval ─────────────────────────────

function makeBcContext() {
  let viCalls = 0;
  let rawSetIntervalCalls = 0;

  const ctx = vm.createContext({
    visibilityInterval: (fn, delay) => { viCalls++; return ++makeBcContext._id; },
    setInterval: () => { rawSetIntervalCalls++; return ++makeBcContext._id; },
    clearInterval: () => {},
    fetch: async () => ({ ok: true, json: async () => ({ status: 'running', tickets: [] }) }),
    _bcPosting: true,
    _bcJobId: 'job-123',
    _bcJobState: null,
    _bcPostingIndices: [],
    _bcRenderStep2: () => {},
    _bcUpdateBadge: () => {},
    _bcPostingComplete: false,
    _bcEventSource: null,
  });

  vm.runInContext(bcBlock, ctx);

  return {
    ctx,
    viCallCount: () => viCalls,
    rawCallCount: () => rawSetIntervalCalls,
  };
}
makeBcContext._id = 0;

test('AC3: _bcStartPostPoll calls visibilityInterval (not raw setInterval)', () => {
  const { ctx, viCallCount, rawCallCount } = makeBcContext();

  vm.runInContext('_bcStartPostPoll()', ctx);

  assert.equal(viCallCount(), 1,
    'visibilityInterval must be called once by _bcStartPostPoll (AC3)');
  assert.equal(rawCallCount(), 0,
    'raw setInterval must NOT be called by _bcStartPostPoll (AC3)');
});

test('AC3: _bcStopPostPoll clears the visibilityInterval handle via clearInterval', () => {
  let clearCalls = 0;
  const ctx = vm.createContext({
    visibilityInterval: (fn, delay) => 55,
    setInterval: () => 9999,
    clearInterval: (id) => { clearCalls++; },
    fetch: async () => ({ ok: true, json: async () => ({ status: 'running', tickets: [] }) }),
    _bcPosting: true,
    _bcJobId: 'job-123',
    _bcJobState: null,
    _bcPostingIndices: [],
    _bcRenderStep2: () => {},
    _bcUpdateBadge: () => {},
    _bcPostingComplete: false,
    _bcEventSource: null,
  });
  vm.runInContext(bcBlock, ctx);

  vm.runInContext('_bcStartPostPoll()', ctx);
  vm.runInContext('_bcStopPostPoll()', ctx);

  assert.equal(clearCalls, 1,
    'clearInterval must be called once to cancel the bulk-post poll handle (AC3)');
});
