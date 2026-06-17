"""P0: outcome ingested row must union agent_runs by ticket_id, not number."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "outcome_union.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    if "db" in sys.modules:
        del sys.modules["db"]
    import db as db_module
    db_module.init_db()
    return db_module


def test_outcome_union_no_phantom_null_ticket(fresh_db):
    """agent_runs extras use ticket_id; already-present issues must not duplicate."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    state = {
        "sprint_label": "sprint-79",
        "issues": [
            {"number": 1106, "title": "A", "status": "done", "agent_status": "completed"},
            {"number": 1107, "title": "B", "status": "done", "agent_status": "completed"},
        ],
        "wall_clock_secs": 3000,
    }
    fresh_db.record_sprint_ready_to_merge("sprint-79", end_reason="natural", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-79", state, project="o/r")
    row = fresh_db.get_sprint("sprint-79")

    extras = [
        {"ticket_id": 1106, "state": "merged", "title": ""},
        {"ticket_id": 1107, "state": "merged", "title": ""},
    ]

    with (
        patch.object(srv, "_has_rework_tickets", return_value=False),
        patch("routers.sprint_history_service._issues_from_agent_runs", return_value=extras),
    ):
        out = srv._outcome_from_ingested_row(row, "sprint-79", "o/r")

    nums = [i["number"] for i in out["issues"]]
    assert None not in nums
    assert len(nums) == 2
    assert out["counts"]["done"] == 2
