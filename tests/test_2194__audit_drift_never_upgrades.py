"""Tests for issue #2194: audit_sprint_terminal_state_drift never auto-upgrades
needs_rework -> ready_to_merge.

AC: a sprint stored as needs_rework whose issues_json shows no per-ticket
failure (derived == ready_to_merge) must NOT be reported or applied as drift.
issues_json alone can't prove a sprint is safe to merge — only the live
GitHub signal can (see sprint_reconcile_service._outcome_reconcile_row).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_SCRIPTS_ROOT = _REPO_ROOT / "scripts"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT / "archive"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2194.db")

import db as _db_module  # noqa: E402
import audit_sprint_terminal_state_drift as audit_mod  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2194.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file  # Path object, not str
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _seed_needs_rework_with_clean_issues(fresh_db, label: str, project: str) -> None:
    """needs_rework sprint whose issues_json shows no failed tickets (unrelated cause)."""
    fresh_db.record_sprint_needs_rework(label, end_reason="gate-failure", project=project)
    issues = [
        {"ticket_id": 1, "number": 1, "title": "Done ticket", "state": "merged",
         "agent_status": "completed"},
        {"ticket_id": 2, "number": 2, "title": "Also done", "state": "merged",
         "agent_status": "completed"},
    ]
    fresh_db.ingest_sprint_run_artifact(label, {
        "sprint_label": label,
        "issues": issues,
        "wall_clock_secs": 60,
    }, project=project)


class TestAuditNeverUpgrades:
    def test_needs_rework_with_clean_issues_json_is_not_flagged(self, fresh_db):
        _seed_needs_rework_with_clean_issues(fresh_db, "sprint-2194-a", "owner/repo")

        drifted = audit_mod.audit(project_filter="owner/repo")

        labels = [d["label"] for d in drifted]
        assert "sprint-2194-a" not in labels, (
            "audit() must never flag needs_rework -> ready_to_merge drift from "
            "issues_json alone — only the live GitHub signal can confirm a "
            "sprint is actually mergeable."
        )

    def test_apply_does_not_upgrade_needs_rework_row(self, fresh_db):
        _seed_needs_rework_with_clean_issues(fresh_db, "sprint-2194-b", "owner/repo")

        audit_mod.audit(project_filter="owner/repo", apply=True)

        row = fresh_db.get_sprint("sprint-2194-b", project="owner/repo")
        stored = fresh_db.canonical_lifecycle(row.get("state") or "")
        assert stored == "needs_rework", (
            f"--apply must not upgrade a needs_rework row to ready_to_merge "
            f"based on issues_json alone, got state={stored!r}"
        )

    def test_ready_to_merge_downgrade_still_flagged(self, fresh_db):
        """Sanity check: the opposite (legitimate) direction still works."""
        fresh_db.record_sprint_ready_to_merge(
            "sprint-2194-c", end_reason="ticket-failures", project="owner/repo",
        )
        issues = [
            {"ticket_id": 1, "state": "merged", "agent_status": "completed"},
            {"ticket_id": 2, "state": "closed", "agent_status": "failed",
             "failure_reason": "Subscription rate limit exhausted"},
        ]
        fresh_db.ingest_sprint_run_artifact("sprint-2194-c", {
            "sprint_label": "sprint-2194-c",
            "issues": issues,
            "wall_clock_secs": 60,
        }, project="owner/repo")

        drifted = audit_mod.audit(project_filter="owner/repo")

        entry = next((d for d in drifted if d["label"] == "sprint-2194-c"), None)
        assert entry is not None, "ready_to_merge -> needs_rework drift must still be flagged"
        assert entry["derived_state"] == "needs_rework"
