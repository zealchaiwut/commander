/* Behavioral tests for issue #2024: removal of 5 orphaned views.
 *
 * Tests that:
 * - switchTab() does not throw when called with removed view keys
 * - Removed init functions are absent from the codebase
 * - Live analytics calibration sub-tab is intact and functional
 * - No regressions in existing tab routing
 *
 * Run with: node --test tests/frontend/remove-dead-views-2024.test.mjs
 *
 * Covered ACs:
 *   AC2 — calling switchTab with removed keys doesn't throw
 *   AC3 — bundle clean (no references to removed init functions)
 *   AC4 — no regressions (existing tabs/dispatches work)
 *   Regression guard — live analytics calibration sub-tab survives
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// ── Setup: stub DOM/window for switchTab import ─────────────────────────────

// Create mock element factory for different element types
const createMockElement = () => ({
  classList: { add: () => {}, remove: () => {}, toggle: () => {} },
  setAttribute: () => {},
  focus: () => {},
  addEventListener: () => {},
  querySelector: () => createMockElement(),
  querySelectorAll: () => ({ forEach: () => {} }),
  id: "",
});

globalThis.document = {
  addEventListener: () => {},
  getElementById: (id) => {
    // Return a minimal mock element for all IDs, or null if not found
    if (!id) return null;
    return createMockElement();
  },
  querySelector: () => createMockElement(),
  querySelectorAll: () => ({ forEach: () => {} }),
  activeElement: null,
};

globalThis.window = {
  addEventListener: () => {},
  innerWidth: 800,
  history: {
    pushState: () => {},
  },
};

// Set up cross-module global variables that tabs.js expects via its /* global */ comment
globalThis._slug = "test-project";
globalThis._activeTab = "sprint-mgmt";
globalThis._cachedFullRepo = {};
globalThis._ticketsLoaded = false;
globalThis._sprintMgmtLoaded = false;
globalThis._smgmtLivePollId = null;
globalThis._smgmtLogPollId = null;
globalThis._statusRefreshId = null;
globalThis._deepLinkView = null;
globalThis._deepLinkFilter = null;
globalThis._arTickerId = null;
globalThis._arInterval = 0;
globalThis._ticketsRepo = null;
globalThis._deepLinkView = null;
globalThis._deepLinkFilter = null;

// Mock all the window functions tabs.js depends on
globalThis.loadSprintMgmt = () => Promise.resolve();
globalThis.loadTickets = () => {};
globalThis._smgmtArInit = () => {};
globalThis._smgmtArStartTicker = () => {};
globalThis.logsDestroy = () => {};
globalThis.deployTabDestroy = () => {};
globalThis.deployTabInit = () => {};
globalThis.metricsInit = () => {};
globalThis.roadmapInit = () => {};
globalThis.advInit = () => {};
globalThis.projSettingsInit = () => {};
globalThis.settingsInitValues = () => {};
globalThis.settingsPopulateRepos = () => {};
globalThis.globalSettingsLoad = () => {};
globalThis._bcInitTab = () => {};
globalThis._lpRenderBc = () => {};
globalThis.logsInit = () => {};
globalThis._deepLinkSprintSubView = () => false;
globalThis._applyDeepLinkSubView = () => {};
globalThis._smgmtSavedSubView = () => "board";
globalThis._smgmtShowSubView = () => {};
globalThis._histLoadLedger = () => {};
globalThis._globalSettingsLinkActive = () => {};
globalThis._evlState = { errorsOnly: false };
globalThis.parseUrl = () => ({ slug: "test-project", tab: "sprint-mgmt" });
globalThis.failuresInit = () => {};
// Kept functions for live analytics calibration
globalThis.anlShowTab = () => {};
globalThis._smgmtUpdateSelectionUI = () => {};
globalThis._bulkUpdateActionBar = () => {};
globalThis._smgmtUpdateToolbarTop = () => {};

const { switchTab } = await import("../../apps/dashboard/static/src/shell/tabs.js");

// ── AC2/AC4: switchTab with removed keys doesn't throw ──────────────────────

test("AC2: switchTab('notes') does not throw", () => {
  // notes was removed but should not crash the dispatcher
  assert.doesNotThrow(() => {
    switchTab("notes", false);
  }, "switchTab should handle removed 'notes' key gracefully");
});

test("AC2: switchTab('timeline') does not throw", () => {
  // timeline (ganttInit) was removed
  assert.doesNotThrow(() => {
    switchTab("timeline", false);
  }, "switchTab should handle removed 'timeline' key gracefully");
});

test("AC2: switchTab('compare') does not throw", () => {
  // compare (compareInit) was removed
  assert.doesNotThrow(() => {
    switchTab("compare", false);
  }, "switchTab should handle removed 'compare' key gracefully");
});

test("AC2: switchTab('est-vs-actual') does not throw", () => {
  // est-vs-actual (evaInit) was removed
  assert.doesNotThrow(() => {
    switchTab("est-vs-actual", false);
  }, "switchTab should handle removed 'est-vs-actual' key gracefully");
});

test("AC2: switchTab('calibration') does not throw", () => {
  // calibration top-level tab was removed (not the sub-tab)
  assert.doesNotThrow(() => {
    switchTab("calibration", false);
  }, "switchTab should handle removed 'calibration' key gracefully");
});

// ── AC3: Bundle clean — no removed init functions ───────────────────────────

test("AC3: Bundle does not contain notesInit function definition", () => {
  const bundlePath = resolve(
    __dirname,
    "../../apps/dashboard/static/dist/bundle.js"
  );
  const bundleContent = readFileSync(bundlePath, "utf8");

  // Assert the function is NOT defined in the bundle
  // Look for "function notesInit" or "const notesInit" or "notesInit =" etc.
  const hasNotesInit = /\bnotesInit\s*[:=]|\bfunction\s+notesInit\b/.test(bundleContent);
  assert.equal(
    hasNotesInit,
    false,
    "Bundle should not contain notesInit definition after removal"
  );
});

test("AC3: Bundle does not contain ganttInit function definition", () => {
  const bundlePath = resolve(
    __dirname,
    "../../apps/dashboard/static/dist/bundle.js"
  );
  const bundleContent = readFileSync(bundlePath, "utf8");

  const hasGanttInit = /\bganttInit\s*[:=]|\bfunction\s+ganttInit\b/.test(bundleContent);
  assert.equal(
    hasGanttInit,
    false,
    "Bundle should not contain ganttInit definition after removal"
  );
});

test("AC3: Bundle does not contain compareInit function definition", () => {
  const bundlePath = resolve(
    __dirname,
    "../../apps/dashboard/static/dist/bundle.js"
  );
  const bundleContent = readFileSync(bundlePath, "utf8");

  const hasCompareInit = /\bcompareInit\s*[:=]|\bfunction\s+compareInit\b/.test(bundleContent);
  assert.equal(
    hasCompareInit,
    false,
    "Bundle should not contain compareInit definition after removal"
  );
});

test("AC3: Bundle does not contain evaInit function definition", () => {
  const bundlePath = resolve(
    __dirname,
    "../../apps/dashboard/static/dist/bundle.js"
  );
  const bundleContent = readFileSync(bundlePath, "utf8");

  const hasEvaInit = /\bevaInit\s*[:=]|\bfunction\s+evaInit\b/.test(bundleContent);
  assert.equal(
    hasEvaInit,
    false,
    "Bundle should not contain evaInit definition after removal"
  );
});

test("AC3: Bundle does not contain calibInit function definition", () => {
  const bundlePath = resolve(
    __dirname,
    "../../apps/dashboard/static/dist/bundle.js"
  );
  const bundleContent = readFileSync(bundlePath, "utf8");

  const hasCalibInit = /\bcalibInit\s*[:=]|\bfunction\s+calibInit\b/.test(bundleContent);
  assert.equal(
    hasCalibInit,
    false,
    "Bundle should not contain calibInit definition after removal"
  );
});

// ── Regression Guard: Live analytics calibration sub-tab is intact ──────────

test("Regression: Bundle contains anlShowTab (live analytics calibration)", () => {
  const bundlePath = resolve(
    __dirname,
    "../../apps/dashboard/static/dist/bundle.js"
  );
  const bundleContent = readFileSync(bundlePath, "utf8");

  const hasAnlShowTab = /\banlShowTab\b/.test(bundleContent);
  assert.equal(
    hasAnlShowTab,
    true,
    "Bundle must still contain anlShowTab (live analytics calibration sub-tab)"
  );
});

test("Regression: project.html contains anlFetchCalibration (live analytics calibration)", () => {
  const projectHtmlPath = resolve(
    __dirname,
    "../../apps/dashboard/static/project.html"
  );
  const htmlContent = readFileSync(projectHtmlPath, "utf8");

  const hasAnlFetchCalibration = /\banlFetchCalibration\b/.test(htmlContent);
  assert.equal(
    hasAnlFetchCalibration,
    true,
    "project.html must still contain anlFetchCalibration (live analytics calibration sub-tab)"
  );
});

test("Regression: project.html contains anl-panel-calibration markup (live analytics)", () => {
  const projectHtmlPath = resolve(
    __dirname,
    "../../apps/dashboard/static/project.html"
  );
  const htmlContent = readFileSync(projectHtmlPath, "utf8");

  const hasAnlPanel = /\banl-panel-calibration\b/.test(htmlContent);
  assert.equal(
    hasAnlPanel,
    true,
    "project.html must still contain anl-panel-calibration for live analytics calibration sub-tab"
  );
});

// ── AC4: Regression — existing valid tabs still dispatch correctly ──────────

test("AC4: switchTab('sprint-mgmt') does not throw (existing tab)", () => {
  assert.doesNotThrow(() => {
    switchTab("sprint-mgmt", false);
  }, "switchTab should still handle valid 'sprint-mgmt' key");
});

test("AC4: switchTab('metrics') does not throw (existing tab)", () => {
  assert.doesNotThrow(() => {
    switchTab("metrics", false);
  }, "switchTab should still handle valid 'metrics' key");
});

test("AC4: switchTab('roadmap') does not throw (existing tab)", () => {
  assert.doesNotThrow(() => {
    switchTab("roadmap", false);
  }, "switchTab should still handle valid 'roadmap' key");
});
