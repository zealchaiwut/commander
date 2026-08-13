/**
 * Behavioral tests for issue #2071: badge and error-handling fixes.
 *
 * AC1: Badge reflects report.generated_at, not poll time. No update on fail.
 * AC2: Badge label is "generated · HH:MM", not "live · HH:MM".
 * AC3: A failed refresh preserves existing content and shows a dismissible banner.
 * AC5: These are the behavioral tests required by CLAUDE.md #1746.
 *
 * Run with: node --test tests/frontend/devreport-badge-2071.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = fileURLToPath(new URL('.', import.meta.url));
const HOME_HTML = resolve(__dir, '../../apps/dashboard/static/home.html');
const html = readFileSync(HOME_HTML, 'utf-8');

/** Extract a named function from JS source by brace-depth counting. */
function extractFunction(source, fnName) {
  const sig = `function ${fnName}(`;
  const start = source.indexOf(sig);
  if (start === -1) return null;
  const prefixStart = source.lastIndexOf('\n', start) + 1;
  const braceOpen = source.indexOf('{', start);
  if (braceOpen === -1) return null;
  let depth = 0;
  let i = braceOpen;
  while (i < source.length) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(prefixStart, i + 1);
    }
    i++;
  }
  return null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeMockRoot(existingContent) {
  const el = {
    _innerHTML: existingContent || '',
    _innerHTMLSet: false,
    get innerHTML() { return this._innerHTML; },
    set innerHTML(v) {
      this._innerHTML = v;
      this._innerHTMLSet = true;
    },
    get children() { return { length: existingContent ? 1 : 0 }; },
    firstChild: null,
    insertBefore() {},
  };
  return el;
}

function makeMockDocument(rootEl) {
  const elements = {
    home: rootEl,
    'date-pick': null,
    'today-btn': null,
    'top-nav-label': null,
    'dr-fetch-error': null,
    'dr-live-ts': null,
  };
  return {
    getElementById(id) { return elements[id] || null; },
    createElement() {
      return { id: '', className: '', innerHTML: '', remove() {} };
    },
  };
}

/**
 * Build a factory function that returns a bound loadReport with all
 * dependencies injected. Extracts loadReport + helpers from home.html.
 */
function makeLoadReportFactory() {
  const loadReportFn = extractFunction(html, 'loadReport');
  const showFetchErrorFn = extractFunction(html, '_showFetchError');
  const dismissFetchErrorFn = extractFunction(html, '_dismissFetchError');

  if (!loadReportFn) throw new Error('loadReport not found in home.html');
  if (!showFetchErrorFn) throw new Error('_showFetchError not found in home.html');
  if (!dismissFetchErrorFn) throw new Error('_dismissFetchError not found in home.html');

  return new Function(
    'fetch', 'document', 'esc', 'todayIso',
    '_renderEmpty', '_renderReport', '_buildProjBySlug',
    `
    var _projBySlug = {};
    var _homeLoaded = false;
    ${showFetchErrorFn}
    ${dismissFetchErrorFn}
    ${loadReportFn}
    return loadReport;
    `,
  );
}

const mockEsc = (s) => String(s ?? '');
const mockTodayIso = () => '2026-08-01';
const mockRenderEmpty = () => '<div class="empty-note">No report yet</div>';
const mockRenderReport = () => '<div class="report-head">report html</div>';
const mockBuildProjBySlug = () => ({});

// ── _updateLiveBadge tests ─────────────────────────────────────────────────

test('AC1/AC5: _updateLiveBadge with null → badge text unchanged', () => {
  const fn = extractFunction(html, '_updateLiveBadge');
  assert.ok(fn, '_updateLiveBadge must be defined in home.html');

  let badgeText = 'original-value';
  const el = {
    get textContent() { return badgeText; },
    set textContent(v) { badgeText = v; },
  };
  const doc = { getElementById: (id) => (id === 'dr-live-ts' ? el : null) };
  const runner = new Function('document', `${fn}\n return _updateLiveBadge(null);`);
  runner(doc);

  assert.equal(badgeText, 'original-value', 'badge must not change when null is passed');
});

test('AC1/AC5: _updateLiveBadge with undefined → badge text unchanged', () => {
  const fn = extractFunction(html, '_updateLiveBadge');
  assert.ok(fn, '_updateLiveBadge must be defined in home.html');

  let badgeText = 'original-value';
  const el = {
    get textContent() { return badgeText; },
    set textContent(v) { badgeText = v; },
  };
  const doc = { getElementById: (id) => (id === 'dr-live-ts' ? el : null) };
  const runner = new Function('document', `${fn}\n return _updateLiveBadge(undefined);`);
  runner(doc);

  assert.equal(badgeText, 'original-value', 'badge must not change when undefined is passed');
});

test('AC1/AC2/AC5: _updateLiveBadge with ISO timestamp → badge contains "generated"', () => {
  const fn = extractFunction(html, '_updateLiveBadge');
  assert.ok(fn, '_updateLiveBadge must be defined in home.html');

  let badgeText = '';
  const el = {
    get textContent() { return badgeText; },
    set textContent(v) { badgeText = v; },
  };
  const doc = { getElementById: (id) => (id === 'dr-live-ts' ? el : null) };
  const runner = new Function('document', `${fn}\n return _updateLiveBadge("2026-07-31T05:45:00Z");`);
  runner(doc);

  assert.ok(badgeText.length > 0, 'badge must be updated with a valid ISO timestamp');
  assert.ok(
    badgeText.includes('generated'),
    `badge must contain "generated", got: "${badgeText}"`,
  );
});

test('AC2/AC5: _updateLiveBadge must NOT produce a "live ·" label', () => {
  const fn = extractFunction(html, '_updateLiveBadge');
  assert.ok(fn, '_updateLiveBadge must be defined in home.html');

  let badgeText = '';
  const el = {
    get textContent() { return badgeText; },
    set textContent(v) { badgeText = v; },
  };
  const doc = { getElementById: (id) => (id === 'dr-live-ts' ? el : null) };
  const runner = new Function('document', `${fn}\n return _updateLiveBadge("2026-07-31T05:45:00Z");`);
  runner(doc);

  assert.ok(
    !badgeText.toLowerCase().startsWith('live'),
    `badge must not start with "live", got: "${badgeText}"`,
  );
});

// ── loadReport error-handling tests ───────────────────────────────────────────

test('AC3/AC5: error after first successful load → root.innerHTML NOT overwritten', async () => {
  const factory = makeLoadReportFactory();
  const root = makeMockRoot('');
  const doc = makeMockDocument(root);

  let callCount = 0;
  const mockFetch = async () => {
    callCount++;
    if (callCount === 1) {
      return { status: 200, ok: true, json: async () => ({ generated_at: '2026-07-31T05:45:00Z', projects: [], stats: {} }) };
    }
    return { status: 500, ok: false };
  };

  const loadReport = factory(mockFetch, doc, mockEsc, mockTodayIso, mockRenderEmpty, mockRenderReport, mockBuildProjBySlug);

  // First: successful load sets _homeLoaded = true
  await loadReport();

  // Reset tracking after initial load
  root._innerHTMLSet = false;

  // Second call: error — must NOT blank the page
  await loadReport();

  assert.equal(root._innerHTMLSet, false,
    'root.innerHTML must not be overwritten on error after a previous successful load');
});

test('AC3/AC5: network error after first successful load → root.innerHTML NOT overwritten', async () => {
  const factory = makeLoadReportFactory();
  const root = makeMockRoot('');
  const doc = makeMockDocument(root);

  let callCount = 0;
  const mockFetch = async () => {
    callCount++;
    if (callCount === 1) {
      return { status: 200, ok: true, json: async () => ({ generated_at: '2026-07-31T05:45:00Z', projects: [], stats: {} }) };
    }
    throw new Error('network failure');
  };

  const loadReport = factory(mockFetch, doc, mockEsc, mockTodayIso, mockRenderEmpty, mockRenderReport, mockBuildProjBySlug);

  await loadReport();
  root._innerHTMLSet = false;

  await loadReport();

  assert.equal(root._innerHTMLSet, false,
    'root.innerHTML must not be overwritten on network error after a previous successful load');
});

test('AC3/AC5: initial fetch failure (no prior load) → error state shown via root.innerHTML', async () => {
  const factory = makeLoadReportFactory();
  const root = makeMockRoot('');
  const doc = makeMockDocument(root);

  const mockFetch = async () => ({ status: 500, ok: false });
  const loadReport = factory(mockFetch, doc, mockEsc, mockTodayIso, mockRenderEmpty, mockRenderReport, mockBuildProjBySlug);

  await loadReport();

  assert.equal(root._innerHTMLSet, true,
    'root.innerHTML must be set with error state on initial fetch failure (no prior successful load)');
  assert.ok(root._innerHTML.includes('empty-note'),
    'must render empty-note content on initial failure');
});

test('AC1/AC5: loadReport success → returns generated_at string', async () => {
  const factory = makeLoadReportFactory();
  const root = makeMockRoot('');
  const doc = makeMockDocument(root);

  const mockFetch = async () => ({
    status: 200,
    ok: true,
    json: async () => ({
      generated_at: '2026-07-31T05:45:00Z',
      projects: [],
      stats: {},
    }),
  });

  const loadReport = factory(mockFetch, doc, mockEsc, mockTodayIso, mockRenderEmpty, mockRenderReport, mockBuildProjBySlug);
  const result = await loadReport();

  assert.equal(result, '2026-07-31T05:45:00Z',
    'loadReport must return data.generated_at on success');
});

test('AC1/AC5: loadReport failure → returns null', async () => {
  const factory = makeLoadReportFactory();
  const root = makeMockRoot('');
  const doc = makeMockDocument(root);

  const mockFetch = async () => ({ status: 500, ok: false });
  const loadReport = factory(mockFetch, doc, mockEsc, mockTodayIso, mockRenderEmpty, mockRenderReport, mockBuildProjBySlug);

  const result = await loadReport();

  assert.equal(result, null, 'loadReport must return null on failure');
});

test('AC1/AC5: loadReport 404 → returns null', async () => {
  const factory = makeLoadReportFactory();
  const root = makeMockRoot('');
  const doc = makeMockDocument(root);

  const mockFetch = async () => ({ status: 404, ok: false });
  const loadReport = factory(mockFetch, doc, mockEsc, mockTodayIso, mockRenderEmpty, mockRenderReport, mockBuildProjBySlug);

  const result = await loadReport();

  assert.equal(result, null, 'loadReport must return null on 404');
});
