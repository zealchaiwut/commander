/**
 * Behavioral tests for issue #2356 — UI Run Sprint → dispatch API.
 *
 * Run with: node --test tests/frontend/run-sprint-dispatch-2356.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { createRequire } from 'node:module';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../..');
const runControlsPath = path.join(
  ROOT,
  'apps/dashboard/static/src/sprint-board/run-controls.js',
);

// Minimal stubs so the module can load without a browser.
globalThis.document = globalThis.document || {
  getElementById: () => null,
};
globalThis._smgmtShowToast = () => {};
globalThis._smgmtRepo = () => 'zealchaiwut/commander';
globalThis.loadSprintMgmt = async () => {};

test('smgmtRunSprint POSTs dispatch URL with all:true for the label', async () => {
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    calls.push({ url, opts });
    return {
      ok: true,
      status: 200,
      json: async () => ({ run_id: 'r1', tickets: [1, 2] }),
    };
  };

  // Dynamic import of the ES module under test.
  const mod = await import(pathToFileURL(runControlsPath).href + '?t=' + Date.now());
  assert.equal(typeof mod.smgmtRunSprint, 'function');

  const planned = mod._dispatchRequestFor('sprint-1030', 'zealchaiwut/commander');
  assert.equal(planned.url, '/api/sprints/sprint-1030/dispatch');
  assert.deepEqual(planned.body, {
    tickets: [],
    all: true,
    repo: 'zealchaiwut/commander',
  });

  await mod.smgmtRunSprint('sprint-1030');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/sprints/sprint-1030/dispatch');
  assert.equal(calls[0].opts.method, 'POST');
  const body = JSON.parse(calls[0].opts.body);
  assert.equal(body.all, true);
  assert.deepEqual(body.tickets, []);
  assert.equal(body.repo, 'zealchaiwut/commander');
});

test('smgmtRunSprint is not a no-op (fetch is invoked)', async () => {
  let hit = false;
  globalThis.fetch = async () => {
    hit = true;
    return { ok: true, status: 200, json: async () => ({ run_id: 'x', tickets: [] }) };
  };
  const mod = await import(pathToFileURL(runControlsPath).href + '?t=' + Date.now() + 'b');
  await mod.smgmtRunSprint('sprint-99');
  assert.equal(hit, true);
});
