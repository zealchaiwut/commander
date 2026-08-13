/**
 * Behavioral tests for issue #2290:
 * Demote the Dev Report to its own route — /report page behavior.
 *
 * AC6a: /report page renders content when /api/dev-report returns 200 with data
 * AC6b: /report page shows a clean empty state when /api/dev-report returns 404
 *
 * Pattern: VM sandbox with fetch spy — not source-regex checks (issue #1746).
 * Run with: node --test tests/frontend/home-devreport-2290.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';

const __dir = dirname(fileURLToPath(import.meta.url));
const REPORT_HTML_PATH = resolve(__dir, '../../apps/dashboard/static/report.html');

function extractInlineScripts(html) {
  const re = /<script(?![^>]*\bsrc\b)[^>]*>([\s\S]*?)<\/script>/gi;
  const parts = [];
  let m;
  while ((m = re.exec(html)) !== null) parts.push(m[1]);
  return parts.join('\n');
}

function makeEl(id) {
  let _innerHTML = '';
  let _textContent = '';
  const el = {
    get innerHTML() { return _innerHTML; },
    set innerHTML(v) { _innerHTML = String(v); },
    get textContent() { return _textContent; },
    set textContent(v) { _textContent = String(v); },
    id: id || '',
    value: '',
    max: '',
    disabled: false,
    style: {},
    getAttribute: () => null,
    setAttribute: () => {},
    addEventListener: () => {},
    classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
    querySelector: () => makeEl(),
    querySelectorAll: () => [],
    insertAdjacentHTML: () => {},
    appendChild: () => {},
    remove: () => {},
    firstChild: null,
  };
  return el;
}

function fakeReportData() {
  return {
    for_date: '2026-08-13',
    generated_at: '2026-08-13T03:00:00Z',
    window_start: '2026-08-12T21:00:00Z',
    window_end: '2026-08-13T03:00:00Z',
    projects: [],
    cost: '$0.12',
    cost_source: 'token_usage',
    completed: ['Fix login bug', 'Add export feature'],
    needs_review: ['Sprint dashboard lag'],
    dead_letter: [],
  };
}

/**
 * Run the report.html scripts in a VM sandbox.
 * Returns { reportRootEl, fetchCalls }.
 */
async function runReportPage(fetchOverride) {
  const html = readFileSync(REPORT_HTML_PATH, 'utf-8');
  const code = extractInlineScripts(html);

  const reportRootEl = makeEl('report-root');
  const fetchCalls = [];
  let domContentLoadedHandler = null;

  const ctx = {
    fetch: async (url) => {
      const u = String(url);
      fetchCalls.push(u);
      return fetchOverride(u);
    },

    document: {
      documentElement: makeEl(),
      addEventListener: (event, handler) => {
        if (event === 'DOMContentLoaded') domContentLoadedHandler = handler;
      },
      getElementById: (id) => {
        if (id === 'report-root') return reportRootEl;
        return makeEl(id);
      },
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: () => makeEl(),
    },

    location: { href: 'http://localhost/report', search: '' },
    URL: class {
      constructor() {
        this.searchParams = {
          get: () => null,
          toString: () => '',
          set: () => {},
          delete: () => {},
        };
      }
    },
    URLSearchParams: class {
      constructor(s) { this._s = s || ''; }
      toString() { return this._s; }
      get() { return null; }
      set() {}
    },

    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,

    encodeURIComponent,
    decodeURIComponent,
    Promise,
    Boolean, Array, Object, String, Number, Set, Math, Date, JSON,
    console: { log() {}, warn() {}, error() {}, info() {} },
  };
  ctx.window = ctx;

  vm.createContext(ctx);

  try {
    vm.runInContext(code, ctx, { timeout: 5000 });
  } catch (_) {}

  // Trigger DOMContentLoaded which calls _loadReport()
  if (domContentLoadedHandler) {
    await domContentLoadedHandler();
  }
  await new Promise(r => setTimeout(r, 50));

  return { reportRootEl, fetchCalls };
}

// ── AC6a: /report renders content when /api/dev-report returns 200 ────────────

test('AC6a: report page fetches /api/dev-report on load', async () => {
  const { fetchCalls } = await runReportPage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: true, json: async () => fakeReportData() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const devReportCalls = fetchCalls.filter(u => u.includes('/api/dev-report'));
  assert.ok(devReportCalls.length >= 1, `report page must fetch /api/dev-report, got: ${JSON.stringify(fetchCalls)}`);
});

test('AC6a: report page renders completed items when /api/dev-report returns 200', async () => {
  const { reportRootEl } = await runReportPage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: true, json: async () => fakeReportData() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = reportRootEl.innerHTML;
  assert.ok(html.includes('Fix login bug'), `rendered HTML must include "Fix login bug", got: ${html.slice(0, 300)}`);
  assert.ok(html.includes('Add export feature'), `rendered HTML must include "Add export feature", got: ${html.slice(0, 300)}`);
});

test('AC6a: report page renders needs-review items when /api/dev-report returns 200', async () => {
  const { reportRootEl } = await runReportPage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: true, json: async () => fakeReportData() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = reportRootEl.innerHTML;
  assert.ok(html.includes('Sprint dashboard lag'), `rendered HTML must include "Sprint dashboard lag", got: ${html.slice(0, 300)}`);
});

// ── AC6b: /report shows clean empty state on 404 ──────────────────────────────

test('AC6b: report page shows empty state when /api/dev-report returns 404', async () => {
  const { reportRootEl } = await runReportPage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = reportRootEl.innerHTML;
  assert.ok(
    html.includes('No report available') || html.includes('empty-state'),
    `empty state must be shown on 404, got: ${html.slice(0, 300)}`,
  );
});

test('AC6b: report page empty state does not show an error alert or blocking UI on 404', async () => {
  const { reportRootEl } = await runReportPage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = reportRootEl.innerHTML;
  // Must not contain blocking error UI patterns
  assert.ok(!html.includes('<alert'), `must not render an alert element on 404, got: ${html.slice(0, 300)}`);
  assert.ok(!html.includes('class="error"'), `must not have class="error" on 404, got: ${html.slice(0, 300)}`);
  // Must have some clean empty-state content
  assert.ok(html.length > 0, 'report root must have content (not be blank) on 404');
});
