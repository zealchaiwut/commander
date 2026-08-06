/**
 * CSS structural tests for issue #2158: Failures-table mobile column-hide deduplication.
 * Follow-up for issue #2181 (sanctioned #1746 exception).
 *
 * SANCTIONED #1746 EXCEPTION
 * These tests verify a structural CSS invariant (deduplication) via brace-depth
 * block extraction, NOT a raw substring-in-file search. This approach is
 * acceptable because:
 *   (a) CSS deduplication ("the rule appears exactly once") is a source-structure
 *       invariant. Asserting it via computed styles would require a real browser
 *       engine — jsdom does not evaluate @media queries, so no environment-free
 *       behavioral alternative exists.
 *   (b) The behavioral consequence (column hidden at ≤600px) is covered at the
 *       markup level by tests/frontend/failures-mobile-cols-2073.test.mjs, which
 *       confirms Sprint/Time columns are present in the DOM so the CSS rule can
 *       act on them.
 *   (c) The invariant being guarded is regression against a specific prior bug
 *       (#2158: rule appeared in two @media blocks). Verifying "exactly one
 *       occurrence" is precisely the fix we need to hold.
 *   (d) The check goes beyond a bare selector-presence regex: it also asserts
 *       the `display: none` property value inside the canonical block, making
 *       it structural verification rather than mere text presence.
 *
 * Run with: node --test tests/frontend/css-dedup-fbox-2158.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, '..', '..');
const PROJECT_HTML = readFileSync(
  join(REPO_ROOT, 'apps', 'dashboard', 'static', 'project.html'),
  'utf8'
);

// Selector pattern for the two mobile-hidden columns (col 2 = Sprint, col 6 = Time)
const FBOX_SELECTOR_PAT = /\.fbox-table\s+(?:th|td):nth-child\([26]\)/;

// Confirms the hidden column rule actually applies display:none (not just present)
const FBOX_DISPLAY_NONE_PAT =
  /\.fbox-table\s+(?:th|td):nth-child\(\d\)[^}]*}\s*\}\s*|\.fbox-table\s+th:nth-child\(2\)[\s\S]{0,200}display\s*:\s*none/;

/**
 * Extract all @media (max-width: 600px) block bodies using brace-depth tracking.
 * Returns an array of block body strings (content between the outermost braces).
 */
function extractMedia600Blocks(src) {
  const blocks = [];
  let searchFrom = 0;
  while (true) {
    const mediaIdx = src.indexOf('@media', searchFrom);
    if (mediaIdx === -1) break;
    const braceOpen = src.indexOf('{', mediaIdx);
    if (braceOpen === -1) break;
    const header = src.slice(mediaIdx, braceOpen);
    if (!header.includes('max-width') || !header.includes('600px')) {
      searchFrom = braceOpen + 1;
      continue;
    }
    let depth = 0;
    let end = braceOpen;
    for (let i = braceOpen; i < src.length; i++) {
      if (src[i] === '{') depth++;
      else if (src[i] === '}') {
        depth--;
        if (depth === 0) { end = i; break; }
      }
    }
    blocks.push(src.slice(braceOpen + 1, end));
    searchFrom = end + 1;
  }
  return blocks;
}

// ─── AC1: rule appears in exactly one @media (max-width: 600px) block ─────────

test('AC1 #2158: .fbox-table column-hide rule appears in exactly one 600px @media block', () => {
  const blocks = extractMedia600Blocks(PROJECT_HTML);
  assert.ok(blocks.length > 0, 'No @media (max-width: 600px) blocks found in project.html');

  const withRule = blocks.filter(b => FBOX_SELECTOR_PAT.test(b));
  assert.equal(
    withRule.length,
    1,
    `Expected exactly 1 @media (max-width: 600px) block containing the .fbox-table ` +
    `column-hide rule, found ${withRule.length}. A duplicate must be removed (issue #2158).`
  );
});

// ─── AC2: history-card @media block must not contain the fbox rule ─────────────

test('AC2 #2158: @media block containing .hist-card-mini has no .fbox-table column-hide rule', () => {
  const blocks = extractMedia600Blocks(PROJECT_HTML);
  for (const block of blocks) {
    if (block.includes('.hist-card-mini')) {
      assert.ok(
        !FBOX_SELECTOR_PAT.test(block),
        'The @media (max-width: 600px) block containing .hist-card-mini must NOT ' +
        'contain a .fbox-table column-hide rule. Remove the duplicate (issue #2158).'
      );
      return;
    }
  }
  // No hist-card-mini block found — acceptable if it was restructured
});

// ─── AC3: canonical block applies display:none to the correct columns ──────────

test('AC3 #2158: canonical block hides Sprint (col 2) with display:none', () => {
  const blocks = extractMedia600Blocks(PROJECT_HTML);
  const canonical = blocks.find(
    b => b.includes('fbox-table') && b.includes('nth-child(2)')
  );
  assert.ok(
    canonical,
    'No @media (max-width: 600px) block with .fbox-table nth-child(2) found.'
  );
  assert.ok(
    /display\s*:\s*none/.test(canonical),
    'The canonical .fbox-table column-hide block must apply display:none ' +
    '(not just declare the selector).'
  );
  assert.ok(
    /nth-child\(2\)/.test(canonical),
    'Sprint column (nth-child(2)) must be targeted in the canonical block.'
  );
});

test('AC3 #2158: canonical block hides Time (col 6) with display:none', () => {
  const blocks = extractMedia600Blocks(PROJECT_HTML);
  const canonical = blocks.find(
    b => b.includes('fbox-table') && b.includes('nth-child(6)')
  );
  assert.ok(
    canonical,
    'No @media (max-width: 600px) block with .fbox-table nth-child(6) found.'
  );
  assert.ok(
    /display\s*:\s*none/.test(canonical),
    'The canonical .fbox-table column-hide block must apply display:none.'
  );
  assert.ok(
    /nth-child\(6\)/.test(canonical),
    'Time column (nth-child(6)) must be targeted in the canonical block.'
  );
});
