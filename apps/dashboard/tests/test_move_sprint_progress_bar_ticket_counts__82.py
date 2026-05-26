"""
Tests for issue #82: Move sprint progress bar, ticket counts, and status alerts
to project-card level; replace top-level metrics with project aggregates.

Acceptance Criteria:
  AC-1: Each project card shows its own sprint progress bar (per-project progress).
  AC-2: Each project card shows ticket-count chips (active/UAT counts).
  AC-3: Each project card can show status alerts scoped to that project.
  AC-4: Top-level metrics row replaced with aggregate values (active_projects,
        open_tickets, active_sprints).
  AC-5: Old global progress bar / per-project chip duplication removed — no broken layout.
  AC-6: Responsive display at 375 px width (visual/mobile — MANUAL).

API-level tests (AC-1..5): verified against http://localhost:8001
Visual tests (AC-6): marked pytest.skip as MANUAL.
"""
import pytest
import httpx
import os

BASE_URL = os.environ.get("UAT_BASE_URL", os.environ.get("TEST_BASE_URL", "http://localhost:8001"))


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as c:
        yield c


# ---------------------------------------------------------------------------
# AC-1 — /api/projects: each project entry has per-project progress fields
# ---------------------------------------------------------------------------
class TestAC1PerProjectProgress:
    """Each project card exposes per-project sprint progress (done / total, pct)."""

    def test_projects_endpoint_returns_200(self, client):
        r = client.get("/api/projects")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_each_project_has_progress_field(self, client):
        data = client.get("/api/projects").json()
        projects = data.get("projects", [])
        assert len(projects) > 0, "No projects found — need at least one project"
        for proj in projects:
            assert "progress" in proj, f"Project {proj.get('repo')} missing 'progress' field"

    def test_progress_has_closed_total_pct(self, client):
        data = client.get("/api/projects").json()
        projects = data.get("projects", [])
        for proj in projects:
            progress = proj["progress"]
            assert "closed" in progress, f"'closed' missing in progress for {proj.get('repo')}"
            assert "total"  in progress, f"'total' missing in progress for {proj.get('repo')}"
            assert "pct"    in progress, f"'pct' missing in progress for {proj.get('repo')}"
            assert isinstance(progress["closed"], int), "'closed' must be int"
            assert isinstance(progress["total"],  int), "'total' must be int"
            assert isinstance(progress["pct"],    (int, float)), "'pct' must be numeric"
            assert 0 <= progress["pct"] <= 100, f"'pct' out of range: {progress['pct']}"

    def test_each_project_has_bar_status(self, client):
        data = client.get("/api/projects").json()
        for proj in data.get("projects", []):
            assert "bar_status" in proj, f"Missing bar_status in {proj.get('repo')}"
            assert proj["bar_status"] in ("good", "warning", "late", "idle"), \
                f"Unexpected bar_status value: {proj['bar_status']}"

    def test_each_project_has_sprint_label(self, client):
        """sprint_label field must be present (may be empty string when no sprint)."""
        data = client.get("/api/projects").json()
        for proj in data.get("projects", []):
            assert "sprint_label" in proj, f"Missing sprint_label in {proj.get('repo')}"


# ---------------------------------------------------------------------------
# AC-2 — /api/projects: each project has activeCount and uatCount chips
# ---------------------------------------------------------------------------
class TestAC2TicketCountChips:
    """Each project card exposes activeCount and uatCount for per-project chips."""

    def test_each_project_has_activeCount(self, client):
        data = client.get("/api/projects").json()
        for proj in data.get("projects", []):
            assert "activeCount" in proj, f"Missing activeCount in {proj.get('repo')}"
            assert isinstance(proj["activeCount"], int), "activeCount must be int"
            assert proj["activeCount"] >= 0

    def test_each_project_has_uatCount(self, client):
        data = client.get("/api/projects").json()
        for proj in data.get("projects", []):
            assert "uatCount" in proj, f"Missing uatCount in {proj.get('repo')}"
            assert isinstance(proj["uatCount"], int), "uatCount must be int"
            assert proj["uatCount"] >= 0

    def test_each_project_has_openCount(self, client):
        data = client.get("/api/projects").json()
        for proj in data.get("projects", []):
            assert "openCount" in proj, f"Missing openCount in {proj.get('repo')}"
            assert isinstance(proj["openCount"], int), "openCount must be int"

    def test_uatCount_le_openCount(self, client):
        """UAT tickets are a subset of open tickets."""
        data = client.get("/api/projects").json()
        for proj in data.get("projects", []):
            assert proj["uatCount"] <= proj["openCount"], \
                f"uatCount ({proj['uatCount']}) > openCount ({proj['openCount']}) for {proj['repo']}"

    def test_activeCount_le_openCount(self, client):
        """Active tickets (in-progress/SIT/UAT) are a subset of open tickets."""
        data = client.get("/api/projects").json()
        for proj in data.get("projects", []):
            assert proj["activeCount"] <= proj["openCount"], \
                f"activeCount ({proj['activeCount']}) > openCount ({proj['openCount']}) for {proj['repo']}"


# ---------------------------------------------------------------------------
# AC-3 — /api/alerts: alert payload supports per-project repo field
# ---------------------------------------------------------------------------
class TestAC3PerProjectAlerts:
    """Alerts carry a 'repo' field so the frontend can attribute them to a project."""

    def _create_alert(self, client, repo=None):
        payload = {"title": "Test blocked", "body": "AC-3 test", "category": "blocked"}
        if repo:
            payload["repo"] = repo
        r = client.post("/api/alerts", json=payload)
        assert r.status_code == 201
        return r.json()

    def _cleanup_alerts(self, client):
        alerts = client.get("/api/alerts").json()
        for idx in range(len(alerts) - 1, -1, -1):
            client.delete(f"/api/alerts/{idx}")

    def test_alert_with_repo_field_accepted(self, client):
        r = client.post("/api/alerts", json={
            "title": "Blocked ticket",
            "body": "Some ticket is blocked",
            "repo": "zealchaiwut/commander",
            "category": "blocked",
        })
        assert r.status_code == 201
        # Cleanup
        alerts = client.get("/api/alerts").json()
        for idx in range(len(alerts) - 1, -1, -1):
            client.delete(f"/api/alerts/{idx}")

    def test_get_alerts_returns_repo_field(self, client):
        """GET /api/alerts returns the repo field on each alert object."""
        self._create_alert(client, repo="zealchaiwut/commander")
        alerts = client.get("/api/alerts").json()
        assert len(alerts) >= 1
        # Find the test alert
        test_alert = next((a for a in alerts if a.get("title") == "Test blocked"), None)
        assert test_alert is not None, "Created alert not found in GET /api/alerts"
        assert "repo" in test_alert, "Alert missing 'repo' field"
        assert test_alert["repo"] == "zealchaiwut/commander"
        self._cleanup_alerts(client)

    def test_alert_without_repo_field_returns_null(self, client):
        """Alerts without a repo field should return repo: null."""
        self._create_alert(client, repo=None)
        alerts = client.get("/api/alerts").json()
        test_alert = next((a for a in alerts if a.get("title") == "Test blocked"), None)
        assert test_alert is not None
        assert test_alert.get("repo") is None
        self._cleanup_alerts(client)

    def test_alert_category_field_preserved(self, client):
        """Alert category field is stored and returned."""
        self._create_alert(client, repo="zealchaiwut/commander")
        alerts = client.get("/api/alerts").json()
        test_alert = next((a for a in alerts if a.get("title") == "Test blocked"), None)
        assert test_alert is not None
        assert test_alert.get("category") == "blocked"
        self._cleanup_alerts(client)


# ---------------------------------------------------------------------------
# AC-4 — /api/projects metrics: replaced with aggregate values
# ---------------------------------------------------------------------------
class TestAC4AggregateMetrics:
    """Top-level metrics row shows project-aggregate values."""

    def test_metrics_has_active_projects(self, client):
        data = client.get("/api/projects").json()
        metrics = data.get("metrics", {})
        assert "active_projects" in metrics, "metrics missing 'active_projects'"
        assert isinstance(metrics["active_projects"], int)
        assert metrics["active_projects"] >= 0

    def test_metrics_has_open_tickets(self, client):
        data = client.get("/api/projects").json()
        metrics = data.get("metrics", {})
        assert "open_tickets" in metrics, "metrics missing 'open_tickets'"
        assert isinstance(metrics["open_tickets"], int)
        assert metrics["open_tickets"] >= 0

    def test_metrics_has_active_sprints(self, client):
        data = client.get("/api/projects").json()
        metrics = data.get("metrics", {})
        assert "active_sprints" in metrics, "metrics missing 'active_sprints'"
        assert isinstance(metrics["active_sprints"], int)
        assert metrics["active_sprints"] >= 0

    def test_metrics_open_tickets_equals_sum_of_projects(self, client):
        """Total open_tickets in metrics equals sum of openCount across projects."""
        data = client.get("/api/projects").json()
        metrics = data.get("metrics", {})
        projects = data.get("projects", [])
        expected = sum(p["openCount"] for p in projects)
        assert metrics["open_tickets"] == expected, \
            f"metrics.open_tickets ({metrics['open_tickets']}) != sum of openCounts ({expected})"

    def test_metrics_active_projects_le_total_projects(self, client):
        """active_projects cannot exceed total number of projects."""
        data = client.get("/api/projects").json()
        metrics = data.get("metrics", {})
        total = len(data.get("projects", []))
        assert metrics["active_projects"] <= total, \
            f"active_projects ({metrics['active_projects']}) > total projects ({total})"


# ---------------------------------------------------------------------------
# AC-5 — /api/projects: old global duplication not present, no broken layout
# (verified indirectly: old fields are absent, project-level fields remain)
# ---------------------------------------------------------------------------
class TestAC5NoGlobalDuplication:
    """The old global sprint-progress fields are no longer in the top-level metrics."""

    def test_metrics_does_not_have_sprint_progress_pct(self, client):
        """Old global progress_pct field should not exist in metrics."""
        data = client.get("/api/projects").json()
        metrics = data.get("metrics", {})
        assert "progress_pct" not in metrics, \
            "Old 'progress_pct' field still present in metrics — AC-5 violation"

    def test_project_details_has_required_fields(self, client):
        """GET /api/project-details returns tickets, agents, github_url."""
        data = client.get("/api/projects").json()
        projects = data.get("projects", [])
        if not projects:
            pytest.skip("No projects configured")
        repo = projects[0]["repo"]
        r = client.get("/api/project-details", params={"repo": repo})
        assert r.status_code == 200
        detail = r.json()
        for field in ("tickets", "agents", "github_url"):
            assert field in detail, f"project-details missing '{field}'"

    def test_html_page_loads_without_error(self, client):
        """Dashboard index.html loads with 200 status."""
        r = client.get("/")
        assert r.status_code == 200
        assert "Commander" in r.text, "Dashboard page missing 'Commander' title"

    def test_html_no_sprint_panel_id(self, client):
        """The old global sprint-panel element should be removed from the HTML."""
        r = client.get("/")
        html = r.text
        assert 'id="sprint-panel"' not in html, \
            "Old 'sprint-panel' element still present in HTML — AC-5 / AC-1 violation"


# ---------------------------------------------------------------------------
# AC-6 — Responsive display at 375 px (VISUAL / MANUAL)
# ---------------------------------------------------------------------------
class TestAC6ResponsiveMobile:
    def test_mobile_display(self):
        pytest.skip(
            "MANUAL: Open dashboard at 375 px width (or via Tailscale on mobile). "
            "Verify project cards stack vertically; progress bars, chips, and alerts "
            "are fully visible without horizontal scrolling."
        )
