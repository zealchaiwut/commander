"""Tests for issue #2197: reconcile must not oscillate a genuinely-failed,
never-reworked sprint between needs_rework and ready_to_merge.

Bug (perf-coach sprint-121): _github_reconcile_row unconditionally promoted
needs_rework -> ready_to_merge whenever GitHub showed no open rework ticket,
ignoring issues_json entirely. _outcome_reconcile_row (issue #2167) then
downgraded it right back based on issues_json's real failure. Every reconcile
pass flipped the state — it never settled.

Fix: the promotion is now conditional — only promote when issues_json shows
no real failure, OR a later lineage member (rerun/child sprint) reached
completed (proof the failure was actually addressed, not just closed). This
preserves the vector-search-demo sprint-15 case (rerun exists) while fixing
the sprint-121 case (no rerun in its lineage).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2197.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2197.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file  # Path object, not str
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _seed_needs_rework_with_failure(fresh_db, label: str, project: str) -> None:
    """needs_rework sprint whose issues_json records a real ticket failure."""
    fresh_db.record_sprint_needs_rework(label, end_reason="ticket-failures", project=project)
    issues = [
        {"ticket_id": 1, "number": 1, "state": "merged", "agent_status": "completed"},
        {"ticket_id": 2, "number": 2, "state": "closed", "agent_status": "failed",
         "failure_reason": "coder-no-test-edits violation"},
    ]
    fresh_db.ingest_sprint_run_artifact(label, {
        "sprint_label": label,
        "issues": issues,
        "wall_clock_secs": 60,
    }, project=project)


class TestNeverReworkedFailureStaysPut:
    """The perf-coach sprint-121 case: real failure, no rerun in the lineage."""

    def test_does_not_promote_when_no_later_completed_sibling(self, fresh_db):
        _seed_needs_rework_with_failure(fresh_db, "sprint-121", "owner/repo")
        row = fresh_db.get_sprint("sprint-121", project="owner/repo")

        from routers import sprint_reconcile_service as svc
        with patch("server._has_rework_tickets", return_value=False):
            patch_result = svc._github_reconcile_row("sprint-121", "owner/repo", row)

        assert patch_result is None, (
            "A needs_rework sprint with a real, never-fixed ticket failure and "
            "no later completed lineage member must not be promoted to "
            f"ready_to_merge just because GitHub shows no open ticket. Got: {patch_result!r}"
        )

    def test_reconcile_apply_leaves_state_unchanged(self, fresh_db):
        _seed_needs_rework_with_failure(fresh_db, "sprint-121", "owner/repo")

        from routers import sprint_reconcile_service as svc
        with patch("server._has_rework_tickets", return_value=False):
            svc.reconcile_sprint_label("sprint-121", "owner/repo")

        row = fresh_db.get_sprint("sprint-121", project="owner/repo")
        assert fresh_db.canonical_lifecycle(row["state"] or "") == "needs_rework", (
            "reconcile_apply must not flip a genuinely-failed, never-reworked "
            f"sprint to ready_to_merge, got state={row['state']!r}"
        )


class TestRerunFixedStillPromotes:
    """The vector-search-demo sprint-15 case: real failure, but a later rerun completed."""

    def test_promotes_when_later_sibling_completed(self, fresh_db):
        _seed_needs_rework_with_failure(fresh_db, "sprint-15", "owner/repo")
        fresh_db.record_sprint_start("sprint-15.1", project="owner/repo")
        fresh_db.record_sprint_finish("sprint-15.1", end_reason="merge_sprint", project="owner/repo")
        row = fresh_db.get_sprint("sprint-15", project="owner/repo")

        from routers import sprint_reconcile_service as svc
        # base_branch_merged_to_develop is not mocked True here, so this
        # exercises the plain-promotion branch (not the superseded-completion
        # branch one level up), confirming the new outcome check doesn't
        # block promotion when a later lineage member did complete.
        with patch("server._has_rework_tickets", return_value=False), \
             patch("github_client.list_merged_sprint_branches", return_value=set()):
            patch_result = svc._github_reconcile_row("sprint-15", "owner/repo", row)

        assert patch_result is not None and patch_result["state"] == "ready_to_merge", (
            "A later-completed lineage member is proof the failure was "
            f"addressed — promotion must still occur. Got: {patch_result!r}"
        )


class TestCleanIssuesJsonStillPromotes:
    """Regression guard: a needs_rework sprint with no recorded failure still promotes."""

    def test_promotes_when_issues_json_empty(self, fresh_db):
        fresh_db.record_sprint_needs_rework("sprint-1", end_reason="ticket-failures", project="owner/repo")
        row = fresh_db.get_sprint("sprint-1", project="owner/repo")

        from routers import sprint_reconcile_service as svc
        with patch("server._has_rework_tickets", return_value=False):
            patch_result = svc._github_reconcile_row("sprint-1", "owner/repo", row)

        assert patch_result is not None and patch_result["state"] == "ready_to_merge"
