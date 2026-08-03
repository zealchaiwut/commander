/**
 * Behavioral tests for issue #2073: Failures table mobile column visibility.
 *
 * AC5: Generated markup must contain Agent and Reason as table header columns
 * so that the CSS hiding only removes Sprint and Time, not the diagnostic fields.
 *
 * Columns: Issue | Sprint | Agent | Category | Reason | Time | Log
 *           col1    col2    col3    col4       col5    col6   col7
 *
 * The mobile CSS hides col2 (Sprint) and col6 (Time). Agent (col3) and
 * Reason (col5) must survive at ≤600px.
 *
 * Run with: node --test tests/frontend/failures-mobile-cols-2073.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── DOM and globals stubs ────────────────────────────────────────────────────

let _fboxRootHtml = '';

globalThis.document = {
  getElementById: (id) => {
    if (id === 'fbox-root') {
      return {
        get innerHTML() { return _fboxRootHtml; },
        set innerHTML(html) { _fboxRootHtml = html; },
      };
    }
    if (id === 'fbox-cat-select') return { value: '' };
    return null;
  },
  querySelector: () => null,
  addEventListener: () => {},
  removeEventListener: () => {},
  querySelectorAll: () => [],
};

if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

globalThis.history = { pushState: () => {} };
globalThis._projectData = { repo: 'owner/test-repo' };

import { failuresInit } from '../../apps/dashboard/static/src/failures/failures.js';

function _installFetchSpy(rows) {
  globalThis.fetch = async () => ({ ok: true, json: async () => rows });
}

function _waitForRender() {
  return new Promise(resolve => setTimeout(resolve, 20));
}

function _makeRow(overrides = {}) {
  return {
    issue_number: 42,
    sprint_label: 'sprint-99',
    agent: 'coder',
    category: 'timed_out',
    reason: 'Exceeded 30-minute budget',
    ts: '2026-08-01T10:00:00Z',
    log_url: 'https://example.com/log',
    ...overrides,
  };
}

// ── AC5: Diagnostic columns present in generated markup ──────────────────────

test('AC5 #2073: generated table header contains Agent column (col 3)', async () => {
  _fboxRootHtml = '';
  globalThis._projectData = { repo: 'owner/test-repo' };
  _installFetchSpy([_makeRow()]);

  failuresInit();
  await _waitForRender();

  assert.ok(
    _fboxRootHtml.includes('<th>Agent</th>'),
    `Table header must contain <th>Agent</th>: ${_fboxRootHtml.slice(0, 200)}`
  );
});

test('AC5 #2073: generated table header contains Reason column (col 5)', async () => {
  _fboxRootHtml = '';
  globalThis._projectData = { repo: 'owner/test-repo' };
  _installFetchSpy([_makeRow()]);

  failuresInit();
  await _waitForRender();

  assert.ok(
    _fboxRootHtml.includes('<th>Reason</th>'),
    `Table header must contain <th>Reason</th>: ${_fboxRootHtml.slice(0, 200)}`
  );
});

test('AC5 #2073: Agent value is rendered in table row (not hidden by markup)', async () => {
  _fboxRootHtml = '';
  globalThis._projectData = { repo: 'owner/test-repo' };
  _installFetchSpy([_makeRow({ agent: 'tester' })]);

  failuresInit();
  await _waitForRender();

  // Agent cell must appear in the row — it is col 3, not hidden by the markup
  assert.ok(
    _fboxRootHtml.includes('>tester<'),
    `Row must include the agent value "tester": ${_fboxRootHtml.slice(0, 300)}`
  );
});

test('AC5 #2073: Reason value is rendered in table row (not hidden by markup)', async () => {
  _fboxRootHtml = '';
  globalThis._projectData = { repo: 'owner/test-repo' };
  _installFetchSpy([_makeRow({ reason: 'Gate failed on lint' })]);

  failuresInit();
  await _waitForRender();

  assert.ok(
    _fboxRootHtml.includes('Gate failed on lint'),
    `Row must include the reason text: ${_fboxRootHtml.slice(0, 300)}`
  );
});

test('AC3 #2073: .fbox-table-wrap scroll container is preserved in generated markup', async () => {
  _fboxRootHtml = '';
  globalThis._projectData = { repo: 'owner/test-repo' };
  _installFetchSpy([_makeRow()]);

  failuresInit();
  await _waitForRender();

  assert.ok(
    _fboxRootHtml.includes('fbox-table-wrap'),
    `.fbox-table-wrap horizontal scroll container must be present: ${_fboxRootHtml.slice(0, 200)}`
  );
});

test('AC5 #2073: Sprint column header is present (it is hidden via CSS, not markup)', async () => {
  _fboxRootHtml = '';
  globalThis._projectData = { repo: 'owner/test-repo' };
  _installFetchSpy([_makeRow()]);

  failuresInit();
  await _waitForRender();

  // Sprint is hidden via CSS @media (max-width:600px) — it MUST remain in markup
  // so the table collapses gracefully via CSS, not by missing a DOM node.
  assert.ok(
    _fboxRootHtml.includes('<th>Sprint</th>'),
    `<th>Sprint</th> must be present in markup (hidden via CSS only): ${_fboxRootHtml.slice(0, 200)}`
  );
});

test('AC5 #2073: column order — Agent is col 3 and Reason is col 5', async () => {
  _fboxRootHtml = '';
  globalThis._projectData = { repo: 'owner/test-repo' };
  _installFetchSpy([_makeRow()]);

  failuresInit();
  await _waitForRender();

  // Extract the header row
  const theadMatch = _fboxRootHtml.match(/<thead><tr>(.*?)<\/tr><\/thead>/);
  assert.ok(theadMatch, `Must find <thead> in rendered markup`);

  const headers = [...theadMatch[1].matchAll(/<th>(.*?)<\/th>/g)].map(m => m[1]);
  // Expected: Issue, Sprint, Agent, Category, Reason, Time, Log
  assert.equal(headers[2], 'Agent', `col 3 (index 2) must be "Agent", got "${headers[2]}"`);
  assert.equal(headers[4], 'Reason', `col 5 (index 4) must be "Reason", got "${headers[4]}"`);
  assert.equal(headers[1], 'Sprint', `col 2 (index 1) must be "Sprint", got "${headers[1]}"`);
  assert.equal(headers[5], 'Time', `col 6 (index 5) must be "Time", got "${headers[5]}"`);
});
