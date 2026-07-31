/**
 * Behavioral tests for parseUrl() tab mapping (issue #2038).
 *
 * Before this fix, 'failures' and 'brain' fell through to 'sprint-mgmt'
 * because they were missing from the ternary chain.  Loading
 * /project/<slug>/failures would silently activate the Sprint pane and
 * history.replaceState would rewrite the URL to /project/<slug>/sprint-mgmt.
 *
 * These tests exercise _parseUrlImpl() — the pure form of parseUrl() — so
 * they run in Node with no DOM dependency.  The same function is called at
 * runtime by the browser's parseUrl() wrapper (which reads window.location).
 *
 * To verify the tests FAIL against the pre-fix code: temporarily change the
 * 'failures' case to 'sprint-mgmt' in url-parser.js and observe the first two
 * tests fail.  git diff confirms no stray edits remain before committing.
 *
 * Run with: node --test tests/frontend/tab-deeplink-2038.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { _parseUrlImpl } from '../../apps/dashboard/static/src/shell/url-parser.js';


// ── Helper ────────────────────────────────────────────────────────────────────

function tab(rawPath) {
  return _parseUrlImpl('/project/slug/' + rawPath).tab;
}


// ── AC1 / AC2 / AC3: failures and brain must map to themselves ───────────────

test('failures → "failures"', () => {
  const result = _parseUrlImpl('/project/commander/failures', '');
  assert.equal(result.tab, 'failures',
    'failures must map to "failures", not fall through to sprint-mgmt');
  assert.equal(result.slug, 'commander');
});

test('brain → "brain"', () => {
  const result = _parseUrlImpl('/project/commander/brain', '');
  assert.equal(result.tab, 'brain',
    'brain must map to "brain", not fall through to sprint-mgmt');
  assert.equal(result.slug, 'commander');
});

// ── Pre-existing valid tabs still map correctly ───────────────────────────────

test('tickets → "tickets"', () => {
  assert.equal(tab('tickets'), 'tickets');
});

test('sprint → "sprint-mgmt" (alias, issue #798)', () => {
  assert.equal(tab('sprint'), 'sprint-mgmt');
});

test('sprint-mgmt → "sprint-mgmt" (unknown falls through to default)', () => {
  // 'sprint-mgmt' is not an explicit case — it is the fallback.
  assert.equal(tab('sprint-mgmt'), 'sprint-mgmt');
});

test('bulk-create → "bulk-create"', () => {
  assert.equal(tab('bulk-create'), 'bulk-create');
});

test('settings → "settings"', () => {
  assert.equal(tab('settings'), 'settings');
});

test('global-settings → "global-settings"', () => {
  assert.equal(tab('global-settings'), 'global-settings');
});

// ── AC4: dead tabs (server-redirected) fall through to sprint-mgmt ────────────
// The server redirects /project/{slug}/logs|status|metrics → 302 → /failures
// and /project/{slug}/analytics → 301 → /failures BEFORE project.html is
// served, so these strings should never reach parseUrl in practice.
// Nonetheless, the ternary chain no longer has explicit branches for them —
// they fall through to the 'sprint-mgmt' default (same as any unknown segment).

test('logs → "sprint-mgmt" (no explicit branch; server handles redirect)', () => {
  assert.equal(tab('logs'), 'sprint-mgmt');
});

test('status → "sprint-mgmt" (no explicit branch; server handles redirect)', () => {
  assert.equal(tab('status'), 'sprint-mgmt');
});

test('metrics → "sprint-mgmt" (no explicit branch; server handles redirect)', () => {
  assert.equal(tab('metrics'), 'sprint-mgmt');
});

test('analytics → "sprint-mgmt" (no explicit branch; server handles redirect)', () => {
  assert.equal(tab('analytics'), 'sprint-mgmt');
});

// ── Edge cases ────────────────────────────────────────────────────────────────

test('empty segment → "sprint-mgmt" (default tab on bare /project/<slug>/)', () => {
  const result = _parseUrlImpl('/project/commander/', '');
  assert.equal(result.tab, 'sprint-mgmt');
  assert.equal(result.slug, 'commander');
});

test('unknown segment → "sprint-mgmt" (graceful fallback)', () => {
  assert.equal(tab('unknown-future-tab'), 'sprint-mgmt');
});

test('non-project path → slug=null, tab=sprint-mgmt', () => {
  const result = _parseUrlImpl('/', '');
  assert.equal(result.slug, null);
  assert.equal(result.tab, 'sprint-mgmt');
});

// ── Query-string parsing (view / filter) ─────────────────────────────────────

test('view param is parsed and lowercased', () => {
  const result = _parseUrlImpl('/project/commander/failures', '?view=Board');
  assert.equal(result.view, 'board');
  assert.equal(result.tab, 'failures');
});

test('filter param is parsed and lowercased', () => {
  const result = _parseUrlImpl('/project/commander/failures', '?filter=Errors');
  assert.equal(result.filter, 'errors');
});

test('absent view/filter → null', () => {
  const result = _parseUrlImpl('/project/commander/failures', '');
  assert.equal(result.view, null);
  assert.equal(result.filter, null);
});
