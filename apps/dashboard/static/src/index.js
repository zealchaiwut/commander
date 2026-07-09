/* Bundle entry point (issue #796).
 *
 * esbuild bundles this into static/dist/bundle.js (IIFE format). The dashboard
 * is served from disk with no build step, so the emitted bundle is committed
 * and loaded directly by project.html.
 *
 * Modules: the log-panel tokenizer (#796) and the sprint-management board
 * (#797 — board render, drag/drop, run-controls, finish modal, rerun modal).
 * Follow-on tickets extract additional self-contained blocks into static/src/
 * and import them here.
 */
import {
  colorizeLogLine,
  escapeLogHtml,
  extractRaw,
  AGENT_NAMES,
} from "./logpanel.js";
import {
  renderProgressActivity,
  updateProgressActivityLog,
  patchProgressActivityInPlace,
  paToggleLog,
  injectProgressActivityCss,
} from "./progress-activity.js";
import {
  mountProgressActivity,
  patchProgressActivity,
  patchProgressActivityStep,
  unmountProgressActivity,
  appendProgressActivityLog,
  getProgressActivityPayload,
  BOARD_OVERLAY_PA_ID,
} from "./progress-host.js";
import {
  switchTab,
  toggleStabDropdown,
  closeAllStabDropdowns,
} from "./shell/tabs.js";
import { loadCommanderFeatures } from "./shell/features.js";
import {
  visibilityInterval,
  installVisibilityGuard,
} from "./shell/visibility.js";
import {
  snavNavStatusFetch,
  snavNavStatusCacheClear,
} from "./shell/snav-cache.js";
import {
  getEnvironment,
  getVersion,
  getSettings,
  invalidateSettings,
} from "./api.js";
import {
  GH_AUTH_POLL_INTERVAL_MS,
  startGhAuthPoll,
  stopGhAuthPoll,
} from "./device-login.js";
import {
  sprintCleanupPreview,
  sprintCleanupConfirm,
  testFilesCleanupPreview,
  testFilesCleanupConfirm,
  psStaleBranchesScan,
  psPruneMergedBranches,
  psCleanupLogClear,
  psCleanupModalConfirm,
  _psCleanupModalClose,
  _psCleanupPaneClose,
  _psCleanupPaneConfirm,
} from "./settings/cleanup.js";
import "./sprint-board/index.js";

// Preserve the historical global API. project.html and run_browser.html call
// these helpers on `window` (see static/AGENTS.md "What NOT to Touch"); the
// bundle keeps that contract intact so the page loads with no ReferenceError.
const root = typeof window !== "undefined" ? window : globalThis;
root.colorizeLogLine = colorizeLogLine;
root.escapeLogHtml = escapeLogHtml;
root.extractRaw = extractRaw;
root.AGENT_NAMES = AGENT_NAMES;

// ProgressActivity component (issue #928)
root.renderProgressActivity = renderProgressActivity;
root.updateProgressActivityLog = updateProgressActivityLog;
root.patchProgressActivityInPlace = patchProgressActivityInPlace;
root.paToggleLog = paToggleLog;
root.mountProgressActivity = mountProgressActivity;
root.patchProgressActivity = patchProgressActivity;
root.patchProgressActivityStep = patchProgressActivityStep;
root.unmountProgressActivity = unmountProgressActivity;
root.appendProgressActivityLog = appendProgressActivityLog;
root.getProgressActivityPayload = getProgressActivityPayload;
root.BOARD_OVERLAY_PA_ID = BOARD_OVERLAY_PA_ID;
injectProgressActivityCss();
root.switchTab = switchTab;
root.toggleStabDropdown = toggleStabDropdown;
root.closeAllStabDropdowns = closeAllStabDropdowns;
root.loadCommanderFeatures = loadCommanderFeatures;
root.visibilityInterval = visibilityInterval;
root.snavNavStatusFetch = snavNavStatusFetch;
root.snavNavStatusCacheClear = snavNavStatusCacheClear;
globalThis.switchTab = switchTab;
globalThis.toggleStabDropdown = toggleStabDropdown;
globalThis.closeAllStabDropdowns = closeAllStabDropdowns;
globalThis.loadCommanderFeatures = loadCommanderFeatures;
globalThis.visibilityInterval = visibilityInterval;
globalThis.snavNavStatusFetch = snavNavStatusFetch;
globalThis.snavNavStatusCacheClear = snavNavStatusCacheClear;
installVisibilityGuard();

// Project Settings cleanup (issue #735 / #808)
root.sprintCleanupPreview = sprintCleanupPreview;
root.sprintCleanupConfirm = sprintCleanupConfirm;
root.testFilesCleanupPreview = testFilesCleanupPreview;
root.testFilesCleanupConfirm = testFilesCleanupConfirm;
root.psStaleBranchesScan = psStaleBranchesScan;
root.psPruneMergedBranches = psPruneMergedBranches;
root.psCleanupLogClear = psCleanupLogClear;
root.psCleanupModalConfirm = psCleanupModalConfirm;
root._psCleanupModalClose = _psCleanupModalClose;
root._psCleanupPaneClose = _psCleanupPaneClose;
root._psCleanupPaneConfirm = _psCleanupPaneConfirm;
globalThis.sprintCleanupPreview = sprintCleanupPreview;
globalThis.sprintCleanupConfirm = sprintCleanupConfirm;
globalThis.testFilesCleanupPreview = testFilesCleanupPreview;
globalThis.testFilesCleanupConfirm = testFilesCleanupConfirm;
globalThis.psStaleBranchesScan = psStaleBranchesScan;
globalThis.psPruneMergedBranches = psPruneMergedBranches;
globalThis.psCleanupLogClear = psCleanupLogClear;
globalThis.psCleanupModalConfirm = psCleanupModalConfirm;
globalThis._psCleanupModalClose = _psCleanupModalClose;
globalThis._psCleanupPaneClose = _psCleanupPaneClose;
globalThis._psCleanupPaneConfirm = _psCleanupPaneConfirm;

// Stable-endpoint cache helpers (issue #1779)
root.getEnvironment = getEnvironment;
root.getVersion = getVersion;
root.getSettings = getSettings;
root.invalidateSettings = invalidateSettings;
globalThis.getEnvironment = getEnvironment;
globalThis.getVersion = getVersion;
globalThis.getSettings = getSettings;
globalThis.invalidateSettings = invalidateSettings;

// Device-login poll manager (issue #1779)
root.GH_AUTH_POLL_INTERVAL_MS = GH_AUTH_POLL_INTERVAL_MS;
root.startGhAuthPoll = startGhAuthPoll;
root.stopGhAuthPoll = stopGhAuthPoll;
globalThis.GH_AUTH_POLL_INTERVAL_MS = GH_AUTH_POLL_INTERVAL_MS;
globalThis.startGhAuthPoll = startGhAuthPoll;
globalThis.stopGhAuthPoll = stopGhAuthPoll;
