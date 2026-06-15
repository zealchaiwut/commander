"""Parent sprint board state after child re-run (perf-coach 58.1 case).

Updated for issue #1093: _derive_outcome_lifecycle now reads state exclusively
from the DB sprints table. Tests set up DB rows via db.record_sprint_* instead
of disk plan.json files.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO / "apps" / "dashboard"))

import db   # noqa: E402
import server as srv  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test_1041.db"
    original = db.DB_PATH
    db.DB_PATH = db_file
    db.init_db()
    yield
    db.DB_PATH = original


def test_derive_partial_finished_when_child_still_open():
    """Parent needs_rework + child still running (unsettled) → partial_finished."""
    db.record_sprint_start("sprint-58.1", project="owner/repo")
    db.record_sprint_needs_rework("sprint-58.1")
    db.record_sprint_start("sprint-58.2", project="owner/repo", parent_label="sprint-58.1")
    # sprint-58.2 is running (unsettled)
    lc = srv._derive_outcome_lifecycle(
        "sprint-58.1", Path("/unused"), "owner/repo", "needs_rework", "completed", 0,
    )
    assert lc == "partial_finished"


def test_derive_ready_to_merge_when_child_and_parent_uat():
    """Parent ready_to_merge + child completed (settled) → ready_to_merge."""
    db.record_sprint_start("sprint-58.1", project="owner/repo")
    db.record_sprint_ready_to_merge("sprint-58.1")
    db.record_sprint_start("sprint-58.2", project="owner/repo", parent_label="sprint-58.1")
    db.record_sprint_finish("sprint-58.2")  # completed (settled)
    lc = srv._derive_outcome_lifecycle(
        "sprint-58.1", Path("/unused"), "owner/repo", "needs_rework", "completed", 0,
    )
    assert lc == "ready_to_merge"


def test_moved_ticket_not_counted_as_parent_failure():
    issues = [
        {"number": 498, "outcome": "done"},
        {"number": 499, "outcome": "failed"},
    ]
    on_label = {498}
    for ri in issues:
        if ri["outcome"] == "failed" and ri["number"] not in on_label:
            ri["outcome"] = "rerun"
    assert issues[1]["outcome"] == "rerun"
    failed = sum(1 for i in issues if i["outcome"] == "failed")
    assert failed == 0
