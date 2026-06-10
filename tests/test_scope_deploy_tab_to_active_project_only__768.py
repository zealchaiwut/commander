"""Tests for issue #768: Scope Deploy tab to active project only (runs against UAT).

The Deploy tab fetches /api/deploy/overview, which aggregates environments across
ALL projects, then filters client-side down to the active project. Two surfaces
are verified:

  * Live UAT HTTP — /api/deploy/overview really does aggregate >1 project, so the
    cross-project filter is necessary (without it, foreign rows would render).
  * Client-side filter — the pure JS helper `_deployFilterByProject(cards, slug)`
    and its render-path wiring in static/project.html, exercised via node.

AC coverage:
  AC1 — Deploy tab filters the deploy-grid using the active project slug
  AC2 — commander's Deploy tab shows only `commander` environments (prd, uat)
  AC3 — perf-coach's Deploy tab shows only `perf-coach` environments (prd, uat)
  AC4 — No environments from other projects appear in any project's Deploy tab
  AC5 — Filter applied at render time; no full-page reload required
  AC6 — Project with no environments shows an empty state (not another project's rows)
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

def _find_project_html():
    """Locate static/project.html regardless of where this test file lives
    (root tests/ or apps/dashboard/tests/)."""
    here = Path(__file__).resolve()
    for base in here.parents:
        cand = base / "apps" / "dashboard" / "static" / "project.html"
        if cand.exists():
            return cand
        cand = base / "static" / "project.html"
        if cand.exists():
            return cand
    raise FileNotFoundError("static/project.html not found from " + str(here))


PROJECT_HTML = _find_project_html()


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def _html():
    return PROJECT_HTML.read_text()


def _extract_filter_fn(html):
    """Pull `_deployFilterByProject` source out of project.html via its END sentinel."""
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
        pytest.skip("manual — node not available to exercise client-side filter")
    script = (
        _extract_filter_fn(_html())
        + "\nconst cards = " + json.dumps(cards) + ";"
        + "\nconst slug = " + json.dumps(slug) + ";"
        + "\nprocess.stdout.write(JSON.stringify(_deployFilterByProject(cards, slug)));"
    )
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, f"node failed: {out.stderr}"
    return json.loads(out.stdout)


# Mixed multi-project overview payload, mirroring /api/deploy/overview shape.
OVERVIEW = [
    {"project": "commander", "env": "prd", "host": "local"},
    {"project": "commander", "env": "uat", "host": "local"},
    {"project": "perf-coach", "env": "prd", "host": "render"},
    {"project": "perf-coach", "env": "uat", "host": "local"},
]


# --- Acceptance Criteria ---

def test_scope_deploy__filters_grid_using_active_project_slug(client):
    # AC1: render path filters via _deployFilterByProject using the active slug,
    # and the live overview endpoint really returns multiple projects to filter.
    r = client.get("/api/deploy/overview")
    assert r.status_code == 200, f"overview endpoint not 200: {r.status_code}"
    envs = r.json().get("environments", [])
    projects = {e.get("project") for e in envs}
    assert len(projects) >= 2, (
        f"overview must aggregate >1 project for the filter to matter; got {projects}"
    )

    body = re.search(r"function _deployRenderGrid\(\) \{.*?\n\}", _html(), re.DOTALL)
    assert body, "project.html must define _deployRenderGrid"
    body = body.group(0)
    assert "_deployFilterByProject(" in body, "render path must filter via _deployFilterByProject"
    assert "_currentProjectSlug" in body or "_slug" in body, "filter must use the active slug"


def test_scope_deploy__commander_shows_only_commander_environments():
    # AC2
    result = _run_filter(OVERVIEW, "commander")
    assert sorted({c["project"] for c in result}) == ["commander"]
    assert sorted(c["env"] for c in result) == ["prd", "uat"]


def test_scope_deploy__perfcoach_shows_only_perfcoach_environments():
    # AC3
    result = _run_filter(OVERVIEW, "perf-coach")
    assert sorted({c["project"] for c in result}) == ["perf-coach"]
    assert sorted(c["env"] for c in result) == ["prd", "uat"]


def test_scope_deploy__no_foreign_project_environments_appear():
    # AC4
    for slug in ("commander", "perf-coach"):
        result = _run_filter(OVERVIEW, slug)
        assert all(c["project"] == slug for c in result), (
            f"filtering for {slug} leaked a foreign project: {result}"
        )


def test_scope_deploy__filter_at_render_time_no_full_reload():
    # AC5: raw overview kept separate (_deployAllCards) so re-render re-filters
    # against the current project without re-fetching / reloading the page.
    html = _html()
    init = re.search(r"function deployTabInit\(\) \{.*?\n\}", html, re.DOTALL)
    assert init, "project.html must define deployTabInit"
    assert "_deployAllCards =" in init.group(0), (
        "deployTabInit must store the raw overview in _deployAllCards, not the rendered subset"
    )
    grid = re.search(r"function _deployRenderGrid\(\) \{.*?\n\}", html, re.DOTALL)
    assert "_deployCards =" in grid.group(0), (
        "_deployRenderGrid must assign the filtered subset at render time"
    )


def test_scope_deploy__empty_project_shows_empty_not_foreign_rows():
    # AC6: unknown/empty project filters to [] (never borrows another project's rows).
    assert _run_filter(OVERVIEW, "some-empty-project") == []
    only_commander = [c for c in OVERVIEW if c["project"] == "commander"]
    assert _run_filter(only_commander, "perf-coach") == [], (
        "must not fall back to another project's environments"
    )
