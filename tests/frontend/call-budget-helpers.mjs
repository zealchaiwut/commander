/**
 * Shared frontend call-count-budget helpers for Node.js test harnesses.
 *
 * Provides a fetch spy that counts calls by URL prefix, usable from any
 * HTML-harness test file (tests/frontend/*.test.mjs).
 *
 * Usage (one-liner):
 *
 *   import { installFetchSpy, assertFetchBudget } from './call-budget-helpers.mjs';
 *
 *   test('board makes exactly 1 fetch', async () => {
 *     const spy = installFetchSpy({ '/api/board': myFakeBoardResponse() });
 *     await loadSprintMgmt(true, null);
 *     assertFetchBudget(spy, '/api/board', 1);
 *   });
 */

import assert from 'node:assert/strict';

/**
 * Install a global fetch spy that records all calls and returns canned
 * responses keyed by URL prefix.
 *
 * @param {Record<string, any>} routes  Map of url-prefix → response body.
 *   The first matching prefix wins. Unmatched URLs throw.
 * @returns {{ calls: string[], reset: () => void }}
 */
export function installFetchSpy(routes) {
  const calls = [];
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    for (const [prefix, body] of Object.entries(routes)) {
      if (u.startsWith(prefix) || u.includes(prefix)) {
        return {
          ok: true,
          status: 200,
          json: async () => body,
          text: async () => JSON.stringify(body),
          body: { getReader: () => ({ read: async () => ({ done: true }) }) },
        };
      }
    }
    throw new Error(`call-budget-helpers: unexpected fetch URL: ${u}`);
  };
  return {
    calls,
    reset() {
      calls.length = 0;
    },
  };
}

/**
 * Assert that the number of fetch calls whose URL contains pathPattern is
 * exactly `expected` (strict equality).
 *
 * @param {{ calls: string[] }} spy
 * @param {string} pathPattern
 * @param {number} expected
 */
export function assertFetchBudget(spy, pathPattern, expected) {
  const matching = spy.calls.filter(u => u.includes(pathPattern));
  assert.equal(
    matching.length,
    expected,
    `Fetch budget for '${pathPattern}': expected ${expected}, got ${matching.length}. Calls: ${JSON.stringify(spy.calls)}`,
  );
}

/**
 * Assert that the number of fetch calls whose URL contains pathPattern is
 * ≤ maxCalls.
 *
 * @param {{ calls: string[] }} spy
 * @param {string} pathPattern
 * @param {number} maxCalls
 */
export function assertFetchBudgetMax(spy, pathPattern, maxCalls) {
  const matching = spy.calls.filter(u => u.includes(pathPattern));
  assert.ok(
    matching.length <= maxCalls,
    `Fetch budget exceeded for '${pathPattern}': expected ≤ ${maxCalls}, got ${matching.length}. Calls: ${JSON.stringify(spy.calls)}`,
  );
}
