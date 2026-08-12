/**
 * Behavioral load-test for issue #1991 — Logs tab dead-code removal (#1870).
 *
 * After #1870 removed logsToggleRun / _logsTicketStatsHtml / _logsIcaCostHtml /
 * _logsConnectLive and their helpers, this test verifies that calling logsInit()
 * at runtime does NOT throw a ReferenceError due to a dangling reference to any
 * of those removed symbols.
 *
 * Approach: extract the Logs Tab script section from project.html using its
 * comment markers, evaluate it in a vm context with minimal DOM stubs, then
 * call logsInit() and assert no error.
 *
 * Defense-in-depth — the source-text checks in test_1870__remove_dead_logs_toggle_run.py
 * confirm the symbols are absent from the source; this test confirms evaluating
 * the remaining code path at runtime does not reach any removed symbol.
 *
 * Run with: node --test tests/frontend/logs-tab-init-no-refererr-1991.test.mjs
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createContext, runInContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const PROJECT_HTML = resolve(
  __dirname,
  "../../apps/dashboard/static/project.html"
);

// ── Logs Tab section extraction ───────────────────────────────────────────────

function extractLogsSection() {
  const html = readFileSync(PROJECT_HTML, "utf8");
  const START = "// ── Logs Tab (issue #420)";
  const END   = "// ── Events Activity Log (issue #633)";
  const start = html.indexOf(START);
  const end   = html.indexOf(END);
  if (start === -1) throw new Error(`Start marker not found in project.html: ${START}`);
  if (end   === -1) throw new Error(`End marker not found in project.html: ${END}`);
  if (end <= start) throw new Error("Logs Tab section markers are out of order");
  return html.slice(start, end);
}

// ── Minimal vm context ────────────────────────────────────────────────────────
//
// All document.getElementById calls return null, causing early-returns in every
// DOM-touching log helper — no removed symbol is ever reached via that path.
// _projectData=null makes logsFetchRuns / logsSyncGitHub bail immediately.

function buildContext() {
  const noop      = () => {};
  const asyncNoop = async () => {};

  return createContext({
    // DOM stubs — null causes early-returns in all log helper DOM paths
    document: {
      getElementById:   () => null,
      querySelectorAll: () => ({ forEach: noop }),
    },
    window: {
      location: { search: "", origin: "http://localhost:8000" },
    },
    // Globals read/written by the logs section
    _evlState:    { sinceTs: null, events: null },
    _slug:        "test-project",
    _projectData: null,
    // External functions called directly from logsInit or its shallow call tree
    evlFetch:           noop,
    visibilityInterval: (_fn, _ms) => 42,
    // JS built-ins not automatically exposed inside vm contexts
    URLSearchParams: globalThis.URLSearchParams,
    clearTimeout:    globalThis.clearTimeout,
    setTimeout:      globalThis.setTimeout,
    clearInterval:   globalThis.clearInterval,
    setInterval:     globalThis.setInterval,
    Promise:         globalThis.Promise,
    Set:             globalThis.Set,
    // Defensive stubs for functions referenced inside the logs section that are
    // NOT called during the logsInit() path with the stubs above, but would throw
    // if the execution path unexpectedly reached them.
    fetch:             asyncNoop,
    escHtml:           (s) => String(s),
    colorizeLogLine:   (line) => String(line),
    evlRender:         noop,
    switchTab:         noop,
    _smgmtShowSubView: noop,
    _smgmtRepo:        () => null,
    console,
  });
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test("Logs Tab section is extractable from project.html with expected functions", () => {
  const section = extractLogsSection();
  assert.ok(section.length > 0, "extracted section must not be empty");
  assert.ok(
    section.includes("function logsInit"),
    "section must contain logsInit function"
  );
  assert.ok(
    section.includes("function logsDestroy"),
    "section must contain logsDestroy function"
  );
});

test("logsInit() does not throw after dead-code removal (#1870)", () => {
  // Evaluate the logs section — registers logsInit, logsDestroy, etc. in the context.
  // Function declarations land on the context object; const/let do not.
  const ctx = buildContext();
  runInContext(extractLogsSection(), ctx);

  assert.equal(typeof ctx.logsInit, "function", "logsInit must be callable after eval");

  // This is the core behavioral assertion: calling logsInit() must not throw.
  // Any ReferenceError here would indicate a dangling reference to a symbol
  // removed in #1870 (logsToggleRun, _logsTicketStatsHtml, etc.).
  assert.doesNotThrow(
    () => { ctx.logsInit(); },
    "logsInit() must not throw — no ReferenceError from removed dead code"
  );
});

test("logsDestroy() does not throw after dead-code removal (#1870)", () => {
  const ctx = buildContext();
  runInContext(extractLogsSection(), ctx);

  assert.equal(typeof ctx.logsDestroy, "function", "logsDestroy must be callable after eval");

  assert.doesNotThrow(
    () => { ctx.logsDestroy(); },
    "logsDestroy() must not throw — _logsDisconnectLive must not reference removed symbols"
  );
});

test("Removed symbols are not callable functions in the evaluated context (#1870)", () => {
  // Function declarations become properties of the vm context; if a removed
  // function were re-introduced, typeof ctx.fnName would be "function".
  const ctx = buildContext();
  runInContext(extractLogsSection(), ctx);

  assert.equal(typeof ctx.logsToggleRun, "undefined",
    "logsToggleRun must be absent — removed in #1870");
  assert.equal(typeof ctx._logsTicketStatsHtml, "undefined",
    "_logsTicketStatsHtml must be absent — removed in #1870");
  assert.equal(typeof ctx._logsIcaCostHtml, "undefined",
    "_logsIcaCostHtml must be absent — removed in #1870");
  assert.equal(typeof ctx._logsConnectLive, "undefined",
    "_logsConnectLive must be absent — removed in #1870");
  assert.equal(typeof ctx._logsFetchTicketStats, "undefined",
    "_logsFetchTicketStats must be absent — removed in #1870");
  assert.equal(typeof ctx._logsFetchIcaCost, "undefined",
    "_logsFetchIcaCost must be absent — removed in #1870");
});

test("Core logs infrastructure is intact after dead-code removal (#1870)", () => {
  const ctx = buildContext();
  runInContext(extractLogsSection(), ctx);

  assert.equal(typeof ctx.logsDestroy, "function",
    "logsDestroy must still exist (AC7)");
  assert.equal(typeof ctx._logsDisconnectLive, "function",
    "_logsDisconnectLive must still exist (AC7)");
  assert.equal(typeof ctx._logsEventsHtml, "function",
    "_logsEventsHtml must still exist (AC7)");
  assert.equal(typeof ctx.logsFetchRuns, "function",
    "logsFetchRuns must still exist (AC7)");
});
