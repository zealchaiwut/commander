/**
 * Behavioral tests for issue #2072: Issues tab loads once and never refreshes.
 *
 * AC1: switchTab("tickets") calls loadTickets on every activation (refetch-on-activation).
 * AC2: Open-ticket count cannot show stale data (satisfied by AC1 — always refetches).
 * AC3: All three error branches render a working retry control.
 * AC4: Behavioral tests per CLAUDE.md #1746:
 *   - activating tab twice issues two loadTickets calls
 *   - error state renders a retry control that refetches when activated
 *
 * Run with: node --test tests/frontend/tickets-refetch-2072.test.mjs
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dir = fileURLToPath(new URL(".", import.meta.url));
const PROJECT_HTML = resolve(__dir, "../../apps/dashboard/static/project.html");
const html = readFileSync(PROJECT_HTML, "utf-8");

// ── Helper: extract named function from HTML source (brace-depth counting) ────

function extractFunction(source, fnName) {
  const sig = `function ${fnName}(`;
  const start = source.indexOf(sig);
  if (start === -1) return null;
  const prefixStart = source.lastIndexOf("\n", start) + 1;
  const braceOpen = source.indexOf("{", start);
  if (braceOpen === -1) return null;
  let depth = 0;
  let i = braceOpen;
  while (i < source.length) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") {
      depth--;
      if (depth === 0) return source.slice(prefixStart, i + 1);
    }
    i++;
  }
  return null;
}

// ── AC1 + AC4: switchTab("tickets") refetches on every activation ─────────────

// Stub globals before import so tabs.js module-level side-effects don't throw.
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

const _noop = () => {};
globalThis._activeTab = "sprint-mgmt";
globalThis._cachedFullRepo = {};
globalThis._ticketsLoaded = false;
globalThis._sprintMgmtLoaded = false;
globalThis._smgmtLivePollId = null;
globalThis._smgmtLogPollId = null;
globalThis._arTickerId = null;
globalThis._arInterval = 0;
globalThis._slug = "test-slug";
globalThis.deployTabDestroy = _noop;
globalThis.deployTabInit = _noop;
globalThis.loadSprintMgmt = () => Promise.resolve();
globalThis._smgmtArInit = _noop;
globalThis._smgmtArStartTicker = _noop;
globalThis.roadmapInit = _noop;
globalThis.advInit = _noop;
globalThis.projSettingsInit = _noop;
globalThis.settingsInitValues = _noop;
globalThis.settingsPopulateRepos = _noop;
globalThis.globalSettingsLoad = _noop;
globalThis._bcInitTab = _noop;
globalThis._lpRenderBc = _noop;
globalThis._deepLinkSprintSubView = () => false;
globalThis._applyDeepLinkSubView = _noop;
globalThis._smgmtSavedSubView = () => null;
globalThis._smgmtShowSubView = _noop;
globalThis._histLoadLedger = _noop;
globalThis._globalSettingsLinkActive = _noop;
globalThis._evlState = { errorsOnly: false };
globalThis.parseUrl = () => ({});
globalThis.failuresInit = _noop;
globalThis.brainInit = _noop;
globalThis._smgmtUpdateSelectionUI = _noop;
globalThis._bulkUpdateActionBar = _noop;
globalThis._smgmtUpdateToolbarTop = _noop;
globalThis.loadTickets = _noop;

const { switchTab } =
  await import("../../apps/dashboard/static/src/shell/tabs.js");

test('AC1: switchTab("tickets") calls loadTickets on every activation, not just first', () => {
  let callCount = 0;
  globalThis.loadTickets = () => {
    callCount++;
  };

  globalThis._activeTab = "sprint-mgmt";
  switchTab("tickets", false);
  assert.equal(callCount, 1, "loadTickets must be called on first activation");

  globalThis._activeTab = "sprint-mgmt";
  switchTab("tickets", false);
  assert.equal(
    callCount,
    2,
    "loadTickets must be called again on second activation (refetch-on-activation, not one-shot gate)",
  );
});

test("AC4: switching away then back to tickets triggers another fetch", () => {
  let callCount = 0;
  globalThis.loadTickets = () => {
    callCount++;
  };

  globalThis._activeTab = "sprint-mgmt";
  switchTab("tickets", false);
  assert.equal(callCount, 1);

  switchTab("failures", false);
  switchTab("tickets", false);
  assert.equal(
    callCount,
    2,
    "loadTickets must be called again after navigating away and back",
  );
});

// ── AC3 + AC4: Error branches render a retry control that refetches ───────────

const loadTicketsFnSrc = extractFunction(html, "loadTickets");

test("AC3: loadTickets is defined in project.html", () => {
  assert.ok(
    loadTicketsFnSrc !== null,
    "loadTickets() must be defined in project.html",
  );
});

/**
 * Run loadTickets() in a sandboxed scope with controlled fetch/document stubs.
 * Returns the container element so tests can inspect innerHTML.
 */
async function runLoadTickets(fetchStub, cachedFullRepo = {}, slug = "myslug") {
  const container = { innerHTML: "" };
  const mockDocument = {
    getElementById: (id) => (id === "tickets-content" ? container : null),
  };
  const mockRenderTickets = _noop;
  const mockLoadEstimates = () => Promise.resolve();
  const runner = new Function(
    "fetch",
    "document",
    "_cachedFullRepo",
    "_slug",
    "renderTickets",
    "_ticketsLoadEstimates",
    `let _ticketsRepo = null;\n${loadTicketsFnSrc}\n  return loadTickets();`,
  );
  await runner(
    fetchStub,
    mockDocument,
    cachedFullRepo,
    slug,
    mockRenderTickets,
    mockLoadEstimates,
  );
  return container;
}

test("AC3: project-info fetch failure renders retry button", async () => {
  const container = await runLoadTickets(
    async () => {
      throw new Error("network error");
    },
    {}, // no cached repo → will try /api/projects
    "no-such-slug",
  );
  assert.ok(
    container.innerHTML.includes("Failed to load project info"),
    'must show "Failed to load project info" message',
  );
  assert.ok(
    container.innerHTML.includes("<button"),
    "error state must render a <button> retry control",
  );
  assert.ok(
    container.innerHTML.includes("loadTickets"),
    "retry button must invoke loadTickets()",
  );
});

test("AC3: project-not-found renders retry button", async () => {
  // fetch returns empty projects list → no slug match → "Project not found."
  const container = await runLoadTickets(
    async (url) => ({
      ok: true,
      json: async () => ({ projects: [{ repo: "owner/other-project" }] }),
    }),
    {},
    "no-such-slug",
  );
  assert.ok(
    container.innerHTML.includes("Project not found"),
    'must show "Project not found" message',
  );
  assert.ok(
    container.innerHTML.includes("<button"),
    "project-not-found state must render a <button> retry control",
  );
  assert.ok(
    container.innerHTML.includes("loadTickets"),
    "retry button must invoke loadTickets()",
  );
});

test("AC3: tickets fetch failure renders retry button", async () => {
  // First fetch (/api/projects) succeeds, second (/api/project-details) fails
  let callCount = 0;
  const container = await runLoadTickets(
    async (url) => {
      callCount++;
      if (url.includes("/api/projects")) {
        return {
          ok: true,
          json: async () => ({ projects: [{ repo: "owner/myslug" }] }),
        };
      }
      throw new Error("tickets fetch failed");
    },
    {},
    "myslug",
  );
  assert.ok(
    container.innerHTML.includes("Failed to load tickets"),
    'must show "Failed to load tickets" message',
  );
  assert.ok(
    container.innerHTML.includes("<button"),
    "tickets error state must render a <button> retry control",
  );
  assert.ok(
    container.innerHTML.includes("loadTickets"),
    "retry button must invoke loadTickets()",
  );
});
