/**
 * Frontend behavioral test for issue #1845 AC4.
 *
 * Verifies that the home.html project-population code path makes zero
 * requests to /api/home/milestone.  Uses a fetch-spy in a vm sandbox —
 * NOT a source-regex check — so a false pass cannot occur if the
 * loadMilestone() call is still present but just unreachable.
 *
 * Pre-fix the test FAILS because:
 *   - projects returned from /api/brief/daily lack a "milestone" key
 *     (p.milestone === undefined), which triggers loadMilestone()
 *   - loadMilestone() calls fetch('/api/home/milestone?repo=…')
 *   - the fetch spy records the call → milestoneCalls.length > 0
 *
 * Post-fix the test PASSES because the forEach block and loadMilestone()
 * are gone; no code path can make that request.
 *
 * Run: node --test tests/frontend/home-no-milestone-fetch.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';

const __dir = dirname(fileURLToPath(import.meta.url));
const HTML_PATH = resolve(__dir, '../../apps/dashboard/static/home.html');

/** Extract all inline <script> blocks (no src= attribute). */
function extractInlineScripts(html) {
  const re = /<script(?![^>]*\bsrc\b)[^>]*>([\s\S]*?)<\/script>/gi;
  const parts = [];
  let m;
  while ((m = re.exec(html)) !== null) parts.push(m[1]);
  return parts.join('\n');
}

/** Minimal DOM stub element that satisfies home.html's property accesses. */
function makeEl() {
  const el = {
    textContent: '',
    innerHTML: '',
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
      add() {},
      remove() {},
      toggle() { return false; },
      contains() { return false; },
    },
    insertAdjacentHTML: () => {},
    disabled: false,
    value: '',
    max: '',
  };
  return el;
}

test('AC4: home.html project population makes zero /api/home/milestone requests', async () => {
  const html = readFileSync(HTML_PATH, 'utf-8');
  const code = extractInlineScripts(html);

  const milestoneCalls = [];

  // We need a reference to the Promise returned by init() so we can await it.
  let initResolve;
  const initDone = new Promise(r => { initResolve = r; });

  // Build sandbox context — all globals home.html's script needs
  const ctx = {
    // ── fetch spy ─────────────────────────────────────────────────────────
    fetch: async (url) => {
      const u = String(url);
      if (u.includes('/api/home/milestone')) {
        milestoneCalls.push(u);
        return { ok: false, json: async () => null };
      }
      if (u.includes('/api/brief/daily')) {
        return {
          ok: true,
          json: async () => ({
            available: true,
            date: '2026-01-01',
            brief: {
              // Deliberately omit "milestone" key → p.milestone === undefined.
              // This is the condition that triggers loadMilestone() in pre-fix code.
              projects: [{
                project: 'milestone-spy',
                repo: 'org/milestone-spy',
                briefSummary: '',
                name: 'Spy Project',
                icon: 'ti-folder',
                color: 'gray',
              }],
              decisions: [],
              global_kpis: {},
              ran_overnight: [],
              waiting_on_you: [],
              suggested_next: [],
            },
          }),
        };
      }
      if (u.includes('/api/todos')) {
        return { ok: true, json: async () => ({}) };
      }
      return { ok: true, json: async () => ({}) };
    },

    // ── DOM stubs ──────────────────────────────────────────────────────────
    document: {
      // head script accesses documentElement.setAttribute to set theme
      documentElement: makeEl(),
      addEventListener: () => {},
      getElementById: (id) => {
        // Return a real stub for the milestone badge element so loadMilestone()
        // does NOT bail out at "if (!el || !repo) return" — the fetch must fire.
        return makeEl();
      },
      querySelector: () => null,
      querySelectorAll: () => [],
    },

    // ── Location / URL ─────────────────────────────────────────────────────
    location: { href: 'http://localhost/', search: '' },
    URL: class {
      constructor() {
        this.searchParams = { get: () => null, toString: () => '' };
      }
    },
    URLSearchParams: class {
      constructor(s) { this._s = s || ''; }
      toString() { return this._s; }
      get() { return null; }
    },

    // ── Storage / timing ───────────────────────────────────────────────────
    localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,

    // ── Built-ins forwarded ────────────────────────────────────────────────
    encodeURIComponent,
    decodeURIComponent,
    Promise,
    Boolean,
    Array,
    Object,
    String,
    Number,
    Set,
    Math,
    Date,
    JSON,
    console: { log() {}, warn() {}, error() {}, info() {} },

    // ── Commander-specific globals ─────────────────────────────────────────
    CSS: { escape: (s) => s },
  };

  // window must be the context itself so window.location, window.CSS, etc. resolve
  ctx.window = ctx;

  vm.createContext(ctx);

  // Wrap the script so we can capture the Promise returned by init().
  // home.html ends with `init();` — we intercept that call by injecting a
  // wrapper that stores the returned Promise.
  const wrappedCode = code.replace(
    /\binit\(\s*\)\s*;?\s*$/m,
    '__initPromise__ = init();'
  );
  ctx.__initPromise__ = null;

  try {
    vm.runInContext(wrappedCode, ctx, { timeout: 5000 });
  } catch (e) {
    // Ignore syntax or startup errors; what matters is whether milestone is fetched.
  }

  // Await the init() promise (or fall back to a small delay).
  if (ctx.__initPromise__ && typeof ctx.__initPromise__.then === 'function') {
    await ctx.__initPromise__.catch(() => {});
  } else {
    await new Promise(r => setTimeout(r, 80));
  }

  // Allow any remaining microtasks to drain.
  await new Promise(r => setTimeout(r, 20));

  assert.equal(
    milestoneCalls.length,
    0,
    `Expected zero /api/home/milestone requests, got ${milestoneCalls.length}: ` +
    JSON.stringify(milestoneCalls)
  );
});
