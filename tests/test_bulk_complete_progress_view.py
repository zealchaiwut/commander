"""Bulk-complete preview adds chain status (members) + tickets grouped by sprint.

Point 1: members shows each step's done/pending status (merged ✓ / completed ✓ /
pending ○). Point 2: tickets_by_sprint groups the close-list by owning sprint.
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


def _preview():
    import server as srv
    from fastapi.testclient import TestClient

    tickets = [
        {"number": 1460, "title": "backfill", "category": "UAT"},
        {"number": 1461, "title": "audit", "category": "UAT"},
        {"number": 1464, "title": "scope reads", "category": "UAT"},
    ]
    parents = {"sprint-94.4": "sprint-94.3", "sprint-94.2": "sprint-94.1"}
    runs = {  # deepest first wins
        "sprint-94.4": [{"issue_number": 1460}, {"issue_number": 1464}],
        "sprint-94.2": [{"issue_number": 1461}],
    }
    completed = {"sprint-94.4"}  # 94.4 done; 94.2 not

    class _FakeDB:
        def get_sprint(self, label, project=None):
            return {"state": "completed"} if label in completed else {"state": "ready_to_merge"}

        def canonical_lifecycle(self, raw):
            return raw or "unknown"

        def agent_runs_for_sprint(self, label, project=None):
            return runs.get(label, [])

    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_bulk_complete_unsettled_children", return_value=[]),
        *_dual("_bulk_complete_collect_issues", return_value=(["sprint-94", "sprint-94.2", "sprint-94.4"], [])),
        *_dual("_bulk_complete_ticket_rows", return_value=tickets),
        # 94.2 still pending; 94.4 already merged (not a pending head); base pending.
        *_dual("_bulk_complete_merge_steps", return_value=[
            {"head": "sprint/sprint-94.2", "base": "sprint/sprint-94.1", "label": "94.2 → 94.1"},
            {"head": "sprint/sprint-94", "base": "develop", "label": "94 → develop"},
        ]),
        *_dual("_sprint_merge_parent_label", side_effect=lambda root, lbl: parents.get(lbl, "sprint-94")),
        patch("server.db", _FakeDB()),
    ]
    for p in patches:
        p.start()
    try:
        client = TestClient(srv.app, raise_server_exceptions=False)
        return client.get("/api/projects/owner/commander/sprints/sprint-94/bulk-complete-preview")
    finally:
        for p in patches:
            p.stop()


def test_members_show_merged_completed_pending():
    resp = _preview()
    assert resp.status_code == 200, resp.text
    members = {m["label"]: m for m in resp.json()["members"]}
    assert members["sprint-94.4"]["completed"] is True
    assert members["sprint-94.4"]["merged"] is True          # not a pending head
    assert members["sprint-94.2"]["merged"] is False         # pending merge step
    assert members["sprint-94.2"]["completed"] is False
    assert members["sprint-94"]["is_base"] is True
    assert members["sprint-94"]["parent"] == "develop"


def test_tickets_grouped_by_owning_sprint():
    resp = _preview()
    groups = {g["label"]: [t["number"] for t in g["tickets"]] for g in resp.json()["tickets_by_sprint"]}
    assert sorted(groups["sprint-94.4"]) == [1460, 1464]
    assert groups["sprint-94.2"] == [1461]
