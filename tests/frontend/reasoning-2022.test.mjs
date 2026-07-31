/**
 * Behavioral tests for issue #2022: Reasoning view endpoint and fetch helper.
 *
 * AC4: fetchRunReasoning(runId) is an async function that:
 *   - GETs /api/runs/{id}/reasoning
 *   - On 200: resolves with {final_message, transcript_path, log_tail}
 *   - On non-200: throws Error('HTTP N') where N is the status code
 *
 * Run with: node --test tests/frontend/reasoning-2022.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';

// Minimal global stubs needed at import time
if (typeof globalThis.document === 'undefined') {
  globalThis.document = { getElementById: () => null };
}
if (typeof globalThis.window === 'undefined') {
  globalThis.window = globalThis;
}

// Record all fetch calls for inspection
let _fetchCalls = [];

// Import the function under test
import { fetchRunReasoning } from '../../apps/dashboard/static/src/reasoning.js';

// ── AC4: fetchRunReasoning behavior tests ────────────────────────────────────

test('AC4.1: fetchRunReasoning GETs /api/runs/{id}/reasoning', async () => {
  _fetchCalls = [];

  globalThis.fetch = async (url) => {
    _fetchCalls.push(url);
    return {
      ok: true,
      json: async () => ({
        final_message: 'Test message',
        transcript_path: '/path/to/transcript.jsonl',
        log_tail: 'Test log tail',
      }),
    };
  };

  const result = await fetchRunReasoning(123);

  assert.equal(_fetchCalls.length, 1, 'fetch should be called exactly once');
  assert.ok(
    _fetchCalls[0].includes('/api/runs/123/reasoning'),
    `Expected /api/runs/123/reasoning in URL, got ${_fetchCalls[0]}`
  );
  assert.deepEqual(result, {
    final_message: 'Test message',
    transcript_path: '/path/to/transcript.jsonl',
    log_tail: 'Test log tail',
  });
});

test('AC4.2: fetchRunReasoning returns response JSON on 200', async () => {
  const expectedData = {
    final_message: 'Agent completed task X',
    transcript_path: '/tmp/run_123.jsonl',
    log_tail: 'Last log lines...',
  };

  globalThis.fetch = async () => ({
    ok: true,
    json: async () => expectedData,
  });

  const result = await fetchRunReasoning(456);

  assert.deepEqual(result, expectedData);
});

test('AC4.3: fetchRunReasoning throws on non-200 status codes', async () => {
  const testCases = [
    { status: 404, description: 'not found' },
    { status: 500, description: 'server error' },
    { status: 403, description: 'forbidden' },
  ];

  for (const { status, description } of testCases) {
    globalThis.fetch = async () => ({
      ok: false,
      status: status,
    });

    let threwCorrectly = false;
    try {
      await fetchRunReasoning(789);
    } catch (err) {
      threwCorrectly = err.message === `HTTP ${status}`;
    }

    assert.ok(
      threwCorrectly,
      `Should throw Error('HTTP ${status}') on ${description} (${status})`
    );
  }
});

test('AC4.4: fetchRunReasoning encodes runId in URL', async () => {
  _fetchCalls = [];

  globalThis.fetch = async (url) => {
    _fetchCalls.push(url);
    return {
      ok: true,
      json: async () => ({}),
    };
  };

  // Test with numeric id
  await fetchRunReasoning(123);
  assert.ok(_fetchCalls[0].includes('/123/'));

  // Test with string id (edge case)
  _fetchCalls = [];
  await fetchRunReasoning('456');
  assert.ok(_fetchCalls[0].includes('/456/'));
});

test('AC4.5: fetchRunReasoning handles null fields in response', async () => {
  const responseWithNulls = {
    final_message: null,
    transcript_path: null,
    log_tail: 'Some tail content',
  };

  globalThis.fetch = async () => ({
    ok: true,
    json: async () => responseWithNulls,
  });

  const result = await fetchRunReasoning(999);

  assert.deepEqual(result, responseWithNulls);
});
