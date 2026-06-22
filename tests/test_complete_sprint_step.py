"""Tests for POST /sprints/{label}/complete-step — one lineage step + finalise.

Single Complete calls it once (94.4 -> 94.3); Bulk loops it deepest-first then
base -> develop. Child merges into its IMMEDIATE parent; base ships to develop +
closes tickets; a conflict returns 409 so the caller stops and resumes by re-run.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


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


def _run(label, merge_ok=True, is_base_merge_calls=None, parent="sprint-94.3"):
    import server as srv
    from fastapi.testclient import TestClient

    merge_calls = is_base_merge_calls if is_base_merge_calls is not None else []

    def fake_merge(repo, head, base, title, delete_branch=True):
        merge_calls.append((head, base))
        return (merge_ok, "merged" if merge_ok else "CONFLICT", 1 if merge_ok else None)

    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_is_sprint_running", return_value=False),
        *_dual("_branch_has_unmerged_commits", return_value=True),
        *_dual("_gh_merge_branch_via_pr", side_effect=fake_merge),
        *_dual("_open_summary_issues_for_labels", return_value=[{"number": 999}]),
        *_dual("_sprint_merge_parent_label", return_value=parent),
        *_dual("_bulk_complete_collect_issues", return_value=(["sprint-94"], [{"number": 1460}, {"number": 1464}])),
        *_dual("_plan_json_set_state", return_value=None),
        *_dual("_sprint_db_set_state", return_value=None),
        patch.object(srv.github_client, "close_issue", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        client = TestClient(srv.app, raise_server_exceptions=False)
        return client.post(
            f"/api/projects/owner/commander/sprints/{label}/complete-step",
            json={"confirmed": True},
        )
    finally:
        for p in patches:
            p.stop()


def test_child_step_merges_to_immediate_parent():
    calls: list = []
    resp = _run("sprint-94.4", is_base_merge_calls=calls, parent="sprint-94.3")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["merged_into"] == "sprint-94.3"
    assert ("sprint/sprint-94.4", "sprint/sprint-94.3") in calls
    assert data["closed_summary"] == [999]
    assert data["closed_tickets"] == []  # child step does not close work tickets


def test_base_step_ships_to_develop_and_closes_tickets():
    calls: list = []
    resp = _run("sprint-94", is_base_merge_calls=calls)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["merged_into"] == "develop"
    assert ("sprint/sprint-94", "develop") in calls
    assert sorted(data["closed_tickets"]) == [1460, 1464]


def test_conflict_returns_409():
    resp = _run("sprint-94.4", merge_ok=False)
    assert resp.status_code == 409
    assert "resolve the conflict and re-run" in resp.json()["detail"]
