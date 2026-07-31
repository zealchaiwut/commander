"""Tests for complete-step base-merge cascade completion of superseded lineage.

Bug (hermes-agent): the base step of Complete merged the whole stacked rerun
chain into develop but marked only the requested label completed — ancestors
stayed needs_rework and were later rebranded ready_to_merge by the reconcile
sweep, zombifying the lineage on the dashboard.

Fix under test: after the base branch ships to develop, complete_sprint_step
completes every other lineage member still in needs_rework / ready_to_merge /
partial_finished (end_reason='superseded'), reports them in
``cascaded_completed``, and emits one sprint_lineage_superseded event.
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

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

_PROJ = "owner/commander"


@pytest.fixture
def fresh_db(tmp_path):
    dbm = importlib.import_module("db")
    db_file = tmp_path / "test_cascade.db"
    original = dbm.DB_PATH
    dbm.DB_PATH = str(db_file)
    dbm.init_db()
    yield dbm
    dbm.DB_PATH = original


def _seed(db, label, state, end_reason=None, ended_at=None):
    db.record_sprint_start(label, project=_PROJ)
    if state == "needs_rework":
        db.record_sprint_needs_rework(label, end_reason=end_reason,
                                      ended_at=ended_at, project=_PROJ)
    elif state == "ready_to_merge":
        db.record_sprint_ready_to_merge(label, end_reason=end_reason,
                                        ended_at=ended_at, project=_PROJ)
    elif state == "completed":
        db.record_sprint_finish(label, end_reason=end_reason,
                                ended_at=ended_at, project=_PROJ)


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


def _complete_step(label, tmp_root, running=None, emit=None, parent="sprint-1.2"):
    """POST complete-step with GitHub/disk boundaries mocked; DB stays real."""
    import server as srv
    from fastapi.testclient import TestClient

    def fake_merge(repo, head, base, title, delete_branch=True, **kw):
        return (True, "merged", 1)

    running = running or (lambda root, lbl: False)
    emit = emit if emit is not None else MagicMock()

    patches = [
        *_dual("_project_root_path", return_value=tmp_root),
        *_dual("_is_sprint_running", side_effect=running),
        *_dual("_has_rework_tickets", return_value=False),
        *_dual("_branch_has_unmerged_commits", return_value=True),
        *_dual("_gh_merge_branch_via_pr", side_effect=fake_merge),
        *_dual("_open_summary_issues_for_labels", return_value=[]),
        *_dual("_sprint_merge_parent_label", return_value=parent),
        *_dual("_bulk_complete_collect_issues", return_value=([label], [])),
        *_dual("_plan_json_set_state", return_value=None),
        *_dual("_emit_dashboard_event", new=emit),
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


def _hermes_shape(db):
    _seed(db, "sprint-1", "needs_rework", end_reason="ticket-failures")
    _seed(db, "sprint-1.1", "needs_rework", end_reason="ticket-failures")
    _seed(db, "sprint-1.2", "ready_to_merge", end_reason="ticket-failures")
    _seed(db, "sprint-1.3", "completed", end_reason="merge_sprint")


def test_base_step_cascades_superseded_ancestors(fresh_db, tmp_path):
    _hermes_shape(fresh_db)
    resp = _complete_step("sprint-1", tmp_path)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["merged_into"] == "develop"
    assert sorted(data["cascaded_completed"]) == ["sprint-1.1", "sprint-1.2"]
    assert data["cascade_errors"] == []

    base = fresh_db.get_sprint("sprint-1", project=_PROJ)
    assert fresh_db.canonical_lifecycle(base["state"]) == "completed"
    assert base["end_reason"] == "merge_sprint"
    for lbl in ("sprint-1.1", "sprint-1.2"):
        row = fresh_db.get_sprint(lbl, project=_PROJ)
        assert fresh_db.canonical_lifecycle(row["state"]) == "completed", lbl
        assert row["end_reason"] == "superseded", lbl


def test_cascade_shares_ended_at_with_base_completion(fresh_db, tmp_path):
    _hermes_shape(fresh_db)
    resp = _complete_step("sprint-1", tmp_path)
    assert resp.status_code == 200, resp.text
    base = fresh_db.get_sprint("sprint-1", project=_PROJ)
    for lbl in ("sprint-1.1", "sprint-1.2"):
        row = fresh_db.get_sprint(lbl, project=_PROJ)
        assert row["ended_at"] == base["ended_at"], lbl


def test_child_step_does_not_cascade(fresh_db, tmp_path):
    _hermes_shape(fresh_db)
    resp = _complete_step("sprint-1.3", tmp_path, parent="sprint-1.2")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cascaded_completed"] == []
    # Siblings untouched by a child step.
    row = fresh_db.get_sprint("sprint-1.1", project=_PROJ)
    assert fresh_db.canonical_lifecycle(row["state"]) == "needs_rework"


def test_cascade_skips_completed_and_running_members(fresh_db, tmp_path):
    _hermes_shape(fresh_db)
    tip_before = fresh_db.get_sprint("sprint-1.3", project=_PROJ)
    running = lambda root, lbl: lbl == "sprint-1.2"  # noqa: E731
    resp = _complete_step("sprint-1", tmp_path, running=running)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["cascaded_completed"] == ["sprint-1.1"]

    # Running member untouched.
    row = fresh_db.get_sprint("sprint-1.2", project=_PROJ)
    assert fresh_db.canonical_lifecycle(row["state"]) == "ready_to_merge"
    # Already-completed tip keeps its original end_reason/ended_at.
    tip = fresh_db.get_sprint("sprint-1.3", project=_PROJ)
    assert tip["end_reason"] == "merge_sprint"
    assert tip["ended_at"] == tip_before["ended_at"]


def test_cascade_emits_dashboard_event(fresh_db, tmp_path):
    _hermes_shape(fresh_db)
    emit = MagicMock()
    resp = _complete_step("sprint-1", tmp_path, emit=emit)
    assert resp.status_code == 200, resp.text
    lineage_calls = [
        c for c in emit.call_args_list
        if c.kwargs.get("type") == "sprint_lineage_superseded"
    ]
    assert len(lineage_calls) == 1
    detail = lineage_calls[0].kwargs["detail"]
    assert detail["trigger"] == "complete_step"
    assert sorted(detail["cascaded"]) == ["sprint-1.1", "sprint-1.2"]
