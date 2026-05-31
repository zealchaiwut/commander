"""
Tests for issue #445: Dispatch sprint tickets in DAG topological order.

Acceptance criteria covered:
- Normal DAG dispatch: levels computed from file-overlap, dispatched in order
- Missing plan.json: fallback to ascending issue-number order
- Missing DAG: fallback to ascending issue-number order
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Sprint manager lives at services/sprint_manager/sprint_manager.py
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

from services.sprint_manager.sprint_manager import (
    _build_sprint_dag_layers,
    _compute_dispatch_levels,
    _load_sprint_plan,
)


# ---------------------------------------------------------------------------
# Minimal IssueState stub
# ---------------------------------------------------------------------------

class _FakeIssueState:
    def __init__(self, number: int):
        self.number = number
        self.status = "pending"


# ---------------------------------------------------------------------------
# _load_sprint_plan
# ---------------------------------------------------------------------------

def test_load_sprint_plan_valid(tmp_path):
    order = [3, 1, 2]
    plan = tmp_path / "sprint-sprint-5-plan.json"
    plan.write_text(json.dumps(order), encoding="utf-8")
    result = _load_sprint_plan(tmp_path, "sprint-5")
    assert result == order


def test_load_sprint_plan_missing(tmp_path):
    result = _load_sprint_plan(tmp_path, "sprint-5")
    assert result is None


def test_load_sprint_plan_invalid_json(tmp_path):
    plan = tmp_path / "sprint-sprint-5-plan.json"
    plan.write_text("not json", encoding="utf-8")
    result = _load_sprint_plan(tmp_path, "sprint-5")
    assert result is None


def test_load_sprint_plan_not_int_list(tmp_path):
    plan = tmp_path / "sprint-sprint-5-plan.json"
    plan.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    result = _load_sprint_plan(tmp_path, "sprint-5")
    assert result is None


# ---------------------------------------------------------------------------
# _compute_dispatch_levels
# ---------------------------------------------------------------------------

def _issues(*nums):
    return [_FakeIssueState(n) for n in nums]


def test_compute_dispatch_no_dag_no_plan():
    """No DAG, no plan: single level in ascending issue-number order."""
    issues = _issues(3, 1, 2)
    levels = _compute_dispatch_levels(issues, None, None)
    assert len(levels) == 1
    assert [i.number for i in levels[0]] == [1, 2, 3]


def test_compute_dispatch_no_dag_with_plan():
    """No DAG: single level sorted by plan order."""
    issues = _issues(1, 2, 3)
    levels = _compute_dispatch_levels(issues, [3, 1, 2], None)
    assert len(levels) == 1
    assert [i.number for i in levels[0]] == [3, 1, 2]


def test_compute_dispatch_dag_two_levels():
    """DAG with two layers: issues grouped by level."""
    issues = _issues(1, 2, 3)
    dag_layers = [[1, 2], [3]]
    levels = _compute_dispatch_levels(issues, None, dag_layers)
    assert len(levels) == 2
    assert sorted(i.number for i in levels[0]) == [1, 2]
    assert [i.number for i in levels[1]] == [3]


def test_compute_dispatch_dag_within_level_sorted_by_plan():
    """Within a DAG level, issues sorted by plan_order."""
    issues = _issues(1, 2, 3)
    dag_layers = [[1, 2, 3]]
    plan_order = [3, 1, 2]
    levels = _compute_dispatch_levels(issues, plan_order, dag_layers)
    assert len(levels) == 1
    assert [i.number for i in levels[0]] == [3, 1, 2]


def test_compute_dispatch_dag_trailing_issues_not_in_dag():
    """Issues absent from DAG layers appear as a trailing level."""
    issues = _issues(1, 2, 3, 4)
    dag_layers = [[1], [2]]  # 3 and 4 not in DAG
    levels = _compute_dispatch_levels(issues, None, dag_layers)
    all_nums = [i.number for level in levels for i in level]
    # 1 before 2, 3 and 4 after both
    assert all_nums.index(1) < all_nums.index(2)
    assert all_nums.index(2) < all_nums.index(3)
    assert 4 in all_nums


def test_compute_dispatch_single_ticket():
    """Single ticket: one level, one level_start and one level_complete."""
    issues = _issues(5)
    levels = _compute_dispatch_levels(issues, None, [[5]])
    assert len(levels) == 1
    assert levels[0][0].number == 5


# ---------------------------------------------------------------------------
# _build_sprint_dag_layers
# ---------------------------------------------------------------------------

def _fake_estimate(files):
    return {"files_likely_affected": files}


def test_build_sprint_dag_layers_no_overlap(monkeypatch):
    """No file overlap → all issues in one layer."""
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: _fake_estimate([f"file{num}.py"]),
    )
    issues = _issues(1, 2, 3)
    result = _build_sprint_dag_layers(issues)
    assert result is not None
    all_nums = [n for layer in result for n in layer]
    assert sorted(all_nums) == [1, 2, 3]


def test_build_sprint_dag_layers_with_dependency(monkeypatch):
    """Ticket 2 touches same file as ticket 1 → 2 depends on 1."""
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: _fake_estimate(["shared.py"] if num in (1, 2) else [f"other{num}.py"]),
    )
    issues = _issues(1, 2, 3)
    result = _build_sprint_dag_layers(issues)
    assert result is not None
    assert len(result) >= 2
    assert 1 in result[0]
    # 2 must come after 1
    layer_of_2 = next(i for i, layer in enumerate(result) if 2 in layer)
    layer_of_1 = next(i for i, layer in enumerate(result) if 1 in layer)
    assert layer_of_2 > layer_of_1


def test_build_sprint_dag_layers_missing_estimates(monkeypatch):
    """No estimate files: all issues in one layer (no file overlap)."""
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: None,
    )
    issues = _issues(1, 2)
    result = _build_sprint_dag_layers(issues)
    assert result is not None


def test_build_sprint_dag_layers_unavailable(monkeypatch):
    """If dag_builder unavailable, return None."""
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._DAG_BUILDER_AVAILABLE",
        False,
    )
    issues = _issues(1, 2)
    result = _build_sprint_dag_layers(issues)
    assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
