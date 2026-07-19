/**
 * Behavioral tests for issue #1978: home.html _projBySlug population.
 *
 * AC1: _projectBadgeHtml(slug) uses the color from _projBySlug when populated
 *      (not always gray).
 * AC2: _projectBadgeHtml(slug) falls back to gray when _projBySlug[slug] is absent.
 * AC3: _runSprintBtn(p, slug) sends full owner/repo from _projBySlug[slug].repo
 *      when _projBySlug is populated, not just the bare slug.
 *
 * Run with: node --test tests/frontend/home-projbyslug.test.mjs
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

/** Extract `const NAME = ...;` or `let NAME = ...;` declarations (single line). */
function extractConst(source, name) {
  const re = new RegExp(`(?:const|let)\\s+${name}\\s*=\\s*[^;]+;`);
  const m = re.exec(source);
  return m ? m[0] : null;
}

const escFn              = extractFunction(html, 'esc');
const projectBadgeFn    = extractFunction(html, '_projectBadgeHtml');
const runSprintBtnFn    = extractFunction(html, '_runSprintBtn');
const buildProjBySlugFn = extractFunction(html, '_buildProjBySlug');
const paletteConst      = extractConst(html, 'PBIC_PALETTE');

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Build a runner for _projectBadgeHtml(slug) with a given _projBySlug map. */
function makeBadgeRunner(projBySlug) {
  return new Function(
    `
    ${escFn}
    ${paletteConst}
    let _projBySlug = ${JSON.stringify(projBySlug)};
    ${projectBadgeFn}
    return function(slug) { return _projectBadgeHtml(slug); };
    `
  )();
}

/** Build a runner for _runSprintBtn(p, slug) with a given _projBySlug map. */
function makeRunBtnRunner(projBySlug) {
  return new Function(
    `
    ${escFn}
    let _projBySlug = ${JSON.stringify(projBySlug)};
    ${runSprintBtnFn}
    return function(p, slug) { return _runSprintBtn(p, slug); };
    `
  )();
}

// ── Smoke: functions exist in home.html ───────────────────────────────────────

test('_buildProjBySlug is defined in home.html', () => {
  assert.ok(buildProjBySlugFn, '_buildProjBySlug must be defined in home.html');
});

test('_projectBadgeHtml is defined in home.html', () => {
  assert.ok(projectBadgeFn, '_projectBadgeHtml must be defined in home.html');
});

test('PBIC_PALETTE is defined in home.html', () => {
  assert.ok(paletteConst, 'PBIC_PALETTE must be defined in home.html');
});

test('_runSprintBtn is defined in home.html', () => {
  assert.ok(runSprintBtnFn, '_runSprintBtn must be defined in home.html');
});

// ── _buildProjBySlug: population from report projects ─────────────────────────

test('_buildProjBySlug maps project slug to repo/icon/color/name', () => {
  const fn = new Function(`${buildProjBySlugFn}\n return _buildProjBySlug;`)();
  const projects = [
    { project: 'commander', repo: 'zealchaiwut/commander', icon: 'ti-terminal', color: 'blue', name: 'Commander' },
    { project: 'perf-coach', repo: 'zealchaiwut/perf-coach', icon: 'ti-star', color: 'purple', name: 'Perf Coach' },
  ];
  const map = fn(projects);
  assert.equal(map['commander'].repo, 'zealchaiwut/commander');
  assert.equal(map['commander'].icon, 'ti-terminal');
  assert.equal(map['commander'].color, 'blue');
  assert.equal(map['commander'].name, 'Commander');
  assert.equal(map['perf-coach'].repo, 'zealchaiwut/perf-coach');
  assert.equal(map['perf-coach'].color, 'purple');
});

test('_buildProjBySlug falls back repo to project slug when repo absent', () => {
  const fn = new Function(`${buildProjBySlugFn}\n return _buildProjBySlug;`)();
  const map = fn([{ project: 'myslug' }]);
  assert.equal(map['myslug'].repo, 'myslug');
  assert.equal(map['myslug'].icon, 'ti-folder');
  assert.equal(map['myslug'].color, 'gray');
});

test('_buildProjBySlug skips entries with no project field', () => {
  const fn = new Function(`${buildProjBySlugFn}\n return _buildProjBySlug;`)();
  const map = fn([{ repo: 'owner/noslug', name: 'No slug' }]);
  assert.deepEqual(map, {});
});

test('_buildProjBySlug returns empty map for empty projects array', () => {
  const fn = new Function(`${buildProjBySlugFn}\n return _buildProjBySlug;`)();
  const map = fn([]);
  assert.deepEqual(map, {});
});

// ── AC1: badge uses color from _projBySlug ────────────────────────────────────

test('AC1: _projectBadgeHtml uses "blue" color when _projBySlug[slug].color is "blue"', () => {
  const run = makeBadgeRunner({ commander: { color: 'blue', icon: 'ti-folder' } });
  const out = run('commander');
  assert.ok(out.includes('pbic--blue'), `Expected pbic--blue in: ${out}`);
  assert.ok(!out.includes('pbic--gray'), `Must not fall back to gray: ${out}`);
});

test('AC1: _projectBadgeHtml uses "purple" color when _projBySlug[slug].color is "purple"', () => {
  const run = makeBadgeRunner({ proj: { color: 'purple', icon: 'ti-star' } });
  const out = run('proj');
  assert.ok(out.includes('pbic--purple'), `Expected pbic--purple in: ${out}`);
});

test('AC1: _projectBadgeHtml uses configured icon class', () => {
  const run = makeBadgeRunner({ proj: { color: 'green', icon: 'ti-rocket' } });
  const out = run('proj');
  assert.ok(out.includes('ti-rocket'), `Expected ti-rocket in: ${out}`);
});

// ── AC2: badge falls back to gray when _projBySlug has no entry ───────────────

test('AC2: _projectBadgeHtml falls back to pbic--gray when slug not in _projBySlug', () => {
  const run = makeBadgeRunner({});
  const out = run('unknown-slug');
  assert.ok(out.includes('pbic--gray'), `Expected pbic--gray fallback in: ${out}`);
});

test('AC2: _projectBadgeHtml falls back to ti-folder icon when slug not in _projBySlug', () => {
  const run = makeBadgeRunner({});
  const out = run('unknown-slug');
  assert.ok(out.includes('ti-folder'), `Expected ti-folder fallback in: ${out}`);
});

// ── AC3: _runSprintBtn uses full owner/repo from _projBySlug ─────────────────

test('AC3: _runSprintBtn sends full owner/repo when _projBySlug[slug].repo is set', () => {
  const run = makeRunBtnRunner({
    commander: { repo: 'zealchaiwut/commander', color: 'blue', icon: 'ti-folder' },
  });
  const p = { status: 'idle', up_next: { label: 'sprint-50', ready: true } };
  const out = run(p, 'commander');
  assert.ok(out.length > 0, 'Run sprint button must be rendered');
  assert.ok(
    out.includes("'zealchaiwut/commander'"),
    `Expected full owner/repo in button; got: ${out}`,
  );
  assert.ok(
    !out.includes("runSprintAction('commander'"),
    `Must not pass bare slug to runSprintAction; got: ${out}`,
  );
});

test('AC3: _runSprintBtn falls back to slug when _projBySlug has no entry', () => {
  const run = makeRunBtnRunner({});
  const p = { status: 'idle', up_next: { label: 'sprint-50', ready: true } };
  const out = run(p, 'myslug');
  assert.ok(out.length > 0, 'Run sprint button must be rendered');
  assert.ok(
    out.includes("'myslug'"),
    `Expected slug fallback in button; got: ${out}`,
  );
});

test('AC3: _runSprintBtn returns empty string when status is running', () => {
  const run = makeRunBtnRunner({
    commander: { repo: 'zealchaiwut/commander', color: 'blue', icon: 'ti-folder' },
  });
  const p = { status: 'running', up_next: { label: 'sprint-50', ready: true } };
  const out = run(p, 'commander');
  assert.equal(out, '', 'Button must be hidden when sprint is already running');
});
