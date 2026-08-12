/**
 * Frontend unit tests for issue #1857 — Logs error visibility.
 *
 * Tests the pure helper functions from logs/error-badge.js:
 *   AC1 — logsCountNewErrors counts error events since last visit
 *   AC2 — evlIsErrorEvent correctly identifies ticket_failed / agent_finished errors
 *   AC3 — buildEvlFetchUrl uses ?since= when sinceTs is provided; falls back to
 *          flat ?limit=200 when sinceTs is null (first visit)
 *
 * Run with: node --test tests/frontend/logs-error-badge.test.mjs
 */

import test from "node:test";
import assert from "node:assert/strict";

// Stub localStorage so logsReadLastVisit / logsWriteLastVisit work in Node.
const _store = {};
globalThis.localStorage = {
  getItem: (k) => (k in _store ? _store[k] : null),
  setItem: (k, v) => {
    _store[k] = v;
  },
  removeItem: (k) => {
    delete _store[k];
  },
};

import {
  logsErrorBadgeKey,
  logsReadLastVisit,
  logsWriteLastVisit,
  evlIsErrorEvent,
  logsCountNewErrors,
  buildEvlFetchUrl,
} from "../../apps/dashboard/static/src/logs-error-badge.js";

// ── AC2: evlIsErrorEvent — event classification ───────────────────────────────

test("AC2: ticket_failed is an error event", () => {
  assert.ok(evlIsErrorEvent({ type: "ticket_failed" }));
});

test("AC2: agent_finished with status=error is an error event", () => {
  assert.ok(
    evlIsErrorEvent({ type: "agent_finished", detail: { status: "error" } }),
  );
});

test("AC2: agent_finished with status=timed_out is an error event", () => {
  assert.ok(
    evlIsErrorEvent({
      type: "agent_finished",
      detail: { status: "timed_out" },
    }),
  );
});

test("AC2: agent_finished with status=done is NOT an error event", () => {
  assert.ok(
    !evlIsErrorEvent({ type: "agent_finished", detail: { status: "done" } }),
  );
});

test("AC2: sprint_run is NOT an error event", () => {
  assert.ok(!evlIsErrorEvent({ type: "sprint_run" }));
});

test("AC2: agent_finished with no detail is NOT an error event", () => {
  assert.ok(!evlIsErrorEvent({ type: "agent_finished" }));
});

test("AC2: empty event is NOT an error event", () => {
  assert.ok(!evlIsErrorEvent({}));
});

// ── AC1: logsCountNewErrors — counting ───────────────────────────────────────

test("AC1: counts zero errors in an empty event list", () => {
  assert.equal(logsCountNewErrors([]), 0);
});

test("AC1: counts one ticket_failed as one error", () => {
  const events = [{ type: "ticket_failed" }, { type: "sprint_run" }];
  assert.equal(logsCountNewErrors(events), 1);
});

test("AC1: counts multiple errors correctly", () => {
  const events = [
    { type: "ticket_failed" },
    { type: "agent_finished", detail: { status: "error" } },
    { type: "agent_finished", detail: { status: "timed_out" } },
    { type: "agent_finished", detail: { status: "done" } },
    { type: "sprint_run" },
  ];
  assert.equal(logsCountNewErrors(events), 3);
});

test("AC1: returns zero when all events are non-error", () => {
  const events = [
    { type: "sprint_run" },
    { type: "agent_finished", detail: { status: "done" } },
    { type: "label_added" },
  ];
  assert.equal(logsCountNewErrors(events), 0);
});

// ── AC3: buildEvlFetchUrl — since= param ──────────────────────────────────────

test("AC3: URL includes since= when sinceTs is provided", () => {
  const ts = "2026-07-01T12:00:00.000Z";
  const url = buildEvlFetchUrl("myrepo", ts);
  assert.ok(url.includes("since="), `URL must contain since=, got: ${url}`);
  assert.ok(
    url.includes(encodeURIComponent(ts)),
    "URL must include encoded timestamp",
  );
});

test("AC3: URL falls back to limit=200 when sinceTs is null (first visit)", () => {
  const url = buildEvlFetchUrl("myrepo", null);
  assert.ok(
    !url.includes("since="),
    `URL must NOT contain since= on first visit, got: ${url}`,
  );
  assert.ok(
    url.includes("limit=200"),
    `URL must contain limit=200, got: ${url}`,
  );
});

test("AC3: URL falls back to limit=200 when sinceTs is empty string", () => {
  const url = buildEvlFetchUrl("myrepo", "");
  assert.ok(
    !url.includes("since="),
    `URL must NOT contain since= for empty string, got: ${url}`,
  );
  assert.ok(
    url.includes("limit=200"),
    `URL must contain limit=200, got: ${url}`,
  );
});

test("AC3: URL targets the correct project slug", () => {
  const url = buildEvlFetchUrl("commander", "2026-01-01T00:00:00Z");
  assert.ok(
    url.includes("/api/projects/commander/events"),
    `URL must target /api/projects/commander/events, got: ${url}`,
  );
});

test("AC3: slug is URI-encoded in the URL", () => {
  const url = buildEvlFetchUrl("my project", "2026-01-01T00:00:00Z");
  assert.ok(
    url.includes("my%20project"),
    `Slug must be URI-encoded, got: ${url}`,
  );
});

// ── localStorage helpers ──────────────────────────────────────────────────────

test("logsErrorBadgeKey returns per-slug key", () => {
  assert.equal(
    logsErrorBadgeKey("commander"),
    "commander_logs_last_visit_commander",
  );
  assert.equal(
    logsErrorBadgeKey("perf-coach"),
    "commander_logs_last_visit_perf-coach",
  );
});

test("logsReadLastVisit returns null when no timestamp stored", () => {
  assert.equal(logsReadLastVisit("fresh-slug"), null);
});

test("logsWriteLastVisit stores ISO timestamp; logsReadLastVisit retrieves it", () => {
  logsWriteLastVisit("test-slug");
  const stored = logsReadLastVisit("test-slug");
  assert.ok(stored !== null, "stored value must not be null");
  assert.ok(
    !isNaN(Date.parse(stored)),
    `stored value must be a valid ISO date, got: ${stored}`,
  );
});

test("logsWriteLastVisit writes current time (within 5 seconds)", () => {
  const before = Date.now();
  logsWriteLastVisit("timing-slug");
  const after = Date.now();
  const stored = logsReadLastVisit("timing-slug");
  const storedMs = Date.parse(stored);
  assert.ok(storedMs >= before - 100, "stored time must not be before write");
  assert.ok(storedMs <= after + 100, "stored time must not be after write");
});
