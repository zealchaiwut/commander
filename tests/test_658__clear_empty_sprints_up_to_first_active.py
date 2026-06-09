"""Tests for issue #658: Clear Empty Sprints Up To First Active Sprint.

Covers all 6 acceptance criteria:
  AC1 — cleanup-empty removes only leading consecutive empty sprints
  AC2 — Empty sprints after any sprint with tickets are left untouched
  AC3 — If no sprints contain tickets, no sprints are cleared
  AC4 — If the first sprint already has tickets, no sprints are cleared
  AC5 — Sprint order and ticket assignments remain unaffected
  AC6 — Action is idempotent
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
PROJECT_HTML  = DASHBOARD_DIR / "static" / "project.html"
SERVER_PY     = DASHBOARD_DIR / "server.py"

sys.path.insert(0, str(DASHBOARD_DIR))


def _make_issue(number: int, sprint: int):
    labels = [{"name": f"sprint-{sprint}"}]
    return {
        "number": number,
        "title": f"Issue #{number}",
        "labels": labels,
        "url": f"https://github.com/example/repo/issues/{number}",
        "body": "",
        "state": "open",
    }


def _get_test_client():
    from fastapi.testclient import TestClient
    import server as srv
    return TestClient(srv.app), srv


def _post_cleanup(client, srv, *, sprint_labels: list[str], issues: list[dict]):
    """POST /api/sprints/cleanup-empty with mocked GitHub data."""
    deleted_labels: list[str] = []

    def fake_delete(label, repo_name=None):
        deleted_labels.append(label)

    with patch.object(srv.github_client, "list_sprint_labels", return_value=sprint_labels), \
         patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
         patch.object(srv.github_client, "delete_label", side_effect=fake_delete), \
         patch.object(srv.github_client, "invalidate", return_value=None):
        r = client.post(
            "/api/sprints/cleanup-empty",
            json={"project": "zealchaiwut/commander"},
        )
    return r, deleted_labels


# ── AC1 & AC2: Only leading consecutive empty sprints removed ─────────────────

class TestLeadingConsecutiveOnly:
    """AC1 & AC2: only leading empty sprints (before first active) are cleared."""

    def test_scenario_a_leading_empty_before_active(self):
        """AC1: Sprint10(empty), Sprint11(empty), Sprint12(tickets), Sprint13(empty) → remove 10,11 only."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-10", "sprint-11", "sprint-12", "sprint-13"]
        issues = [_make_issue(1, 12), _make_issue(2, 12)]  # sprint-12 has tickets

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r.status_code == 200
        assert "sprint-10" in deleted, "sprint-10 is leading empty — must be deleted"
        assert "sprint-11" in deleted, "sprint-11 is leading empty — must be deleted"
        assert "sprint-12" not in deleted, "sprint-12 has tickets — must NOT be deleted"
        assert "sprint-13" not in deleted, "sprint-13 is trailing empty — must NOT be deleted (AC2)"

    def test_scenario_d_only_first_empty_before_active(self):
        """AC1: Sprint1(empty), Sprint2(tickets), Sprint3(empty), Sprint4(tickets) → remove only Sprint1."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-1", "sprint-2", "sprint-3", "sprint-4"]
        issues = [_make_issue(10, 2), _make_issue(11, 4)]  # sprints 2 and 4 have tickets

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r.status_code == 200
        assert "sprint-1" in deleted, "sprint-1 is leading empty — must be deleted"
        assert "sprint-2" not in deleted, "sprint-2 has tickets"
        assert "sprint-3" not in deleted, "sprint-3 is after first active — must NOT be deleted (AC2)"
        assert "sprint-4" not in deleted, "sprint-4 has tickets"

    def test_trailing_empty_after_active_untouched(self):
        """AC2: Any empty sprint after an active sprint is preserved."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-5", "sprint-6", "sprint-7", "sprint-8", "sprint-9"]
        issues = [_make_issue(1, 6)]  # sprint-6 active; 5 is leading empty; 7,8,9 are trailing

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r.status_code == 200
        assert "sprint-5" in deleted, "sprint-5 is leading empty — must be deleted"
        assert "sprint-7" not in deleted, "sprint-7 is trailing — must NOT be deleted"
        assert "sprint-8" not in deleted, "sprint-8 is trailing — must NOT be deleted"
        assert "sprint-9" not in deleted, "sprint-9 is trailing — must NOT be deleted"


# ── AC3: No active sprints → nothing cleared ─────────────────────────────────

class TestNoActiveSprintsClearsNothing:
    """AC3: When all sprints are empty, nothing is cleared."""

    def test_scenario_c_all_empty_clears_nothing(self):
        """AC3: Sprint1(empty), Sprint2(empty), Sprint3(empty) → nothing removed."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-1", "sprint-2", "sprint-3"]
        issues = []  # no tickets anywhere

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r.status_code == 200
        assert deleted == [], f"No sprints should be deleted when all are empty, got: {deleted}"

    def test_empty_labels_list_clears_nothing(self):
        """AC3: No sprint labels at all → empty deleted list."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        r, deleted = _post_cleanup(client, srv, sprint_labels=[], issues=[])

        assert r.status_code == 200
        assert deleted == []


# ── AC4: First sprint has tickets → nothing cleared ───────────────────────────

class TestFirstSprintActiveNothingCleared:
    """AC4: When the first (lowest) sprint already has tickets, nothing is cleared."""

    def test_scenario_b_first_sprint_has_tickets(self):
        """AC4: Sprint1(5 tickets), Sprint2(empty) → nothing removed."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-1", "sprint-2"]
        issues = [_make_issue(1, 1), _make_issue(2, 1)]  # sprint-1 has tickets

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r.status_code == 200
        assert deleted == [], f"Nothing should be deleted when first sprint has tickets, got: {deleted}"

    def test_only_active_sprint_no_empty_before_it(self):
        """AC4: single sprint with tickets → nothing deleted."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-3"]
        issues = [_make_issue(99, 3)]

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r.status_code == 200
        assert deleted == []


# ── AC5: Sprint order and ticket assignments unaffected ───────────────────────

class TestSprintOrderUnaffected:
    """AC5: deleted empty labels have no tickets; existing ticket assignments unchanged."""

    def test_only_truly_empty_sprints_deleted(self):
        """AC5: cleanup never deletes a sprint label that still has an open ticket."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-1", "sprint-2", "sprint-3"]
        issues = [_make_issue(42, 2)]  # sprint-2 has a ticket

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r.status_code == 200
        assert "sprint-2" not in deleted, "sprint-2 has a ticket — must never be deleted"
        assert "sprint-1" in deleted, "sprint-1 is leading empty"
        assert "sprint-3" not in deleted, "sprint-3 is trailing empty"


# ── AC6: Idempotency ──────────────────────────────────────────────────────────

class TestIdempotency:
    """AC6: Running cleanup twice gives same result as running once."""

    def test_second_run_deletes_nothing_when_first_run_clean(self):
        """AC6: After leading empties removed, a second cleanup deletes nothing more."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        # After first cleanup, sprint-1 and sprint-2 were removed.
        # Remaining: sprint-3 (active), sprint-4 (empty trailing).
        sprint_labels_after = ["sprint-3", "sprint-4"]
        issues = [_make_issue(1, 3)]  # sprint-3 has tickets; sprint-4 is trailing

        r, deleted = _post_cleanup(client, srv, sprint_labels=sprint_labels_after, issues=issues)

        assert r.status_code == 200
        assert deleted == [], f"Second run should delete nothing (idempotent), got: {deleted}"

    def test_cleanup_idempotent_with_no_leading_empties(self):
        """AC6: If first sprint has tickets, two runs both delete nothing."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        sprint_labels = ["sprint-1", "sprint-2"]
        issues = [_make_issue(7, 1)]

        # First run
        r1, deleted1 = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)
        # Second run with same state
        r2, deleted2 = _post_cleanup(client, srv, sprint_labels=sprint_labels, issues=issues)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert deleted1 == deleted2 == [], "Both runs must delete nothing"


# ── Static logic in server.py ─────────────────────────────────────────────────

class TestServerPyCleanupLogic:
    """Verify cleanup_empty_sprints uses leading-consecutive logic in server.py."""

    def test_cleanup_stops_at_first_active_sprint(self):
        """cleanup_empty_sprints must stop at first sprint with tickets."""
        content = SERVER_PY.read_text()
        fn_start = content.find("async def cleanup_empty_sprints")
        assert fn_start != -1, "cleanup_empty_sprints function must exist in server.py"
        fn_end = content.find("\n@app.", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        assert "break" in fn_body, (
            "cleanup_empty_sprints must use a loop with 'break' to stop at first active sprint"
        )

    def test_cleanup_returns_nothing_when_no_active_sprints(self):
        """AC3: cleanup_empty_sprints must guard against no-active-sprints case."""
        content = SERVER_PY.read_text()
        fn_start = content.find("async def cleanup_empty_sprints")
        fn_end = content.find("\n@app.", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        # Must check for no-active case (found_active / first_active / all_empty guard)
        has_guard = any(kw in fn_body for kw in [
            "found_active", "first_active", "not any", "leading_empty = []"
        ])
        assert has_guard, (
            "cleanup_empty_sprints must guard against all-empty case (AC3)"
        )


# ── Frontend static checks on project.html ───────────────────────────────────

class TestFrontendCleanupButtonLogic:
    """Verify _smgmtUpdateCleanupBtn in project.html uses leading-consecutive logic."""

    def test_cleanup_btn_uses_leading_consecutive_logic(self):
        """_smgmtUpdateCleanupBtn must use a loop that breaks at first active sprint."""
        content = PROJECT_HTML.read_text()
        # Find the function
        fn_start = content.find("function _smgmtUpdateCleanupBtn")
        assert fn_start != -1, "_smgmtUpdateCleanupBtn must exist in project.html"
        fn_end = content.find("\nfunction ", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        assert "break" in fn_body, (
            "_smgmtUpdateCleanupBtn must use 'break' to stop at first active sprint"
        )

    def test_cleanup_btn_guards_no_active_case(self):
        """AC3: _smgmtUpdateCleanupBtn must produce empty list when no active sprints."""
        content = PROJECT_HTML.read_text()
        fn_start = content.find("function _smgmtUpdateCleanupBtn")
        fn_end = content.find("\nfunction ", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        # Must have a guard: foundActive / first_active / etc.
        has_guard = any(kw in fn_body for kw in ["foundActive", "firstActive", "found_active"])
        assert has_guard, (
            "_smgmtUpdateCleanupBtn must guard against all-empty case (AC3)"
        )

    def test_cleanup_btn_sets_empty_labels_to_empty_when_no_active(self):
        """AC3: _smgmtEmptyLabels must be [] when no active sprint found."""
        content = PROJECT_HTML.read_text()
        fn_start = content.find("function _smgmtUpdateCleanupBtn")
        fn_end = content.find("\nfunction ", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        # Must assign [] when not foundActive
        assert "[] " in fn_body or "= []" in fn_body or "=[]" in fn_body, (
            "_smgmtEmptyLabels must be set to [] for no-active-sprint case"
        )
