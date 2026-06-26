"""A merged `needs_rework` lineage must be completable, and a silent DB
rejection must surface — not masquerade as success.

Root cause (board showed Sprint 94 PARTIAL after a full merge): superseded
ancestors sit in `needs_rework`; `needs_rework → completed` is a reconcile-only
B2 edge, but complete-step finished with actor="manager" → the state machine
rejected it and returned accepted=False WITHOUT raising → complete-step reported
success while the DB never changed.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "needs_rework.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _seed_needs_rework(db, label, project):
    db.transition_sprint_state(label, "running", actor="manager", project=project)
    db.transition_sprint_state(label, "ready_to_merge", actor="manager", project=project)
    db.transition_sprint_state(label, "needs_rework", actor="manager", project=project)


def test_reconcile_completes_needs_rework_but_manager_cannot(fresh_db):
    db = fresh_db

    proj = "owner/needs-rework-test"
    _seed_needs_rework(db, "sprint-nrw1", proj)

    # actor="manager" is rejected (B2 edge is reconcile-only) — silently, no raise.
    res_mgr = db.record_sprint_finish("sprint-nrw1", project=proj, actor="manager")
    assert res_mgr.accepted is False

    # actor="reconcile" completes the merged superseded ancestor.
    res_rec = db.record_sprint_finish("sprint-nrw1", project=proj, actor="reconcile")
    assert res_rec.accepted is True
    row = db.get_sprint("sprint-nrw1", project=proj)
    assert db.canonical_lifecycle(row["state"]) == "completed"


def test_set_state_returns_false_on_rejection(fresh_db):
    import startup

    db = fresh_db
    proj = "owner/needs-rework-test"
    _seed_needs_rework(db, "sprint-nrw2", proj)
    # manager → rejected → False; reconcile → accepted → True
    assert startup._sprint_db_set_state("sprint-nrw2", proj, "completed", actor="manager") is False
    assert startup._sprint_db_set_state("sprint-nrw2", proj, "completed", actor="reconcile") is True


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


def test_mark_merged_completed_uses_manager_for_running(fresh_db):
    import startup

    db = fresh_db
    proj = "owner/running-complete-test"
    db.transition_sprint_state("sprint-run1", "running", actor="manager", project=proj)
    # reconcile-only path fails on running→completed
    assert startup._sprint_db_set_state(
        "sprint-run1", proj, "completed", actor="reconcile",
    ) is False
    assert startup._sprint_db_mark_merged_completed("sprint-run1", proj) is True
    row = db.get_sprint("sprint-run1", project=proj)
    assert db.canonical_lifecycle(row["state"]) == "completed"


def test_mark_merged_completed_uses_reconcile_for_needs_rework(fresh_db):
    import startup

    db = fresh_db
    proj = "owner/nrw-complete-test"
    _seed_needs_rework(db, "sprint-nrw3", proj)
    assert startup._sprint_db_mark_merged_completed("sprint-nrw3", proj) is True
    row = db.get_sprint("sprint-nrw3", project=proj)
    assert db.canonical_lifecycle(row["state"]) == "completed"


def test_mark_merged_completed_bootstraps_draft_row(fresh_db):
    """perf-coach-style sprints with no DB row must still settle after git merge."""
    import startup

    db = fresh_db
    proj = "zealchaiwut/perf-coach"
    assert db.get_sprint("sprint-85.5", project=proj) is None
    assert startup._sprint_db_mark_merged_completed("sprint-85.5", proj) is True
    row = db.get_sprint("sprint-85.5", project=proj)
    assert row is not None
    assert row["project"] == proj
    assert db.canonical_lifecycle(row["state"]) == "completed"


def test_complete_step_surfaces_silent_db_rejection():
    """If the DB transition is rejected (returns False), complete-step must 500 —
    not report success while the lifecycle stays unchanged."""
    import server as srv
    from fastapi.testclient import TestClient

    def fake_merge(repo, head, base, title, delete_branch=True):
        return (True, "merged", 1)

    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_is_sprint_running", return_value=False),
        *_dual("_branch_has_unmerged_commits", return_value=False),  # already merged
        *_dual("_gh_merge_branch_via_pr", side_effect=fake_merge),
        *_dual("_open_summary_issues_for_labels", return_value=[]),
        *_dual("_sprint_merge_parent_label", return_value="sprint-94.3"),
        *_dual("_bulk_complete_collect_issues", return_value=(["sprint-94"], [])),
        *_dual("_plan_json_set_state", return_value=None),
        *_dual("_sprint_db_mark_merged_completed", return_value=False),  # silent rejection
    ]
    for p in patches:
        p.start()
    try:
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.post(
            "/api/projects/owner/commander/sprints/sprint-94.4/complete-step",
            json={"confirmed": True},
        )
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 500
    assert "rejected by the" in resp.json()["detail"]
