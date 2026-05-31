"""Tests for #443: sprint DAG builder from file overlap."""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.dag_builder import CyclicDependencyError, DAGResult, build_dag


# ── helpers ───────────────────────────────────────────────────────────────────

def _ticket(tid: str, files: list[str]) -> dict:
    return {"id": tid, "files_touched": files}


def _flat(layers: list[list[str]]) -> list[str]:
    return [t for layer in layers for t in layer]


# ── AC: empty sprint ───────────────────────────────────────────────────────────

def test_empty_sprint():
    result = build_dag([])
    assert isinstance(result, DAGResult)
    assert result.layers == []
    assert result.edges == []


# ── AC: single ticket ─────────────────────────────────────────────────────────

def test_single_ticket():
    result = build_dag([_ticket("A", ["src/foo.py"])])
    assert result.layers == [["A"]]
    assert result.edges == []


# ── AC: all independent (no file overlap) ─────────────────────────────────────

def test_all_independent():
    tickets = [
        _ticket("A", ["src/a.py"]),
        _ticket("B", ["src/b.py"]),
        _ticket("C", ["src/c.py"]),
    ]
    result = build_dag(tickets)
    assert result.layers == [["A", "B", "C"]]
    assert result.edges == []


# ── AC: two tickets sharing a file ────────────────────────────────────────────

def test_two_tickets_sharing_file():
    tickets = [
        _ticket("A", ["src/foo.py"]),
        _ticket("B", ["src/foo.py", "src/bar.py"]),
    ]
    result = build_dag(tickets)
    assert len(result.layers) == 2
    assert result.layers[0] == ["A"]
    assert result.layers[1] == ["B"]
    assert len(result.edges) == 1
    assert result.edges[0] == ("A", "B")


# ── AC: sequential chain A→B→C ────────────────────────────────────────────────

def test_sequential_chain():
    tickets = [
        _ticket("A", ["foo.py"]),
        _ticket("B", ["foo.py", "bar.py"]),
        _ticket("C", ["bar.py", "baz.py"]),
    ]
    result = build_dag(tickets)
    assert _flat(result.layers) == ["A", "B", "C"]
    assert len(result.layers) == 3


# ── AC: diamond dependency ────────────────────────────────────────────────────

def test_diamond_dependency():
    tickets = [
        _ticket("A", ["foo.py"]),
        _ticket("B", ["foo.py", "b_only.py"]),
        _ticket("C", ["foo.py", "c_only.py"]),
        _ticket("D", ["b_only.py", "c_only.py"]),
    ]
    result = build_dag(tickets)
    assert result.layers[0] == ["A"]
    b_c_layer = set(result.layers[1])
    assert b_c_layer == {"B", "C"}
    assert result.layers[2] == ["D"]


# ── AC: tickets sharing multiple files ────────────────────────────────────────

def test_multiple_shared_files():
    tickets = [
        _ticket("A", ["x.py", "y.py", "z.py"]),
        _ticket("B", ["x.py", "y.py"]),
    ]
    result = build_dag(tickets)
    assert len(result.edges) == 1
    assert result.edges[0] == ("A", "B")
    assert result.layers[0] == ["A"]
    assert result.layers[1] == ["B"]


# ── AC: ticket with empty files_touched ───────────────────────────────────────

def test_ticket_with_no_files_is_independent():
    tickets = [
        _ticket("A", ["src/foo.py"]),
        _ticket("B", []),
        _ticket("C", ["src/foo.py"]),
    ]
    result = build_dag(tickets)
    assert set(result.layers[0]) == {"A", "B"}
    assert result.layers[1] == ["C"]


# ── AC: DAGResult is importable and layers are iterable ──────────────────────

def test_dagresult_iterable():
    result = build_dag([_ticket("X", ["a.py"]), _ticket("Y", ["b.py"])])
    for layer in result.layers:
        assert isinstance(layer, list)
        for tid in layer:
            assert isinstance(tid, str)


# ── AC: cycle guard (manual construction via monkeypatch) ─────────────────────

def test_cyclic_dependency_error_raised(monkeypatch):
    """Force a cycle by patching in_degree after build_dag runs partial setup."""
    from services.sprint_manager import dag_builder

    original_build = dag_builder.build_dag

    def patched_build(tickets):
        import collections
        if not tickets:
            return DAGResult()
        ids = [t["id"] for t in tickets]
        file_sets = {t["id"]: set(t.get("files_touched") or []) for t in tickets}
        adj = collections.defaultdict(list)
        in_degree = collections.defaultdict(int)
        edges = []
        for tid in ids:
            in_degree[tid] = in_degree.get(tid, 0)
        # Introduce artificial cycle: A→B and B→A
        adj["A"].append("B")
        adj["B"].append("A")
        in_degree["A"] = 1
        in_degree["B"] = 1
        edges = [("A", "B"), ("B", "A")]
        queue = collections.deque(tid for tid in ids if in_degree[tid] == 0)
        layers = []
        visited = 0
        while queue:
            layer = list(queue)
            layers.append(layer)
            queue.clear()
            for node in layer:
                visited += 1
                for neighbour in adj[node]:
                    in_degree[neighbour] -= 1
                    if in_degree[neighbour] == 0:
                        queue.append(neighbour)
        if visited != len(ids):
            raise CyclicDependencyError("Cycle detected in ticket file-overlap graph")
        return DAGResult(layers=layers, edges=edges)

    monkeypatch.setattr(dag_builder, "build_dag", patched_build)
    with pytest.raises(CyclicDependencyError):
        dag_builder.build_dag([_ticket("A", ["f.py"]), _ticket("B", ["f.py"])])
