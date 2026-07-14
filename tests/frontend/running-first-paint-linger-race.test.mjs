/**
 * Behavioral tests for issue #1926: Running pane shows a stale "finished
 * (snapshot)" banner while a sprint is actively running.
 *
 * Root cause: _smgmtRunningFirstPaint() fetched /api/running (the
 * authoritative "is this label running now" source) but then gated on
 * `_smgmtRunningLabels.has(label)` — a copy of that same fact populated
 * separately from the board aggregate endpoint's own (differently-cached)
 * response. When the aggregate cache hadn't caught up to a fresh
 * re-dispatch yet, the check failed and the fresh /api/running data was
 * discarded, leaving _smgmtIsLinger() with no signal to override the
 * stale "finished" snapshot from localStorage.
 *
 * These tests extract the real function bodies from project.html and
 * execute them with a stubbed fetch/DOM — not a source-text regex match
 * (issue #1746) — so they actually reproduce the discarded-response bug
 * on the pre-fix source and confirm the fix self-heals _smgmtRunningLabels.
 *
 * Run with: node --test tests/frontend/running-first-paint-linger-race.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_HTML = readFileSync(
  path.join(__dirname, '../../apps/dashboard/static/project.html'),
  'utf-8',
);

function extractFunction(name, src) {
  const re = new RegExp(
    `(?:async )?function ${name}\\([^)]*\\)\\s*\\{`, 'm',
  );
  const m = re.exec(src);
  if (!m) throw new Error(`${name} not found in project.html`);
  let i = m.index + m[0].length;
  let depth = 1;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(m.index, i);
}

const runningFirstPaintSrc = extractFunction('_smgmtRunningFirstPaint', PROJECT_HTML);
const isLingerSrc = extractFunction('_smgmtIsLinger', PROJECT_HTML);
const lingerActiveSrc = extractFunction('_smgmtLingerActive', PROJECT_HTML);

assert.ok(
  runningFirstPaintSrc.includes('/api/running'),
  'sanity: extracted _smgmtRunningFirstPaint must reference /api/running',
);

// ── Sandbox builder ──────────────────────────────────────────────────────

function buildSandbox({ fetchImpl, runningLabelsInitial = [] } = {}) {
  const calls = { livePollTick: 0, runHeadPatch: [], runningViewUpdate: [] };

  const sandbox = {
    console,
    fetch: fetchImpl,
    _smgmtRepo: () => 'owner/repo',
    _smgmtLiveCacheSeq: 0,
    _smgmtRunningLabels: new Set(runningLabelsInitial),
    _smgmtLiveCache: {},
    _smgmtLiveCacheWriteSeq: {},
    _smgmtLiveHeartbeatAt: null,
    _smgmtData: null,
    _smgmtRender: () => {},
    _smgmtLivePatch: () => {},
    _sprintProgressRender: () => {},
    _snavPanelOpen: false,
    _snavRenderPanel: () => {},
    _smgmtRunningViewUpdate: (label, data) => { calls.runningViewUpdate.push([label, data]); },
    _smgmtRunHeadPatch: (label, data) => { calls.runHeadPatch.push([label, data]); },
    _smgmtLivePollTick: () => { calls.livePollTick++; },
    // Linger machinery
    _smgmtLingerLabels: new Map(),
    _SMGMT_LINGER_MS: 60 * 60 * 1000,
  };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  vm.runInContext(lingerActiveSrc, ctx);
  vm.runInContext(isLingerSrc, ctx);
  sandbox._smgmtIsLinger = ctx._smgmtIsLinger;
  vm.runInContext(runningFirstPaintSrc, ctx);
  sandbox._smgmtRunningFirstPaint = ctx._smgmtRunningFirstPaint;
  return { sandbox, ctx, calls };
}

function fetchOk(body) {
  return async () => ({ ok: true, json: async () => body });
}

function fetchNotOk() {
  return async () => ({ ok: false, json: async () => ({}) });
}

// ── AC2: self-heals _smgmtRunningLabels from /api/running ────────────────

test('_smgmtRunningFirstPaint: adds a fresh label to _smgmtRunningLabels instead of discarding the response', async () => {
  const { sandbox, ctx, calls } = buildSandbox({
    // label NOT already in _smgmtRunningLabels — the exact race condition
    fetchImpl: fetchOk({ sprint_label: 'sprint-108.1', issues: [{ number: 1 }] }),
    runningLabelsInitial: [],
  });

  await vm.runInContext('_smgmtRunningFirstPaint()', ctx);

  assert.ok(
    sandbox._smgmtRunningLabels.has('sprint-108.1'),
    '_smgmtRunningLabels must be self-healed from the /api/running response',
  );
  assert.deepEqual(
    sandbox._smgmtLiveCache['sprint-108.1'],
    { sprint_label: 'sprint-108.1', issues: [{ number: 1 }] },
    'live cache must be populated from the fresh snapshot, not discarded',
  );
  assert.equal(calls.livePollTick, 0, 'must not fall back to the stale-poll path when data is valid');
  assert.equal(calls.runningViewUpdate.length, 1, '_smgmtRunningViewUpdate must be called with the fresh data');
});

test('_smgmtRunningFirstPaint: label already present still works (no regression)', async () => {
  const { sandbox, ctx } = buildSandbox({
    fetchImpl: fetchOk({ sprint_label: 'sprint-108.1', issues: [] }),
    runningLabelsInitial: ['sprint-108.1'],
  });

  await vm.runInContext('_smgmtRunningFirstPaint()', ctx);

  assert.ok(sandbox._smgmtRunningLabels.has('sprint-108.1'));
  assert.ok(sandbox._smgmtLiveCache['sprint-108.1']);
});

// ── AC4: no-sprint-running / error paths unchanged ────────────────────────

test('_smgmtRunningFirstPaint: 404 (no running sprint) still falls back to _smgmtLivePollTick', async () => {
  const { calls, ctx } = buildSandbox({
    fetchImpl: fetchNotOk(),
    runningLabelsInitial: [],
  });

  await vm.runInContext('_smgmtRunningFirstPaint()', ctx);

  assert.equal(calls.livePollTick, 1, 'must fall back to the poll tick when /api/running 404s');
});

test('_smgmtRunningFirstPaint: network error falls back to _smgmtLivePollTick', async () => {
  const { calls, ctx } = buildSandbox({
    fetchImpl: async () => { throw new Error('network down'); },
    runningLabelsInitial: [],
  });

  await vm.runInContext('_smgmtRunningFirstPaint()', ctx);

  assert.equal(calls.livePollTick, 1, 'must fall back to the poll tick on a network error');
});

// ── AC3: _smgmtIsLinger correctly defers to a genuine restart ────────────

test('_smgmtIsLinger: returns false once the label is present in _smgmtRunningLabels (restart overrides stale finish)', async () => {
  const { sandbox, ctx } = buildSandbox({
    fetchImpl: fetchOk({ sprint_label: 'sprint-108.1', issues: [] }),
    runningLabelsInitial: [],
  });

  // Simulate a linger entry from a previous finish, still within its 1h window.
  sandbox._smgmtLingerLabels.set('sprint-108.1', {
    endedAt: Date.now() - 5000,
    live: { issues: [] },
    project: 'owner/repo',
  });

  // Before the fix runs: label not yet in _smgmtRunningLabels → linger wins (stale banner).
  assert.equal(
    vm.runInContext('_smgmtIsLinger("sprint-108.1")', ctx),
    true,
    'sanity: linger is active before the running label is (re)discovered',
  );

  // The fix: first paint self-heals _smgmtRunningLabels from /api/running.
  await vm.runInContext('_smgmtRunningFirstPaint()', ctx);

  assert.equal(
    vm.runInContext('_smgmtIsLinger("sprint-108.1")', ctx),
    false,
    'a genuine restart must override the stale finished/linger banner',
  );
});

test('_smgmtIsLinger: still returns true for a label that is linger-flagged and NOT running', async () => {
  const { sandbox, ctx } = buildSandbox({ fetchImpl: fetchOk({ sprint_label: null }) });
  sandbox._smgmtLingerLabels.set('sprint-9', {
    endedAt: Date.now() - 1000,
    live: { issues: [] },
    project: 'owner/repo',
  });

  assert.equal(
    vm.runInContext('_smgmtIsLinger("sprint-9")', ctx),
    true,
    'a genuinely finished sprint (not running) must still show its linger snapshot',
  );
});
