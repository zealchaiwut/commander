import test from "node:test";
import assert from "node:assert/strict";

import {
  BOARD_OVERLAY_PA_ID,
  mountProgressActivity,
  patchProgressActivity,
  patchProgressActivityStep,
  appendProgressActivityLog,
  getProgressActivityPayload,
  unmountProgressActivity,
} from "../../apps/dashboard/static/src/progress-host.js";

function _withDocument(hostEl, fn) {
  const prev = globalThis.document;
  globalThis.document = {
    getElementById(id) {
      if (id === hostEl.id) return hostEl;
      return null;
    },
  };
  try {
    return fn();
  } finally {
    globalThis.document = prev;
  }
}

test("exports board overlay id constant", () => {
  assert.equal(BOARD_OVERLAY_PA_ID, "board-overlay-pa");
});

test("mount stores payload and renders markup", () => {
  const host = { id: "host-a", dataset: {}, hidden: true, innerHTML: "" };
  _withDocument(host, () => {
    mountProgressActivity("host-a", { mode: "bar", done: 1, total: 4, current: "Working" });
  });
  assert.equal(host.hidden, false);
  assert.ok(host.innerHTML.includes("pa-root"));
  assert.ok(host.innerHTML.includes("Working"));
  const snap = getProgressActivityPayload(host);
  assert.equal(snap.done, 1);
  assert.equal(snap.total, 4);
});

test("patch step updates existing step state", () => {
  const host = { id: "host-b", dataset: {}, hidden: true, innerHTML: "" };
  _withDocument(host, () => {
    mountProgressActivity(host, {
      mode: "stepper",
      steps: [{ key: "lock", label: "Lock", state: "pending", note: "" }],
    });
    patchProgressActivityStep(host, "lock", "checking", "Running");
  });
  const snap = getProgressActivityPayload(host);
  assert.equal(snap.steps[0].state, "checking");
  assert.equal(snap.steps[0].note, "Running");
});

test("append log extends log_tail and patch merges fields", () => {
  const host = { id: "host-c", dataset: {}, hidden: true, innerHTML: "" };
  _withDocument(host, () => {
    mountProgressActivity(host, { status: "running", mode: "bar", done: 0, total: 2 });
    appendProgressActivityLog(host, "Step started", "dispatch");
    patchProgressActivity(host, { done: 1, current: "Step 1 done" });
  });
  const snap = getProgressActivityPayload(host);
  assert.equal(snap.done, 1);
  assert.equal(snap.current, "Step 1 done");
  assert.equal(Array.isArray(snap.log_tail), true);
  assert.equal(snap.log_tail.length, 1);
  assert.equal(snap.log_tail[0].message, "Step started");
});

test("unmount clears host and payload", () => {
  const host = { id: "host-d", dataset: {}, hidden: false, innerHTML: "x" };
  _withDocument(host, () => {
    mountProgressActivity(host, { mode: "bar", done: 0, total: 1 });
    unmountProgressActivity(host);
  });
  assert.equal(host.hidden, true);
  assert.equal(host.innerHTML, "");
  assert.equal(getProgressActivityPayload(host), null);
});

test("append log preserves scroll position when user scrolled up", () => {
  const streams = new Map();
  const host = { id: "host-e", dataset: {}, hidden: true, innerHTML: "" };
  globalThis.document = {
    getElementById(id) {
      if (id === host.id) return host;
      if (id.startsWith("pa-log-stream-")) return streams.get(id) || null;
      return null;
    },
  };
  try {
    mountProgressActivity(host, {
      status: "running",
      mode: "bar",
      done: 0,
      total: 3,
      log_tail: [{ type: "dispatch", message: "line-1", timestamp: "00:00:00" }],
    }, { id: "overlay-pa" });
    const streamId = "pa-log-stream-overlay-pa";
    const stream = {
      scrollTop: 40,
      scrollHeight: 400,
      clientHeight: 100,
    };
    streams.set(streamId, stream);
    appendProgressActivityLog(host, "line-2", "dispatch", { id: "overlay-pa" });
    assert.equal(stream.scrollTop, 40, "scroll position preserved when not at bottom");
    stream.scrollTop = 300;
    appendProgressActivityLog(host, "line-3", "dispatch", { id: "overlay-pa" });
    assert.equal(stream.scrollTop, stream.scrollHeight, "auto-scroll when at bottom");
  } finally {
    globalThis.document = undefined;
  }
});
