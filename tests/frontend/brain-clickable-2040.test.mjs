/**
 * Frontend behavioral tests for issue #2040: Brain items clickable.
 *
 * Tests:
 *   - fetchBrainDoc(slug, path)   — fetch-spy: asserts /api/projects/{slug}/docs/{path} is hit
 *   - Rendered HTML carries data-doc-path attribute on hits and panel items
 *   - Activating an item (via openBrainDoc) triggers a fetch to the docs endpoint
 *
 * AC4 (frontend, CLAUDE.md issue #1746): fetch-spy confirms /api/projects/.../docs/...
 * is called with the expected path. No source-regex checks.
 *
 * Run with: node --test tests/frontend/brain-clickable-2040.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── DOM and global stubs (must precede module imports) ────────────────────────

let _brainDocViewerEl = null;
let _brainDocContentEl = null;
let _brainDocPathLabelEl = null;
let _brainResultsEl = null;
let _brainRootEl = null;
let _brainPanelsEl = null;
let _brainSearchInputEl = null;
let _brainRootDivEl = null;

function _makeEl(id) {
  let html = '';
  let txt = '';
  let style = {};
  return {
    id,
    get innerHTML() { return html; },
    set innerHTML(v) { html = v; },
    get textContent() { return txt; },
    set textContent(v) { txt = v; },
    style,
    closest: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
  };
}

globalThis.document = {
  getElementById: (id) => {
    if (id === 'brain-doc-viewer') return _brainDocViewerEl;
    if (id === 'brain-doc-content') return _brainDocContentEl;
    if (id === 'brain-doc-path-label') return _brainDocPathLabelEl;
    if (id === 'brain-results') return _brainResultsEl;
    if (id === 'brain-root') return _brainRootEl;
    if (id === 'brain-panels') return _brainPanelsEl;
    if (id === 'brain-search-input') return _brainSearchInputEl;
    return null;
  },
  querySelector: () => null,
  addEventListener: () => {},
  querySelectorAll: () => [],
};

if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

// Stub _projectData global with slug-bearing repo
globalThis._projectData = { repo: 'zealchaiwut/commander' };

// Stub _mdToHtml (project.html global)
globalThis._mdToHtml = (md) => `<p class="md-p">${md}</p>`;

// ── Import module under test ──────────────────────────────────────────────────

import {
  fetchBrainDoc,
  openBrainDoc,
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

function _waitForAsync() {
  return new Promise(resolve => setTimeout(resolve, 20));
}

// ── AC1: fetchBrainDoc calls /api/projects/{slug}/docs/{path} ─────────────────

test('AC4: fetchBrainDoc() calls /api/projects/slug/docs/path', async () => {
  const fetchCalls = _installFetchSpy([
    ['/api/projects/commander/docs/', { path: 'docs/decisions/x.md', content: '# Title' }],
  ]);

  await fetchBrainDoc('commander', 'docs/decisions/x.md');

  const docCalls = fetchCalls.filter(u => u.includes('/api/projects/commander/docs/'));
  assert.equal(docCalls.length, 1, 'must make exactly one /api/projects/commander/docs/ call');
  assert.ok(
    docCalls[0].includes('docs/decisions/x.md'),
    'URL must include the path: ' + docCalls[0]
  );
});

test('AC4: fetchBrainDoc() URL-encodes the slug', async () => {
  const fetchCalls = _installFetchSpy([
    ['/api/projects/', { path: 'docs/x.md', content: '' }],
  ]);

  await fetchBrainDoc('my project', 'docs/x.md');

  assert.ok(
    fetchCalls[0].includes('my%20project'),
    'slug must be URL-encoded: ' + fetchCalls[0]
  );
});

test('AC4: fetchBrainDoc() throws on non-ok HTTP response', async () => {
  globalThis.fetch = async () => ({ ok: false, status: 404 });

  await assert.rejects(
    () => fetchBrainDoc('commander', 'docs/missing.md'),
    /HTTP 404/
  );
});

test('AC4: fetchBrainDoc() returns parsed {path, content}', async () => {
  const body = { path: 'docs/decisions/2026-07-01-1-foo.md', content: '# Foo\nWe chose A.' };
  _installFetchSpy([['/api/projects/commander/docs/', body]]);

  const result = await fetchBrainDoc('commander', 'docs/decisions/2026-07-01-1-foo.md');
  assert.equal(result.path, body.path);
  assert.equal(result.content, body.content);
});

// ── AC1: rendered search hits carry data-doc-path ─────────────────────────────

test('AC1: brainSearch results carry data-doc-path attribute', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainSearchInputEl = { value: 'decision' };

  _installFetchSpy([
    ['/api/brain/search', [
      { path: 'docs/decisions/2026-07-01-1-foo.md', source: 'decisions', snippet: 'We chose SQLite.' },
    ]],
  ]);

  brainSearch();
  await _waitForAsync();

  assert.ok(
    _brainResultsEl.innerHTML.includes('data-doc-path'),
    'rendered hits must have data-doc-path attribute: ' + _brainResultsEl.innerHTML.slice(0, 300)
  );
  assert.ok(
    _brainResultsEl.innerHTML.includes('docs/decisions/2026-07-01-1-foo.md'),
    'data-doc-path must contain the hit path'
  );
});

test('AC3: rendered search hits carry tabindex="0"', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainSearchInputEl = { value: 'retro' };

  _installFetchSpy([
    ['/api/brain/search', [
      { path: 'docs/retros/2026-06-01-s47.md', source: 'retros', snippet: 'Learnt about X.' },
    ]],
  ]);

  brainSearch();
  await _waitForAsync();

  assert.ok(
    _brainResultsEl.innerHTML.includes('tabindex="0"'),
    'rendered hits must have tabindex="0" for keyboard access: ' + _brainResultsEl.innerHTML.slice(0, 300)
  );
});

test('AC3: rendered search hits carry role="button"', async () => {
  _brainResultsEl = _makeEl('brain-results');
  _brainSearchInputEl = { value: 'docs' };

  _installFetchSpy([
    ['/api/brain/search', [
      { path: 'docs/architecture/overview.md', source: 'docs', snippet: 'System overview.' },
    ]],
  ]);

  brainSearch();
  await _waitForAsync();

  assert.ok(
    _brainResultsEl.innerHTML.includes('role="button"'),
    'rendered hits must have role="button" for accessibility: ' + _brainResultsEl.innerHTML.slice(0, 300)
  );
});

// ── AC4 (activation): openBrainDoc calls /api/projects/{slug}/docs/{path} ──────

test('AC4: openBrainDoc() triggers fetch to /api/projects/slug/docs/path', async () => {
  _brainDocViewerEl = _makeEl('brain-doc-viewer');
  _brainDocContentEl = _makeEl('brain-doc-content');
  _brainDocPathLabelEl = _makeEl('brain-doc-path-label');
  _brainResultsEl = _makeEl('brain-results');
  _brainRootEl = _makeEl('brain-root');

  const docPath = 'docs/decisions/2026-07-01-1-foo.md';
  const fetchCalls = _installFetchSpy([
    ['/api/projects/commander/docs/', { path: docPath, content: '# Foo Decision\nChose A.' }],
  ]);

  openBrainDoc(docPath);
  await _waitForAsync();

  const docCalls = fetchCalls.filter(u => u.includes('/api/projects/commander/docs/'));
  assert.equal(docCalls.length, 1, 'must make exactly one docs fetch');
  assert.ok(
    docCalls[0].includes('docs/decisions/2026-07-01-1-foo.md'),
    'fetch URL must include the doc path: ' + docCalls[0]
  );
});

test('AC4: openBrainDoc() shows doc content after successful fetch', async () => {
  _brainDocViewerEl = _makeEl('brain-doc-viewer');
  _brainDocContentEl = _makeEl('brain-doc-content');
  _brainDocPathLabelEl = _makeEl('brain-doc-path-label');
  _brainResultsEl = _makeEl('brain-results');
  _brainRootEl = _makeEl('brain-root');

  _installFetchSpy([
    ['/api/projects/commander/docs/', { path: 'docs/x.md', content: '# Hello\nWorld.' }],
  ]);

  openBrainDoc('docs/x.md');
  await _waitForAsync();

  assert.ok(
    _brainDocContentEl.innerHTML.includes('Hello'),
    'content area must contain rendered markdown: ' + _brainDocContentEl.innerHTML.slice(0, 300)
  );
});

test('AC4: openBrainDoc() shows error state on fetch failure', async () => {
  _brainDocViewerEl = _makeEl('brain-doc-viewer');
  _brainDocContentEl = _makeEl('brain-doc-content');
  _brainDocPathLabelEl = _makeEl('brain-doc-path-label');
  _brainResultsEl = _makeEl('brain-results');
  _brainRootEl = _makeEl('brain-root');

  globalThis.fetch = async () => { throw new Error('Network error'); };

  openBrainDoc('docs/missing.md');
  await _waitForAsync();

  assert.ok(
    _brainDocContentEl.innerHTML.includes('brain-state-error') ||
    _brainDocContentEl.innerHTML.includes('Failed'),
    'must show error state on fetch failure: ' + _brainDocContentEl.innerHTML.slice(0, 200)
  );
});

test('AC4: openBrainDoc() shows error state when no project is available', async () => {
  _brainDocViewerEl = _makeEl('brain-doc-viewer');
  _brainDocContentEl = _makeEl('brain-doc-content');
  _brainDocPathLabelEl = _makeEl('brain-doc-path-label');
  _brainResultsEl = _makeEl('brain-results');
  _brainRootEl = _makeEl('brain-root');

  // Temporarily unset _projectData
  const orig = globalThis._projectData;
  globalThis._projectData = null;

  const fetchCalls = _installFetchSpy([]);
  openBrainDoc('docs/x.md');
  await _waitForAsync();

  assert.equal(fetchCalls.length, 0, 'must not fetch when no project is available');
  assert.ok(
    _brainDocContentEl.innerHTML.includes('brain-state-error'),
    'must show error state when no project: ' + _brainDocContentEl.innerHTML.slice(0, 200)
  );

  globalThis._projectData = orig;
});
