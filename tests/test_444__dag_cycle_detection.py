"""Tests for #444: cycle detection in sprint DAG builder."""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from services.sprint_manager.dag_builder import (
    CycleError,
    DAGResult,
    _find_all_cycles,
    build_dag,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _ticket(tid: str, files: list[str]) -> dict:
    return {"id": tid, "files_touched": files}


def _adj(*edges: tuple[str, str]) -> dict[str, list[str]]:
    """Build adjacency list from edge tuples."""
    adj: dict[str, list[str]] = {}
    for src, dst in edges:
        adj.setdefault(src, []).append(dst)
        adj.setdefault(dst, [])
    return adj


# ── _find_all_cycles: no cycle ────────────────────────────────────────────────

def test_find_no_cycle_linear():
    adj = _adj(("A", "B"), ("B", "C"))
    result = _find_all_cycles(adj, ["A", "B", "C"])
    assert result == []


def test_find_no_cycle_diamond():
    adj = _adj(("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"))
    result = _find_all_cycles(adj, ["A", "B", "C", "D"])
    assert result == []


def test_find_no_cycle_single_node():
    result = _find_all_cycles({"A": []}, ["A"])
    assert result == []


def test_find_no_cycle_empty():
    result = _find_all_cycles({}, [])
    assert result == []


# ── _find_all_cycles: single two-node cycle ───────────────────────────────────

def test_find_two_node_cycle():
    adj = _adj(("A", "B"), ("B", "A"))
    result = _find_all_cycles(adj, ["A", "B"])
    assert len(result) == 1
    assert set(result[0]) == {"A", "B"}


def test_find_two_node_cycle_ids_normalized():
    """Cycle starts at lexicographically smallest node."""
    adj = _adj(("Z", "A"), ("A", "Z"))
    result = _find_all_cycles(adj, ["Z", "A"])
    assert len(result) == 1
    assert result[0][0] == "A"


# ── _find_all_cycles: longer cycle (3+ nodes) ────────────────────────────────

def test_find_three_node_cycle():
    adj = _adj(("A", "B"), ("B", "C"), ("C", "A"))
    result = _find_all_cycles(adj, ["A", "B", "C"])
    assert len(result) == 1
    assert set(result[0]) == {"A", "B", "C"}


def test_find_four_node_cycle():
    adj = _adj(("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"))
    result = _find_all_cycles(adj, ["A", "B", "C", "D"])
    assert len(result) == 1
    assert set(result[0]) == {"A", "B", "C", "D"}


# ── _find_all_cycles: multiple disjoint cycles ────────────────────────────────

def test_find_two_disjoint_two_node_cycles():
    """Two completely separate 2-node cycles."""
    adj = _adj(("A", "B"), ("B", "A"), ("C", "D"), ("D", "C"))
    result = _find_all_cycles(adj, ["A", "B", "C", "D"])
    assert len(result) == 2
    cycle_sets = [frozenset(c) for c in result]
    assert frozenset({"A", "B"}) in cycle_sets
    assert frozenset({"C", "D"}) in cycle_sets


def test_find_two_disjoint_cycles_different_lengths():
    """A 2-node cycle and a 3-node cycle with no shared nodes."""
    adj = _adj(
        ("A", "B"), ("B", "A"),          # 2-node cycle
        ("X", "Y"), ("Y", "Z"), ("Z", "X"),  # 3-node cycle
    )
    result = _find_all_cycles(adj, ["A", "B", "X", "Y", "Z"])
    assert len(result) == 2
    cycle_sets = [frozenset(c) for c in result]
    assert frozenset({"A", "B"}) in cycle_sets
    assert frozenset({"X", "Y", "Z"}) in cycle_sets


def test_find_no_duplicate_cycle():
    """Same cycle reached from different starting points is reported once."""
    adj = _adj(("#1", "#2"), ("#2", "#1"))
    result = _find_all_cycles(adj, ["#1", "#2"])
    assert len(result) == 1


# ── build_dag returns CycleError (not exception) ──────────────────────────────

def test_build_dag_returns_cycle_error_not_raises(monkeypatch):
    from services.sprint_manager import dag_builder

    monkeypatch.setattr(dag_builder, "_find_all_cycles", lambda adj, ids: [["A", "B"]])
    result = build_dag([_ticket("A", ["f.py"]), _ticket("B", ["g.py"])])
    assert isinstance(result, CycleError)
    assert not isinstance(result, Exception)


def test_build_dag_cycle_error_has_cycles(monkeypatch):
    from services.sprint_manager import dag_builder

    monkeypatch.setattr(dag_builder, "_find_all_cycles", lambda adj, ids: [["#1", "#2"]])
    result = build_dag([_ticket("#1", []), _ticket("#2", [])])
    assert isinstance(result, CycleError)
    assert result.cycles == [["#1", "#2"]]


def test_build_dag_acyclic_returns_dag_result():
    tickets = [
        _ticket("A", ["foo.py"]),
        _ticket("B", ["bar.py"]),
    ]
    result = build_dag(tickets)
    assert isinstance(result, DAGResult)


# ── CycleError.to_payload format ──────────────────────────────────────────────

def test_cycle_error_to_payload_single_cycle():
    err = CycleError(cycles=[["#147", "#155"]])
    payload = err.to_payload()
    assert payload["error"] == "cycle_detected"
    assert payload["cycles"] == [["#147", "#155"]]


def test_cycle_error_to_payload_multiple_cycles():
    err = CycleError(cycles=[["#1", "#2"], ["#201", "#202", "#203"]])
    payload = err.to_payload()
    assert payload["error"] == "cycle_detected"
    assert len(payload["cycles"]) == 2
    cycle_sets = [frozenset(c) for c in payload["cycles"]]
    assert frozenset({"#1", "#2"}) in cycle_sets
    assert frozenset({"#201", "#202", "#203"}) in cycle_sets


# ── build_dag: acyclic graphs run without regression ─────────────────────────

def test_build_dag_no_regression_empty():
    result = build_dag([])
    assert isinstance(result, DAGResult)
    assert result.layers == []
    assert result.edges == []


def test_build_dag_no_regression_chain():
    tickets = [
        _ticket("A", ["foo.py"]),
        _ticket("B", ["foo.py", "bar.py"]),
        _ticket("C", ["bar.py"]),
    ]
    result = build_dag(tickets)
    assert isinstance(result, DAGResult)
    flat = [t for layer in result.layers for t in layer]
    assert flat == ["A", "B", "C"]
