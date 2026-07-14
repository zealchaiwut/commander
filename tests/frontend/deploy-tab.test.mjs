/**
 * Tests for issue #1769: Fix stuck Deploy loader and mobile-responsive cards.
 *
 * AC2/AC3 – Regression: switchTab('deploy') fires deployTabInit regardless of
 *           entry path (direct click or Manage ▾ dropdown).
 * AC5     – .deploy-grid is single-column (grid-template-columns: 1fr) at ≤430px.
 * AC6     – .deploy-btn has min-height ≥44px and width:100% at ≤430px.
 * AC6     – .deploy-card__actions stacks buttons vertically at ≤430px.
 * Bug-fix – toggleStabDropdown positions dropdown as position:fixed at ≤430px
 *           so overflow-x:auto on .sub-tabs (which forces overflow-y:auto) cannot
 *           clip the dropdown items, making Deploy reachable via the dropdown.
 *
 * Run with: node --test tests/frontend/deploy-tab.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const html = readFileSync(
  path.join(__dirname, '../../apps/dashboard/static/project.html'),
  'utf8',
);

// ── CSS helpers ───────────────────────────────────────────────────────────────

/** Collect all CSS text from <style> blocks. */
function getAllCss() {
  const blocks = [];
  const re = /<style[^>]*>([\s\S]*?)<\/style>/gi;
  let m;
  while ((m = re.exec(html)) !== null) blocks.push(m[1]);
  return blocks.join('\n');
}

/**
 * Extract declarations for `selector` from inside a `@media (max-width: 430px)`
 * block. Multiple rule blocks for the same selector are merged.
 */
function cssIn430(selector) {
  const allCss = getAllCss();
  const lines = allCss.split('\n');
  let inMedia = false;
  let depth = 0;
  let mediaContent = '';

  for (const line of lines) {
    if (!inMedia) {
      if (/^\s*@media\s*\(\s*max-width:\s*430px\s*\)/.test(line)) {
        inMedia = true;
        const opens = (line.match(/{/g) || []).length;
        const closes = (line.match(/}/g) || []).length;
        depth = opens - closes;
        if (depth <= 0) inMedia = false;
      }
    } else {
      mediaContent += line + '\n';
      depth += (line.match(/{/g) || []).length;
      depth -= (line.match(/}/g) || []).length;
      if (depth <= 0) {
        inMedia = false;
        depth = 0;
      }
    }
  }

  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`^\\s*${escaped}\\s*\\{([^}]*)\\}`, 'gm');
  const found = [];
  let mm;
  while ((mm = re.exec(mediaContent)) !== null) found.push(mm[1]);
  return found.join('\n');
}

function parseDecls(text) {
  const result = {};
  for (const decl of text.split(';')) {
    const t = decl.trim();
    if (!t) continue;
    const colon = t.indexOf(':');
    if (colon < 0) continue;
    result[t.slice(0, colon).trim().toLowerCase()] = t.slice(colon + 1).trim();
  }
  return result;
}

function css430(selector) {
  return parseDecls(cssIn430(selector));
}

// ── AC5: single-column deploy grid at ≤430px ─────────────────────────────────

test('AC5: .deploy-grid has grid-template-columns: 1fr inside @media (max-width: 430px)', () => {
  const props = css430('.deploy-grid');
  assert.equal(
    props['grid-template-columns'],
    '1fr',
    '.deploy-grid must be single-column (1fr) at ≤430px so cards do not overflow a 390px viewport',
  );
});

// ── AC6: touch-friendly action buttons at ≤430px ─────────────────────────────

test('AC6: .deploy-btn has min-height ≥44px inside @media (max-width: 430px)', () => {
  const props = css430('.deploy-btn');
  const minH = props['min-height'];
  assert.ok(minH, '.deploy-btn must declare min-height inside @media (max-width: 430px)');
  const px = parseFloat(minH);
  assert.ok(
    !isNaN(px) && px >= 44,
    `.deploy-btn min-height must be ≥44px for touch targets (got ${minH})`,
  );
});

test('AC6: .deploy-btn has width: 100% inside @media (max-width: 430px)', () => {
  const props = css430('.deploy-btn');
  assert.equal(
    props['width'],
    '100%',
    '.deploy-btn must be full-width at ≤430px so buttons fill the card',
  );
});

test('AC6: .deploy-card__actions has flex-direction: column inside @media (max-width: 430px)', () => {
  const props = css430('.deploy-card__actions');
  assert.equal(
    props['flex-direction'],
    'column',
    '.deploy-card__actions must stack buttons vertically at ≤430px',
  );
});

// ── AC2/AC3: regression — switchTab('deploy') always calls deployTabInit ──────
//
// This covers BOTH the direct-click path (desktop flat tab) and the dropdown
// path (mobile Manage ▾ → Deploy). Both resolve to switchTab('deploy') which
// must unconditionally call deployTabInit.

// Stubs must be set before importing the module (module-level code uses them).
let _deployTabInitCallCount = 0;

globalThis.document = {
  addEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => ({ forEach: () => {} }),
};
globalThis.window = {
  addEventListener: () => {},
  history: { pushState: () => {} },
  innerWidth: 1280,
};
globalThis._activeTab = null;
globalThis._smgmtLivePollId = null;
globalThis._smgmtLogPollId = null;
globalThis._statusRefreshId = null;
globalThis._arTickerId = null;
globalThis._arInterval = 0;
globalThis._globalSettingsLinkActive = () => {};
globalThis._slug = 'test-project';
globalThis.deployTabInit = () => { _deployTabInitCallCount++; };
// Stubs for other tab inits that must not throw when switchTab calls them:
globalThis.deployTabDestroy = () => {};
globalThis.logsDestroy = () => {};
globalThis.ganttInit = () => {};
globalThis.compareInit = () => {};
globalThis.metricsInit = () => {};
globalThis.evaInit = () => {};
globalThis.calibInit = () => {};
globalThis.notesInit = () => {};
globalThis.roadmapInit = () => {};
globalThis.advInit = () => {};
globalThis.projSettingsInit = () => {};
globalThis.settingsInitValues = () => {};
globalThis.settingsPopulateRepos = () => {};
globalThis.globalSettingsLoad = () => {};
globalThis.logsInit = () => {};
globalThis._bcInitTab = () => {};
globalThis._lpRenderBc = () => {};
globalThis._deepLinkSprintSubView = () => false;
globalThis._smgmtSavedSubView = () => null;
globalThis._smgmtShowSubView = () => {};
globalThis._smgmtArInit = () => {};
globalThis._smgmtArStartTicker = () => {};
globalThis._histLoadLedger = () => {};
globalThis._cachedFullRepo = {};
globalThis._sprintMgmtLoaded = false;
globalThis._ticketsLoaded = false;
globalThis.loadTickets = () => {};
globalThis.loadSprintMgmt = () => Promise.resolve();
globalThis._applyDeepLinkSubView = () => {};
globalThis._smgmtUpdateSelectionUI = undefined;
globalThis._bulkUpdateActionBar = undefined;
globalThis._smgmtUpdateToolbarTop = undefined;

const { switchTab } = await import('../../apps/dashboard/static/src/shell/tabs.js');

test('AC3: switchTab("deploy") calls deployTabInit — covers both direct-click and dropdown entry paths', () => {
  const before = _deployTabInitCallCount;
  switchTab('deploy', false);
  assert.equal(
    _deployTabInitCallCount,
    before + 1,
    'switchTab("deploy") must call deployTabInit() exactly once',
  );
});

test('AC3: repeated switchTab("deploy") calls each trigger deployTabInit', () => {
  const before = _deployTabInitCallCount;
  // Simulate leaving the tab then re-entering via dropdown
  globalThis._activeTab = 'sprint-mgmt';
  switchTab('deploy', false);
  globalThis._activeTab = 'sprint-mgmt';
  switchTab('deploy', false);
  assert.equal(
    _deployTabInitCallCount,
    before + 2,
    'Each re-entry to deploy tab must call deployTabInit',
  );
});

// ── Bug-fix: toggleStabDropdown uses position:fixed at ≤430px ─────────────────
//
// At ≤430px, .sub-tabs { overflow-x: auto } forces overflow-y:auto, which clips
// the absolutely-positioned .stab-dropdown (a DOM descendant of .sub-tabs even
// though its containing block is .sub-tabs-row). Fix: position the dropdown as
// fixed using the trigger's getBoundingClientRect so it escapes the clip edge.

test('Bug-fix: toggleStabDropdown sets position:fixed on dropdown when innerWidth ≤430', async () => {
  // Re-import is cached; we set innerWidth via window before calling.
  const { toggleStabDropdown, closeAllStabDropdowns } = await import(
    '../../apps/dashboard/static/src/shell/tabs.js'
  );

  const dropdownProps = {};
  const mockDropdown = {
    style: {
      setProperty: (name, val) => { dropdownProps[name] = val; },
      removeProperty: (name) => { delete dropdownProps[name]; },
    },
  };
  const mockTrigger = {
    getBoundingClientRect: () => ({ bottom: 88, left: 0 }),
  };
  const mockGroup = {
    classList: {
      contains: (cls) => cls === 'open' ? false : false,
      add: () => {},
      remove: () => {},
    },
    querySelector: (sel) => {
      if (sel === '.stab-trigger') return mockTrigger;
      if (sel === '.stab-dropdown') return mockDropdown;
      return null;
    },
  };

  // Override getElementById to return our mock group for 'stab-group-manage'
  const origGetById = globalThis.document.getElementById;
  globalThis.document.getElementById = (id) => {
    if (id === 'stab-group-manage') return mockGroup;
    return null;
  };

  // Set narrow viewport
  globalThis.window.innerWidth = 390;

  const mockEvent = { stopPropagation: () => {} };
  toggleStabDropdown('manage', mockEvent);

  globalThis.document.getElementById = origGetById;
  globalThis.window.innerWidth = 1280;

  assert.equal(
    dropdownProps['position'],
    'fixed',
    'At ≤430px, toggleStabDropdown must set position:fixed on the dropdown to escape overflow clipping by .sub-tabs',
  );
  assert.ok(
    dropdownProps['top'],
    'At ≤430px, toggleStabDropdown must set a top value on the dropdown',
  );
});

test('Bug-fix: closeAllStabDropdowns resets inline position styles set by mobile fix', async () => {
  const { closeAllStabDropdowns } = await import(
    '../../apps/dashboard/static/src/shell/tabs.js'
  );

  const removedProps = [];
  const mockDropdown = {
    style: {
      removeProperty: (name) => { removedProps.push(name); },
      setProperty: () => {},
    },
  };
  const mockGroup = {
    classList: {
      contains: () => true, // is open
      remove: () => {},
    },
    querySelector: (sel) => sel === '.stab-dropdown' ? mockDropdown : null,
  };

  globalThis.document.querySelectorAll = () => ({
    forEach: (fn) => fn(mockGroup),
  });

  closeAllStabDropdowns();

  // Reset querySelectorAll stub
  globalThis.document.querySelectorAll = () => ({ forEach: () => {} });

  assert.ok(
    removedProps.includes('position'),
    'closeAllStabDropdowns must remove the inline position style to restore default CSS',
  );
  assert.ok(
    removedProps.includes('top'),
    'closeAllStabDropdowns must remove the inline top style',
  );
});
