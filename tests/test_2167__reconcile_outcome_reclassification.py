"""Tests for issue #2167: reconcile re-derives sprint terminal state from ticket outcomes.

AC2: behavioral test — seed a ready_to_merge sprint row whose ticket outcomes
     include a skipped ticket with a failure category, run reconcile, and assert
     it flags/corrects the mismatch. Must exercise the real code path, not a
     source-regex check.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2167.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2167.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file  # Path object, not str
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── helpers ───────────────────────────────────────────────────────────────────

def _seed_ready_sprint_with_failed_ticket(fresh_db, label: str, project: str) -> None:
    """Seed a ready_to_merge sprint whose issues_json has a RETRY_EXHAUSTED ticket."""
    fresh_db.record_sprint_ready_to_merge(label, end_reason="ticket-failures", project=project)
    # Issues contain one done ticket and one skipped ticket with failure category
    # (RETRY_EXHAUSTED — the exact scenario from perf-coach sprint-121).
    issues = [
        {
            "ticket_id": 1420, "number": 1420, "title": "Done ticket",
            "state": "merged", "agent_status": "completed",
        },
        {
            "ticket_id": 1525, "number": 1525, "title": "Skipped ticket",
            "state": "closed", "agent_status": "failed",
            "failure_reason": "Subscription rate limit exhausted",
        },
    ]
    fresh_db.ingest_sprint_run_artifact(label, {
        "sprint_label": label,
        "issues": issues,
        "wall_clock_secs": 120,
    }, project=project)


# ── AC2: behavioral tests ─────────────────────────────────────────────────────


class TestOutcomeReclassification:
    """AC2: stale ready_to_merge corrected to needs_rework by outcome check."""

    def test_derive_terminal_state_returns_needs_rework_for_failed_ticket(self, fresh_db):
        """_derive_terminal_state_from_issues_json flags failure_reason."""
        from routers import sprint_reconcile_service as svc

        issues_with_failure = json.dumps([
            {"ticket_id": 1, "state": "merged", "agent_status": "completed"},
            {
                "ticket_id": 2, "state": "closed", "agent_status": "failed",
                "failure_reason": "Subscription rate limit exhausted",
            },
        ])
        result = svc._derive_terminal_state_from_issues_json(issues_with_failure)
        assert result == "needs_rework"

    def test_derive_terminal_state_returns_ready_when_all_pass(self, fresh_db):
        """_derive_terminal_state_from_issues_json returns ready_to_merge when clean."""
        from routers import sprint_reconcile_service as svc

        issues_all_done = json.dumps([
            {"ticket_id": 1, "state": "merged", "agent_status": "completed"},
            {"ticket_id": 2, "state": "merged", "agent_status": "completed"},
        ])
        result = svc._derive_terminal_state_from_issues_json(issues_all_done)
        assert result == "ready_to_merge"

    def test_derive_terminal_state_returns_none_for_empty_json(self, fresh_db):
        """_derive_terminal_state_from_issues_json returns None when no data."""
        from routers import sprint_reconcile_service as svc

        assert svc._derive_terminal_state_from_issues_json("[]") is None
        assert svc._derive_terminal_state_from_issues_json("") is None

    def test_outcome_reconcile_row_flags_mismatch(self, fresh_db):
        """_outcome_reconcile_row returns patch when ready_to_merge has failed tickets."""
        from routers import sprint_reconcile_service as svc

        label, project = "sprint-121", "zealchaiwut/perf-coach"
        _seed_ready_sprint_with_failed_ticket(fresh_db, label, project)
        row = fresh_db.get_sprint(label, project=project)

        patch_result = svc._outcome_reconcile_row(row)
        assert patch_result is not None
        assert patch_result["state"] == "needs_rework"

    def test_outcome_reconcile_row_no_patch_when_correct(self, fresh_db):
        """_outcome_reconcile_row returns None when stored state matches outcomes."""
        from routers import sprint_reconcile_service as svc

        label, project = "sprint-200", "o/r"
        fresh_db.record_sprint_needs_rework(label, project=project)
        issues = json.dumps([
            {
                "ticket_id": 10, "state": "closed", "agent_status": "failed",
                "failure_reason": "Gate failed",
            },
        ])
        # Force-write issues_json to the needs_rework row
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [
                {"number": 10, "title": "t", "status": "skipped", "agent_status": "failed",
                 "failure_reason": "Gate failed"},
            ],
            "wall_clock_secs": 60,
        }, project=project)
        row = fresh_db.get_sprint(label, project=project)
        # needs_rework storing a failed ticket → no mismatch
        patch_result = svc._outcome_reconcile_row(row)
        assert patch_result is None

    def test_reconcile_apply_corrects_stale_ready_to_merge(self, fresh_db):
        """Core AC2 scenario: reconcile_apply corrects ready_to_merge → needs_rework.

        GitHub label check passes (no has_rework tickets because dead-lettered),
        but outcome check detects the mismatch and corrects the DB row.
        """
        from routers import sprint_reconcile_service as svc

        label, project = "sprint-121", "zealchaiwut/perf-coach"
        _seed_ready_sprint_with_failed_ticket(fresh_db, label, project)

        row_before = fresh_db.get_sprint(label, project=project)
        assert fresh_db.canonical_lifecycle(row_before["state"]) == "ready_to_merge"

        # GitHub sees no open rework tickets (dead-lettered → no needs-rework label).
        # _has_rework_tickets returns False, so _github_reconcile_row returns None.
        with patch("server._has_rework_tickets", return_value=False), \
             patch("server._project_root_path", side_effect=Exception("no project")):
            result = svc.reconcile_apply(label, project)

        assert result["updated"] is True, (
            "reconcile_apply should report updated=True when outcome check corrects state"
        )
        row_after = fresh_db.get_sprint(label, project=project)
        assert fresh_db.canonical_lifecycle(row_after["state"]) == "needs_rework", (
            "DB state must be corrected to needs_rework by outcome reclassification"
        )

    def test_reconcile_preview_flags_outcome_mismatch(self, fresh_db):
        """reconcile_preview reports outcome_mismatch=True for stale ready_to_merge."""
        from routers import sprint_reconcile_service as svc

        label, project = "sprint-121", "zealchaiwut/perf-coach"
        _seed_ready_sprint_with_failed_ticket(fresh_db, label, project)

        with patch("server._has_rework_tickets", return_value=False), \
             patch("server._project_root_path", side_effect=Exception("no project")):
            preview = svc.reconcile_preview(label, project)

        assert preview["exists"] is True
        assert preview["db_state"] == "ready_to_merge"
        assert preview["would_change"] is True
        assert preview.get("outcome_mismatch") is True
        assert preview["github_state"] == "needs_rework"

    def test_derive_handles_agent_status_failed_without_failure_reason(self, fresh_db):
        """agent_status=failed alone (no failure_reason) also triggers needs_rework."""
        from routers import sprint_reconcile_service as svc

        issues = json.dumps([
            {"ticket_id": 5, "state": "closed", "agent_status": "failed"},
        ])
        result = svc._derive_terminal_state_from_issues_json(issues)
        assert result == "needs_rework"
