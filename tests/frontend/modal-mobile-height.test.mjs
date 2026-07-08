/**
 * CSS contract tests for issue #1766: cap modal height on mobile.
 *
 * Parses CSS declarations from project.html for the affected selectors and
 * asserts exact property values. These tests exercise the CSS contract — the
 * declarative spec that drives browser layout — which is the correct behavior
 * target for a pure-CSS change.
 *
 * Run with: node --test tests/frontend/modal-mobile-height.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const html = readFileSync(
  path.join(__dirname, '../../apps/dashboard/static/project.html'),
  'utf8',
);

/**
 * Extract all CSS declaration blocks for an exact standalone selector from
 * all <style> sections in the HTML. Returns the merged declarations text.
 *
 * Uses line-start anchoring so ".modal {" matches but ".pf-modal .modal-body {"
 * does NOT match when selector is ".modal-body".
 */
function declarationsFor(selector) {
  // Collect all <style>…</style> blocks
  const styleBlocks = [];
  const styleRe = /<style[^>]*>([\s\S]*?)<\/style>/gi;
  let sm;
  while ((sm = styleRe.exec(html)) !== null) {
    styleBlocks.push(sm[1]);
  }
  const cssText = styleBlocks.join('\n');

  // Anchor to start of line (^) so compound selectors like ".pf-modal .modal-body"
  // don't match when we're looking for ".modal-body". The multiline flag makes
  // ^ match start of each line within the string.
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`^\\s*${escaped}\\s*\\{([^}]*)\\}`, 'gm');
  const blocks = [];
  let m;
  while ((m = re.exec(cssText)) !== null) {
    blocks.push(m[1]);
  }
  return blocks.join('\n');
}

/**
 * Parse declarations text into a { property: value } map.
 * Last declaration wins (CSS cascade within same block).
 */
function parseDeclarations(text) {
  const result = {};
  for (const decl of text.split(';')) {
    const trimmed = decl.trim();
    if (!trimmed) continue;
    const colon = trimmed.indexOf(':');
    if (colon < 0) continue;
    const prop = trimmed.slice(0, colon).trim().toLowerCase();
    const val = trimmed.slice(colon + 1).trim();
    result[prop] = val;
  }
  return result;
}

function cssFor(selector) {
  return parseDeclarations(declarationsFor(selector));
}

// ── AC1: .modal base class ────────────────────────────────────────────────────

test('AC1: .modal has max-height: calc(100dvh - 32px)', () => {
  const props = cssFor('.modal');
  assert.equal(props['max-height'], 'calc(100dvh - 32px)',
    '.modal must declare max-height: calc(100dvh - 32px)');
});

test('AC1: .modal has display: flex', () => {
  const props = cssFor('.modal');
  assert.equal(props['display'], 'flex', '.modal must declare display: flex');
});

test('AC1: .modal has flex-direction: column', () => {
  const props = cssFor('.modal');
  assert.equal(props['flex-direction'], 'column',
    '.modal must declare flex-direction: column');
});

test('AC1: .modal has overflow: hidden', () => {
  const props = cssFor('.modal');
  assert.equal(props['overflow'], 'hidden',
    '.modal must declare overflow: hidden');
});

// ── AC2: .modal-body ──────────────────────────────────────────────────────────

test('AC2: .modal-body has flex: 1 1 auto', () => {
  const props = cssFor('.modal-body');
  assert.equal(props['flex'], '1 1 auto',
    '.modal-body must declare flex: 1 1 auto');
});

test('AC2: .modal-body has overflow-y: auto', () => {
  const props = cssFor('.modal-body');
  assert.equal(props['overflow-y'], 'auto',
    '.modal-body must declare overflow-y: auto');
});

test('AC2: .modal-body has min-height: 0', () => {
  const props = cssFor('.modal-body');
  assert.equal(props['min-height'], '0',
    '.modal-body must declare min-height: 0');
});

// ── AC3: header and footer are flex-shrink: 0 ────────────────────────────────

test('AC3: .modal-header has flex-shrink: 0', () => {
  const props = cssFor('.modal-header');
  assert.equal(props['flex-shrink'], '0',
    '.modal-header must have flex-shrink: 0 so it stays fixed when body scrolls');
});

test('AC3: .modal-footer has flex-shrink: 0', () => {
  const props = cssFor('.modal-footer');
  assert.equal(props['flex-shrink'], '0',
    '.modal-footer must have flex-shrink: 0 so it stays fixed when body scrolls');
});

// ── AC7: already-normalized width variants (must not be broken) ───────────────

test('AC7: .ns-modal uses min() width expression', () => {
  const decls = declarationsFor('.ns-modal');
  assert.ok(decls.includes('min('), '.ns-modal must use min() for width');
  assert.ok(decls.includes('100vw - 32px'), '.ns-modal must include 100vw - 32px guard');
});

test('AC7: .ct-modal uses min() width expression', () => {
  const decls = declarationsFor('.ct-modal');
  assert.ok(decls.includes('min('), '.ct-modal must use min() for width');
  assert.ok(decls.includes('100vw - 32px'), '.ct-modal must include 100vw - 32px guard');
});

test('AC7: .cl-modal uses min() width expression', () => {
  const decls = declarationsFor('.cl-modal');
  assert.ok(decls.includes('min('), '.cl-modal must use min() for width');
  assert.ok(decls.includes('100vw - 32px'), '.cl-modal must include 100vw - 32px guard');
});

test('AC7: .bcm-modal uses min() width expression', () => {
  const decls = declarationsFor('.bcm-modal');
  assert.ok(decls.includes('min('), '.bcm-modal must use min() for width');
  assert.ok(decls.includes('100vw - 32px'), '.bcm-modal must include 100vw - 32px guard');
});

test('AC7: .re-est-modal uses min() width expression', () => {
  const decls = declarationsFor('.re-est-modal');
  assert.ok(decls.includes('min('), '.re-est-modal must use min() for width');
  assert.ok(decls.includes('100vw - 32px'), '.re-est-modal must include 100vw - 32px guard');
});

// ── AC8: .hs-modal and .mt-modal must not have min-width > 358px ─────────────

test('AC8: .mt-modal does not declare a min-width that would exceed 358px on 390px viewport', () => {
  const props = cssFor('.mt-modal');
  // Either min-width is absent, is 0, or uses a viewport-relative value.
  // A fixed px value > 358 would overflow a 390px viewport after 16px margin each side.
  const minW = props['min-width'];
  if (minW === undefined || minW === '0' || minW === '0px') return; // OK
  const fixedPx = parseFloat(minW);
  if (!isNaN(fixedPx)) {
    assert.ok(fixedPx <= 358,
      `.mt-modal min-width must not exceed 358px (got ${minW}); remove or override it`);
  }
  // viewport-relative min-width (e.g. calc(100vw - 32px)) is fine — assert passes
});

test('AC8: .hs-modal does not declare a min-width that would exceed 358px on 390px viewport', () => {
  const props = cssFor('.hs-modal');
  const minW = props['min-width'];
  if (minW === undefined || minW === '0' || minW === '0px') return; // OK
  const fixedPx = parseFloat(minW);
  if (!isNaN(fixedPx)) {
    assert.ok(fixedPx <= 358,
      `.hs-modal min-width must not exceed 358px (got ${minW}); remove or override it`);
  }
});

// ── AC9: already-capped modals are unchanged ──────────────────────────────────

test('AC9: .nt-modal still has its own max-height guard', () => {
  const decls = declarationsFor('.nt-modal');
  assert.ok(decls.includes('max-height'), '.nt-modal must still declare max-height');
});

test('AC9: .lv-modal still has its own max-height guard', () => {
  const decls = declarationsFor('.lv-modal');
  assert.ok(decls.includes('max-height'), '.lv-modal must still declare max-height');
});

test('AC9: .pf-modal still has its own max-height guard', () => {
  const decls = declarationsFor('.pf-modal');
  assert.ok(decls.includes('max-height'), '.pf-modal must still declare max-height');
});
