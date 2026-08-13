/**
 * Behavioral tests for issue #2290:
 * Demote the Dev Report to its own route so a missing digest cannot blank the home page.
 *
 * AC2: home page renders digest block when /api/dev-report returns 200; empty slot on 404
 * AC3: 404 / 500 / network error from /api/dev-report never blocks project navigation
 * AC5: with /api/dev-report stubbed to 404, home still renders project list, no error state
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
const HTML_PATH = resolve(__dir, '../../apps/dashboard/static/home.html');

function extractInlineScripts(html) {
  const re = /<script(?![^>]*\bsrc\b)[^>]*>([\s\S]*?)<\/script>/gi;
  const parts = [];
  let m;
  while ((m = re.exec(html)) !== null) parts.push(m[1]);
  return parts.join('\n');
}

function makeEl() {
  let _innerHTML = '';
  const el = {
    get innerHTML() { return _innerHTML; },
    set innerHTML(v) { _innerHTML = String(v); },
    textContent: '',
    children: { length: 0 },
    getAttribute: () => null,
    setAttribute: () => {},
    querySelector: () => ({
      textContent: '',
      classList: { add() {}, remove() {} },
      setAttribute() {},
    }),
    querySelectorAll: () => [],
    classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
    insertAdjacentHTML: () => {},
    insertBefore: () => {},
    remove: () => {},
    appendChild: () => {},
    firstChild: null,
    disabled: false,
    value: '',
    max: '',
    style: {},
    id: '',
  };
  return el;
}

function fakeHomeResponse() {
  return {
    stats: {
      sprint_running: { count: 1, projects: [] },
      awaiting_uat: { count: 2, projects: 1, oldest_age_sec: 3600 },
      backlog: { count: 5, per_project: [] },
      sprints_planned: { count: 1, total_tickets: 4 },
    },
    projects: [
      {
        slug: 'alpha',
        name: 'Alpha Project',
        repo: 'org/alpha',
        icon: 'ti-rocket',
        color: 'blue',
        status: 'running',
        uat_count: 2,
        backlog_count: 3,
        last_activity_at: new Date(Date.now() - 3600 * 1000).toISOString(),
        sprint_running: { label: 'sprint-10', elapsed_sec: 120 },
        last_sprint: { sprint_num: 10, date: '2026-08-13', status: 'running' },
      },
    ],
    activity: [],
  };
}

function fakeDevReportResponse() {
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
 * Build a VM context and run init() through the inline scripts.
 * Returns { homeEl, digestEl, fetchCalls }.
 */
async function runHomePage(fetchOverride) {
  const html = readFileSync(HTML_PATH, 'utf-8');
  const code = extractInlineScripts(html);

  const homeEl = makeEl();
  const digestEl = makeEl();
  const fetchCalls = [];

  const ctx = {
    fetch: async (url) => {
      const u = String(url);
      fetchCalls.push(u);
      return fetchOverride(u);
    },

    document: {
      documentElement: makeEl(),
      addEventListener: () => {},
      getElementById: (id) => {
        if (id === 'home') return homeEl;
        if (id === 'digest-slot') return digestEl;
        return makeEl();
      },
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: () => makeEl(),
    },

    location: { href: 'http://localhost/', search: '' },
    URL: class {
      constructor() {
        this.searchParams = { get: () => null, toString: () => '', set: () => {}, delete: () => {} };
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
    CSS: { escape: (s) => s },
    visibilityInterval: () => {},
  };
  ctx.window = ctx;

  vm.createContext(ctx);

  try {
    vm.runInContext(code, ctx, { timeout: 5000 });
  } catch (_) {}

  if (ctx._homeBootPromise && typeof ctx._homeBootPromise.then === 'function') {
    await ctx._homeBootPromise.catch(() => {});
  } else {
    await new Promise(r => setTimeout(r, 100));
  }
  await new Promise(r => setTimeout(r, 50));

  return { homeEl, digestEl, fetchCalls };
}

// ── AC5: /api/dev-report 404 → project list renders, no error state ───────────

test('AC5: project links render when /api/dev-report returns 404', async () => {
  const { homeEl, fetchCalls } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: false, status: 404, json: async () => null });
    }
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(
    html.includes('pb-title-link'),
    `project links (.pb-title-link) must be present even with /api/dev-report returning 404, got: ${html.slice(0, 300)}`,
  );
  assert.ok(
    html.includes('/project/alpha/sprint-mgmt'),
    'alpha project link must be present despite /api/dev-report 404',
  );
});

test('AC5: no error state shown when /api/dev-report returns 404', async () => {
  const { homeEl, digestEl } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: false, status: 404, json: async () => null });
    }
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.length > 100, `home must not be blank, got length ${html.length}`);
  assert.ok(!html.includes('Could not load projects'), 'error banner must not appear when /api/home succeeds');
  // digest slot must be empty (no content from 404)
  assert.equal(digestEl.innerHTML, '', 'digest slot must be empty when dev-report returns 404');
});

// ── AC2: digest block visible when /api/dev-report returns 200 ────────────────

test('AC2: digest block appears in digest-slot when dev-report returns 200', async () => {
  const { digestEl, homeEl } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: true, json: async () => fakeDevReportResponse() });
    }
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const digestHtml = digestEl.innerHTML;
  assert.ok(
    digestHtml.length > 0,
    `digest slot must contain content when /api/dev-report returns 200, got empty string`,
  );
  assert.ok(
    digestHtml.includes('/report'),
    `digest block must link to /report, got: ${digestHtml.slice(0, 200)}`,
  );
  // project list also renders (dev-report doesn't block it)
  assert.ok(
    homeEl.innerHTML.includes('pb-title-link'),
    'project links must be present even when dev-report loads successfully',
  );
});

test('AC2: digest slot is empty when /api/dev-report returns 404', async () => {
  const { digestEl } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: false, status: 404, json: async () => null });
    }
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  assert.equal(digestEl.innerHTML, '', 'digest slot must be empty when dev-report returns 404');
});

// ── AC3: 500 and network errors don't block navigation ────────────────────────

test('AC3: project links present when /api/dev-report returns 500', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: false, status: 500, json: async () => null });
    }
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  assert.ok(
    homeEl.innerHTML.includes('pb-title-link'),
    'project links must be present even with /api/dev-report returning 500',
  );
});

test('AC3: project links present when /api/dev-report throws network error', async () => {
  const { homeEl, digestEl } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.reject(new Error('network failure'));
    }
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  assert.ok(
    homeEl.innerHTML.includes('pb-title-link'),
    'project links must be present even when /api/dev-report network fails',
  );
  assert.equal(digestEl.innerHTML, '', 'digest slot must be empty on network error');
});
