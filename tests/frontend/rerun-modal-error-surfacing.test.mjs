/**
 * Behavioral tests for issue #1754: "Re-run -> N.1" board button silently
 * no-ops while the API works fine.
 *
 * Root cause: smgmtRerunSprint() ran several unguarded DOM/helper calls
 * (getElementById(...).textContent=, _rrShowPreviewLoading -> renderProgressActivity)
 * BEFORE calling _rrOpen() to actually show the modal, with no try/catch around
 * that setup. Any exception in that window (e.g. renderProgressActivity throwing)
 * aborted the whole handler silently — the modal never appeared, no toast, no
 * error — exactly the reported symptom ("clicks land on the element, nothing
 * happens").
 *
 * These tests drive the real exported smgmtRerunSprint() with a DOM/fetch double
 * and assert the modal becomes visible and an error is surfaced whenever setup
 * or the network call throws — a source-regex check would not have caught this
 * (issue #1746).
 *
 * Run with: node --test tests/frontend/rerun-modal-error-surfacing.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Fake DOM ──────────────────────────────────────────────────────────────
function makeEl(id) {
  const classes = new Set();
  return {
    id,
    textContent: '',
    innerHTML: '',
    disabled: false,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
  };
}

const _elIds = [
  'rr-modal-title', 'rr-loading', 'rr-content', 'rr-error', 'rr-confirm-btn',
  'rr-ticket-list', 'rr-backdrop', 'rr-modal', 'rr-uat-warning',
];

let _elements;
function resetDom() {
  _elements = new Map(_elIds.map((id) => [id, makeEl(id)]));
}
resetDom();

globalThis.document = {
  getElementById: (id) => _elements.get(id) || null,
  querySelectorAll: () => [],
};
globalThis.window = globalThis;

// ── Stubs for globals referenced in rerun-modal.js ────────────────────────
let _toastMessages;
globalThis._setBodyInert = () => {};
globalThis._clearBodyInert = () => {};
globalThis._smgmtRepo = () => 'owner/repo';
globalThis.sprintLabelDisplay = (l) => `Sprint ${l.replace('sprint-', '')}`;
globalThis.escHtml = (s) => s;
globalThis._smgmtShowToast = (msg) => { _toastMessages.push(msg); };
globalThis.loadSprintMgmt = async () => {};
globalThis._smgmtApplyRerunOptimistic = () => {};
globalThis.smgmtRunSprint = () => {};
globalThis.renderProgressActivity = () => '';
// Modal-local state normally seeded by state.js before rerun-modal.js runs.
globalThis._rrLabel = null;
globalThis._rrVersionedLabel = null;

import { smgmtRerunSprint } from '../../apps/dashboard/static/src/sprint-board/rerun-modal.js';

function modalIsOpen() {
  return !_elements.get('rr-backdrop').classList.contains('hidden')
    && !_elements.get('rr-modal').classList.contains('hidden');
}

function setUp({ renderThrows = false, fetch: fetchImpl } = {}) {
  resetDom();
  // Modal starts hidden, same as the real page's initial static markup.
  _elements.get('rr-backdrop').classList.add('hidden');
  _elements.get('rr-modal').classList.add('hidden');
  _toastMessages = [];
  globalThis.renderProgressActivity = renderThrows
    ? () => { throw new Error('boom in renderProgressActivity'); }
    : () => '';
  globalThis.fetch = fetchImpl;
}

// ── AC1: modal must open even when pre-fetch setup throws ────────────────

test('smgmtRerunSprint: modal opens even if renderProgressActivity throws during setup', async () => {
  setUp({
    renderThrows: true,
    fetch: async () => ({ ok: true, json: async () => ({ tickets: [], suggested_versioned_label: 'sprint-106.1' }) }),
  });

  await smgmtRerunSprint('sprint-106');

  assert.equal(modalIsOpen(), true, 'modal must be visible even when setup threw');
});

// ── AC2: any failure surfaces a visible error, never a silent no-op ───────

test('smgmtRerunSprint: setup exception surfaces a visible error (not swallowed)', async () => {
  setUp({
    renderThrows: true,
    fetch: async () => ({ ok: true, json: async () => ({ tickets: [], suggested_versioned_label: 'sprint-106.1' }) }),
  });

  await smgmtRerunSprint('sprint-106');

  const errEl = _elements.get('rr-error');
  assert.equal(errEl.classList.contains('hidden'), false, 'error element must be shown');
  assert.ok(errEl.textContent.length > 0, 'error element must contain a message');
  assert.ok(_toastMessages.length > 0, 'a toast must also be shown (AC2)');
});

test('smgmtRerunSprint: preview fetch 422 surfaces the server detail, modal stays open', async () => {
  setUp({
    fetch: async () => ({ ok: false, status: 422, text: async () => 'ticket_numbers required' }),
  });

  await smgmtRerunSprint('sprint-106');

  assert.equal(modalIsOpen(), true, 'modal must remain open on a fetch error');
  const errEl = _elements.get('rr-error');
  assert.equal(errEl.classList.contains('hidden'), false);
  assert.match(errEl.textContent, /ticket_numbers required/);
});

test('smgmtRerunSprint: network rejection surfaces an error, modal stays open', async () => {
  setUp({
    fetch: async () => { throw new Error('Failed to fetch'); },
  });

  await smgmtRerunSprint('sprint-106');

  assert.equal(modalIsOpen(), true);
  const errEl = _elements.get('rr-error');
  assert.equal(errEl.classList.contains('hidden'), false);
  assert.match(errEl.textContent, /Failed to fetch/);
});

// ── Happy path unaffected ──────────────────────────────────────────────────

test('smgmtRerunSprint: happy path still opens modal and populates ticket list', async () => {
  setUp({
    fetch: async () => ({
      ok: true,
      json: async () => ({
        suggested_versioned_label: 'sprint-106.1',
        tickets: [{ number: 42, title: 'Fix thing', category: 'UAT', checked: true }],
      }),
    }),
  });

  await smgmtRerunSprint('sprint-106');

  assert.equal(modalIsOpen(), true);
  assert.equal(_elements.get('rr-error').classList.contains('hidden'), true);
  assert.match(_elements.get('rr-ticket-list').innerHTML, /#42/);
});
