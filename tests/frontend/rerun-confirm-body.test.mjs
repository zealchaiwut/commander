/**
 * Frontend behavioral tests for issue #1754: rerun confirm body + error handling.
 *
 * AC3: stub fetch, click handler sends a valid body and handles a 422 by
 *      showing the error (not swallowing).
 * AC2: non-2xx from the rerun endpoint surfaces as a visible error toast.
 *
 * Run with: node --test tests/frontend/rerun-confirm-body.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal DOM stub ──────────────────────────────────────────────────────────

function _makeEl(overrides) {
  return {
    textContent: '',
    innerHTML: '',
    disabled: false,
    classList: {
      _classes: new Set(),
      add(c) { this._classes.add(c); },
      remove(c) { this._classes.delete(c); },
      contains(c) { return this._classes.has(c); },
    },
    ...overrides,
  };
}

function _installDom(checkedIssues) {
  const els = {
    'rr-confirm-btn': _makeEl({ disabled: true, textContent: 'Create sprint and run' }),
    'rr-content': _makeEl(),
    'rr-loading': _makeEl(),
    'rr-error': _makeEl(),
  };

  const checkboxes = (checkedIssues || []).map(num => ({
    checked: true,
    dataset: { issue: String(num) },
  }));

  globalThis.document = {
    getElementById: (id) => els[id] || null,
    querySelectorAll: (sel) => {
      if (sel === '#rr-ticket-list input[type=checkbox]') return checkboxes;
      return [];
    },
  };

  return els;
}

// ── Global stubs required by rerun-modal.js at import time ───────────────────

const toastCalls = [];
const _noop = () => {};

globalThis._setBodyInert = _noop;
globalThis._clearBodyInert = _noop;
globalThis._smgmtRepo = () => 'owner/repo';
globalThis.sprintLabelDisplay = (s) => String(s);
globalThis.escHtml = (s) => String(s);
globalThis._smgmtShowToast = (msg, level) => toastCalls.push({ msg, level });
globalThis.loadSprintMgmt = () => Promise.resolve();
globalThis._smgmtApplyRerunOptimistic = _noop;
globalThis.smgmtRunSprint = _noop;
globalThis.renderProgressActivity = () => '';
globalThis.window = globalThis;

// Seed modal state globals (normally done by state.js)
globalThis._rrLabel = null;
globalThis._rrVersionedLabel = null;

// ── Import module under test ──────────────────────────────────────────────────

_installDom([]);  // install a basic document before import to avoid null errors

import {
  _rrConfirm,
  smgmtRerunSprint,
} from '../../apps/dashboard/static/src/sprint-board/rerun-modal.js';


// ── Helpers ───────────────────────────────────────────────────────────────────

function _installFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, opts) => {
    const entry = { url: String(url), opts };
    calls.push(entry);
    return handler(url, opts, entry);
  };
  return calls;
}

function _ok(body) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function _err(status, detail) {
  const body = JSON.stringify({ detail });
  return {
    ok: false,
    status,
    json: async () => ({ detail }),
    text: async () => body,
  };
}


// ── AC3 / AC2 tests ───────────────────────────────────────────────────────────

test('_rrConfirm: sends POST with valid body (ticket_numbers array + auto_run)', async () => {
  _installDom([42, 99]);
  globalThis._rrLabel = 'sprint-106';
  globalThis._rrVersionedLabel = 'sprint-106.1';

  const fetchCalls = _installFetch(() => _ok({
    sub_label: 'sprint-106.1',
    noop: false,
    errors: [],
  }));

  await _rrConfirm();

  assert.equal(fetchCalls.length, 1, 'exactly one fetch call');
  const call = fetchCalls[0];
  assert.ok(call.url.includes('/api/sprints/sprint-106/rerun'), 'URL targets rerun endpoint');
  assert.ok(call.url.includes('project='), 'URL has project param');

  const sentBody = JSON.parse(call.opts.body);
  assert.ok(Array.isArray(sentBody.ticket_numbers), 'ticket_numbers must be an array');
  assert.deepEqual(sentBody.ticket_numbers, [42, 99], 'ticket_numbers contains checked issues');
  assert.ok('auto_run' in sentBody, 'body must include auto_run field');
  assert.equal(call.opts.headers['Content-Type'], 'application/json', 'Content-Type header set');
});

test('_rrConfirm: shows error toast on 422 (not swallowing)', async () => {
  _installDom([42]);
  globalThis._rrLabel = 'sprint-106';
  globalThis._rrVersionedLabel = 'sprint-106.1';
  toastCalls.length = 0;

  _installFetch(() => _err(422, 'body field required'));

  await _rrConfirm();

  const errorToasts = toastCalls.filter(t => t.msg.includes('body field required') || t.msg.includes('Re-run failed'));
  assert.ok(errorToasts.length > 0, 'error toast must fire on 422');
  assert.ok(errorToasts.some(t => (t.level || '').includes('error')), 'toast level must indicate error');
});

test('_rrConfirm: shows toast with server detail from 422 response body', async () => {
  _installDom([7]);
  globalThis._rrLabel = 'sprint-5';
  globalThis._rrVersionedLabel = 'sprint-5.1';
  toastCalls.length = 0;

  _installFetch(() => _err(422, 'Missing required field: ticket_numbers'));

  await _rrConfirm();

  const allMsgs = toastCalls.map(t => t.msg).join(' ');
  assert.ok(allMsgs.includes('Missing required field'), 'toast must include server detail from 422 body');
});

test('smgmtRerunSprint: shows warning toast when repo is null', async () => {
  const origRepo = globalThis._smgmtRepo;
  globalThis._smgmtRepo = () => null;
  toastCalls.length = 0;

  // Install DOM so the function can proceed if it doesn't short-circuit
  _installDom([]);
  _installFetch(() => _ok({}));

  await smgmtRerunSprint('sprint-99');

  assert.ok(toastCalls.length > 0, 'must show toast when repo is null');
  const warn = toastCalls.find(t => t.level === 'warning' || t.msg.toLowerCase().includes('no project'));
  assert.ok(warn, 'toast must be a warning about no project loaded');

  globalThis._smgmtRepo = origRepo;
});

test('_rrConfirm: returns early (no fetch) when no tickets are checked', async () => {
  _installDom([]);  // no checkboxes
  globalThis._rrLabel = 'sprint-106';

  const fetchCalls = _installFetch(() => _ok({}));

  await _rrConfirm();

  assert.equal(fetchCalls.length, 0, 'no fetch when ticket list is empty');
});
