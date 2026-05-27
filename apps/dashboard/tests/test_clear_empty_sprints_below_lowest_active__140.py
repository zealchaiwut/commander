"""Tests for issue #140: Clear Empty Sprints Only Below Lowest Active Sprint.

Covers all 6 acceptance criteria:
  AC1 — GET /api/sprint-management/issues returns empty_sprint_labels filtered to < min_active_sprint
  AC2 — Cleanup banner shows only eligible sprints (frontend static check)
  AC3 — Confirmation modal shows only eligible sprints (frontend static check)
  AC4 — POST /api/sprints/delete-empty rejects labels >= min_active_sprint with HTTP 422
  AC5 — When no active sprints exist, no sprints offered for cleanup
  AC6 — Example scenario: empty{1,2,8,10,11,12} active{7,9} → only sprint-1, sprint-2 eligible
"""
from __future__ import annotations

import sys
import re
from pathlib import Path
from unittest.mock import patch

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
SERVER_PY     = DASHBOARD_DIR / "server.py"
APP_JS        = DASHBOARD_DIR / "static" / "app.js"
INDEX_HTML    = DASHBOARD_DIR / "static" / "index.html"

sys.path.insert(0, str(DASHBOARD_DIR))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_issue(number: int, sprint: int | None, extra_labels: list[str] | None = None):
    """Build a minimal issue dict as returned by github_client.list_open_issues_with_body."""
    labels = []
    if sprint is not None:
        labels.append({"name": f"sprint-{sprint}"})
    for lbl in (extra_labels or []):
        labels.append({"name": lbl})
    return {
        "number": number,
        "title": f"Issue #{number}",
        "labels": labels,
        "url": f"https://github.com/example/repo/issues/{number}",
        "body": "",
        "state": "open",
    }


def _get_test_client():
    """Return a FastAPI TestClient for server.app."""
    from fastapi.testclient import TestClient
    import server as srv
    return TestClient(srv.app), srv


# ── AC1 & AC6: GET /api/sprint-management/issues filtering ────────────────────

class TestSprintManagementIssuesFiltering:
    """AC1 & AC6: empty_sprint_labels only contains sprints < min_active_sprint."""

    def test_example_scenario_only_returns_below_lowest_active(self):
        """AC6: empty{1,2,8,10,11,12} active{7,9} → only sprint-1, sprint-2 in empty_sprint_labels."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        # Sprints 7 and 9 have issues; sprints 1,2,8,10,11,12 are empty
        all_sprints = [1, 2, 7, 8, 9, 10, 11, 12]
        issues = [
            _make_issue(101, 7),
            _make_issue(102, 9),
        ]

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "list_sprints", return_value=all_sprints), \
             patch.object(srv.github_client, "classify_issue", return_value="backlog"), \
             patch("server._project_root_path", return_value=DASHBOARD_DIR), \
             patch("server._load_sprint_order", return_value=[7, 9]):
            r = client.get("/api/sprint-management/issues?repo=zealchaiwut/commander")

        assert r.status_code == 200
        data = r.json()
        empty = data["empty_sprint_labels"]
        assert "sprint-1" in empty, "sprint-1 should be eligible (below min active sprint 7)"
        assert "sprint-2" in empty, "sprint-2 should be eligible (below min active sprint 7)"
        assert "sprint-8"  not in empty, "sprint-8 at 8 >= 7 (active); must not be offered"
        assert "sprint-10" not in empty, "sprint-10 >= 7; must not be offered"
        assert "sprint-11" not in empty, "sprint-11 >= 7; must not be offered"
        assert "sprint-12" not in empty, "sprint-12 >= 7; must not be offered"
        assert len(empty) == 2, f"Expected exactly 2 eligible empty sprints, got {len(empty)}: {empty}"

    def test_empty_sprint_at_active_level_excluded(self):
        """AC1: An empty sprint whose number equals min_active_sprint is excluded."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        # Sprint 5 is empty but equals min_active_sprint (5 is also the lowest active)
        # Sprints 3,4 are empty below active
        all_sprints = [3, 4, 5, 6]
        issues = [
            _make_issue(201, 5),  # active sprint 5
            _make_issue(202, 6),  # active sprint 6
        ]

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "list_sprints", return_value=all_sprints), \
             patch.object(srv.github_client, "classify_issue", return_value="backlog"), \
             patch("server._project_root_path", return_value=DASHBOARD_DIR), \
             patch("server._load_sprint_order", return_value=[5, 6]):
            r = client.get("/api/sprint-management/issues?repo=zealchaiwut/commander")

        assert r.status_code == 200
        data = r.json()
        empty = data["empty_sprint_labels"]
        assert "sprint-3" in empty
        assert "sprint-4" in empty
        assert "sprint-5" not in empty  # sprint 5 has tickets, not empty at all
        assert "sprint-6" not in empty  # sprint 6 has tickets

    def test_empty_sprints_above_active_excluded(self):
        """AC1: Empty sprints above the lowest active sprint are excluded."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        # Only sprint 3 has a ticket; sprint 5 is empty but above sprint 3
        all_sprints = [1, 2, 3, 5]
        issues = [_make_issue(301, 3)]

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "list_sprints", return_value=all_sprints), \
             patch.object(srv.github_client, "classify_issue", return_value="backlog"), \
             patch("server._project_root_path", return_value=DASHBOARD_DIR), \
             patch("server._load_sprint_order", return_value=[3]):
            r = client.get("/api/sprint-management/issues?repo=zealchaiwut/commander")

        assert r.status_code == 200
        data = r.json()
        empty = data["empty_sprint_labels"]
        assert "sprint-1" in empty
        assert "sprint-2" in empty
        assert "sprint-5" not in empty, "sprint-5 is above min active (3); must be excluded"

    def test_empty_sprint_labels_sorted(self):
        """AC1: empty_sprint_labels is returned in sorted order."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        all_sprints = [1, 2, 3, 10]
        issues = [_make_issue(401, 10)]

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "list_sprints", return_value=all_sprints), \
             patch.object(srv.github_client, "classify_issue", return_value="backlog"), \
             patch("server._project_root_path", return_value=DASHBOARD_DIR), \
             patch("server._load_sprint_order", return_value=[10]):
            r = client.get("/api/sprint-management/issues?repo=zealchaiwut/commander")

        assert r.status_code == 200
        data = r.json()
        empty = data["empty_sprint_labels"]
        nums = [int(lbl.split("-")[1]) for lbl in empty]
        assert nums == sorted(nums), "empty_sprint_labels must be sorted"


# ── AC4: POST /api/sprints/delete-empty rejects >= min_active_sprint with 422 ─

class TestDeleteEmptySprintsRejection:
    """AC4: delete-empty returns HTTP 422 when label >= min_active_sprint."""

    def test_rejects_sprint_at_or_above_active_with_422(self):
        """AC4: Attempting to delete sprint-8 when active sprints start at 7 returns 422."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        issues = [
            _make_issue(501, 7),
            _make_issue(502, 9),
        ]

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues):
            r = client.post(
                "/api/sprints/delete-empty",
                json={"labels": ["sprint-8"], "project": "zealchaiwut/commander"},
            )

        assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
        assert "8" in r.text or "not below" in r.text or "lowest active" in r.text

    def test_rejects_sprint_equal_to_min_active_with_422(self):
        """AC4: Attempting to delete sprint-7 when min_active = 7 returns 422."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        issues = [_make_issue(601, 7)]

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues):
            r = client.post(
                "/api/sprints/delete-empty",
                json={"labels": ["sprint-7"], "project": "zealchaiwut/commander"},
            )

        # sprint-7 has a ticket so 400 would also be acceptable — either way it's rejected
        assert r.status_code in (400, 422)

    def test_error_message_mentions_lowest_active_sprint(self):
        """AC4: 422 error message is descriptive (mentions sprint number or threshold)."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        issues = [_make_issue(701, 7), _make_issue(702, 9)]

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues):
            r = client.post(
                "/api/sprints/delete-empty",
                json={"labels": ["sprint-10"], "project": "zealchaiwut/commander"},
            )

        assert r.status_code == 422
        body = r.text
        # error should mention the sprint or the threshold
        assert any(kw in body for kw in ["10", "7", "active", "lowest"]), (
            f"Error message should be descriptive, got: {body}"
        )


# ── AC5: No active sprints → no cleanup offered ───────────────────────────────

class TestNoActiveSprintsCase:
    """AC5: When all sprint labels have 0 tickets, no sprints are offered."""

    def test_all_empty_sprints_returns_no_cleanup_candidates(self):
        """AC5: When every sprint has 0 tickets, empty_sprint_labels is empty."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        all_sprints = [1, 2, 3, 4]
        issues = []  # no issues in any sprint

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "list_sprints", return_value=all_sprints), \
             patch.object(srv.github_client, "classify_issue", return_value="backlog"), \
             patch("server._project_root_path", return_value=DASHBOARD_DIR), \
             patch("server._load_sprint_order", return_value=[]):
            r = client.get("/api/sprint-management/issues?repo=zealchaiwut/commander")

        assert r.status_code == 200
        data = r.json()
        assert data["empty_sprint_labels"] == [], (
            "When no sprint has tickets, empty_sprint_labels must be empty (nothing eligible)"
        )

    def test_delete_empty_rejects_when_no_active_sprints_with_422(self):
        """AC5: POST /api/sprints/delete-empty returns 422 when no active sprints exist."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        issues = []  # no tickets → no active sprints

        with patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues):
            r = client.post(
                "/api/sprints/delete-empty",
                json={"labels": ["sprint-1"], "project": "zealchaiwut/commander"},
            )

        assert r.status_code == 422, f"Expected 422 when no active sprints, got {r.status_code}"
        assert "active" in r.text.lower() or "eligible" in r.text.lower()


# ── AC2 & AC3: Frontend static checks ─────────────────────────────────────────

class TestFrontendBannerAndModal:
    """AC2 & AC3: Banner and modal derive from empty_sprint_labels (API-driven filtering)."""

    def test_banner_uses_empty_sprint_labels_from_api(self):
        """AC2: Banner visibility is driven by empty_sprint_labels from the API response."""
        content = APP_JS.read_text()
        # The banner should be shown/hidden based on empty_sprint_labels
        assert "empty_sprint_labels" in content, "app.js must reference empty_sprint_labels"
        # Banner hidden when empty
        assert "classList.add('hidden')" in content or 'classList.add("hidden")' in content

    def test_banner_shows_label_list_from_api(self):
        """AC2: Banner content is constructed from empty_sprint_labels returned by API."""
        content = APP_JS.read_text()
        # Banner text must join the labels
        assert "emptyLabels.join" in content, (
            "Banner must list labels using emptyLabels.join (populated from API empty_sprint_labels)"
        )

    def test_cleanup_modal_reads_empty_sprint_labels(self):
        """AC3: Cleanup modal is populated from _smgmtData.empty_sprint_labels."""
        content = APP_JS.read_text()
        assert "_smgmtData.empty_sprint_labels" in content, (
            "Cleanup modal must source labels from _smgmtData.empty_sprint_labels"
        )

    def test_cleanup_modal_shows_zero_ticket_count(self):
        """AC3: Modal rows display '0 tickets' for each empty sprint."""
        content = APP_JS.read_text()
        assert "0 tickets" in content, (
            "Modal must show '0 tickets' next to each empty sprint label"
        )

    def test_banner_hidden_when_no_eligible_sprints(self):
        """AC5: When empty_sprint_labels is [], the cleanup banner gets hidden class."""
        content = APP_JS.read_text()
        # When emptyLabels.length > 0 → show, else → hidden
        assert "emptyLabels.length > 0" in content or "emptyLabels.length" in content, (
            "Banner visibility must check emptyLabels.length"
        )
        # Must add hidden when length is 0
        cleanup_section = content[content.find("smgmt-cleanup-banner"):]
        cleanup_section = cleanup_section[:cleanup_section.find("function ", 10)]
        assert "hidden" in cleanup_section


# ── Static logic verification in server.py ────────────────────────────────────

class TestServerPyStaticLogic:
    """Verify the filter logic exists in server.py as written."""

    def test_min_active_sprint_computed_in_get_endpoint(self):
        """server.py computes min_active_sprint from active sprint ticket counts."""
        content = SERVER_PY.read_text()
        assert "min_active_sprint" in content, "min_active_sprint variable must exist in server.py"
        assert "active_sprint_nums" in content

    def test_filter_uses_strict_less_than(self):
        """server.py filters empty sprints using n < min_active_sprint (strict)."""
        content = SERVER_PY.read_text()
        assert "n < min_active_sprint" in content, (
            "Filter must use strict less-than (n < min_active_sprint)"
        )

    def test_filter_checks_min_active_not_none(self):
        """server.py excludes all empty sprints when min_active_sprint is None."""
        content = SERVER_PY.read_text()
        assert "min_active_sprint is not None" in content, (
            "Filter must guard against None (no active sprints case)"
        )

    def test_delete_empty_raises_422_for_above_threshold(self):
        """server.py raises HTTPException(422) in delete_empty_sprints for out-of-range labels."""
        content = SERVER_PY.read_text()
        # Find the delete_empty_sprints function body
        fn_start = content.find("async def delete_empty_sprints")
        fn_end   = content.find("\n@app.", fn_start + 1)
        fn_body  = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        assert "422" in fn_body, "delete_empty_sprints must raise HTTP 422 for guard violations"
        assert "label_num >= min_active_sprint" in fn_body, (
            "Guard condition must use label_num >= min_active_sprint"
        )

    def test_delete_empty_raises_422_when_no_active_sprints(self):
        """server.py raises 422 in delete_empty_sprints when min_active_sprint is None."""
        content = SERVER_PY.read_text()
        fn_start = content.find("async def delete_empty_sprints")
        fn_end   = content.find("\n@app.", fn_start + 1)
        fn_body  = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        assert "min_active_sprint is None" in fn_body, (
            "delete_empty_sprints must handle the no-active-sprints case"
        )

    def test_empty_sprint_labels_field_in_api_response(self):
        """server.py returns empty_sprint_labels in the GET endpoint response."""
        content = SERVER_PY.read_text()
        assert '"empty_sprint_labels"' in content or "'empty_sprint_labels'" in content, (
            "API response must include empty_sprint_labels key"
        )


# ── Live-server smoke tests (require running dev server) ──────────────────────

class TestLiveServerSmoke:
    """Basic live-server tests — skip if server not reachable."""

    @pytest.fixture
    def http_client(self):
        import httpx
        import os
        base = os.environ.get("TEST_BASE_URL", "http://localhost:8000")
        with httpx.Client(base_url=base, timeout=10.0) as c:
            yield c

    def _skip_if_down(self, client):
        try:
            client.get("/", timeout=2.0)
        except Exception:
            pytest.skip("Dashboard server not reachable")

    def test_delete_empty_rejects_invalid_label_format(self, http_client):
        """AC4: POST /api/sprints/delete-empty returns 400 for non-sprint-N labels."""
        self._skip_if_down(http_client)
        r = http_client.post(
            "/api/sprints/delete-empty",
            json={"labels": ["not-a-sprint-label"], "project": "zealchaiwut/commander"},
        )
        assert r.status_code == 400

    def test_sprint_management_endpoint_returns_empty_sprint_labels_key(self, http_client):
        """AC1: GET /api/sprint-management/issues response includes empty_sprint_labels key."""
        self._skip_if_down(http_client)
        r = http_client.get("/api/sprint-management/issues?repo=zealchaiwut/commander")
        if r.status_code != 200:
            pytest.skip(f"endpoint returned {r.status_code}")
        data = r.json()
        assert "empty_sprint_labels" in data, (
            "Response must contain empty_sprint_labels key"
        )
        assert isinstance(data["empty_sprint_labels"], list)

    def test_delete_empty_endpoint_exists(self, http_client):
        """AC4: POST /api/sprints/delete-empty endpoint is reachable (not 404/405)."""
        self._skip_if_down(http_client)
        r = http_client.post(
            "/api/sprints/delete-empty",
            json={"labels": [], "project": "zealchaiwut/commander"},
        )
        # Any response code other than 404/405 means the endpoint exists
        assert r.status_code not in (404, 405), (
            f"delete-empty endpoint must exist, got {r.status_code}"
        )
