/**
 * Behavioral tests for issue #1843 AC3.
 *
 * Verifies that anlTrendsInit() passes ?project=<slug> when calling
 * /api/metrics/sprints so Velocity/Failure/Duration charts show only
 * the viewed project's data.
 *
 * Uses vm.runInNewContext to evaluate the Metrics IIFE from project.html
 * in a sandboxed scope with a fetch spy — exercises the real code path,
 * not a source-regex check.
 *
 * Run with: node --test tests/frontend/metrics-project-filter.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(
  join(__dirname, '../../apps/dashboard/static/project.html'),
  'utf8'
);

/** Extract just the Metrics IIFE from project.html using its comment marker. */
function extractMetricsIIFE() {
  const MARKER = '// ── Metrics tab (issue #476)';
  const start = HTML.indexOf(MARKER);
  if (start === -1) throw new Error('Metrics IIFE marker not found in project.html');
  // The IIFE ends with '}());' followed by a blank line
  // Search forward from the marker for the closing brace
  let depth = 0;
  let iifeParen = false;
  let i = start;
  // skip to the first '(' that opens the IIFE
  while (i < HTML.length && HTML[i] !== '(') i++;
  // now walk character by character counting parens/braces
  const code = HTML.slice(start);
  let pos = code.indexOf('(function');
  if (pos === -1) throw new Error('IIFE open not found after marker');
  // count to matching closing '}());'
  let braceDepth = 0;
  let started = false;
  for (let k = pos; k < code.length; k++) {
    const ch = code[k];
    if (ch === '{') { braceDepth++; started = true; }
    else if (ch === '}') {
      braceDepth--;
      if (started && braceDepth === 0) {
        // found the closing brace; grab up to and including '}());'
        const end = code.indexOf(');', k);
        if (end === -1) throw new Error('IIFE closing ");" not found');
        return code.slice(0, end + 2);
      }
    }
  }
  throw new Error('IIFE end not found — brace matching failed');
}

/** Build a minimal sandbox context for running the Metrics IIFE. */
function makeContext(slug) {
  const fetchedUrls = [];

  const ctx = vm.createContext({
    // Globals referenced by anlTrendsInit
    _slug: slug,
    document: {
      getElementById: () => null,
      createElement: () => ({
        set src(_) {},
        set onload(fn) { /* CDN load not needed in test */ },
        set onerror(fn) {},
      }),
      head: { appendChild: () => {} },
      querySelectorAll: () => [],
    },
    fetch: async (url) => {
      fetchedUrls.push(String(url));
      return {
        ok: true,
        json: async () => (url.includes('/analytics/cost')
          ? { by_sprint: [] }
          : []),
      };
    },
    // Stub Chart.js as truthy so _loadChartJs calls cb() synchronously
    Chart: function Chart() {},
    // Expose recorded fetch calls for assertions
    _fetchedUrls: fetchedUrls,
    // Standard globals the IIFE uses
    encodeURIComponent: encodeURIComponent,
    clearTimeout: () => {},
    setTimeout: (fn, ms) => { fn(); return 0; },
    console: { warn: () => {}, error: () => {}, log: () => {} },
    window: null, // set below after context creation
  });
  // window must be the same object as the context itself
  ctx.window = ctx;
  return { ctx, fetchedUrls };
}

// ── AC3: fetch call includes ?project=<slug> ─────────────────────────────────

test('AC3: anlTrendsInit fetches /api/metrics/sprints with ?project=<slug>', async () => {
  const iife = extractMetricsIIFE();
  const { ctx, fetchedUrls } = makeContext('commander');

  vm.runInContext(iife, ctx);

  const fn = ctx.window.anlTrendsInit;
  assert.ok(typeof fn === 'function', 'anlTrendsInit must be assigned to window');

  await fn();

  const metricsCall = fetchedUrls.find(u => u.includes('/api/metrics/sprints'));
  assert.ok(metricsCall, `fetch must be called with /api/metrics/sprints; got: ${JSON.stringify(fetchedUrls)}`);
  assert.ok(
    metricsCall.includes('project='),
    `URL must include project= query param; got: ${metricsCall}`
  );
  assert.ok(
    metricsCall.includes('commander'),
    `URL must include the slug 'commander'; got: ${metricsCall}`
  );
});

test('AC3: ?project= value matches the current _slug exactly', async () => {
  const iife = extractMetricsIIFE();
  const { ctx, fetchedUrls } = makeContext('perf-coach');

  vm.runInContext(iife, ctx);

  await ctx.window.anlTrendsInit();

  const metricsCall = fetchedUrls.find(u => u.includes('/api/metrics/sprints'));
  assert.ok(metricsCall, 'must call /api/metrics/sprints');
  // The project param should be exactly the slug
  const url = new URL(metricsCall, 'http://x');
  const projectParam = url.searchParams.get('project');
  assert.equal(projectParam, 'perf-coach', `Expected project=perf-coach, got: ${projectParam}`);
});

test('AC3: tokens fetch is NOT changed — still uses /api/projects/{slug}/analytics/cost', async () => {
  const iife = extractMetricsIIFE();
  const { ctx, fetchedUrls } = makeContext('commander');

  vm.runInContext(iife, ctx);
  await ctx.window.anlTrendsInit();

  const costCall = fetchedUrls.find(u => u.includes('/analytics/cost'));
  assert.ok(costCall, 'tokens fetch must still call /analytics/cost');
  assert.ok(costCall.includes('/api/projects/'), 'tokens fetch must be project-scoped');
});
