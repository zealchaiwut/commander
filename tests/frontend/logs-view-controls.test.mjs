/**
 * Frontend behavioral tests for issue #1858 — Logs raw view auto-select and
 * toolbar visibility gating.
 *
 * Tests pure helpers from logs-view-controls.js:
 *   AC1 — shouldAutoLoadRaw fires correctly when runs arrive and nothing is loaded
 *   AC2 — pickAutoSprintLabel respects explicit filter; auto-selects runs[0] only when empty
 *   AC3 — logsToolbarVisibility hides/shows the correct controls per view
 *
 * Run with: node --test tests/frontend/logs-view-controls.test.mjs
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  shouldAutoLoadRaw,
  pickAutoSprintLabel,
  logsToolbarVisibility,
} from "../../apps/dashboard/static/src/logs-view-controls.js";

// ── AC1: shouldAutoLoadRaw ────────────────────────────────────────────────────

test("AC1: triggers when raw view is active, rawLines is null, and runs exist", () => {
  assert.ok(shouldAutoLoadRaw("raw", null, 3));
});

test("AC1: does NOT trigger when viewMode is activity (not the raw view)", () => {
  assert.ok(!shouldAutoLoadRaw("activity", null, 3));
});

test("AC1: does NOT trigger when rawLines is already populated (content loaded)", () => {
  assert.ok(!shouldAutoLoadRaw("raw", ["line 1", "line 2"], 3));
});

test("AC1: does NOT trigger when rawLines is an empty array (finished but empty)", () => {
  assert.ok(!shouldAutoLoadRaw("raw", [], 3));
});

test("AC1: does NOT trigger when run list is empty (nothing to auto-select)", () => {
  assert.ok(!shouldAutoLoadRaw("raw", null, 0));
});

// ── AC2: pickAutoSprintLabel ──────────────────────────────────────────────────

test("AC2: returns filterSprint when a sprint filter is explicitly set", () => {
  const runs = [
    { sprint_label: "sprint-5" },
    { sprint_label: "sprint-4" },
  ];
  assert.equal(pickAutoSprintLabel(runs, "sprint-3"), "sprint-3");
});

test("AC2: auto-selects runs[0] when no filter is set (fills the empty case only)", () => {
  const runs = [
    { sprint_label: "sprint-114" },
    { sprint_label: "sprint-113" },
  ];
  assert.equal(pickAutoSprintLabel(runs, ""), "sprint-114");
});

test("AC2: returns null when no filter and runs list is empty", () => {
  assert.equal(pickAutoSprintLabel([], ""), null);
});

test("AC2: returns null when filterSprint is null and runs is empty", () => {
  assert.equal(pickAutoSprintLabel([], null), null);
});

test("AC2: filter takes precedence even when runs contains different labels", () => {
  const runs = [{ sprint_label: "sprint-latest" }];
  assert.equal(pickAutoSprintLabel(runs, "sprint-specific"), "sprint-specific");
});

// ── AC3: logsToolbarVisibility ────────────────────────────────────────────────

test("AC3: activity view — agent select is visible", () => {
  assert.ok(logsToolbarVisibility("activity").agentSelect);
});

test("AC3: activity view — source select is visible", () => {
  assert.ok(logsToolbarVisibility("activity").sourceSelect);
});

test("AC3: activity view — severity seg is visible", () => {
  assert.ok(logsToolbarVisibility("activity").severitySeg);
});

test("AC3: activity view — raw-level select is hidden", () => {
  assert.ok(!logsToolbarVisibility("activity").rawLevelSelect);
});

test("AC3: raw view — agent select is hidden", () => {
  assert.ok(!logsToolbarVisibility("raw").agentSelect);
});

test("AC3: raw view — source select is hidden", () => {
  assert.ok(!logsToolbarVisibility("raw").sourceSelect);
});

test("AC3: raw view — severity seg is hidden", () => {
  assert.ok(!logsToolbarVisibility("raw").severitySeg);
});

test("AC3: raw view — raw-level select is visible", () => {
  assert.ok(logsToolbarVisibility("raw").rawLevelSelect);
});

test("AC3: sprint select is visible in all views", () => {
  assert.ok(logsToolbarVisibility("activity").sprintSelect);
  assert.ok(logsToolbarVisibility("raw").sprintSelect);
});

test("AC3: search input is visible in all views", () => {
  assert.ok(logsToolbarVisibility("activity").searchInput);
  assert.ok(logsToolbarVisibility("raw").searchInput);
});
