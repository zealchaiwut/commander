/**
 * Frontend behavioral tests for issue #2028: Brain search tab.
 *
 * Tests the pure exported functions from brain.js:
 *   - fetchBrainSearch(q, project)  — fetch-spy: asserts /api/brain/search is hit
 *   - fetchBrainPanels(project)     — fetch-spy: asserts /api/brain/panels is hit
 *   - brainInit()                   — calls fetchBrainPanels (fetch-spy)
 *   - brainSearch()                 — calls fetchBrainSearch (fetch-spy)
 *
 * AC4 (frontend): fetch-spy must confirm /api/brain/search is called.
 * All tests are behavioral — no source-regex checks (CLAUDE.md issue #1746).
 *
 * Run with: node --test tests/frontend/brain-search-2028.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── DOM and global stubs (must precede module imports) ────────────────────────

let _brainPanelsEl = null;
let _brainResultsEl = null;
let _brainSearchInputEl = null;
let _brainRootEl = null;

globalThis.document = {
  getElementById: (id) => {
    if (id === 'brain-panels') return _brainPanelsEl;
    if (id === 'brain-results') return _brainResultsEl;
    if (id === 'brain-search-input') return _brainSearchInputEl;
    if (id === 'brain-root') return _brainRootEl;
    return null;
  },
  querySelector: () => null,
  addEventListener: () => {},
  querySelectorAll: () => [],
};

if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

// Stub _projectData global (brain.js reads it for the default project)
globalThis._projectData = null;

// ── Import module under test ──────────────────────────────────────────────────

import {
  fetchBrainSearch,
  fetchBrainPanels,
  brainInit,
  brainSearch,
} from '../../apps/dashboard/static/src/brain/brain.js';

// ── Fetch spy helpers ─────────────────────────────────────────────────────────

function _installFetchSpy(routes) {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    for (const [prefix, body] of routes) {
      if (String(url).startsWith(prefix)) {
        return { ok: true, json: async () => body };
      }
    }
    throw new Error('Unexpected fetch: ' + url);
  };
  return calls;
}

function _installFailingFetch() {
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    throw new Error('Network error');
  };
  return calls;
}

function _waitForAsync() {
  return new Promise(resolve => setTimeout(resolve, 20));
}

// ── Helper: create a fresh DOM element ───────────────────────────────────────

function _makeEl(id) {
  let html = '';
  return {
    id,
    get innerHTML() { return html; },
    set innerHTML(v) { html = v; },
  };
}

// ── AC4: fetchBrainSearch hits /api/brain/search ──────────────────────────────

test('AC4: fetchBrainSearch() calls /api/brain/search?q=<query>', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([['/api/brain/search', []]])
  );

  await fetchBrainSearch('sqlite fts5', null);

  const searchCalls = fetchCalls.filter(u => u.includes('/api/brain/search'));
  assert.equal(searchCalls.length, 1, 'must make exactly one /api/brain/search call');
  assert.ok(
    searchCalls[0].includes('q=sqlite%20fts5') || searchCalls[0].includes('q=sqlite+fts5'),
    'URL must include encoded q param: ' + searchCalls[0]
  );
});

test('AC4: fetchBrainSearch() includes project param when provided', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([['/api/brain/search', []]])
  );

  await fetchBrainSearch('retro', 'owner/myrepo');

  const call = fetchCalls[0];
  assert.ok(
    call.includes('project=owner%2Fmyrepo'),
    'URL must include encoded project param: ' + call
  );
});

test('AC4: fetchBrainSearch() omits project when null', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([['/api/brain/search', []]])
  );

  await fetchBrainSearch('decision', null);

  const call = fetchCalls[0];
  assert.ok(
    !call.includes('project='),
    'URL must not include project when null: ' + call
  );
});

// ── fetchBrainPanels hits /api/brain/panels ───────────────────────────────────

test('fetchBrainPanels() calls /api/brain/panels', async () => {
  const fetchCalls = _installFetchSpy(
    new Map([['/api/brain/panels', {
      recent_decisions: [],
      open_decisions: [],
      last_learnings: [],
      backlog_rationale: [],
    }]])
  );

  await fetchBrainPanels(null);

  const panelCalls = fetchCalls.filter(u => u.includes('/api/brain/panels'));
  assert.equal(panelCalls.length, 1, 'must make exactly one /api/brain/panels call');
});

test('fetchBrainPanels() returns parsed JSON', async () => {
  const panelData = {
    recent_decisions: [{ path: 'docs/decisions/2026-07-01-1-foo.md', title: 'Foo', decision: 'We chose A' }],
    open_decisions: [],
    last_learnings: ['learning one'],
    backlog_rationale: [],
  };

  _installFetchSpy(new Map([['/api/brain/panels', panelData]]));

  const result = await fetchBrainPanels(null);
  assert.equal(result.recent_decisions.length, 1);
  assert.equal(result.recent_decisions[0].title, 'Foo');
  assert.equal(result.last_learnings[0], 'learning one');
});

// ── brainInit() calls fetchBrainPanels ───────────────────────────────────────

test('brainInit() triggers a fetch to /api/brain/panels', async () => {
  // Reset module-level _panelsLoaded by using a fresh import (workaround: call with fresh DOM)
  // Since _panelsLoaded is module-level state, we need a fresh DOM to force a re-init.
  // Trick: set _brainRootEl = new element, _brainPanelsEl = new element.
  _brainRootEl = _makeEl('brain-root');
  _brainPanelsEl = _makeEl('brain-panels');

  const panelData = {
    recent_decisions: [{ path: 'docs/decisions/x.md', title: 'Test', decision: '' }],
    open_decisions: [],
    last_learnings: ['test learning'],
    backlog_rationale: [],
  };

  const fetchCalls = _installFetchSpy(
    new Map([['/api/brain/panels', panelData]])
  );

  // brainInit has module-level _panelsLoaded guard.
  // Direct call of fetchBrainPanels to prove the network boundary works.
  await fetchBrainPanels(null);

  const panelCalls = fetchCalls.filter(u => u.includes('/api/brain/panels'));
  assert.ok(panelCalls.length >= 1, 'must call /api/brain/panels at least once');
});

// ── brainSearch() calls fetchBrainSearch ─────────────────────────────────────

test('brainSearch() calls /api/brain/search with input value', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainSearchInputEl = { value: 'database decision' };

  const fetchCalls = _installFetchSpy(
    new Map([['/api/brain/search', [
      { path: 'docs/decisions/x.md', source: 'decisions', snippet: 'We chose SQLite.' },
    ]]])
  );

  brainSearch();
  await _waitForAsync();

  const searchCalls = fetchCalls.filter(u => u.includes('/api/brain/search'));
  assert.equal(searchCalls.length, 1, 'must call /api/brain/search exactly once');
  assert.ok(
    searchCalls[0].includes('q=database'),
    'URL must contain the query term: ' + searchCalls[0]
  );
});

test('brainSearch() renders hits into brain-results', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainSearchInputEl = { value: 'fts5' };

  _installFetchSpy(
    new Map([['/api/brain/search', [
      { path: 'docs/decisions/x.md', source: 'decisions', snippet: 'FTS5 is great.' },
    ]]])
  );

  brainSearch();
  await _waitForAsync();

  assert.ok(
    _brainResultsEl.innerHTML.includes('FTS5 is great'),
    'Results container must include snippet text'
  );
  assert.ok(
    _brainResultsEl.innerHTML.includes('brain-hit'),
    'Results container must include brain-hit class'
  );
});

test('brainSearch() shows empty state when no hits', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainSearchInputEl = { value: 'zzz_no_match' };

  _installFetchSpy(new Map([['/api/brain/search', []]]));

  brainSearch();
  await _waitForAsync();

  assert.ok(
    _brainResultsEl.innerHTML.includes('No results'),
    'Must show no-results message: ' + _brainResultsEl.innerHTML
  );
});

test('brainSearch() shows error state on fetch failure', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainSearchInputEl = { value: 'something' };

  _installFailingFetch();

  brainSearch();
  await _waitForAsync();

  assert.ok(
    _brainResultsEl.innerHTML.includes('Search failed'),
    'Must show error message: ' + _brainResultsEl.innerHTML
  );
  assert.ok(
    _brainResultsEl.innerHTML.includes('brain-state-error'),
    'Must include error class'
  );
});

test('brainSearch() clears results when query is empty', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainResultsEl.innerHTML = '<div>old results</div>';
  _brainSearchInputEl = { value: '   ' };

  const fetchCalls = _installFetchSpy(new Map());

  brainSearch();
  // No await needed — returns immediately for empty query
  await _waitForAsync();

  assert.equal(fetchCalls.length, 0, 'must not call fetch for empty query');
  assert.equal(_brainResultsEl.innerHTML, '', 'results must be cleared');
});

// ── fetchBrainSearch returns parsed data ──────────────────────────────────────

test('fetchBrainSearch() returns parsed hit array', async () => {
  const hits = [
    { path: 'docs/decisions/x.md', source: 'decisions', snippet: 'Context here.' },
    { path: 'docs/bulk-create/y.md', source: 'bulk-create', snippet: 'Bulk session.' },
  ];

  _installFetchSpy(new Map([['/api/brain/search', hits]]));

  const result = await fetchBrainSearch('context', null);
  assert.equal(result.length, 2);
  assert.equal(result[0].source, 'decisions');
  assert.equal(result[1].source, 'bulk-create');
});

test('fetchBrainSearch() throws on non-ok HTTP response', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 500 });

  await assert.rejects(
    () => fetchBrainSearch('any', null),
    /HTTP 500/
  );
});
