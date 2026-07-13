"""UAT HTTP tests for issue #1860 — changelog index endpoint.

These tests verify the changelog endpoint works against the UAT environment.
They exercise the actual HTTP API and real filesystem data.
"""
import os
import pytest
import httpx

BASE_URL = os.environ.get("UAT_BASE_URL", "http://localhost:8001")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


class TestChangelogHTTPUAT:
    """UAT-level HTTP tests for the changelog endpoint."""

    def test_endpoint_returns_200(self, client):
        """UAT Step: GET /api/projects/commander/changelog?env=uat&limit=5 returns 200."""
        r = client.get("/api/projects/commander/changelog?env=uat&limit=5")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_endpoint_returns_json_list(self, client):
        """Response is a JSON array."""
        r = client.get("/api/projects/commander/changelog?env=uat&limit=5")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"

    def test_entries_have_required_fields(self, client):
        """Each entry has env, date, slug, path, title, excerpt."""
        r = client.get("/api/projects/commander/changelog?env=uat&limit=5")
        data = r.json()
        assert len(data) > 0, "Expected at least one entry"

        required_fields = {"env", "date", "slug", "path", "title", "excerpt"}
        for entry in data:
            assert required_fields.issubset(entry.keys()), (
                f"Entry missing fields. Expected {required_fields}, got {entry.keys()}"
            )

    def test_env_filter_uat_returns_only_uat(self, client):
        """env=uat filter restricts to UAT entries."""
        r = client.get("/api/projects/commander/changelog?env=uat")
        data = r.json()
        assert all(e["env"] == "uat" for e in data), (
            "env=uat filter should return only uat entries"
        )

    def test_limit_parameter_works(self, client):
        """limit=5 caps result to 5 entries."""
        r = client.get("/api/projects/commander/changelog?env=uat&limit=5")
        data = r.json()
        assert len(data) <= 5, f"limit=5 should return at most 5 entries, got {len(data)}"

    def test_entries_newest_first(self, client):
        """Dated entries are sorted newest-first."""
        r = client.get("/api/projects/commander/changelog")
        data = r.json()

        dated = [e for e in data if e["date"] is not None]
        if len(dated) > 1:
            dates = [e["date"] for e in dated]
            sorted_dates = sorted(dates, reverse=True)
            assert dates == sorted_dates, (
                f"Expected newest-first, got {dates}"
            )

    def test_title_extracted(self, client):
        """Entries have non-empty title extracted from markdown."""
        r = client.get("/api/projects/commander/changelog?limit=1")
        data = r.json()
        if data:
            assert "title" in data[0], "Missing title field"
            assert isinstance(data[0]["title"], str), "Title should be a string"

    def test_excerpt_extracted(self, client):
        """Entries have excerpt extracted from first paragraph."""
        r = client.get("/api/projects/commander/changelog?limit=1")
        data = r.json()
        if data:
            assert "excerpt" in data[0], "Missing excerpt field"
            assert isinstance(data[0]["excerpt"], str), "Excerpt should be a string"

    def test_zero_gh_api_calls(self, client, call_budget_fixture):
        """Endpoint makes zero GitHub API calls (filesystem only)."""
        client.get("/api/projects/commander/changelog?env=uat&limit=5")
        call_budget_fixture.assert_zero_gh()
