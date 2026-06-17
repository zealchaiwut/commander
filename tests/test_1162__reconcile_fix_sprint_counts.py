"""Tests for issue #1162: extend reconcile job to fix denormalized sprint counts.

AC1: for every terminal sprint processed, issues_json and count columns are
     re-derived from agent_runs.
AC2: db.update_sprint_reconciliation() is invoked when a count discrepancy is
     detected.
AC3: a sprint row whose stored counts disagree with agent_runs is corrected.
AC4: the reconcile job does NOT mutate non-terminal (active/pending) sprint
     rows' counts.
AC5: a test seeds a drifted sprint row, runs the reconcile logic, and asserts
     the row is updated to match the agent_runs-derived counts.
AC6: no regression to existing lifecycle-state correction behavior.
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

os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1162.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = str(db_file)
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC3 + AC5: seeded drifted row is corrected ───────────────────────────────


class TestReconcileCountsFixesDrift:
    """AC3 + AC5: seeded drifted sprint row is corrected after reconcile."""

    def test_missing_issues_merged_and_counts_corrected(self, fresh_db):
        """Agent_runs has issues absent from issues_json; reconcile unions them."""
        label = "sprint-77"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural")
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [
                {"number": 100, "title": "t1", "status": "done", "agent_status": "completed"},
            ],
            "wall_clock_secs": 60,
        })
        row_before = fresh_db.get_sprint(label)
        assert row_before["summary_settled_done"] == 1
        assert row_before["summary_failure_count"] == 0

        # agent_runs records 2 more issues the state file missed
        fresh_db.record_agent_start(100, label, "tester")
        fresh_db.record_agent_finish(100, label, "tester", outcome="completed")
        fresh_db.record_agent_start(101, label, "coder")
        fresh_db.record_agent_finish(101, label, "coder", outcome="completed")
        fresh_db.record_agent_start(102, label, "coder")
        fresh_db.record_agent_finish(102, label, "coder", outcome="failed")

        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label))
        assert updated is True

        row = fresh_db.get_sprint(label)
        issues = json.loads(row["issues_json"])
        issue_ids = {int(i.get("ticket_id") or i.get("number") or 0) for i in issues}
        assert 100 in issue_ids
        assert 101 in issue_ids
        assert 102 in issue_ids
        # 2 done (100, 101 completed), 1 failed (102)
        assert row["summary_settled_done"] == 2
        assert row["summary_failure_count"] == 1

    def test_no_agent_runs_returns_false(self, fresh_db):
        """When no agent_runs rows exist, reconcile is a no-op."""
        label = "sprint-no-runs"
        fresh_db.record_sprint_ready_to_merge(label)
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [{"number": 50, "title": "t", "status": "done", "agent_status": "completed"}],
            "wall_clock_secs": 10,
        })
        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label))
        assert updated is False

    def test_exact_match_returns_false(self, fresh_db):
        """When agent_runs and issues_json already agree, no update is made."""
        label = "sprint-exact"
        fresh_db.record_sprint_ready_to_merge(label)
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [{"number": 60, "title": "t", "status": "done", "agent_status": "completed"}],
            "wall_clock_secs": 10,
        })
        # Agent run matching the already-ingested issue
        fresh_db.record_agent_start(60, label, "coder")
        fresh_db.record_agent_finish(60, label, "coder", outcome="completed")
        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label))
        assert updated is False

    def test_failed_issue_corrects_failure_count(self, fresh_db):
        """Failed agent_run for missing issue increments failure_count."""
        label = "sprint-fail"
        fresh_db.record_sprint_needs_rework(label, end_reason="ticket-failures")
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [],
            "wall_clock_secs": 5,
        })
        fresh_db.record_agent_start(200, label, "coder")
        fresh_db.record_agent_finish(200, label, "coder", outcome="failed")

        from routers import sprint_reconcile_service as svc
        updated = svc._reconcile_counts(label, fresh_db.get_sprint(label))
        assert updated is True

        row = fresh_db.get_sprint(label)
        assert row["summary_failure_count"] == 1
        assert row["summary_settled_done"] == 0


# ── AC4: non-terminal sprints are NOT mutated ────────────────────────────────


class TestNonTerminalSprintsUntouched:
    """AC4: _reconcile_counts skips non-terminal sprint rows."""

    def test_running_sprint_skipped(self, fresh_db):
        label = "sprint-running"
        fresh_db.record_sprint_start(label, project="o/r")
        fresh_db.record_agent_start(300, label, "coder")
        fresh_db.record_agent_finish(300, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        row = fresh_db.get_sprint(label)
        assert row["state"] == "running"

        updated = svc._reconcile_counts(label, row)
        assert updated is False

        row_after = fresh_db.get_sprint(label)
        assert row_after["summary_settled_done"] == (row["summary_settled_done"] or 0)
        assert row_after["summary_failure_count"] == (row["summary_failure_count"] or 0)


# ── AC2: db.update_sprint_reconciliation is called ────────────────────────────


class TestReconciliationMarkerCalled:
    """AC2: db.update_sprint_reconciliation is invoked when counts are fixed."""

    def test_update_sprint_reconciliation_called_on_drift(self, fresh_db):
        label = "sprint-ac2"
        fresh_db.record_sprint_ready_to_merge(label)
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [],
            "wall_clock_secs": 10,
        })
        fresh_db.record_agent_start(400, label, "coder")
        fresh_db.record_agent_finish(400, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        with patch.object(fresh_db, "update_sprint_reconciliation") as mock_recon:
            updated = svc._reconcile_counts(label, fresh_db.get_sprint(label))
        assert updated is True
        mock_recon.assert_called_once()
        call_label = mock_recon.call_args[0][0]
        assert call_label == label


# ── AC1: reconcile_sprint_label invokes count fix for terminal sprints ────────


class TestReconcileSprintLabelInvokesCountFix:
    """AC1: reconcile_sprint_label triggers count reconcile for terminal sprints."""

    def test_reconcile_sprint_label_fixes_counts_no_lifecycle_drift(
        self, fresh_db, monkeypatch
    ):
        label = "sprint-ac1"
        project = "o/r"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=project)
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [
                {"number": 500, "title": "t", "status": "done", "agent_status": "completed"}
            ],
            "wall_clock_secs": 20,
        }, project=project)
        # agent_runs has an extra issue not in issues_json
        fresh_db.record_agent_start(500, label, "coder")
        fresh_db.record_agent_finish(500, label, "coder", outcome="completed")
        fresh_db.record_agent_start(501, label, "coder")
        fresh_db.record_agent_finish(501, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        # Suppress GitHub lifecycle check (no lifecycle drift)
        monkeypatch.setattr(svc, "_github_reconcile_row", lambda *a, **kw: None)

        result = svc.reconcile_sprint_label(label, project)
        assert result is True

        row = fresh_db.get_sprint(label)
        assert row["summary_settled_done"] == 2

    def test_reconcile_sprint_label_returns_false_when_no_drift(
        self, fresh_db, monkeypatch
    ):
        label = "sprint-nodrift"
        project = "o/r"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=project)
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [
                {"number": 600, "title": "t", "status": "done", "agent_status": "completed"}
            ],
            "wall_clock_secs": 15,
        }, project=project)
        # agent_runs matches issues_json exactly
        fresh_db.record_agent_start(600, label, "coder")
        fresh_db.record_agent_finish(600, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        monkeypatch.setattr(svc, "_github_reconcile_row", lambda *a, **kw: None)

        result = svc.reconcile_sprint_label(label, project)
        assert result is False


# ── AC6: lifecycle-state correction behavior preserved ───────────────────────


class TestLifecycleStateRegressionPrevented:
    """AC6: existing lifecycle-state correction is preserved alongside count fix."""

    def test_lifecycle_correction_fires_alongside_count_fix(
        self, fresh_db, monkeypatch
    ):
        label = "sprint-ac6"
        project = "o/r"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=project)
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [
                {"number": 700, "title": "t", "status": "done", "agent_status": "completed"}
            ],
            "wall_clock_secs": 20,
        }, project=project)
        # agent_runs adds a failed issue
        fresh_db.record_agent_start(700, label, "coder")
        fresh_db.record_agent_finish(700, label, "coder", outcome="failed")
        fresh_db.record_agent_start(701, label, "coder")
        fresh_db.record_agent_finish(701, label, "coder", outcome="failed")

        from routers import sprint_reconcile_service as svc
        # GitHub signals lifecycle drift too
        monkeypatch.setattr(
            svc, "_github_reconcile_row",
            lambda *a, **kw: {"state": "needs_rework", "end_reason": "github-reconcile"},
        )

        result = svc.reconcile_sprint_label(label, project)
        assert result is True

        row = fresh_db.get_sprint(label)
        # Lifecycle correction
        assert row["state"] == "needs_rework"
        # Count correction: 700 updated to failed, 701 added as failed
        assert row["summary_failure_count"] == 2

    def test_reconcile_project_includes_terminal_sprints(
        self, fresh_db, monkeypatch
    ):
        """reconcile_project processes terminal sprints and fixes count drift."""
        label = "sprint-proj"
        project = "o/r"
        fresh_db.record_sprint_ready_to_merge(label, end_reason="natural", project=project)
        fresh_db.ingest_sprint_run_artifact(label, {
            "sprint_label": label,
            "issues": [
                {"number": 800, "title": "t", "status": "done", "agent_status": "completed"}
            ],
            "wall_clock_secs": 30,
        }, project=project)
        # Add a missed issue to agent_runs
        fresh_db.record_agent_start(800, label, "coder")
        fresh_db.record_agent_finish(800, label, "coder", outcome="completed")
        fresh_db.record_agent_start(801, label, "coder")
        fresh_db.record_agent_finish(801, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        monkeypatch.setattr(svc, "_github_reconcile_row", lambda *a, **kw: None)

        updated = svc.reconcile_project(project)
        assert label in updated

        row = fresh_db.get_sprint(label)
        assert row["summary_settled_done"] == 2

    def test_running_sprint_skipped_by_reconcile_project(
        self, fresh_db, monkeypatch
    ):
        """reconcile_project does not process running sprints."""
        label = "sprint-running2"
        project = "o/r"
        fresh_db.record_sprint_start(label, project=project)
        fresh_db.record_agent_start(900, label, "coder")
        fresh_db.record_agent_finish(900, label, "coder", outcome="completed")

        from routers import sprint_reconcile_service as svc
        monkeypatch.setattr(svc, "_github_reconcile_row", lambda *a, **kw: None)

        updated = svc.reconcile_project(project)
        assert label not in updated
