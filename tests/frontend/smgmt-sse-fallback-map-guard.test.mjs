/**
 * Behavioral tests for issue #1917: _smgmtSseEs Map-as-nullable bug.
 *
 * Issue #1818 converted _smgmtSseEs from a nullable EventSource to a permanent
 * Map().  Two call sites still checked `if (!_smgmtSseEs)` — a Map is always
 * truthy so that condition was always false, permanently disabling both REST
 * fallbacks.
 *
 * AC1: _smgmtLanesUpdate must call _smgmtWorkerLogFetch (multi-worker REST
 *      fetch) when SSE is NOT connected for the sprint label, and must NOT call
 *      it when SSE IS connected.
 *
 * AC2: _smgmtInspectorStreamRestart must start the 5 s poll via
 *      visibilityInterval when SSE is NOT connected for the inspector label,
 *      and must NOT start it when SSE IS connected.
 *
 * Run with: node --test tests/frontend/smgmt-sse-fallback-map-guard.test.mjs
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

// ── Extract code blocks ───────────────────────────────────────────────────────

const sseMapMarker = 'const _smgmtSseEs = new Map();';
const sseMapIdx = html.indexOf(sseMapMarker);
assert.ok(sseMapIdx !== -1, '_smgmtSseEs Map init not found in project.html');

const lanesStartMarker = 'function _smgmtLanesUpdate(label, live) {';
const lanesEndMarker = '// ── Running-pane Gantt timeline (issue #1147)';
const lanesStartIdx = html.indexOf(lanesStartMarker);
const lanesEndIdx = html.indexOf(lanesEndMarker);
assert.ok(lanesStartIdx !== -1, '_smgmtLanesUpdate not found in project.html');
assert.ok(lanesEndIdx !== -1, 'Gantt timeline sentinel not found in project.html');
const lanesBlock = html.slice(lanesStartIdx, lanesEndIdx).trimEnd();

const inspStartMarker = 'function _smgmtInspectorStreamRestart() {';
const inspEndMarker = '/** Switch the active log tab and restart streaming. */';
const inspStartIdx = html.indexOf(inspStartMarker);
const inspEndIdx = html.indexOf(inspEndMarker);
assert.ok(inspStartIdx !== -1, '_smgmtInspectorStreamRestart not found in project.html');
assert.ok(inspEndIdx !== -1, 'inspectorSelectTab sentinel not found in project.html');
const inspBlock = html.slice(inspStartIdx, inspEndIdx).trimEnd();

// ── AC1: _smgmtLanesUpdate — multi-worker REST fallback ──────────────────────

function makeLanesContext() {
  let workerLogFetchCalled = false;
  const mockLanesEl = { hidden: false, innerHTML: '' };

  const ctx = vm.createContext({
    // SSE Map (starts empty — simulates SSE disconnected)
    document: {
      getElementById: (id) => id === 'smgmt-lanes' ? mockLanesEl : null,
      querySelector: () => null,
    },
    _smgmtLaneStage: (iss) => iss._stage || 'coding',
    _smgmtWorkerLanesHtml: () => '',
    _smgmtWorkerLogFetch: () => { workerLogFetchCalled = true; },
    _smgmtRepo: () => 'owner/repo',
    _smgmtLanesHtml: () => '',
    _smgmtLaneFoldToggle: () => {},
  });

  // Inject _smgmtSseEs Map into context
  vm.runInContext(sseMapMarker, ctx);
  vm.runInContext(lanesBlock, ctx);

  return { ctx, mockLanesEl, isWorkerLogFetchCalled: () => workerLogFetchCalled };
}

/** Build a live snapshot with two active coders (triggers multi-slot path). */
function multiCoderLive() {
  return {
    max_coder_slots: 2,
    issues: [
      { number: 10, _stage: 'coding', agent: 'coder' },
      { number: 11, _stage: 'coding', agent: 'coder' },
    ],
  };
}

test('AC1: _smgmtLanesUpdate calls _smgmtWorkerLogFetch when SSE is not connected for label', () => {
  const { ctx, isWorkerLogFetchCalled } = makeLanesContext();

  // SSE map is empty (no connection for 'sprint-100') — fallback MUST fire
  vm.runInContext(
    `_smgmtLanesUpdate('sprint-100', ${JSON.stringify(multiCoderLive())})`,
    ctx
  );

  assert.ok(
    isWorkerLogFetchCalled(),
    '_smgmtWorkerLogFetch must be called when _smgmtSseEs has no entry for the label (AC1)'
  );
});

test('AC1: _smgmtLanesUpdate does NOT call _smgmtWorkerLogFetch when SSE is connected for label', () => {
  const { ctx, isWorkerLogFetchCalled } = makeLanesContext();

  // Connect SSE for the label — fallback must be suppressed
  vm.runInContext(`_smgmtSseEs.set('sprint-100', { close: () => {} })`, ctx);

  vm.runInContext(
    `_smgmtLanesUpdate('sprint-100', ${JSON.stringify(multiCoderLive())})`,
    ctx
  );

  assert.ok(
    !isWorkerLogFetchCalled(),
    '_smgmtWorkerLogFetch must NOT be called when SSE is connected for the label (AC1)'
  );
});

// ── AC2: _smgmtInspectorStreamRestart — 5 s poll fallback ────────────────────

function makeInspectorContext() {
  let visibilityIntervalCalled = false;
  let clearIntervalCalled = false;

  const ctx = vm.createContext({
    clearInterval: () => { clearIntervalCalled = true; },
    visibilityInterval: () => { visibilityIntervalCalled = true; return 42; },
    _smgmtInspectorStreamTick: () => {},
    _smgmtInspectorPollId: null,
    _smgmtInspectorLabel: 'sprint-100',
  });

  vm.runInContext(sseMapMarker, ctx);
  vm.runInContext(inspBlock, ctx);

  return {
    ctx,
    isVisibilityIntervalCalled: () => visibilityIntervalCalled,
  };
}

test('AC2: _smgmtInspectorStreamRestart starts 5 s poll when SSE is not connected for inspector label', () => {
  const { ctx, isVisibilityIntervalCalled } = makeInspectorContext();

  // SSE map empty — no connection for 'sprint-100' — poll MUST start
  vm.runInContext('_smgmtInspectorStreamRestart()', ctx);

  assert.ok(
    isVisibilityIntervalCalled(),
    'visibilityInterval must be called when _smgmtSseEs has no entry for _smgmtInspectorLabel (AC2)'
  );
});

test('AC2: _smgmtInspectorStreamRestart does NOT start 5 s poll when SSE is connected for inspector label', () => {
  const { ctx, isVisibilityIntervalCalled } = makeInspectorContext();

  // Connect SSE for the inspector label — poll must be suppressed
  vm.runInContext(`_smgmtSseEs.set('sprint-100', { close: () => {} })`, ctx);

  vm.runInContext('_smgmtInspectorStreamRestart()', ctx);

  assert.ok(
    !isVisibilityIntervalCalled(),
    'visibilityInterval must NOT be called when SSE is connected for _smgmtInspectorLabel (AC2)'
  );
});
