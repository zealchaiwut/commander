"""History running-sprint roster: queued tickets must show, matching the live view.

Bug: a running sprint's History list was rebuilt from agent_runs (only dispatched
tickets), so a still-queued ticket (e.g. #1460 "waiting") was dropped — History
showed 2 issues while the live Running view showed the full planned roster of 3.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from routers import sprint_history_service as h  # noqa: E402


def test_union_planned_roster_adds_queued(monkeypatch, tmp_path):
    monkeypatch.setattr(h, "_read_plan_file", lambda dirs, label: {"tickets": [1460, 1461, 1464]})
    rec = {
        "label": "sprint-94.2", "lifecycle_state": "running",
        "issues": [{"ticket_id": 1461, "state": "open"}, {"ticket_id": 1464, "state": "merged"}],
    }
    h._union_planned_roster(rec, tmp_path)
    assert sorted(i["ticket_id"] for i in rec["issues"]) == [1460, 1461, 1464]
    queued = next(i for i in rec["issues"] if i["ticket_id"] == 1460)
    assert queued.get("queued") is True and queued["state"] == "open"


def test_union_no_plan_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(h, "_read_plan_file", lambda dirs, label: None)
    rec = {"label": "sprint-x", "lifecycle_state": "running", "issues": [{"ticket_id": 1}]}
    h._union_planned_roster(rec, tmp_path)
    assert [i["ticket_id"] for i in rec["issues"]] == [1]


def _fake_db(runs):
    class _D:
        def agent_runs_for_sprint(self, label, project=None):
            return runs.get(label, [])
    return lambda: _D()


def test_running_keeps_queued_ticket(monkeypatch):
    monkeypatch.setattr(h, "_db", _fake_db({"sprint-94.2": [{"issue_number": 1461}, {"issue_number": 1464}]}))
    recs = [{
        "label": "sprint-94.2", "project": "p", "lifecycle_state": "running",
        "issues": [{"ticket_id": 1460}, {"ticket_id": 1461}, {"ticket_id": 1464}],
    }]
    h._attribute_issues_to_runs(recs)
    assert sorted(i["ticket_id"] for i in recs[0]["issues"]) == [1460, 1461, 1464]


def test_finished_narrows_to_ran(monkeypatch):
    monkeypatch.setattr(h, "_db", _fake_db({"sprint-94.2": [{"issue_number": 1461}, {"issue_number": 1464}]}))
    recs = [{
        "label": "sprint-94.2", "project": "p", "lifecycle_state": "completed",
        "issues": [{"ticket_id": 1460}, {"ticket_id": 1461}, {"ticket_id": 1464}],
    }]
    h._attribute_issues_to_runs(recs)
    assert sorted(i["ticket_id"] for i in recs[0]["issues"]) == [1461, 1464]
