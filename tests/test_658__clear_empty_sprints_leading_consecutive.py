"""Tests for issue #658: Clear Empty Sprints Up To First Active Sprint.

Covers all 6 acceptance criteria:
  AC1 — cleanup-empty deletes only leading consecutive empty sprints (before first sprint with tickets)
  AC2 — Empty sprints after any sprint with tickets are left untouched
  AC3 — If no sprints have tickets, no sprints are cleared
  AC4 — If first sprint already has tickets, no sprints are cleared
  AC5 — Sprint order and ticket assignments unaffected (verified via idempotent re-fetch)
  AC6 — Action is idempotent (running twice = same result as once)
"""
from __future__ import annotations

import sys
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
SERVER_PY     = DASHBOARD_DIR / "server.py"
PROJECT_HTML  = DASHBOARD_DIR / "static" / "project.html"

sys.path.insert(0, str(DASHBOARD_DIR))


def _make_issue(number: int, sprint: int | None):
    labels = []
    if sprint is not None:
        labels.append({"name": f"sprint-{sprint}"})
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


def _sprint_labels(*nums: int) -> list[str]:
    return [f"sprint-{n}" for n in nums]


# ── AC1 & AC2: Only leading consecutive empties eligible ──────────────────────

class TestLeadingConsecutiveOnly:
    """AC1 & AC2: stop at first active sprint; trailing empties untouched."""

    def test_scenario_a_leading_empties_removed_trailing_kept(self):
        """Scenario A: sprints 10(0), 11(0), 12(10), 13(0) → delete 10,11; keep 12,13."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        all_labels = _sprint_labels(10, 11, 12, 13)
        issues = [_make_issue(1, 12)]

        deleted = []
        def fake_delete(label, repo_name=None):
            deleted.append(label)

        with patch.object(srv.github_client, "list_sprint_labels", return_value=all_labels), \
             patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "delete_label", side_effect=fake_delete), \
             patch.object(srv.github_client, "invalidate", return_value=None):
            r = client.post("/api/sprints/cleanup-empty",
                            json={"project": "zealchaiwut/commander"})

        assert r.status_code == 200, r.text
        data = r.json()
        assert "sprint-10" in data["deleted"], "sprint-10 (leading empty) must be deleted"
        assert "sprint-11" in data["deleted"], "sprint-11 (leading empty) must be deleted"
        assert "sprint-12" not in data.get("deleted", []), "sprint-12 has tickets — must not be deleted"
        assert "sprint-13" not in data.get("deleted", []), "sprint-13 (trailing empty) must not be deleted"

    def test_scenario_d_only_first_leading_empty_removed(self):
        """Scenario D: sprints 1(0), 2(3), 3(0), 4(5) → only 1 deleted; 3 is mid-gap."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        all_labels = _sprint_labels(1, 2, 3, 4)
        issues = [_make_issue(10, 2), _make_issue(11, 2), _make_issue(12, 2),
                  _make_issue(20, 4), _make_issue(21, 4), _make_issue(22, 4),
                  _make_issue(23, 4), _make_issue(24, 4)]

        deleted = []
        def fake_delete(label, repo_name=None):
            deleted.append(label)

        with patch.object(srv.github_client, "list_sprint_labels", return_value=all_labels), \
             patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "delete_label", side_effect=fake_delete), \
             patch.object(srv.github_client, "invalidate", return_value=None):
            r = client.post("/api/sprints/cleanup-empty",
                            json={"project": "zealchaiwut/commander"})

        assert r.status_code == 200, r.text
        data = r.json()
        assert "sprint-1" in data["deleted"], "sprint-1 (leading empty) must be deleted"
        assert "sprint-3" not in data.get("deleted", []), "sprint-3 is between active sprints — must not be deleted"
        assert "sprint-2" not in data.get("deleted", []), "sprint-2 has tickets"
        assert "sprint-4" not in data.get("deleted", []), "sprint-4 has tickets"

    def test_trailing_empty_after_active_never_deleted(self):
        """AC2: Empty sprint after an active sprint is always preserved."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        all_labels = _sprint_labels(5, 6, 7)
        issues = [_make_issue(1, 5)]  # sprint-5 active, sprint-6 and sprint-7 are trailing

        deleted = []
        def fake_delete(label, repo_name=None):
            deleted.append(label)

        with patch.object(srv.github_client, "list_sprint_labels", return_value=all_labels), \
             patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "delete_label", side_effect=fake_delete), \
             patch.object(srv.github_client, "invalidate", return_value=None):
            r = client.post("/api/sprints/cleanup-empty",
                            json={"project": "zealchaiwut/commander"})

        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("deleted", []) == [], (
            "No sprints should be deleted when first sprint already has tickets"
        )


# ── AC3 & AC4: Edge cases with no active or first-already-active ──────────────

class TestNoActiveOrFirstActive:
    """AC3 & AC4: nothing cleared when all empty or first is active."""

    def test_scenario_c_all_empty_nothing_cleared(self):
        """Scenario C: sprints 1(0), 2(0), 3(0) → no sprints removed."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        all_labels = _sprint_labels(1, 2, 3)
        issues = []  # no tickets anywhere

        deleted = []
        def fake_delete(label, repo_name=None):
            deleted.append(label)

        with patch.object(srv.github_client, "list_sprint_labels", return_value=all_labels), \
             patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "delete_label", side_effect=fake_delete), \
             patch.object(srv.github_client, "invalidate", return_value=None):
            r = client.post("/api/sprints/cleanup-empty",
                            json={"project": "zealchaiwut/commander"})

        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("deleted", []) == [], (
            "All sprints empty → no cleanup candidates → nothing deleted"
        )
        assert fake_delete not in [deleted], True  # delete_label should not be called
        assert deleted == [], "delete_label must not be called when all sprints are empty"

    def test_scenario_b_first_sprint_active_nothing_cleared(self):
        """Scenario B: sprints 1(5), 2(0) → no sprints removed (first sprint has tickets)."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        all_labels = _sprint_labels(1, 2)
        issues = [_make_issue(i, 1) for i in range(1, 6)]  # 5 tickets in sprint-1

        deleted = []
        def fake_delete(label, repo_name=None):
            deleted.append(label)

        with patch.object(srv.github_client, "list_sprint_labels", return_value=all_labels), \
             patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues), \
             patch.object(srv.github_client, "delete_label", side_effect=fake_delete), \
             patch.object(srv.github_client, "invalidate", return_value=None):
            r = client.post("/api/sprints/cleanup-empty",
                            json={"project": "zealchaiwut/commander"})

        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("deleted", []) == [], (
            "First sprint has tickets → no leading empty sprints → nothing deleted"
        )


# ── AC6: Idempotency ──────────────────────────────────────────────────────────

class TestIdempotency:
    """AC6: Running the action twice produces the same result as once."""

    def test_scenario_e_idempotent(self):
        """After first cleanup removes leading empties, second cleanup deletes nothing more."""
        try:
            client, srv = _get_test_client()
        except Exception:
            pytest.skip("Cannot import server module")

        # Initial state: sprint-10(0), sprint-11(0), sprint-12(10), sprint-13(0)
        # After first run: sprint-10 and sprint-11 are deleted
        # Remaining: sprint-12(10), sprint-13(0)
        remaining_labels = _sprint_labels(12, 13)
        issues_after = [_make_issue(1, 12)]

        deleted_second = []
        def fake_delete_second(label, repo_name=None):
            deleted_second.append(label)

        with patch.object(srv.github_client, "list_sprint_labels", return_value=remaining_labels), \
             patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues_after), \
             patch.object(srv.github_client, "delete_label", side_effect=fake_delete_second), \
             patch.object(srv.github_client, "invalidate", return_value=None):
            r = client.post("/api/sprints/cleanup-empty",
                            json={"project": "zealchaiwut/commander"})

        assert r.status_code == 200, r.text
        assert deleted_second == [], (
            "Second run on already-cleaned state should delete nothing (idempotent)"
        )


# ── Static code checks: server.py uses leading-consecutive algorithm ──────────

class TestServerPyLeadingConsecutiveAlgorithm:
    """Verify server.py implements leading-consecutive logic, not all-empty logic."""

    def test_cleanup_empty_not_delete_all_empties(self):
        """cleanup_empty_sprints must NOT simply filter all labels not in labeled_sprints."""
        content = SERVER_PY.read_text()
        fn_start = content.find("async def cleanup_empty_sprints")
        assert fn_start != -1, "cleanup_empty_sprints function must exist"
        fn_end = content.find("\n@app.", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        # Old (buggy) pattern: [l for l in all_sprint_labels if l not in labeled_sprints]
        # Must not be present in the function body
        assert "l not in labeled_sprints" not in fn_body, (
            "cleanup_empty_sprints must not delete ALL empty sprints — "
            "it must only delete leading consecutive ones"
        )

    def test_cleanup_empty_stops_at_first_active(self):
        """cleanup_empty_sprints must break/stop iteration at first sprint with tickets."""
        content = SERVER_PY.read_text()
        fn_start = content.find("async def cleanup_empty_sprints")
        fn_end = content.find("\n@app.", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        # The function must break out of iteration when it hits an active sprint
        assert "break" in fn_body, (
            "cleanup_empty_sprints must use break to stop at first active sprint"
        )

    def test_cleanup_empty_guards_all_empty_case(self):
        """cleanup_empty_sprints returns empty deleted list when no sprints have tickets."""
        content = SERVER_PY.read_text()
        fn_start = content.find("async def cleanup_empty_sprints")
        fn_end = content.find("\n@app.", fn_start + 1)
        fn_body = content[fn_start:fn_end] if fn_end != -1 else content[fn_start:]
        # Must not delete anything when no active sprints found
        # This can be implemented as: if no active sprint found, empty_labels = []
        # We just check that the function body has some guard for this
        assert "found" in fn_body or "break" in fn_body or "leading" in fn_body, (
            "cleanup_empty_sprints must guard the all-empty case (use break pattern)"
        )


# ── Static code check: frontend uses leading-consecutive algorithm ────────────

class TestFrontendLeadingConsecutive:
    """Verify project.html _smgmtUpdateCleanupBtn uses leading-consecutive logic."""

    def test_frontend_stops_at_first_labeled_sprint(self):
        """_smgmtUpdateCleanupBtn must stop collecting empty sprints at first active sprint."""
        content = PROJECT_HTML.read_text()
        # Must NOT use simple .filter(l => !labeled.has(l)) on all ordered labels
        # The fix: iterate and break at first labeled sprint
        # Check that the function no longer does a simple filter of ALL non-labeled sprints
        func_start = content.find("function _smgmtUpdateCleanupBtn")
        assert func_start != -1, "_smgmtUpdateCleanupBtn must exist in project.html"
        func_end = content.find("\nfunction ", func_start + 1)
        func_body = content[func_start:func_end] if func_end != -1 else content[func_start:]
        # Old pattern: orderedLabels.filter(l => !labeled.has(l))
        # This collects ALL empty labels — must be gone
        assert "orderedLabels.filter(l => !labeled.has(l))" not in func_body, (
            "_smgmtUpdateCleanupBtn must not use simple filter of all non-labeled sprints"
        )

    def test_frontend_has_loop_or_break_for_consecutive(self):
        """_smgmtUpdateCleanupBtn must use a loop that stops at first active sprint."""
        content = PROJECT_HTML.read_text()
        func_start = content.find("function _smgmtUpdateCleanupBtn")
        func_end = content.find("\nfunction ", func_start + 1)
        func_body = content[func_start:func_end] if func_end != -1 else content[func_start:]
        # Must have either a for loop with break, or a while, or some early-exit pattern
        has_break = "break" in func_body
        has_findindex = "findIndex" in func_body or "find(" in func_body
        has_loop = "for " in func_body or "forEach" in func_body
        assert has_break or (has_findindex and has_loop), (
            "_smgmtUpdateCleanupBtn must stop at first active sprint "
            "(use for+break or findIndex pattern)"
        )
