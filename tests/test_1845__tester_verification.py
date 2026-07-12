"""Tester verification for issue #1845: remove dead milestone fetch/render from Home.

This test file validates the acceptance criteria:
AC1: loadMilestone() and its DOM stub are removed from home.html
AC2: milestone enrichment call removed from _enrich_home_artifact
AC3: .pb-milestone CSS block removed
AC4: Home page loads with zero milestone-related requests (behavioral test)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import httpx


# Setup paths
REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers.brief import _enrich_home_artifact


# Resolved from environment at runtime; Sprint manager passes this.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    BASE_URL = "http://localhost:8001"


@pytest.fixture
def client():
    """HTTP client pointing to UAT environment."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ===========================================================================
# AC1: loadMilestone() function and DOM stub removed from home.html
# ===========================================================================

def test_1845__ac1_loadmilestone_function_removed():
    """AC1: loadMilestone() async function must be completely removed from home.html."""
    home_html_path = DASHBOARD_DIR / "static" / "home.html"
    assert home_html_path.exists(), "home.html not found"

    content = home_html_path.read_text(encoding="utf-8")

    # The function signature should not exist
    assert "async function loadMilestone" not in content, (
        "loadMilestone() function definition must be removed from home.html"
    )
    assert "function loadMilestone" not in content, (
        "loadMilestone() function definition must be removed from home.html"
    )


def test_1845__ac1_milestone_dom_stub_removed():
    """AC1: The ms-${slug} milestone DOM stub <a> element must be removed."""
    home_html_path = DASHBOARD_DIR / "static" / "home.html"
    content = home_html_path.read_text(encoding="utf-8")

    # The milestone badge element with id='ms-...' should not exist
    assert 'id="ms-' not in content, (
        'Milestone badge element with id="ms-..." must be removed'
    )
    assert 'class="pb-milestone"' not in content, (
        'Milestone badge element with class="pb-milestone" must be removed'
    )
    assert 'pb-ms-name' not in content, (
        "Milestone name span (pb-ms-name class) must be removed"
    )
    assert 'pb-ms-count' not in content, (
        "Milestone count span (pb-ms-count class) must be removed"
    )


# ===========================================================================
# AC2: milestone enrichment removed from _enrich_home_artifact
# ===========================================================================

def test_1845__ac2_enrichment_no_milestone_key():
    """AC2: _enrich_home_artifact must not add a 'milestone' key to project entries."""
    from unittest.mock import patch

    artifact = {
        "available": True,
        "brief": {"projects": [{"project": "test-proj"}]},
        "date": "2026-01-01",
    }

    # Mock dependencies
    with patch("routers.brief._projects_module.load_projects", return_value=[
        {"repo": "org/test-proj", "name": "Test"}
    ]):
        with patch("routers.brief.brief_summary.get_or_create_project_summary",
                   return_value={"summary": "test summary"}):
            _enrich_home_artifact(artifact)

    # After enrichment, projects should NOT have a 'milestone' key
    for proj in artifact["brief"]["projects"]:
        assert "milestone" not in proj, (
            f"Project {proj.get('project')} must not have 'milestone' key after enrichment"
        )


def test_1845__ac2_enrichment_has_required_keys():
    """AC2: _enrich_home_artifact must add all non-milestone required keys."""
    from unittest.mock import patch

    artifact = {
        "available": True,
        "brief": {"projects": [{"project": "alpha"}]},
        "date": "2026-01-01",
    }

    with patch("routers.brief._projects_module.load_projects", return_value=[
        {"repo": "org/alpha", "name": "Alpha", "icon": "ti-folder", "color": "gray"}
    ]):
        with patch("routers.brief.brief_summary.get_or_create_project_summary",
                   return_value={"summary": "alpha summary"}):
            _enrich_home_artifact(artifact)

    proj = artifact["brief"]["projects"][0]
    # These SHOULD be present
    assert "repo" in proj, "Enrichment must add 'repo' field"
    assert "name" in proj, "Enrichment must add 'name' field"
    assert "icon" in proj, "Enrichment must add 'icon' field"
    assert "color" in proj, "Enrichment must add 'color' field"
    assert "briefSummary" in proj, "Enrichment must add 'briefSummary' field"


# ===========================================================================
# AC3: .pb-milestone CSS block removed
# ===========================================================================

def test_1845__ac3_pb_milestone_css_removed():
    """AC3: All .pb-milestone CSS rules must be removed from home.html."""
    home_html_path = DASHBOARD_DIR / "static" / "home.html"
    content = home_html_path.read_text(encoding="utf-8")

    # The .pb-milestone CSS class styling should not exist
    assert ".pb-milestone {" not in content, (
        ".pb-milestone CSS class definition must be removed"
    )
    assert ".pb-milestone." not in content, (
        ".pb-milestone pseudo-classes (:hover, :focus, etc.) must be removed"
    )

    # Related milestone CSS should also be removed
    assert ".pb-ms-name" not in content, (
        ".pb-ms-name CSS class must be removed"
    )
    assert ".pb-ms-count" not in content, (
        ".pb-ms-count CSS class must be removed"
    )


# ===========================================================================
# AC4: Home page loads with zero milestone-related requests
# ===========================================================================

@pytest.mark.skip(reason="Requires UAT server running; sprint_manager gates will verify")
def test_1845__ac4_api_brief_daily_no_milestone_field(client):
    """AC4: GET /api/brief/daily must not include 'milestone' field in projects."""
    resp = client.get("/api/brief/daily")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()
    projects = data.get("brief", {}).get("projects", [])

    # Even if empty, iterate over all projects and verify no milestone field
    for proj in projects:
        assert "milestone" not in proj, (
            f"Project {proj.get('project')} must not have 'milestone' in response"
        )


@pytest.mark.skip(reason="Requires UAT server running; sprint_manager gates will verify")
def test_1845__ac4_api_brief_daily_returns_required_fields(client):
    """AC4: /api/brief/daily must still return all non-milestone required fields."""
    resp = client.get("/api/brief/daily")
    assert resp.status_code == 200

    data = resp.json()
    # Basic structure checks
    assert "brief" in data, "Response must have 'brief' key"
    assert "projects" in data["brief"], "Brief must have 'projects' array"

    # If there are projects, check they have the right fields
    projects = data["brief"]["projects"]
    if projects:
        proj = projects[0]
        # These should be present (as per AC2)
        assert "repo" in proj, "Project must have 'repo' field"
        assert "name" in proj, "Project must have 'name' field"


def test_1845__ac4_loadmilestone_call_removed_from_init():
    """AC4: The projects.forEach(...loadMilestone...) block must be removed."""
    home_html_path = DASHBOARD_DIR / "static" / "home.html"
    content = home_html_path.read_text(encoding="utf-8")

    # The forEach block that checked p.milestone === undefined should be gone
    assert "p.milestone === undefined" not in content, (
        "Dead code path checking p.milestone === undefined must be removed"
    )
    assert "projects.forEach" not in content or (
        "loadMilestone" not in content
    ), (
        "The projects.forEach(...loadMilestone...) block must be removed"
    )


def test_1845__source_code_integration():
    """Verify that home_milestone_service import is removed from brief.py."""
    brief_py_path = DASHBOARD_DIR / "routers" / "brief.py"
    assert brief_py_path.exists(), "brief.py not found"

    content = brief_py_path.read_text(encoding="utf-8")

    # The import should not exist
    assert "from . import home_milestone_service" not in content, (
        "Import of home_milestone_service must be removed from brief.py"
    )

    # The enrichment call within _enrich_home_artifact should not exist
    # (check by looking for the try block that sets p["milestone"])
    assert 'p["milestone"]' not in content, (
        "Code that sets p['milestone'] must be removed from _enrich_home_artifact"
    )
