"""preview_dag only reports a file conflict when both tickets land in the SAME
dispatch level (real parallel risk).

Bug: two tickets sharing a file were flagged as a conflict even when the DAG
already sequenced them into different levels — a phantom "#a ∥ #b" with a fix chip
that no-ops because they're already ordered. The fix filters conflicts to
same-level pairs.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


def _preview(layers):
    import server as srv
    from routers import sprints_service
    from services.sprint_manager.dag_builder import DAGResult

    # #1 and #2 both touch server.py (a pairwise conflict); #3 is independent.
    files = {1: ["server.py"], 2: ["server.py"], 3: ["other.py"]}
    issues = [
        {"number": n, "title": f"t{n}", "labels": [{"name": "sprint-9"}], "body": ""}
        for n in files
    ]
    patches = [
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch.object(srv.github_client, "classify_issue", return_value="backlog"),
        patch.object(srv, "_project_root_path", return_value=Path("/tmp")),
        patch.object(srv, "_commander_dir", return_value=Path("/tmp/.commander")),
        patch.object(srv, "_resolve_issue_estimate",
                     side_effect=lambda iss, d: {"estimated": True, "files": files[iss["number"]], "size": "M"}),
        patch.object(srv, "_build_dag", return_value=DAGResult(layers=layers, edges=[])),
        patch.object(srv, "_DAG_BUILDER_AVAILABLE", True),
    ]
    for p in patches:
        p.start()
    try:
        return sprints_service.preview_dag("sprint-9", "owner/repo")
    finally:
        for p in patches:
            p.stop()


def test_conflict_dropped_when_tickets_in_different_levels():
    # #1 in level 0, #2 in level 1 — sequenced, no parallel risk.
    res = _preview(layers=[["#1", "#3"], ["#2"]])
    assert res["conflicts"] == []


def test_conflict_kept_when_tickets_in_same_level():
    # #1 and #2 both in level 0 — they'd run in parallel: real conflict.
    res = _preview(layers=[["#1", "#2"], ["#3"]])
    ids = {(c["ticket1_id"], c["ticket2_id"]) for c in res["conflicts"]}
    assert (1, 2) in ids
