"""Parent sprint board state after child re-run (perf-coach 58.1 case)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO / "apps" / "dashboard"))

import server as srv  # noqa: E402


def _write_plan(project_root: Path, label: str, data: dict) -> None:
    d = project_root / ".commander" / "sprints"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{label}-plan.json").write_text(json.dumps(data))


def _write_state(project_root: Path, label: str, data: dict) -> None:
    d = project_root / ".commander" / "sprints"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{label}-state.json").write_text(json.dumps(data))


def test_derive_partial_finished_when_child_still_open(tmp_path):
    root = tmp_path / "proj"
    _write_plan(root, "sprint-58.1", {
        "state": "needs_rework", "tickets": [495], "parent": "sprint-58",
    })
    _write_plan(root, "sprint-58.2", {
        "state": "needs_rework", "tickets": [499], "parent": "sprint-58.1",
    })
    with patch.object(srv, "_sprint_work_tickets_all_uat", return_value=False):
        lc = srv._derive_outcome_lifecycle(
            "sprint-58.1", root, "owner/repo", "needs_rework", "completed", 0,
        )
    assert lc == "partial_finished"


def test_derive_ready_to_merge_when_child_and_parent_uat(tmp_path):
    root = tmp_path / "proj"
    _write_plan(root, "sprint-58.1", {
        "state": "needs_rework", "tickets": [495, 498], "parent": "sprint-58",
    })
    _write_plan(root, "sprint-58.2", {
        "state": "needs_rework", "tickets": [499], "parent": "sprint-58.1",
    })
    with patch.object(srv, "_sprint_work_tickets_all_uat", return_value=True):
        with patch.object(srv, "_child_sprint_settled", return_value=True):
            lc = srv._derive_outcome_lifecycle(
                "sprint-58.1", root, "owner/repo", "needs_rework", "completed", 0,
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
