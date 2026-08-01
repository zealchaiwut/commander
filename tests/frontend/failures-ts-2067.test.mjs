/**
 * AC5 (issue #2067): Frontend timestamp normalization for the Failures table.
 *
 * _fmtTs() must treat bare ISO strings (no offset) as UTC, not local time.
 * We test via the exported _normalizeTs helper that _fmtTs delegates to.
 *
 * Run with: node --test tests/frontend/failures-ts-2067.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// ── DOM stubs required at failures.js import time ────────────────────────────

globalThis.document = {
  getElementById: () => null,
  querySelector: () => null,
  addEventListener: () => {},
  querySelectorAll: () => [],
};
if (typeof globalThis.window === 'undefined') globalThis.window = globalThis;
globalThis.history = { pushState: () => {} };
globalThis._projectData = null;

import {
  _normalizeTs,
} from '../../apps/dashboard/static/src/failures/failures.js';


// ── AC5: _normalizeTs appends Z to bare ISO strings ──────────────────────────

test('_normalizeTs: bare ISO string (no offset) gets Z appended', () => {
  const result = _normalizeTs('2026-01-15T14:30:00');
  assert.equal(result, '2026-01-15T14:30:00Z');
});

test('_normalizeTs: ISO string with microseconds (legacy isoformat()) gets Z appended', () => {
  const result = _normalizeTs('2026-01-15T14:30:00.123456');
  assert.equal(result, '2026-01-15T14:30:00.123456Z');
});

test('_normalizeTs: ISO string already ending in Z is unchanged', () => {
  const result = _normalizeTs('2026-01-15T14:30:00Z');
  assert.equal(result, '2026-01-15T14:30:00Z');
});

test('_normalizeTs: ISO string with explicit +00:00 offset is unchanged', () => {
  const result = _normalizeTs('2026-01-15T14:30:00+00:00');
  assert.equal(result, '2026-01-15T14:30:00+00:00');
});


// ── AC5: normalizing bare ISO ensures UTC interpretation ─────────────────────

test('_normalizeTs: bare ISO and Z-suffixed form parse to same UTC instant', () => {
  const bare = _normalizeTs('2026-01-15T14:30:00');
  const withZ = _normalizeTs('2026-01-15T14:30:00Z');
  assert.equal(
    new Date(bare).getTime(),
    new Date(withZ).getTime(),
    `bare ISO normalized form must parse to same instant as Z form`
  );
});

test('_normalizeTs: isoformat() microsecond form normalizes to same UTC instant', () => {
  const legacy = _normalizeTs('2026-01-15T14:30:00.000000');
  const canonical = _normalizeTs('2026-01-15T14:30:00Z');
  assert.equal(
    new Date(legacy).getTime(),
    new Date(canonical).getTime(),
    `microsecond legacy form must parse to same UTC instant`
  );
});
