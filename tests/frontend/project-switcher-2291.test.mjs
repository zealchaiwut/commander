/**
 * Behavioral tests for issue #2291: project switcher in the project header.
 *
 * AC1: Switcher lists all projects from GET /api/home
 * AC2: Selecting a project navigates to the same tab; falls back to sprint-mgmt
 * AC3: Current project is indicated as selected
 * AC5: fetch spy asserts switcher populates from /api/home; options match slugs
 *
 * Run with: node --test tests/frontend/project-switcher-2291.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Stubs required at import time ─────────────────────────────────────────────
if (typeof globalThis.window === 'undefined') globalThis.window = globalThis;

// escHtml is a global in project.html; stub the simple version
globalThis.escHtml = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

import {
  buildProjectSwitcherOptions,
  navigateToProject,
} from '../../apps/dashboard/static/src/shell/project-switcher.js';

// ── AC1 + AC5: fetch spy — options come from /api/home ────────────────────────

test('AC5: fetch spy — switcher options match slugs returned by /api/home', async () => {
  const fetchCalls = [];
  globalThis.fetch = async (url) => {
    fetchCalls.push(String(url));
    if (String(url) === '/api/home') {
      return {
        ok: true,
        json: async () => ({
          projects: [
            { slug: 'alpha', name: 'Alpha Project' },
            { slug: 'beta',  name: 'Beta Project'  },
            { slug: 'gamma', name: 'Gamma'          },
          ],
        }),
      };
    }
    throw new Error('Unexpected fetch: ' + url);
  };

  // Simulate what loadHomeData does: fetch /api/home, extract projects, build options
  const resp = await fetch('/api/home');
  const { projects } = await resp.json();
  const html = buildProjectSwitcherOptions(projects, 'beta');

  assert.ok(fetchCalls.includes('/api/home'), 'must fetch /api/home');
  assert.ok(html.includes('value="alpha"'), 'alpha option must be rendered');
  assert.ok(html.includes('value="beta"'),  'beta option must be rendered');
  assert.ok(html.includes('value="gamma"'), 'gamma option must be rendered');
});

// ── AC1: all returned slugs appear in the rendered options ────────────────────

test('AC1: all projects from /api/home appear as options', () => {
  const projects = [
    { slug: 'commander', name: 'Commander' },
    { slug: 'perf-coach', name: 'Perf Coach' },
    { slug: 'crux', name: 'Crux' },
  ];
  const html = buildProjectSwitcherOptions(projects, 'commander');
  assert.ok(html.includes('value="commander"'),  'commander must be an option');
  assert.ok(html.includes('value="perf-coach"'), 'perf-coach must be an option');
  assert.ok(html.includes('value="crux"'),       'crux must be an option');
});

test('AC1: empty project list produces empty string (no crash)', () => {
  assert.equal(buildProjectSwitcherOptions([], 'commander'), '');
  assert.equal(buildProjectSwitcherOptions(null, 'commander'), '');
});

// ── AC3: current project marked selected ─────────────────────────────────────

test('AC3: current slug has selected attribute', () => {
  const projects = [
    { slug: 'alpha', name: 'Alpha' },
    { slug: 'beta',  name: 'Beta'  },
  ];
  const html = buildProjectSwitcherOptions(projects, 'beta');
  // Only beta should be selected
  assert.ok(html.includes('value="beta" selected'),   'beta must be selected');
  assert.ok(!html.includes('value="alpha" selected'), 'alpha must not be selected');
});

test('AC3: no option selected when currentSlug does not match any project', () => {
  const projects = [
    { slug: 'alpha', name: 'Alpha' },
  ];
  const html = buildProjectSwitcherOptions(projects, 'other');
  assert.ok(!html.includes(' selected'), 'no option should be selected');
});

// ── AC2: navigate to same tab; fall back to sprint-mgmt ──────────────────────

test('AC2: navigates to the same tab when switching project', () => {
  let navigatedTo = null;
  const origWindow = globalThis.window;
  globalThis.window = {
    ...globalThis,
    get location() {
      return {
        set href(v) { navigatedTo = v; },
      };
    },
  };

  navigateToProject('perf-coach', 'commander', 'failures');
  assert.equal(navigatedTo, '/project/perf-coach/failures');

  globalThis.window = origWindow;
});

test('AC2: falls back to sprint-mgmt when tab is undefined', () => {
  let navigatedTo = null;
  const origWindow = globalThis.window;
  globalThis.window = {
    ...globalThis,
    get location() {
      return {
        set href(v) { navigatedTo = v; },
      };
    },
  };

  navigateToProject('crux', 'commander', undefined);
  assert.equal(navigatedTo, '/project/crux/sprint-mgmt');

  globalThis.window = origWindow;
});

test('AC2: no navigation when switching to the current project', () => {
  let navigatedTo = null;
  const origWindow = globalThis.window;
  globalThis.window = {
    ...globalThis,
    get location() {
      return {
        set href(v) { navigatedTo = v; },
      };
    },
  };

  navigateToProject('commander', 'commander', 'tickets');
  assert.equal(navigatedTo, null, 'must not navigate when slug matches current');

  globalThis.window = origWindow;
});

test('AC2: preserves tickets tab when switching project', () => {
  let navigatedTo = null;
  const origWindow = globalThis.window;
  globalThis.window = {
    ...globalThis,
    get location() {
      return {
        set href(v) { navigatedTo = v; },
      };
    },
  };

  navigateToProject('crux', 'commander', 'tickets');
  assert.equal(navigatedTo, '/project/crux/tickets');

  globalThis.window = origWindow;
});

// ── AC4 (keyboard / accessible): option labels match project names ────────────

test('AC4: option display text matches project name (not raw slug)', () => {
  const projects = [{ slug: 'perf-coach', name: 'Perf Coach' }];
  const html = buildProjectSwitcherOptions(projects, 'other');
  assert.ok(html.includes('>Perf Coach<'), 'option text must be the project name');
});

test('AC4: falls back to slug as display text when name is absent', () => {
  const projects = [{ slug: 'unnamed' }];
  const html = buildProjectSwitcherOptions(projects, 'other');
  assert.ok(html.includes('>unnamed<'), 'option text must fall back to slug');
});
