/**
 * Behavioral tests for issue #2261:
 * Live cross-project status view — rewire home page from /api/dev-report to /api/home.
 *
 * AC1: page fetches /api/home (not /api/dev-report)
 * AC2: every project row links to /project/{slug}/sprint-mgmt
 * AC3: each row shows name, status, UAT count, backlog count, last sprint, last activity
 * AC4: header strip shows global stats (running, awaiting sign-off, backlog, planned)
 * AC5: /api/dev-report stubbed to 404 → project links still present
 *
 * Pattern: VM sandbox with fetch spy — not source-regex checks (issue #1746).
 * Run with: node --test tests/frontend/home-api-home-2261.test.mjs
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

/** DOM element stub — tracks innerHTML writes so we can assert rendered output. */
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
    classList: {
      add() {}, remove() {}, toggle() { return false; }, contains() { return false; },
    },
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

/** Fake /api/home response with two projects. */
function fakeHomeResponse() {
  return {
    stats: {
      sprint_running: { count: 2, projects: [{ name: 'alpha', sprint_label: 'sprint-5', elapsed_sec: 300 }] },
      awaiting_uat: { count: 7, projects: 3, oldest_age_sec: 86400 },
      backlog: { count: 42, per_project: [] },
      sprints_planned: { count: 5, total_tickets: 18 },
    },
    projects: [
      {
        slug: 'alpha',
        name: 'Alpha Project',
        repo: 'org/alpha',
        icon: 'ti-rocket',
        color: 'blue',
        status: 'running',
        uat_count: 3,
        backlog_count: 10,
        last_activity_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
        sprint_running: { label: 'sprint-5', elapsed_sec: 300 },
        last_sprint: { sprint_num: 5, date: '2026-08-10', status: 'running' },
      },
      {
        slug: 'beta',
        name: 'Beta Service',
        repo: 'org/beta',
        icon: 'ti-server',
        color: 'green',
        status: 'uat-pending',
        uat_count: 2,
        backlog_count: 5,
        last_activity_at: new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
        last_sprint: { sprint_num: 12, date: '2026-08-09', status: 'uat' },
      },
    ],
    activity: [],
  };
}

/**
 * Build a VM context and run init() through the inline scripts.
 * Returns { homeEl, fetchCalls }.
 */
async function runHomePage(fetchOverride) {
  const html = readFileSync(HTML_PATH, 'utf-8');
  const code = extractInlineScripts(html);

  const homeEl = makeEl();
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
        return makeEl();
      },
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: () => makeEl(),
    },

    location: { href: 'http://localhost/', search: '' },
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

    CSS: { escape: (s) => s },
    visibilityInterval: () => {},
  };
  ctx.window = ctx;

  vm.createContext(ctx);

  // Run the script; _homeBootPromise is set automatically at the bottom of home.html
  // when init() is called — no regex replacement needed.
  try {
    vm.runInContext(code, ctx, { timeout: 5000 });
  } catch (_) {}

  if (ctx._homeBootPromise && typeof ctx._homeBootPromise.then === 'function') {
    await ctx._homeBootPromise.catch(() => {});
  } else {
    await new Promise(r => setTimeout(r, 100));
  }
  await new Promise(r => setTimeout(r, 20));

  return { homeEl, fetchCalls };
}

// ── AC1: page fetches /api/home, not /api/dev-report ─────────────────────────

test('AC1: home page requests /api/home on init', async () => {
  const { fetchCalls } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const homeCalls = fetchCalls.filter(u => u.includes('/api/home') && !u.includes('/api/home/'));
  assert.ok(homeCalls.length >= 1, `expected at least one /api/home call, got: ${JSON.stringify(fetchCalls)}`);
});

test('AC1: home page does NOT request /api/dev-report', async () => {
  const { fetchCalls } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const devReportCalls = fetchCalls.filter(u => u.includes('/api/dev-report'));
  assert.equal(devReportCalls.length, 0, `must make zero /api/dev-report calls, got: ${JSON.stringify(devReportCalls)}`);
});

// ── AC2: project rows link to /project/{slug}/sprint-mgmt ────────────────────

test('AC2: rendered HTML contains pb-title-link elements for each project', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.includes('pb-title-link'), `rendered HTML must contain .pb-title-link class, got: ${html.slice(0, 200)}`);
});

test('AC2: project links point to /project/{slug}/sprint-mgmt', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(
    html.includes('/project/alpha/sprint-mgmt'),
    'must contain link to alpha sprint-mgmt',
  );
  assert.ok(
    html.includes('/project/beta/sprint-mgmt'),
    'must contain link to beta sprint-mgmt',
  );
});

// ── AC3: row shows name, status, UAT count, backlog, last sprint, last activity ──

test('AC3: project name appears in rendered row', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.includes('Alpha Project'), 'project name "Alpha Project" must appear');
  assert.ok(html.includes('Beta Service'), 'project name "Beta Service" must appear');
});

test('AC3: status pill for running project is rendered', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.includes('pbstatus run'), 'running status pill must use .pbstatus.run class');
});

test('AC3: UAT count appears in rendered row', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.includes('3 UAT') || html.includes('3&nbsp;UAT') || html.match(/3\s*UAT/), 'UAT count 3 must appear for alpha');
});

test('AC3: backlog count appears in rendered row', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.includes('10 backlog') || html.match(/10\s*backlog/), 'backlog count 10 must appear for alpha');
});

test('AC3: last sprint number appears in rendered row', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  // alpha has sprint_num=5, beta has sprint_num=12
  assert.ok(html.includes('S5') || html.includes('Sprint 5'), 'sprint number 5 must appear for alpha');
  assert.ok(html.includes('S12') || html.includes('Sprint 12'), 'sprint number 12 must appear for beta');
});

// ── AC4: header strip shows global stats ──────────────────────────────────────

test('AC4: stats strip renders running count', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(
    html.includes('home-stats-strip'),
    'stats strip (.home-stats-strip) must appear in rendered output',
  );
  assert.ok(html.includes('2 running') || html.match(/2\s+running/), 'running count 2 must appear in stats strip');
});

test('AC4: stats strip renders awaiting sign-off count', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(
    html.includes('awaiting sign-off') || html.includes('sign-off'),
    'awaiting sign-off label must appear in stats strip',
  );
  assert.ok(html.includes('7'), 'awaiting_uat count 7 must appear');
});

test('AC4: stats strip renders backlog count', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.includes('42'), 'backlog count 42 must appear in stats');
});

// ── AC5: /api/dev-report 404 → project links still present ───────────────────

test('AC5: project links render even when /api/dev-report returns 404', async () => {
  const { homeEl, fetchCalls } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      // Simulate the broken state that caused the outage
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
    'project links (.pb-title-link) must be present even with /api/dev-report returning 404',
  );
  assert.ok(
    html.includes('/project/alpha/sprint-mgmt'),
    'alpha project link must be present despite /api/dev-report 404',
  );
});

test('AC5: page does not blank when /api/dev-report is absent', async () => {
  const { homeEl } = await runHomePage(url => {
    if (url.includes('/api/dev-report')) {
      return Promise.resolve({ ok: false, status: 404, json: async () => null });
    }
    if (url.includes('/api/home') && !url.includes('/api/home/')) {
      return Promise.resolve({ ok: true, json: async () => fakeHomeResponse() });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });

  const html = homeEl.innerHTML;
  assert.ok(html.length > 100, `rendered HTML must not be blank or nearly empty, got length ${html.length}`);
  assert.ok(!html.includes('No report yet'), 'old "No report yet" message must not appear');
  assert.ok(!html.includes('nightly'), 'references to nightly cron must not appear');
});
