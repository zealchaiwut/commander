/* Issue #1856: Analytics promoted to top-level tab.
 *
 * Behaviorally tests that computeRovingTabindex treats "metrics" as a
 * first-class top-level tab, not as a child of the manage dropdown group.
 *
 * Run with: node --test tests/frontend/tabs-analytics-top-level.test.mjs
 *
 * Covered ACs:
 *   AC2 — Analytics gets a top-level tab slot (metrics no longer in manage group)
 */

import test from "node:test";
import assert from "node:assert/strict";

// Stub DOM APIs before importing tabs.js
globalThis.document = {
  addEventListener: () => {},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => ({ forEach: () => {} }),
};
globalThis.window = { addEventListener: () => {} };

const { computeRovingTabindex } =
  await import("../../apps/dashboard/static/src/shell/tabs.js");

// ── AC2: metrics is a top-level tab, not a child of the manage group ─────────

test("AC2: metrics active → metrics gets tabIndex=0, not manage", () => {
  const map = computeRovingTabindex("metrics", false);
  assert.equal(
    map["metrics"],
    0,
    `metrics should own tabIndex=0 when it is the active tab, got: ${JSON.stringify(map)}`,
  );
  assert.equal(
    map["manage"],
    -1,
    "manage trigger must NOT receive tabIndex=0 when metrics is active",
  );
});

test("AC2: metrics is a key in the roving tabindex map", () => {
  const map = computeRovingTabindex("metrics", false);
  assert.ok(
    "metrics" in map,
    "metrics must appear as a top-level key in the roving tabindex map",
  );
});

test("AC2: switching to metrics gives metrics=0 and all others -1", () => {
  const map = computeRovingTabindex("metrics", false);
  const zeros = Object.entries(map).filter(([, v]) => v === 0);
  assert.equal(
    zeros.length,
    1,
    `Exactly one entry must be 0 when metrics is active, got: ${JSON.stringify(map)}`,
  );
  assert.equal(zeros[0][0], "metrics", "The zero entry must be 'metrics'");
});

test("AC2: switching from sprint-mgmt to metrics produces correct map each time", () => {
  const fromSprint = computeRovingTabindex("sprint-mgmt", false);
  assert.equal(fromSprint["sprint-mgmt"], 0);
  assert.equal(fromSprint["metrics"], -1);

  const toMetrics = computeRovingTabindex("metrics", false);
  assert.equal(toMetrics["metrics"], 0);
  assert.equal(toMetrics["sprint-mgmt"], -1);
});

// Verify Logs and Deploy still belong to manage (they were not promoted)
test("logs active → manage trigger still gets 0 (Logs remains in Manage)", () => {
  const map = computeRovingTabindex("logs", false);
  assert.equal(
    map["manage"],
    0,
    "manage should own tabIndex=0 when logs is active — Logs stays in Manage dropdown",
  );
  assert.equal(
    map["metrics"],
    -1,
    "metrics should be -1 when logs is active",
  );
});

test("deploy active → manage trigger still gets 0 (Deploy remains in Manage)", () => {
  const map = computeRovingTabindex("deploy", false);
  assert.equal(
    map["manage"],
    0,
    "manage should own tabIndex=0 when deploy is active — Deploy stays in Manage dropdown",
  );
  assert.equal(
    map["metrics"],
    -1,
    "metrics should be -1 when deploy is active",
  );
});
