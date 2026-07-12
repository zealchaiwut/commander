/**
 * Behavioral test for issue #1854 AC3: Refresh button in Home top-nav.
 *
 * AC3  Home top-nav gets a Refresh button (next to #today-btn) that POSTs
 *      /api/brief/daily/regenerate and re-runs loadBrief(_curDate).
 *
 * Strategy: extract refreshBrief() from home.html using brace-depth counting,
 * run it in a controlled context with a fetch spy and loadBrief spy, assert
 * the right POST was made and loadBrief was called with the current date.
 *
 * Run with: node --test tests/frontend/home-refresh.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = fileURLToPath(new URL('.', import.meta.url));
const HOME_HTML = resolve(__dir, '../../apps/dashboard/static/home.html');

const html = readFileSync(HOME_HTML, 'utf-8');

/**
 * Extract a named async function from JavaScript source by counting braces.
 * Returns the full function text including its body, or null if not found.
 */
function extractFunction(source, fnName) {
  const sig = `function ${fnName}(`;
  const start = source.indexOf(sig);
  if (start === -1) return null;

  // Walk back to find 'async' if present
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

// ── AC3: Refresh button POSTs to /api/brief/daily/regenerate ─────────────────

test('AC3: refreshBrief is defined in home.html', () => {
  const fn = extractFunction(html, 'refreshBrief');
  assert.ok(fn !== null, 'refreshBrief() must be defined in home.html');
});

test('AC3: refreshBrief POSTs to /api/brief/daily/regenerate', async () => {
  const fn = extractFunction(html, 'refreshBrief');
  assert.ok(fn, 'refreshBrief must be defined in home.html');

  const fetchCalls = [];
  const loadBriefCalls = [];

  const mockFetch = async (url, opts = {}) => {
    fetchCalls.push({ url: String(url), method: (opts.method || 'GET').toUpperCase() });
    return { ok: true, json: async () => ({}) };
  };
  const mockLoadBrief = async (date) => { loadBriefCalls.push(date); };
  const mockDocument = { getElementById: () => null };
  const curDate = '2026-07-12';

  // Evaluate the extracted function in a controlled scope then call it
  const runner = new Function(
    'fetch', 'loadBrief', 'document', '_curDate',
    `${fn}\n  return refreshBrief();`,
  );
  await runner(mockFetch, mockLoadBrief, mockDocument, curDate);

  const posts = fetchCalls.filter(c => c.method === 'POST');
  assert.equal(posts.length, 1, `refreshBrief must make exactly one POST request (got ${fetchCalls.length} total)`);
  assert.ok(
    posts[0].url.includes('/api/brief/daily/regenerate'),
    `POST must target /api/brief/daily/regenerate, got: ${posts[0].url}`,
  );

  assert.ok(loadBriefCalls.length > 0, 'refreshBrief must call loadBrief after the POST');
  assert.equal(
    loadBriefCalls[0], curDate,
    `loadBrief must be called with _curDate ('${curDate}'), got: ${loadBriefCalls[0]}`,
  );
});

test('AC3: #refresh-btn exists next to #today-btn in home.html nav', () => {
  // Verify the DOM structure: refresh-btn must appear after today-btn in the nav
  const todayIdx = html.indexOf('id="today-btn"');
  const refreshIdx = html.indexOf('id="refresh-btn"');
  assert.ok(todayIdx !== -1, '#today-btn must exist in home.html');
  assert.ok(refreshIdx !== -1, '#refresh-btn must exist in home.html');

  // Both must be inside the top-nav section (same container)
  const navStart = html.lastIndexOf('<nav', todayIdx);
  const navEnd = html.indexOf('</nav>', todayIdx);
  assert.ok(
    refreshIdx > navStart && refreshIdx < navEnd,
    '#refresh-btn must be inside the same <nav> as #today-btn',
  );
});
