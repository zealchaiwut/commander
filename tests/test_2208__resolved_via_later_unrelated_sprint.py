"""Tests for issue #2208: reconcile/Complete must recognize a failed ticket
fixed via a DIFFERENT, unrelated later sprint — not just a same-lineage rerun.

perf-coach sprint-121's shape: two tickets (#1420, #1525) failed during
sprint-121's own run (exhausted fix-loop), but were never reworked via a
sprint-121 child. Instead they were independently re-dispatched and actually
fixed under unrelated later sprint numbers (sprint-122, sprint-122.1).
_lineage_has_later_completed (#2197's carve-out) doesn't recognize this —
only a same-lineage descendant. This adds a second, independent carve-out:
every failed ticket's LATEST agent_runs outcome (across any sprint) is done,
and the sprint's own branch already merged.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2208.db")

import db as _db_module  # noqa: E402

_PROJ = "owner/perf-coach"


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_2208.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _seed_sprint_121_shape(db):
    """Real shape: 2 failed tickets, no sprint-121 child, branch merged."""
    db.record_sprint_start("sprint-121", project=_PROJ)
    issues = [
        {"ticket_id": 849, "state": "merged", "agent_status": "completed"},
        {
            "ticket_id": 1420, "state": "merged", "agent_status": "completed",
            "failure_reason": "Fix-loop exhausted after 2 attempt(s): LINT_FAIL",
        },
        {
            "ticket_id": 1525, "state": "merged", "agent_status": "completed",
            "failure_reason": "Fix-loop exhausted after 2 attempt(s): LINT_FAIL",
        },
    ]
    db.ingest_sprint_run_artifact("sprint-121", {
        "sprint_label": "sprint-121", "wall_clock_secs": 6432, "issues": issues,
    }, project=_PROJ)
    db.record_sprint_needs_rework("sprint-121", end_reason="ticket-failures", project=_PROJ)

    # #1420 originally attempted in sprint-121, later fixed in sprint-122.
    r1 = db.record_agent_start(1420, "sprint-121", "coder")
    db.record_agent_finish(1420, "sprint-121", "coder", outcome="failed", run_id=r1)
    r2 = db.record_agent_start(1420, "sprint-122", "coder")
    db.record_agent_finish(1420, "sprint-122", "coder", outcome="merged", run_id=r2)

    # #1525 originally attempted in sprint-121, later fixed in sprint-122.1.
    r3 = db.record_agent_start(1525, "sprint-121", "coder")
    db.record_agent_finish(1525, "sprint-121", "coder", outcome="failed", run_id=r3)
    r4 = db.record_agent_start(1525, "sprint-122.1", "coder")
    db.record_agent_finish(1525, "sprint-122.1", "coder", outcome="merged", run_id=r4)


class TestFailedTicketsResolvedByLaterRun:
    def test_true_when_every_failed_ticket_later_merged_elsewhere(self, fresh_db):
        from routers import sprint_reconcile_service as svc
        _seed_sprint_121_shape(fresh_db)
        row = fresh_db.get_sprint("sprint-121", project=_PROJ)

        assert svc._failed_tickets_resolved_by_later_run(row) is True

    def test_false_when_a_failed_ticket_has_no_later_run(self, fresh_db):
        from routers import sprint_reconcile_service as svc
        fresh_db.record_sprint_start("sprint-1", project=_PROJ)
        issues = [
            {"ticket_id": 1, "state": "merged", "agent_status": "completed",
             "failure_reason": "exhausted"},
        ]
        fresh_db.ingest_sprint_run_artifact("sprint-1", {
            "sprint_label": "sprint-1", "wall_clock_secs": 10, "issues": issues,
        }, project=_PROJ)
        row = fresh_db.get_sprint("sprint-1", project=_PROJ)

        assert svc._failed_tickets_resolved_by_later_run(row) is False

    def test_false_when_latest_run_is_still_failed(self, fresh_db):
        from routers import sprint_reconcile_service as svc
        fresh_db.record_sprint_start("sprint-1", project=_PROJ)
        issues = [
            {"ticket_id": 1, "state": "merged", "agent_status": "completed",
             "failure_reason": "exhausted"},
        ]
        fresh_db.ingest_sprint_run_artifact("sprint-1", {
            "sprint_label": "sprint-1", "wall_clock_secs": 10, "issues": issues,
        }, project=_PROJ)
        r1 = fresh_db.record_agent_start(1, "sprint-1", "coder")
        fresh_db.record_agent_finish(1, "sprint-1", "coder", outcome="failed", run_id=r1)
        r2 = fresh_db.record_agent_start(1, "sprint-1.1", "coder")
        fresh_db.record_agent_finish(1, "sprint-1.1", "coder", outcome="failed", run_id=r2)
        row = fresh_db.get_sprint("sprint-1", project=_PROJ)

        assert svc._failed_tickets_resolved_by_later_run(row) is False


class TestGithubReconcileRowResolvedElsewhere:
    def test_promotes_to_completed_when_resolved_via_unrelated_sprint(self, fresh_db):
        from routers import sprint_reconcile_service as svc
        _seed_sprint_121_shape(fresh_db)
        row = fresh_db.get_sprint("sprint-121", project=_PROJ)

        with patch("server._has_rework_tickets", return_value=False), \
             patch("github_client.list_merged_sprint_branches",
                    return_value={"sprint/sprint-121"}):
            patch_result = svc._github_reconcile_row("sprint-121", _PROJ, row)

        assert patch_result is not None
        assert patch_result["state"] == "completed", (
            f"Expected sprint-121 to be promoted to completed once its failed "
            f"tickets are confirmed fixed elsewhere and its branch merged, "
            f"got: {patch_result!r}"
        )
        assert patch_result["end_reason"] == "resolved-in-later-sprint"

    def test_does_not_promote_when_branch_never_merged(self, fresh_db):
        """Safety check: resolved tickets alone aren't enough without the
        sprint's own branch actually shipping."""
        from routers import sprint_reconcile_service as svc
        _seed_sprint_121_shape(fresh_db)
        row = fresh_db.get_sprint("sprint-121", project=_PROJ)

        with patch("server._has_rework_tickets", return_value=False), \
             patch("github_client.list_merged_sprint_branches", return_value=set()):
            patch_result = svc._github_reconcile_row("sprint-121", _PROJ, row)

        assert patch_result is None

    def test_still_blocks_when_tickets_never_resolved(self, fresh_db):
        """Regression guard: a genuinely still-broken sprint stays blocked."""
        from routers import sprint_reconcile_service as svc
        fresh_db.record_sprint_start("sprint-1", project=_PROJ)
        issues = [
            {"ticket_id": 1, "state": "merged", "agent_status": "completed",
             "failure_reason": "exhausted"},
        ]
        fresh_db.ingest_sprint_run_artifact("sprint-1", {
            "sprint_label": "sprint-1", "wall_clock_secs": 10, "issues": issues,
        }, project=_PROJ)
        fresh_db.record_sprint_needs_rework("sprint-1", end_reason="ticket-failures", project=_PROJ)
        row = fresh_db.get_sprint("sprint-1", project=_PROJ)

        with patch("server._has_rework_tickets", return_value=False), \
             patch("github_client.list_merged_sprint_branches",
                    return_value={"sprint/sprint-1"}):
            patch_result = svc._github_reconcile_row("sprint-1", _PROJ, row)

        assert patch_result is None


class TestCompleteStepAllowsResolvedElsewhere:
    """The #2206 write-path guard must not block a sprint whose failed
    tickets were genuinely fixed under a different, unrelated later sprint."""

    def _dual(self, name, **kw):
        import importlib
        out = []
        for mod_name in ("server", "startup"):
            try:
                mod = importlib.import_module(mod_name)
            except Exception:
                continue
            if hasattr(mod, name):
                out.append(patch(f"{mod_name}.{name}", **kw))
        return out

    def _complete_step(self, label, tmp_root):
        import server as srv
        from fastapi.testclient import TestClient

        def fake_merge(repo, head, base, title, delete_branch=True, **kw):
            return (True, "merged", 1)

        patches = [
            *self._dual("_project_root_path", return_value=tmp_root),
            *self._dual("_is_sprint_running", return_value=False),
            *self._dual("_has_rework_tickets", return_value=False),
            *self._dual("_branch_has_unmerged_commits", return_value=True),
            *self._dual("_gh_merge_branch_via_pr", side_effect=fake_merge),
            *self._dual("_open_summary_issues_for_labels", return_value=[]),
            *self._dual("_sprint_merge_parent_label", return_value="develop"),
            *self._dual("_bulk_complete_collect_issues", return_value=([label], [])),
            *self._dual("_plan_json_set_state", return_value=None),
            *self._dual("_emit_dashboard_event", new=MagicMock()),
            patch.object(srv.github_client, "close_issue", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
            patch.object(srv.github_client, "list_merged_sprint_branches",
                         return_value={f"sprint/{label}"}),
        ]
        for p in patches:
            p.start()
        try:
            client = TestClient(srv.app, raise_server_exceptions=False)
            return client.post(
                f"/api/projects/{_PROJ}/sprints/{label}/complete-step",
                json={"confirmed": True},
            )
        finally:
            for p in patches:
                p.stop()

    def test_completes_when_failed_tickets_resolved_via_unrelated_sprint(self, fresh_db, tmp_path):
        _seed_sprint_121_shape(fresh_db)

        resp = self._complete_step("sprint-121", tmp_path)

        assert resp.status_code != 409, (
            f"A sprint whose failed tickets were fixed via a different, "
            f"unrelated later sprint (and whose own branch already merged) "
            f"must be completable; got 409: {resp.text}"
        )
