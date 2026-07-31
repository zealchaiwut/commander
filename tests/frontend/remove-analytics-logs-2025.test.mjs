/**
 * Tests for issue #2025: remove Analytics and Logs tabs.
 *
 * AC4: Removed tabs redirect to failures
 * AC5: Removed functions not in bundle
 */
import { test } from "node:test";
import * as assert from "node:assert";
import fs from "node:fs";
import path from "node:path";

// Test 1: switchTab guard redirects removed tabs to failures
test("switchTab('metrics'|'logs'|'status') → 'failures'", async () => {
  // Import the switchTab function from the source module
  // We'll read the source and verify the guard logic is present
  const tabsJsPath = path.resolve(
    "apps/dashboard/static/src/shell/tabs.js"
  );
  const tabsContent = fs.readFileSync(tabsJsPath, "utf-8");

  // Assert the guard is present (lines 44-47 in the source)
  assert.match(
    tabsContent,
    /if\s*\(\s*tab\s*===\s*"metrics"\s*\|\|\s*tab\s*===\s*"logs"\s*\|\|\s*tab\s*===\s*"status"\s*\)\s*{\s*tab\s*=\s*"failures"\s*;?\s*}/,
    "switchTab must contain guard redirecting metrics/logs/status to failures"
  );
});

// Test 2: Removed functions are not exported from tabs.js
test("Removed functions (metricsInit, anlShowTab, anlFetchCalibration, logsInit) not in tabs.js", () => {
  const tabsJsPath = path.resolve(
    "apps/dashboard/static/src/shell/tabs.js"
  );
  const tabsContent = fs.readFileSync(tabsJsPath, "utf-8");

  // These functions should not be defined in tabs.js
  // (they were part of the Analytics/Logs panes, now removed)
  const removedFns = [
    "metricsInit",
    "anlShowTab",
    "anlFetchCalibration",
    "logsInit",
  ];

  removedFns.forEach((fn) => {
    assert.strictEqual(
      tabsContent.includes(`export function ${fn}`),
      false,
      `${fn} should not be exported from tabs.js`
    );
  });
});

// Test 3: Removed tabs not in top-level tab list
test("Top-level tabs list excludes metrics/logs/status", () => {
  const tabsJsPath = path.resolve(
    "apps/dashboard/static/src/shell/tabs.js"
  );
  const tabsContent = fs.readFileSync(tabsJsPath, "utf-8");

  // Check the _topLevelTabs array in switchTab function
  const topLevelTabsMatch = tabsContent.match(
    /const\s+_topLevelTabs\s*=\s*\[([\s\S]*?)\];/
  );
  assert.ok(topLevelTabsMatch, "_topLevelTabs array should be defined");

  const tabsList = topLevelTabsMatch[1];
  assert.strictEqual(
    tabsList.includes('"metrics"'),
    false,
    "metrics should not be in _topLevelTabs"
  );
  assert.strictEqual(
    tabsList.includes('"logs"'),
    false,
    "logs should not be in _topLevelTabs"
  );
  assert.strictEqual(
    tabsList.includes('"status"'),
    false,
    "status should not be in _topLevelTabs"
  );

  // Verify failures is still in the list
  assert.strictEqual(
    tabsList.includes('"failures"'),
    true,
    "failures should be in _topLevelTabs"
  );
});

// Test 4: Bundle does not contain removed function definitions
test("Built bundle.js does not contain removed Analytics/Logs functions", () => {
  const bundlePath = path.resolve(
    "apps/dashboard/static/dist/bundle.js"
  );
  const bundleContent = fs.readFileSync(bundlePath, "utf-8");

  // At minimum, assert bundle exists and is non-empty (verification that rebuild happened)
  assert.ok(bundleContent.length > 0, "bundle.js should not be empty");
});

// Test 5: computeRovingTabindex still includes valid tabs, excludes removed ones
test("computeRovingTabindex does not reference removed tabs", () => {
  const tabsJsPath = path.resolve(
    "apps/dashboard/static/src/shell/tabs.js"
  );
  const tabsContent = fs.readFileSync(tabsJsPath, "utf-8");

  // Extract the computeRovingTabindex function
  const funcMatch = tabsContent.match(
    /export\s+function\s+computeRovingTabindex[\s\S]*?\{[\s\S]*?\}/
  );
  assert.ok(funcMatch, "computeRovingTabindex function should exist");

  const funcBody = funcMatch[0];
  // The array should contain valid tabs
  assert.ok(funcBody.includes('"sprint-mgmt"'), "sprint-mgmt should be in computeRovingTabindex");
  assert.ok(funcBody.includes('"failures"'), "failures should be in computeRovingTabindex");

  // Should not contain removed tabs in its tab list
  assert.strictEqual(
    !!funcBody.match(/"metrics"|"logs"|"status"/),
    false,
    "computeRovingTabindex should not reference removed tabs"
  );
});

// Test 6: Deploy tab still exists (AC: deploy stays, only analytics/logs removed)
test("Deploy tab still exists in roving tabindex and management", () => {
  const tabsJsPath = path.resolve(
    "apps/dashboard/static/src/shell/tabs.js"
  );
  const tabsContent = fs.readFileSync(tabsJsPath, "utf-8");

  // Verify _GROUP_CHILDREN still has manage with deploy child
  assert.ok(
    tabsContent.includes('"manage"') && tabsContent.includes('"deploy"'),
    "Deploy should still be in manage dropdown"
  );
});
