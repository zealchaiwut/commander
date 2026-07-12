/**
 * Behavioral tests for issue #1855: Home failure-first triage.
 *
 * AC1  renderRanOvernight sorts failures/partials first; failures are never
 *      hidden behind the "See N more" collapse.
 * AC2  renderKpis renders a red "Failed" cell when overnight_failures > 0;
 *      the cell is absent when overnight_failures is 0 or missing.
 * AC3  renderProject renders _statusLine at the top of pb-cell-summary;
 *      the AI-summary block is demoted below it.
 * AC4  _bottomHidden() returns true on first visit (null localStorage);
 *      existing localStorage choices ('0' / '1') are still respected.
 *
 * Run with: node --test tests/frontend/home-triage.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = fileURLToPath(new URL('.', import.meta.url));
const HOME_HTML = resolve(__dir, '../../apps/dashboard/static/home.html');
const html = readFileSync(HOME_HTML, 'utf-8');

/** Extract a named function from source by brace-counting. */
function extractFunction(source, fnName) {
  const sig = `function ${fnName}(`;
  const start = source.indexOf(sig);
  if (start === -1) return null;
  const prefixStart = source.lastIndexOf('\n', start) + 1;
  const braceOpen = source.indexOf('{', start);
  if (braceOpen === -1) return null;
  let depth = 0;
  let i = braceOpen;
  while (i < source.length) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(prefixStart, i + 1);
    }
    i++;
  }
  return null;
}

const escFn              = extractFunction(html, 'esc');
const prettyLabelFn      = extractFunction(html, 'prettyLabel');
const briefLinkHrefFn    = extractFunction(html, '_briefLinkHref');
const briefToggleMoreFn  = extractFunction(html, '_briefToggleMore');
const briefCollapsibleFn = extractFunction(html, '_briefCollapsibleRows');
const renderRanFn        = extractFunction(html, 'renderRanOvernight');
const renderKpisFn       = extractFunction(html, 'renderKpis');
const bottomHiddenFn     = extractFunction(html, '_bottomHidden');
const renderProjectFn    = extractFunction(html, 'renderProject');

// ── Shared helper runner for renderRanOvernight ───────────────────────────────

function makeRanRunner() {
  return new Function('list', `
    var _repoBySlug = {};
    ${escFn}
    ${prettyLabelFn}
    ${briefLinkHrefFn}
    ${briefToggleMoreFn}
    ${briefCollapsibleFn}
    ${renderRanFn}
    return renderRanOvernight(list);
  `);
}

// ── AC1: Sort failures first; never hidden ─────────────────────────────────────

test('AC1: renderRanOvernight is defined in home.html', () => {
  assert.ok(renderRanFn, 'renderRanOvernight must be defined in home.html');
});

test('AC1: failure outcome badge appears before success badge', () => {
  const run = makeRanRunner();
  const list = [
    { outcome: 'success', label: 'sprint-1', project: 'proj' },
    { outcome: 'failure', label: 'sprint-2', project: 'proj' },
  ];
  const out = run(list);
  const failIdx    = out.indexOf('outcome-badge failure');
  const successIdx = out.indexOf('outcome-badge success');
  assert.ok(failIdx !== -1,  'failure badge must appear in rendered output');
  assert.ok(successIdx !== -1, 'success badge must appear in rendered output');
  assert.ok(
    failIdx < successIdx,
    `failure badge (pos ${failIdx}) must appear before success badge (pos ${successIdx})`,
  );
});

test('AC1: partial outcome badge appears before success badge', () => {
  const run = makeRanRunner();
  const list = [
    { outcome: 'success', label: 'sprint-1', project: 'proj' },
    { outcome: 'partial', label: 'sprint-2', project: 'proj' },
  ];
  const out = run(list);
  const partIdx    = out.indexOf('outcome-badge partial');
  const successIdx = out.indexOf('outcome-badge success');
  assert.ok(partIdx < successIdx, 'partial badge must appear before success badge');
});

test('AC1: failure is not inside the hidden planner-more section (1 failure + 4 successes)', () => {
  const run = makeRanRunner();
  const list = [
    { outcome: 'success', label: 'sprint-1', project: 'proj' },
    { outcome: 'success', label: 'sprint-2', project: 'proj' },
    { outcome: 'success', label: 'sprint-3', project: 'proj' },
    { outcome: 'success', label: 'sprint-4', project: 'proj' },
    { outcome: 'failure', label: 'sprint-5', project: 'proj' },
  ];
  const out = run(list);

  // Extract the planner-more (hidden) section
  const moreStart = out.indexOf('planner-more');
  const failIdx   = out.indexOf('outcome-badge failure');

  assert.ok(failIdx !== -1, 'failure badge must be present in rendered output');
  if (moreStart === -1) {
    // If there's no collapse section (threshold >= total), that's also fine
    return;
  }
  assert.ok(
    failIdx < moreStart,
    `failure badge (pos ${failIdx}) must appear BEFORE the planner-more section (pos ${moreStart})`,
  );
});

test('AC1: all failures visible when there are 4 failures + 2 successes (> threshold)', () => {
  const run = makeRanRunner();
  const list = [
    { outcome: 'failure', label: 'sprint-1', project: 'proj' },
    { outcome: 'failure', label: 'sprint-2', project: 'proj' },
    { outcome: 'failure', label: 'sprint-3', project: 'proj' },
    { outcome: 'failure', label: 'sprint-4', project: 'proj' },
    { outcome: 'success', label: 'sprint-5', project: 'proj' },
    { outcome: 'success', label: 'sprint-6', project: 'proj' },
  ];
  const out = run(list);

  // The planner-more section must not contain any failure badge
  const moreStart = out.indexOf('id="ran-more"');
  if (moreStart === -1) return; // no collapse needed — pass
  const moreSection = out.slice(moreStart);
  assert.ok(
    !moreSection.includes('outcome-badge failure'),
    'no failure badge must appear inside the hidden planner-more section',
  );
});

// ── AC2: Red Failed KPI cell ──────────────────────────────────────────────────

test('AC2: renderKpis is defined in home.html', () => {
  assert.ok(renderKpisFn, 'renderKpis must be defined in home.html');
});

test('AC2: renderKpis includes red Failed cell when overnight_failures > 0', () => {
  const run = new Function('gk', `${renderKpisFn}\n  return renderKpis(gk);`);
  const out = run({ overnight_failures: 3, sprints_shipped: 1 });
  assert.ok(out.includes('Failed'), 'output must contain "Failed" label when overnight_failures > 0');
  // The value cell must carry the red CSS modifier
  assert.ok(
    out.includes('stat-v r') || out.includes('stat-v" class="r') || out.match(/stat-v[^"]*r/),
    'Failed stat-v must carry red color modifier (e.g. "stat-v r")',
  );
});

test('AC2: renderKpis omits Failed cell when overnight_failures is 0', () => {
  const run = new Function('gk', `${renderKpisFn}\n  return renderKpis(gk);`);
  const out = run({ overnight_failures: 0, sprints_shipped: 2 });
  assert.ok(!out.includes('Failed'), 'Failed cell must be absent when overnight_failures is 0');
});

test('AC2: renderKpis omits Failed cell when overnight_failures is absent', () => {
  const run = new Function('gk', `${renderKpisFn}\n  return renderKpis(gk);`);
  const out = run({ sprints_shipped: 1 });
  assert.ok(!out.includes('Failed'), 'Failed cell must be absent when overnight_failures is not in gk');
});

// ── AC3: _statusLine at top of pb-cell-summary ────────────────────────────────

test('AC3: renderProject is defined in home.html', () => {
  assert.ok(renderProjectFn, 'renderProject must be defined in home.html');
});

test('AC3: pb-statusline appears before pb-summary in renderProject output', () => {
  const mockEsc = s => String(s ?? '');
  const mockStatusLine = () => '<div class="pb-statusline run">Status</div>';
  const runner = new Function(
    'p', 'esc', '_bottomHidden', '_projectTitleLinkHtml', '_cardMeta',
    '_statusPill', '_statusLine', 'summaryLoadingHtml', '_shippedDetail', '_timeline',
    `${renderProjectFn}\n  return renderProject(p);`,
  );
  const out = runner(
    { project: 'test', in_progress: { sprint_label: 'sprint-1' } },
    mockEsc,
    () => false,
    () => '<a>title</a>',
    () => '',
    () => '<span class="pbstatus run">running</span>',
    mockStatusLine,
    () => '<span>loading</span>',
    () => '<div>shipped</div>',
    () => '<div>timeline</div>',
  );
  const statusIdx  = out.indexOf('pb-statusline');
  const summaryIdx = out.indexOf('pb-summary');
  assert.ok(statusIdx !== -1, 'pb-statusline must appear in renderProject output');
  assert.ok(summaryIdx !== -1, 'pb-summary must appear in renderProject output');
  assert.ok(
    statusIdx < summaryIdx,
    `pb-statusline (pos ${statusIdx}) must appear BEFORE pb-summary (pos ${summaryIdx}) — status line should be at the top`,
  );
});

// ── AC4: _bottomHidden defaults to true on first visit ────────────────────────

test('AC4: _bottomHidden is defined in home.html', () => {
  assert.ok(bottomHiddenFn, '_bottomHidden must be defined in home.html');
});

test('AC4: _bottomHidden returns true when localStorage has no entry (first visit)', () => {
  const mockLS = { getItem: () => null };
  const run = new Function(
    'localStorage',
    `${bottomHiddenFn}\n  return _bottomHidden('myproj');`,
  );
  const result = run(mockLS);
  assert.equal(result, true, '_bottomHidden must return true (hidden) on first visit when localStorage returns null');
});

test('AC4: _bottomHidden returns true when localStorage entry is "1" (user chose hidden)', () => {
  const mockLS = { getItem: () => '1' };
  const run = new Function(
    'localStorage',
    `${bottomHiddenFn}\n  return _bottomHidden('myproj');`,
  );
  const result = run(mockLS);
  assert.equal(result, true, '_bottomHidden must return true when localStorage value is "1"');
});

test('AC4: _bottomHidden returns false when localStorage entry is "0" (user chose shown)', () => {
  const mockLS = { getItem: () => '0' };
  const run = new Function(
    'localStorage',
    `${bottomHiddenFn}\n  return _bottomHidden('myproj');`,
  );
  const result = run(mockLS);
  assert.equal(result, false, '_bottomHidden must return false when localStorage value is "0"');
});
