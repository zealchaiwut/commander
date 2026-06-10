"""Tests for issue #768 — Scope Deploy tab to active project only.

The Deploy tab fetches /api/deploy/overview, which aggregates environments across
ALL projects. The tab must show only the active project's environments.

AC coverage:
  AC1 — Deploy tab filters the deploy-grid using the active project slug
  AC2 — commander's Deploy tab shows only `commander` environments (prd, uat)
  AC3 — perf-coach's Deploy tab shows only `perf-coach` environments (prd, uat)
  AC4 — No environments from other projects appear in any project's Deploy tab
  AC5 — Filter is applied at render time; no full-page reload required
  AC6 — Project with no environments shows an empty state (not another project's rows)

The filtering is implemented as a pure JS helper `_deployFilterByProject(cards, slug)`
in project.html. Behaviour ACs (AC2/AC3/AC4/AC6) exercise that helper directly via
node; wiring ACs (AC1/AC5) assert the render path calls it.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"


def _html():
    return PROJECT_HTML.read_text()


def _extract_filter_fn(html):
    """Pull the `_deployFilterByProject` function source out of project.html.

    Anchored between its `function` declaration and the `// END _deployFilterByProject`
    sentinel so the extraction is exact and not dependent on brace counting.
    """
    m = re.search(
        r"(function _deployFilterByProject\(cards, slug\) \{.*?\n\})\s*\n// END _deployFilterByProject",
        html,
        re.DOTALL,
    )
    assert m, "project.html must define _deployFilterByProject(cards, slug) with an END sentinel"
    return m.group(1)


def _run_filter(cards, slug):
    """Eval `_deployFilterByProject` in node against the given cards + slug."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    fn_src = _extract_filter_fn(_html())
    script = (
        fn_src
        + "\n"
        + "const cards = "
        + json.dumps(cards)
        + ";\n"
        + "const slug = "
        + json.dumps(slug)
        + ";\n"
        + "process.stdout.write(JSON.stringify(_deployFilterByProject(cards, slug)));\n"
    )
    out = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


# Mixed multi-project overview payload, mirroring /api/deploy/overview shape.
OVERVIEW = [
    {"project": "commander", "env": "prd", "host": "render"},
    {"project": "commander", "env": "uat", "host": "local"},
    {"project": "perf-coach", "env": "prd", "host": "render"},
    {"project": "perf-coach", "env": "uat", "host": "local"},
]


def _projects(cards):
    return sorted({c["project"] for c in cards})


def _envs(cards):
    return sorted(c["env"] for c in cards)


# ── AC2 — commander shows only commander envs ────────────────────────────────

def test_commander_shows_only_commander_environments():
    result = _run_filter(OVERVIEW, "commander")
    assert _projects(result) == ["commander"]
    assert _envs(result) == ["prd", "uat"]


# ── AC3 — perf-coach shows only perf-coach envs ──────────────────────────────

def test_perfcoach_shows_only_perfcoach_environments():
    result = _run_filter(OVERVIEW, "perf-coach")
    assert _projects(result) == ["perf-coach"]
    assert _envs(result) == ["prd", "uat"]


# ── AC4 — no other project's environments leak through ───────────────────────

def test_no_foreign_project_environments_appear():
    for slug in ("commander", "perf-coach"):
        result = _run_filter(OVERVIEW, slug)
        assert all(c["project"] == slug for c in result), (
            f"filtering for {slug} leaked a foreign project: {result}"
        )


# ── AC6 — project with no environments → empty (not another project's rows) ───

def test_project_with_no_environments_is_empty():
    result = _run_filter(OVERVIEW, "some-empty-project")
    assert result == [], "a project with no envs must filter to an empty list"


def test_filter_does_not_borrow_rows_when_active_project_absent():
    # Only commander envs exist, but the active project is perf-coach.
    only_commander = [c for c in OVERVIEW if c["project"] == "commander"]
    result = _run_filter(only_commander, "perf-coach")
    assert result == [], "must not fall back to another project's environments"


# ── AC1 + AC5 — render path filters by active slug, at render time ────────────

def test_render_grid_filters_by_active_slug():
    """AC1/AC5: _deployRenderGrid must derive _deployCards from the helper using
    the active project slug — so the grid re-scopes on each render with no reload."""
    html = _html()
    m = re.search(r"function _deployRenderGrid\(\) \{.*?\n\}", html, re.DOTALL)
    assert m, "project.html must define _deployRenderGrid"
    body = m.group(0)
    assert "_deployFilterByProject(" in body, (
        "_deployRenderGrid must filter cards via _deployFilterByProject at render time"
    )
    assert "_deployCards =" in body, (
        "_deployRenderGrid must assign the filtered subset to _deployCards"
    )
    # Active slug must come from the page's project context, not a hardcoded value.
    assert "_slug" in body or "_currentProjectSlug" in body, (
        "_deployRenderGrid must use the active project slug to filter"
    )


def test_overview_fetch_keeps_raw_cards_separate_from_rendered():
    """AC5: the unfiltered overview is stored separately so re-render can re-filter
    against the current project without re-fetching (no full-page reload)."""
    html = _html()
    m = re.search(r"function deployTabInit\(\) \{.*?\n\}", html, re.DOTALL)
    assert m, "project.html must define deployTabInit"
    body = m.group(0)
    assert "_deployAllCards =" in body, (
        "deployTabInit must store the raw overview in _deployAllCards (not the rendered subset)"
    )
