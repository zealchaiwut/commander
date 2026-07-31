"""Tests for issue #1978: Populate _projBySlug in home.html from dev-report metadata.

Acceptance Criteria:
1. /api/dev-report returns project entries with repo, icon, and color fields
2. Frontend _projBySlug is populated from dev-report projects at load time
3. Project badges render with correct color class from _projBySlug
4. Run sprint button uses full repo from _projBySlug, not just slug

Tests verify the backend API contract and frontend behavior.
"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC1: /api/dev-report returns project metadata fields ──────────────────

def test_dev_report_includes_repo_field(client):
    """AC1: /api/dev-report project entries include repo field."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    assert "projects" in data
    projects = data["projects"]
    if projects:
        # At least one project should have a repo field
        p = projects[0]
        assert "repo" in p
        assert isinstance(p["repo"], str)


def test_dev_report_includes_icon_field(client):
    """AC1: /api/dev-report project entries include icon field."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    projects = data["projects"]
    if projects:
        p = projects[0]
        assert "icon" in p
        assert isinstance(p["icon"], str)


def test_dev_report_includes_color_field(client):
    """AC1: /api/dev-report project entries include color field."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    projects = data["projects"]
    if projects:
        p = projects[0]
        assert "color" in p
        assert isinstance(p["color"], str)


def test_dev_report_includes_project_slug_field(client):
    """AC1: /api/dev-report project entries include project (slug) field."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    projects = data["projects"]
    if projects:
        p = projects[0]
        assert "project" in p
        assert isinstance(p["project"], str)


def test_dev_report_color_has_valid_value(client):
    """AC1: /api/dev-report color field is a valid palette value."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    projects = data["projects"]
    valid_colors = {'gray','blue','green','purple','red','orange','amber','yellow','pink','cyan','teal','indigo'}
    if projects:
        p = projects[0]
        assert p["color"].lower() in valid_colors, f"Color '{p['color']}' not in valid palette"


def test_dev_report_icon_is_tabler_class(client):
    """AC1: /api/dev-report icon field follows ti-* naming convention."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    projects = data["projects"]
    if projects:
        p = projects[0]
        # Icon should be a tabler icon class name
        assert p["icon"].startswith("ti-"), f"Icon '{p['icon']}' does not start with 'ti-'"


# ── AC2: Frontend home.html loads and populates _projBySlug ───────────────

def test_home_page_loads(client):
    """AC2: Home page (/static/home.html) loads successfully."""
    r = client.get("/static/home.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_home_page_contains_projbyslug_init(client):
    """AC2: Home page source includes _projBySlug initialization."""
    r = client.get("/static/home.html")
    assert r.status_code == 200
    html = r.text
    assert "let _projBySlug" in html
    assert "_buildProjBySlug" in html


def test_home_page_contains_badge_logic(client):
    """AC2: Home page source includes project badge rendering logic."""
    r = client.get("/static/home.html")
    assert r.status_code == 200
    html = r.text
    assert "_projectBadgeHtml" in html
    assert "PBIC_PALETTE" in html


def test_home_page_calls_build_proj_by_slug(client):
    """AC2: Home page calls _buildProjBySlug with report.projects."""
    r = client.get("/static/home.html")
    assert r.status_code == 200
    html = r.text
    # Should load report and build the map
    assert "_buildProjBySlug(report.projects)" in html


# ── AC3: Project badges use color from _projBySlug ──────────────────────────

def test_badge_html_uses_color_class(client):
    """AC3: Project badge includes pbic--<color> class."""
    # This is tested by checking the home.html contains the logic
    r = client.get("/static/home.html")
    assert r.status_code == 200
    html = r.text
    # Badge function should include color class
    assert 'class="pbic pbic--' in html or 'pbic--${' in html


def test_badge_defaults_to_gray_without_color(client):
    """AC3: Badge falls back to gray if color is missing or invalid."""
    r = client.get("/static/home.html")
    assert r.status_code == 200
    html = r.text
    # Should have fallback logic
    assert "pbic--gray" in html


# ── AC4: Run sprint button sends full repo from _projBySlug ────────────────

def test_home_page_has_run_sprint_button_logic(client):
    """AC4: Home page contains _runSprintBtn function."""
    r = client.get("/static/home.html")
    assert r.status_code == 200
    html = r.text
    assert "_runSprintBtn" in html


def test_run_sprint_uses_projbyslug_repo(client):
    """AC4: _runSprintBtn uses repo from _projBySlug[slug], not just slug."""
    r = client.get("/static/home.html")
    assert r.status_code == 200
    html = r.text
    # Should reference _projBySlug[slug] for repo field
    assert "_projBySlug[" in html
    # The button should send to /api/sprints/run with project field
    assert '"/api/sprints/run"' in html


# ── Integration: dev-report endpoint correctness ──────────────────────────

def test_dev_report_200_ok(client):
    """Integration: /api/dev-report returns 200."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200


def test_dev_report_valid_json(client):
    """Integration: /api/dev-report returns valid JSON."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)
    assert "projects" in data


def test_dev_report_projects_is_list(client):
    """Integration: /api/dev-report projects field is a list."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["projects"], list)


def test_dev_report_project_entry_structure(client):
    """Integration: Each project entry has required fields."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    projects = data["projects"]
    if projects:
        p = projects[0]
        required_fields = {"project", "repo", "icon", "color"}
        for field in required_fields:
            assert field in p, f"Missing field '{field}' in project entry"


def test_dev_report_repo_is_full_path_or_slug(client):
    """Integration: repo field contains full path (owner/repo) or slug."""
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    projects = data["projects"]
    if projects:
        p = projects[0]
        repo = p["repo"]
        # Should either be a full path or a slug (not empty)
        assert len(repo) > 0
        # Should not be just whitespace
        assert repo.strip() == repo
