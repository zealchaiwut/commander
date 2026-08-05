"""Tests for issue #2206: Complete / Bulk Complete honesty guards must not
let a genuinely-failed, never-reworked sprint (end_reason="ticket-failures")
sail through just because its tickets are all closed.

Same bug shape as #2197 (reconcile), #2199 (History), #2200/#2202/#2204
(Board/Estimates/Summaries display), #2205 (board payload consistency):
_has_rework_tickets only checks whether GitHub currently has an OPEN
rework-labeled ticket. perf-coach sprint-121's tickets all eventually
merged (via exhausted fix-loops), so _has_rework_tickets reads False even
though the sprint manager's own end_reason is "ticket-failures". Without
this fix, Complete/Bulk Complete would overwrite the DB state to
"completed", destroying the classification every other (already-fixed)
reader depends on.

The guard still respects a genuine rerun fix: if a later lineage member
completed (the hermes-agent cascade-completion shape complete_sprint_step
itself supports), the sprint is allowed through -- matching #2197's carve-out.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2206.db")

_PROJ = "owner/perf-coach"


@pytest.fixture
def fresh_db(tmp_path):
    dbm = importlib.import_module("db")
    db_file = tmp_path / "test_2206.db"
    original = dbm.DB_PATH
    dbm.DB_PATH = db_file
    dbm.init_db()
    yield dbm
    dbm.DB_PATH = original


def _seed_never_reworked_failure(db, label):
    """sprint-121's exact shape: real failure, no children, no rerun."""
    db.record_sprint_start(label, project=_PROJ)
    db.record_sprint_needs_rework(label, end_reason="ticket-failures", project=_PROJ)


def _dual(name, **kw):
    out = []
    for mod_name in ("server", "startup"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, name):
            out.append(patch(f"{mod_name}.{name}", **kw))
    return out


def _complete_step(label, tmp_root):
    import server as srv
    from fastapi.testclient import TestClient

    def fake_merge(repo, head, base, title, delete_branch=True, **kw):
        return (True, "merged", 1)

    patches = [
        *_dual("_project_root_path", return_value=tmp_root),
        *_dual("_is_sprint_running", return_value=False),
        *_dual("_has_rework_tickets", return_value=False),
        *_dual("_branch_has_unmerged_commits", return_value=True),
        *_dual("_gh_merge_branch_via_pr", side_effect=fake_merge),
        *_dual("_open_summary_issues_for_labels", return_value=[]),
        *_dual("_sprint_merge_parent_label", return_value="develop"),
        *_dual("_bulk_complete_collect_issues", return_value=([label], [])),
        *_dual("_plan_json_set_state", return_value=None),
        *_dual("_emit_dashboard_event", new=MagicMock()),
        patch.object(srv.github_client, "close_issue", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
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


class TestCompleteStepBlocksNeverReworkedFailure:
    def test_refuses_to_complete_ticket_failures_sprint_with_no_rerun(self, fresh_db, tmp_path):
        _seed_never_reworked_failure(fresh_db, "sprint-121")

        resp = _complete_step("sprint-121", tmp_path)

        assert resp.status_code == 409, (
            f"Complete must refuse a never-reworked ticket-failures sprint "
            f"even though its tickets are all closed; got {resp.status_code}: {resp.text}"
        )
        row = fresh_db.get_sprint("sprint-121", project=_PROJ)
        assert fresh_db.canonical_lifecycle(row["state"] or "") == "needs_rework", (
            "DB state must not be overwritten to completed"
        )

    def test_still_allows_completion_when_later_sibling_completed(self, fresh_db, tmp_path):
        """Carve-out: a genuine rerun fix must not be blocked (matches #2197)."""
        fresh_db.record_sprint_start("sprint-15", project=_PROJ)
        fresh_db.record_sprint_needs_rework("sprint-15", end_reason="ticket-failures", project=_PROJ)
        fresh_db.record_sprint_start("sprint-15.1", project=_PROJ)
        fresh_db.record_sprint_finish("sprint-15.1", end_reason="merge_sprint", project=_PROJ)

        resp = _complete_step("sprint-15", tmp_path)

        assert resp.status_code != 409, (
            f"A sprint superseded by a later completed lineage member must "
            f"still be completable; got 409: {resp.text}"
        )
