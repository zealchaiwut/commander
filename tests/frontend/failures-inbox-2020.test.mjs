/**
 * Frontend unit tests for issue #2020: Failures inbox tab.
 *
 * Tests the pure exported functions that power the failures tab:
 *   - fetchFailures(project, category) — fetch boundary (exportable for fetch spy)
 *   - failuresInit() — init handler that reads _projectData and renders
 *   - failuresCategoryChange(value) — category filter handler
 *
 * AC2: failuresInit() calls /api/failures?project=<repo> (fetch spy)
 * AC3: failuresCategoryChange() adds &category=<cat> param (fetch spy)
 * AC4: Empty state on [], error state on reject (render helpers)
 * AC1: tabs.js dispatches to failuresInit on 'failures' tab
 *
 * Run with: node --test tests/frontend/failures-inbox-2020.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Module import with DOM stubs ───────────────────────────────────────────────

// Stub globals BEFORE importing any modules that reference them at load time
// This must happen before any imports

// Create a minimal DOM stub that failures.js and tabs.js expect
globalThis.document = {
  getElementById: (id) => {
    // Return stubs for the main elements failures.js uses
    if (id === 'fbox-root') return { innerHTML: '', classList: { toggle: () => {} } };
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

// Stub globals declared in /* global */ directives in failures.js
globalThis._projectData = null;

// Global stubs needed by tabs.js at import time
globalThis._slug = 'test-project';
globalThis._activeTab = 'sprint-mgmt';
globalThis._cachedFullRepo = {};
globalThis._ticketsLoaded = false;
globalThis._sprintMgmtLoaded = false;
globalThis._smgmtLivePollId = null;
globalThis._smgmtLogPollId = null;
globalThis._statusRefreshId = null;

import {
  fetchFailures,
  failuresInit,
  failuresCategoryChange,
} from '../../apps/dashboard/static/src/failures/failures.js';

// ── Fetch spy helper ─────────────────────────────────────────────────────────

/**
 * Install a fetch spy that records calls and returns canned responses.
 * @param {Map<string, any>} routes - Map of URL patterns to response bodies
 * @returns {Array} array of recorded fetch URLs
 */
function _installFetchSpy(routes) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    // Match by prefix; return the first matching route
    for (const [pattern, body] of routes) {
      if (String(url).startsWith(pattern)) {
        return {
          ok: true,
          json: async () => body,
        };
      }
    }
    // If no route matches, throw to fail the test
    throw new Error(`Unexpected fetch: ${url}`);
  };
  return calls;
}

/**
 * Install a fetch spy that rejects (for error-path testing).
 */
function _installFailingFetchSpy() {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    const err = new Error('Network error');
    err.status = 500;
    throw err;
  };
  return calls;
}

/**
 * Helper to wait for async operations to complete (for failuresInit which
 * uses then/catch instead of returning a promise).
 */
function _waitForRender() {
  return new Promise(resolve => setTimeout(resolve, 20));
}

// ── Test fixtures ─────────────────────────────────────────────────────────────

function _makeFakeRow(overrides = {}) {
  return {
    issue_number: 123,
    sprint_label: 'sprint-10',
    agent: 'tester',
    category: 'timed_out',
    reason: 'Test timeout after 30s',
    ts: '2026-07-30T10:00:00Z',
    log_url: 'https://example.com/log',
    ...overrides,
  };
}

// ── AC2: fetchFailures with project param ──────────────────────────────────

test('AC2: fetchFailures() calls /api/failures?project=<repo>', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([
      ['/api/failures', []],
    ])
  );

  await fetchFailures('owner/test-repo', null);

  const failuresCalls = fetchCalls.filter(u => u.includes('/api/failures'));
  assert.equal(failuresCalls.length, 1, 'must make exactly one /api/failures call');
  assert.ok(
    failuresCalls[0].includes('project=owner%2Ftest-repo'),
    `URL must include project param: ${failuresCalls[0]}`
  );
});

// ── AC3: failuresCategoryChange with category param ────────────────────────

test('AC3: fetchFailures() adds &category=<cat> when category is provided', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([
      ['/api/failures', []],
    ])
  );

  await fetchFailures('owner/test-repo', 'timed_out');

  const call = fetchCalls[0];
  assert.ok(
    call.includes('category=timed_out'),
    `URL must include category param: ${call}`
  );
  assert.ok(
    call.includes('project=owner%2Ftest-repo'),
    `URL must include project param: ${call}`
  );
});

test('AC3: fetchFailures() omits category when null', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([
      ['/api/failures', []],
    ])
  );

  await fetchFailures('owner/test-repo', null);

  const call = fetchCalls[0];
  assert.ok(
    !call.includes('category='),
    `URL must not include category param when null: ${call}`
  );
});

// ── AC4: Empty state on [] ──────────────────────────────────────────────────

test('AC4: failuresInit() renders empty state when API returns []', async () => {
  // Setup: stub DOM elements and _projectData
  let renderedHtml = '';
  const fboxRoot = {
    innerHTML: '',
    get innerHTML() { return renderedHtml; },
    set innerHTML(html) { renderedHtml = html; },
  };

  globalThis.document.getElementById = (id) => {
    if (id === 'fbox-root') return fboxRoot;
    return null;
  };

  globalThis._projectData = { repo: 'owner/test-repo' };

  _installFetchSpy(
    new Map([
      ['/api/failures', []],
    ])
  );

  // Call failuresInit (returns void but uses async then/catch)
  failuresInit();

  // Wait for async operations to complete
  await _waitForRender();

  // Assert: HTML contains "No failures" text (from empty state rendering)
  assert.ok(
    renderedHtml.includes('No failures'),
    `Rendered HTML must include "No failures" message: ${renderedHtml}`
  );
  assert.ok(
    renderedHtml.includes('fbox-table'),
    `Rendered HTML must include table structure`
  );
});

// ── AC4: Error state on fetch rejection ─────────────────────────────────────

test('AC4: failuresInit() renders error state when fetch rejects', async () => {
  // Setup: stub DOM elements and _projectData
  let renderedHtml = '';
  const fboxRoot = {
    innerHTML: '',
    get innerHTML() { return renderedHtml; },
    set innerHTML(html) { renderedHtml = html; },
  };

  globalThis.document.getElementById = (id) => {
    if (id === 'fbox-root') return fboxRoot;
    return null;
  };

  globalThis._projectData = { repo: 'owner/test-repo' };

  _installFailingFetchSpy();

  // Call failuresInit (returns void but uses async then/catch)
  failuresInit();

  // Wait for async operations to complete
  await _waitForRender();

  // Assert: HTML contains error message
  assert.ok(
    renderedHtml.includes('Failed to load failures'),
    `Rendered HTML must include error message: ${renderedHtml}`
  );
  assert.ok(
    renderedHtml.includes('fbox-state-error'),
    `Rendered HTML must include error state class`
  );
});

// ── AC4: Rows rendered on success ───────────────────────────────────────────

test('AC4: failuresInit() renders table rows when API returns data', async () => {
  const row1 = _makeFakeRow({ issue_number: 100 });
  const row2 = _makeFakeRow({ issue_number: 101 });

  let renderedHtml = '';
  const fboxRoot = {
    innerHTML: '',
    get innerHTML() { return renderedHtml; },
    set innerHTML(html) { renderedHtml = html; },
  };

  globalThis.document.getElementById = (id) => {
    if (id === 'fbox-root') return fboxRoot;
    return null;
  };

  globalThis._projectData = { repo: 'owner/test-repo' };

  _installFetchSpy(
    new Map([
      ['/api/failures', [row1, row2]],
    ])
  );

  failuresInit();

  // Wait for async operations to complete
  await _waitForRender();

  // Assert: HTML contains the issue numbers from the rows
  assert.ok(
    renderedHtml.includes('#100'),
    `Rendered HTML must include issue #100`
  );
  assert.ok(
    renderedHtml.includes('#101'),
    `Rendered HTML must include issue #101`
  );
  assert.ok(
    renderedHtml.includes('timed_out'),
    `Rendered HTML must include category`
  );
});

// ── failuresCategoryChange re-fetches with new category ────────────────────

test('failuresCategoryChange() re-fetches with the selected category', async () => {
  let renderedHtml = '';
  const fboxRoot = {
    innerHTML: '',
    get innerHTML() { return renderedHtml; },
    set innerHTML(html) { renderedHtml = html; },
  };

  globalThis.document.getElementById = (id) => {
    if (id === 'fbox-root') return fboxRoot;
    return null;
  };

  globalThis._projectData = { repo: 'owner/test-repo' };

  const fetchCalls = _installFetchSpy(
    new Map([
      ['/api/failures', [_makeFakeRow({ category: 'auth_failed' })]],
    ])
  );

  // Call failuresCategoryChange with a new category
  await failuresCategoryChange('auth_failed');

  // Assert: fetch was called with the new category
  const failureCalls = fetchCalls.filter(u => u.includes('/api/failures'));
  assert.ok(
    failureCalls.length > 0,
    'must make at least one fetch call'
  );
  assert.ok(
    failureCalls[failureCalls.length - 1].includes('category=auth_failed'),
    `Latest fetch must include category=auth_failed`
  );
});

// ── AC1: tabs.js dispatcher — verified by source inspection ────────────────
// tabs.js registers 'failures' in _topLevelTabs (line 94) and dispatches
// to failuresInit() on switchTab('failures') (line 210). Cannot test the
// dispatch in Node without a full browser context, but the pattern is verified
// by code inspection and the failures.js module export tests above ensure that
// failuresInit() works correctly when called.
//
// The tab registration in pages.py _VALID_PROJECT_TABS is verified by grep
// and code inspection.

test('AC1 [source verified]: failures tab is exported and callable', async () => {
  // Verify that failuresInit is a callable function
  assert.equal(typeof failuresInit, 'function', 'failuresInit must be exported');

  // Verify that failuresCategoryChange is a callable function
  assert.equal(typeof failuresCategoryChange, 'function', 'failuresCategoryChange must be exported');

  // These are the two functions called by tabs.js on failures tab dispatch
});

// ── failuresInit does not crash when _projectData is missing ────────────────

test('failuresInit() handles missing _projectData gracefully', async () => {
  let renderedHtml = '';
  const fboxRoot = {
    innerHTML: '',
    get innerHTML() { return renderedHtml; },
    set innerHTML(html) { renderedHtml = html; },
  };

  globalThis.document.getElementById = (id) => {
    if (id === 'fbox-root') return fboxRoot;
    return null;
  };

  globalThis._projectData = null;

  await failuresInit();

  // Assert: error message is shown
  assert.ok(
    renderedHtml.includes('No project selected'),
    `Rendered HTML must include "No project selected" message: ${renderedHtml}`
  );
});

// ── Edge cases ─────────────────────────────────────────────────────────────

test('fetchFailures() encodes special characters in project and category', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([
      ['/api/failures', []],
    ])
  );

  await fetchFailures('owner/repo&special', 'cat_with_&');

  const call = fetchCalls[0];
  // URL-encoded: & → %26, / → %2F
  assert.ok(
    call.includes('project=owner%2Frepo%26special'),
    `project param should be URL-encoded: ${call}`
  );
  assert.ok(
    call.includes('category=cat_with_%26'),
    `category param should be URL-encoded: ${call}`
  );
});

test('fetchFailures() returns the parsed JSON from the response', async () => {
  const rows = [
    _makeFakeRow({ issue_number: 200 }),
    _makeFakeRow({ issue_number: 201 }),
  ];

  _installFetchSpy(
    new Map([
      ['/api/failures', rows],
    ])
  );

  const result = await fetchFailures('owner/test-repo', null);

  assert.equal(result.length, 2, 'should return 2 rows');
  assert.equal(result[0].issue_number, 200);
  assert.equal(result[1].issue_number, 201);
});
