/**
 * Behavioral tests for the preflight-fix SSE frame parser (issue #1746 AC1).
 *
 * Feeds real `event: X\ndata: {json}` frames through _parsePfSSEFrame and
 * asserts parsed values. This would have caught the #982 regression where the
 * regex lacked "data:" and every frame's JSON parse silently failed.
 *
 * Run with: node --test tests/frontend/sse-parser.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── Minimal stubs required at run-controls.js import time ────────────────────
if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}
// Stubs for globals referenced in function bodies (not called during import)
const _noop = () => {};
globalThis._pfCurrentLabel = null;
globalThis._pfCurrentRepo = null;
globalThis._pfStepState = _noop;
globalThis._pfShrinkWarnings = _noop;
globalThis._pfStepperSummary = _noop;
globalThis._pfAutofixPending = false;
globalThis._pfUpdateConfirmBtn = _noop;
globalThis.smgmtRunSprint = _noop;
globalThis._smgmtRunningLabels = new Set();
globalThis._slug = 'test';

import { _parsePfSSEFrame } from '../../apps/dashboard/static/src/sprint-board/run-controls.js';


// ── AC1: SSE frame parser parses real event frames ───────────────────────────

test('_parsePfSSEFrame: parses a log frame with string JSON', () => {
  const frame = 'event: log\ndata: "Generating AC for ticket #42"';
  const result = _parsePfSSEFrame(frame);
  assert.ok(result !== null, 'must parse a valid log frame');
  assert.equal(result.type, 'log');
  assert.equal(result.raw, '"Generating AC for ticket #42"');
  // Verify raw is valid JSON that decodes to the expected string
  assert.equal(JSON.parse(result.raw), 'Generating AC for ticket #42');
});

test('_parsePfSSEFrame: parses a log frame with object JSON', () => {
  const frame = 'event: log\ndata: {"message":"Estimating ticket #10"}';
  const result = _parsePfSSEFrame(frame);
  assert.ok(result !== null);
  assert.equal(result.type, 'log');
  const d = JSON.parse(result.raw);
  assert.equal(d.message, 'Estimating ticket #10');
});

test('_parsePfSSEFrame: parses a done frame and raw decodes to summary counts', () => {
  const payload = JSON.stringify({ filled: 3, estimated: 2, errors: [] });
  const frame = `event: done\ndata: ${payload}`;
  const result = _parsePfSSEFrame(frame);
  assert.ok(result !== null, 'must parse a valid done frame');
  assert.equal(result.type, 'done');
  const d = JSON.parse(result.raw);
  assert.equal(d.filled, 3);
  assert.equal(d.estimated, 2);
  assert.deepEqual(d.errors, []);
});

test('_parsePfSSEFrame: parses a done frame with errors array', () => {
  const payload = JSON.stringify({ filled: 1, estimated: 0, errors: ['#5 failed'] });
  const frame = `event: done\ndata: ${payload}`;
  const result = _parsePfSSEFrame(frame);
  assert.ok(result !== null);
  const d = JSON.parse(result.raw);
  assert.equal(d.errors.length, 1);
  assert.equal(d.errors[0], '#5 failed');
});

test('_parsePfSSEFrame: returns null for a frame missing the data: prefix', () => {
  // This is the broken regex pattern that caused the #982 regression.
  // A frame that lacks "data:" should NOT silently match and produce garbage JSON.
  const broken = 'event: done\n{"filled":3,"estimated":2,"errors":[]}';
  const result = _parsePfSSEFrame(broken);
  // The correct regex requires "data:" — without it the frame is invalid.
  assert.equal(result, null, 'frame without data: prefix must not parse');
});

test('_parsePfSSEFrame: returns null for an empty string', () => {
  assert.equal(_parsePfSSEFrame(''), null);
});

test('_parsePfSSEFrame: returns null for a data-only line (no event)', () => {
  assert.equal(_parsePfSSEFrame('data: {"filled":1}'), null);
});

test('_parsePfSSEFrame: returns null for an event-only line (no data)', () => {
  assert.equal(_parsePfSSEFrame('event: done'), null);
});

test('_parsePfSSEFrame: handles extra whitespace after event: and data:', () => {
  const frame = 'event:  log\ndata:  "hello"';
  const result = _parsePfSSEFrame(frame);
  assert.ok(result !== null, 'must parse frame with extra whitespace');
  assert.equal(result.type, 'log');
  assert.equal(JSON.parse(result.raw), 'hello');
});

test('_parsePfSSEFrame: parses unknown event types (forward compat)', () => {
  const frame = 'event: progress\ndata: {"pct":50}';
  const result = _parsePfSSEFrame(frame);
  assert.ok(result !== null);
  assert.equal(result.type, 'progress');
  assert.equal(JSON.parse(result.raw).pct, 50);
});

// ── Regression: verify broken regex would have failed ────────────────────────

test('regression guard: broken regex (no data: prefix) silently corrupts JSON', () => {
  // Simulate the pre-#1740 broken regex: /^event:\s*(\S+)\n\s*([\s\S]*)$/
  // With this regex, the data: prefix is consumed by \s* and raw starts with "data:{"
  const brokenMatch = (part) => {
    const m = part.match(/^event:\s*(\S+)\n\s*([\s\S]*)$/);
    if (!m) return null;
    return { type: m[1], raw: m[2] };
  };

  const payload = JSON.stringify({ filled: 3, estimated: 2, errors: [] });
  const frame = `event: done\ndata: ${payload}`;
  const broken = brokenMatch(frame);

  // The broken regex does match — but raw starts with "data: " making JSON.parse fail
  assert.ok(broken !== null, 'broken regex matches the frame');
  let parsedOk = true;
  try { JSON.parse(broken.raw); } catch (_) { parsedOk = false; }
  assert.equal(parsedOk, false,
    'broken regex raw value starts with "data: " so JSON.parse fails — this is the #982 regression');

  // The correct parser must succeed
  const correct = _parsePfSSEFrame(frame);
  assert.ok(correct !== null);
  const d = JSON.parse(correct.raw);
  assert.equal(d.filled, 3, 'correct parser extracts filled count');
});
