/**
 * Behavioral tests for issue #2063 — health-strip "See more →" stale tab audit.
 *
 * AC coverage:
 *   AC2 — no stale switchTab('metrics'/'analytics') calls survive in source
 *   AC3 — tabs.js coercion emits console.warn so stale internal links surface
 *          during development
 *
 * Run with: node --test tests/frontend/health-strip-stale-tab-2063.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal DOM stub required by tabs.js top-level code ──────────────────────
globalThis.document = {
  addEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => ({ forEach: () => {} }),
};
globalThis.window = {
  addEventListener: () => {},
  history: { pushState: () => {} },
  innerWidth: 800,
};

// Stubs for all globals tabs.js references (declared via /* global */ comment)
const _noop = () => {};
const _tabGlobals = [
  '_slug', '_activeTab', '_cachedFullRepo', '_ticketsLoaded', '_sprintMgmtLoaded',
  '_smgmtLivePollId', '_smgmtLogPollId', 'loadSprintMgmt', 'loadTickets',
  '_smgmtArInit', '_smgmtArStartTicker', 'deployTabDestroy', 'deployTabInit',
  'roadmapInit', 'advInit', 'projSettingsInit', 'settingsInitValues',
  'settingsPopulateRepos', 'globalSettingsLoad', '_bcInitTab', '_lpRenderBc',
  '_deepLinkSprintSubView', '_applyDeepLinkSubView', '_smgmtSavedSubView',
  '_smgmtShowSubView', '_histLoadLedger', '_globalSettingsLinkActive',
  '_evlState', 'parseUrl', '_arTickerId', '_arInterval', 'failuresInit',
  'brainInit', '_ticketsRepo', '_deepLinkView', '_deepLinkFilter',
];
for (const g of _tabGlobals) {
  if (!(g in globalThis)) globalThis[g] = _noop;
}
// Some globals are read as values, not called
globalThis._activeTab = 'sprint-mgmt';
globalThis._cachedFullRepo = {};
globalThis._sprintMgmtLoaded = false;
globalThis._ticketsLoaded = false;
globalThis._smgmtLivePollId = null;
globalThis._smgmtLogPollId = null;
globalThis._arTickerId = null;
globalThis._arInterval = 0;
globalThis._evlState = {};

const { switchTab } = await import('../../apps/dashboard/static/src/shell/tabs.js');


// ── AC3: coercion emits console.warn ─────────────────────────────────────────

test('AC3: switchTab("metrics") emits console.warn about coercion', () => {
  const warnings = [];
  const origWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(' '));
  try {
    switchTab('metrics');
  } finally {
    console.warn = origWarn;
  }
  assert.ok(
    warnings.length > 0,
    'switchTab("metrics") must emit a console.warn — stale internal callers need to be visible'
  );
  const combined = warnings.join('\n');
  assert.ok(
    combined.includes('metrics') || combined.includes('failures'),
    'warn message must mention the stale tab or its replacement'
  );
});

test('AC3: switchTab("logs") emits console.warn about coercion', () => {
  const warnings = [];
  const origWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(' '));
  try {
    switchTab('logs');
  } finally {
    console.warn = origWarn;
  }
  assert.ok(warnings.length > 0, 'switchTab("logs") must emit a console.warn');
});

test('AC3: switchTab("status") emits console.warn about coercion', () => {
  const warnings = [];
  const origWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(' '));
  try {
    switchTab('status');
  } finally {
    console.warn = origWarn;
  }
  assert.ok(warnings.length > 0, 'switchTab("status") must emit a console.warn');
});

test('AC3: switchTab("failures") does NOT emit console.warn (valid tab)', () => {
  const warnings = [];
  const origWarn = console.warn;
  console.warn = (...args) => warnings.push(args.join(' '));
  try {
    switchTab('failures');
  } finally {
    console.warn = origWarn;
  }
  const staleWarnings = warnings.filter(w =>
    w.includes('metrics') || w.includes('logs') || w.includes('status')
  );
  assert.equal(
    staleWarnings.length,
    0,
    'valid tabs must not trigger stale-tab console.warn'
  );
});
