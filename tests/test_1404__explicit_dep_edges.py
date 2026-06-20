"""Tests for issue #1404: Merge explicit ticket dependencies into sprint dispatch DAG.

Acceptance Criteria covered:
- AC1: _build_sprint_dag_layers reads depends_on and blocks from estimate JSON
- AC2: depends_on adds edge dep → ticket
- AC3: blocks adds edge ticket → blocked
- AC4: explicit edges added before file-overlap (hard constraints)
- AC5: B with depends_on:[A] always in later layer, even with disjoint files
- AC6: cycle in explicit deps → warning emitted + flat single layer fallback
- AC7: preview-dag reflects merged graph (explicit + file-overlap)
- AC8: cycle during preview-dag → visible warning in response, not 500
- AC9: sprint start logs visible warning when cycle detected
- AC10: depends_on/blocks referencing IDs not in sprint silently skipped
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from services.sprint_manager.dag_builder import CycleError, DAGResult, build_dag
from services.sprint_manager.sprint_manager import _build_sprint_dag_layers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ticket(tid: str, files: list[str]) -> dict:
    return {"id": tid, "files_touched": files}


class _FakeIssueState:
    def __init__(self, number: int):
        self.number = number
        self.status = "pending"


def _issues(*nums):
    return [_FakeIssueState(n) for n in nums]


def _est(files: list[str], depends_on: list[int] | None = None, blocks: list[int] | None = None) -> dict:
    return {
        "files_likely_affected": files,
        "depends_on": depends_on or [],
        "blocks": blocks or [],
    }


def _issue_payload(number: int, title: str, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "url": f"https://github.com/test/repo/issues/{number}",
        "body": "## Acceptance Criteria\n- [ ] do it",
        "labels": [{"name": lbl} for lbl in labels],
    }


# ---------------------------------------------------------------------------
# AC2: build_dag with explicit_edges adds dep → ticket edge
# ---------------------------------------------------------------------------

def test_explicit_edges_dep_to_ticket():
    """AC2: explicit edge dep→ticket makes dep appear before ticket."""
    tickets = [
        _ticket("A", ["a_only.py"]),
        _ticket("B", ["b_only.py"]),  # disjoint files — no file-overlap edge
    ]
    result = build_dag(tickets, explicit_edges=[("A", "B")])
    assert isinstance(result, DAGResult)
    # A must be in an earlier layer than B
    layer_a = next(i for i, layer in enumerate(result.layers) if "A" in layer)
    layer_b = next(i for i, layer in enumerate(result.layers) if "B" in layer)
    assert layer_a < layer_b, f"A must precede B; got layers {result.layers}"


def test_explicit_edges_blocks_to_blocked():
    """AC3: explicit edge ticket→blocked makes ticket appear before blocked."""
    tickets = [
        _ticket("Y", ["y_only.py"]),
        _ticket("Z", ["z_only.py"]),  # disjoint files
    ]
    result = build_dag(tickets, explicit_edges=[("Y", "Z")])
    assert isinstance(result, DAGResult)
    layer_y = next(i for i, layer in enumerate(result.layers) if "Y" in layer)
    layer_z = next(i for i, layer in enumerate(result.layers) if "Z" in layer)
    assert layer_y < layer_z, f"Y must precede Z; got layers {result.layers}"


def test_explicit_edges_none_leaves_behavior_unchanged():
    """Passing explicit_edges=None is equivalent to not passing it."""
    tickets = [
        _ticket("A", ["shared.py"]),
        _ticket("B", ["shared.py"]),
    ]
    result_without = build_dag(tickets)
    result_with_none = build_dag(tickets, explicit_edges=None)
    assert result_without.layers == result_with_none.layers
    assert result_without.edges == result_with_none.edges


def test_explicit_edges_before_file_overlap():
    """AC4: explicit edges are present alongside file-overlap edges in the DAG."""
    # A and B share a file (file-overlap edge A→B already exists)
    # Explicit edge added too — should not duplicate
    tickets = [
        _ticket("A", ["shared.py"]),
        _ticket("B", ["shared.py", "b_only.py"]),
        _ticket("C", ["c_only.py"]),  # disjoint from all
    ]
    result = build_dag(tickets, explicit_edges=[("A", "C")])
    assert isinstance(result, DAGResult)
    # A must be in layer 0, C in a later layer
    layer_a = next(i for i, layer in enumerate(result.layers) if "A" in layer)
    layer_c = next(i for i, layer in enumerate(result.layers) if "C" in layer)
    assert layer_a < layer_c


def test_explicit_edge_cycle_returns_cycle_error():
    """Cycle introduced via explicit_edges is detected and returns CycleError."""
    tickets = [
        _ticket("A", ["a.py"]),
        _ticket("B", ["b.py"]),
    ]
    result = build_dag(tickets, explicit_edges=[("A", "B"), ("B", "A")])
    assert isinstance(result, CycleError)
    assert len(result.cycles) >= 1


def test_explicit_edge_unknown_id_silently_skipped():
    """AC10: explicit edge referencing ID not in tickets list is ignored."""
    tickets = [
        _ticket("A", ["a.py"]),
        _ticket("B", ["b.py"]),
    ]
    # "Z" is not a ticket ID — should silently skip without crash
    result = build_dag(tickets, explicit_edges=[("Z", "A"), ("B", "Q")])
    assert isinstance(result, DAGResult)
    all_ids = {tid for layer in result.layers for tid in layer}
    assert all_ids == {"A", "B"}


# ---------------------------------------------------------------------------
# AC1, AC5: _build_sprint_dag_layers reads depends_on from estimate JSON
# ---------------------------------------------------------------------------

def test_build_dag_layers_reads_depends_on(monkeypatch):
    """AC1, AC5: B with depends_on:[A] goes in later layer even with disjoint files."""
    estimates = {
        1: _est(["a_only.py"], depends_on=[]),
        2: _est(["b_only.py"], depends_on=[1]),  # B depends on A, disjoint files
    }
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: estimates.get(num),
    )
    issues = _issues(1, 2)
    result = _build_sprint_dag_layers(issues)
    assert result is not None
    # Find layers for issue 1 and issue 2
    layer_1 = next(i for i, layer in enumerate(result) if 1 in layer)
    layer_2 = next(i for i, layer in enumerate(result) if 2 in layer)
    assert layer_1 < layer_2, f"Issue 1 must precede issue 2; got layers {result}"


def test_build_dag_layers_reads_blocks(monkeypatch):
    """AC3: Y with blocks:[Z] puts Z in a later layer, even with disjoint files."""
    estimates = {
        10: _est(["y_only.py"], blocks=[20]),  # Y blocks Z
        20: _est(["z_only.py"]),
    }
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: estimates.get(num),
    )
    issues = _issues(10, 20)
    result = _build_sprint_dag_layers(issues)
    assert result is not None
    layer_10 = next(i for i, layer in enumerate(result) if 10 in layer)
    layer_20 = next(i for i, layer in enumerate(result) if 20 in layer)
    assert layer_10 < layer_20, f"Issue 10 must precede issue 20; got layers {result}"


def test_build_dag_layers_depends_on_not_in_sprint_skipped(monkeypatch):
    """AC10: depends_on referencing ID not in sprint is silently skipped."""
    estimates = {
        1: _est(["a.py"], depends_on=[999]),  # 999 not in sprint
        2: _est(["b.py"]),
    }
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: estimates.get(num),
    )
    issues = _issues(1, 2)
    # Should not raise, should not emit warning
    result = _build_sprint_dag_layers(issues)
    assert result is not None
    all_nums = {n for layer in result for n in layer}
    assert all_nums == {1, 2}


def test_build_dag_layers_blocks_not_in_sprint_skipped(monkeypatch):
    """AC10: blocks referencing ID not in sprint is silently skipped."""
    estimates = {
        1: _est(["a.py"], blocks=[888]),  # 888 not in sprint
        2: _est(["b.py"]),
    }
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: estimates.get(num),
    )
    issues = _issues(1, 2)
    result = _build_sprint_dag_layers(issues)
    assert result is not None


# ---------------------------------------------------------------------------
# AC6, AC9: cycle in explicit deps → warning emitted + flat layer fallback
# ---------------------------------------------------------------------------

def test_build_dag_layers_cycle_warning_and_flat_fallback(monkeypatch, capsys):
    """AC6, AC9: explicit dep cycle → visible warning on stdout, flat single layer."""
    estimates = {
        1: _est(["a.py"], depends_on=[2]),  # A depends on B
        2: _est(["b.py"], depends_on=[1]),  # B depends on A (cycle!)
    }
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: estimates.get(num),
    )
    issues = _issues(1, 2)
    result = _build_sprint_dag_layers(issues)

    # AC6: must return flat single layer (not None, not crash)
    assert result is not None, "Should return flat layer, not None"
    assert len(result) == 1, f"Expected 1 flat layer, got {result}"
    all_nums = {n for layer in result for n in layer}
    assert all_nums == {1, 2}

    # AC9: visible warning on stdout
    captured = capsys.readouterr()
    assert "cycle" in captured.out.lower() or "cycle" in captured.err.lower(), (
        f"Expected cycle warning in output; got stdout={captured.out!r} stderr={captured.err!r}"
    )


def test_build_dag_layers_cycle_does_not_crash(monkeypatch):
    """AC6: sprint does not crash on explicit dep cycle."""
    estimates = {
        5: _est(["x.py"], depends_on=[6]),
        6: _est(["y.py"], depends_on=[5]),
    }
    monkeypatch.setattr(
        "services.sprint_manager.sprint_manager._load_estimate",
        lambda num: estimates.get(num),
    )
    issues = _issues(5, 6)
    result = _build_sprint_dag_layers(issues)
    assert result is not None


# ---------------------------------------------------------------------------
# AC7, AC8: preview-dag endpoint reflects merged graph; cycle returns warning
# ---------------------------------------------------------------------------

def _write_estimate_json(estimates_dir: Path, issue_num: int, files: list[str],
                          depends_on: list[int] | None = None,
                          blocks: list[int] | None = None,
                          size: str = "S") -> None:
    estimates_dir.mkdir(parents=True, exist_ok=True)
    est = {
        "issue_number": issue_num,
        "size": size,
        "confidence": "high",
        "files_likely_affected": files,
        "depends_on": depends_on or [],
        "blocks": blocks or [],
        "risk_flags": [],
    }
    (estimates_dir / f"issue-{issue_num}.json").write_text(json.dumps(est), encoding="utf-8")


def _make_test_client(tmp_path: Path, issues: list[dict]):
    """Context manager yielding (client, tmp_path) for preview-dag tests."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        yield client, tmp_path


def test_preview_dag_explicit_dep_reflected_in_levels(tmp_path):
    """AC7: B with depends_on:[A] with disjoint files → B in later level than A."""
    issues = [
        _issue_payload(1, "A", ["sprint-99"]),
        _issue_payload(2, "B", ["sprint-99"]),
    ]
    gen = _make_test_client(tmp_path, issues)
    client, root = next(gen)
    est_dir = root / "repo" / ".commander" / "estimates"
    _write_estimate_json(est_dir, 1, ["a_only.py"])
    _write_estimate_json(est_dir, 2, ["b_only.py"], depends_on=[1])  # B depends on A

    resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    levels = data["levels"]
    # Find level index for each ticket
    level_of = {}
    for idx, lvl in enumerate(levels):
        for tid in lvl:
            level_of[tid] = idx
    assert "#1" in level_of and "#2" in level_of, f"Both tickets must appear; got {levels}"
    assert level_of["#1"] < level_of["#2"], (
        f"#2 (depends on #1) must be in a later level; got {levels}"
    )


def test_preview_dag_blocks_reflected_in_levels(tmp_path):
    """AC7: Y with blocks:[Z] with disjoint files → Z in later level than Y."""
    issues = [
        _issue_payload(10, "Y", ["sprint-99"]),
        _issue_payload(20, "Z", ["sprint-99"]),
    ]
    gen = _make_test_client(tmp_path, issues)
    client, root = next(gen)
    est_dir = root / "repo" / ".commander" / "estimates"
    _write_estimate_json(est_dir, 10, ["y_only.py"], blocks=[20])  # Y blocks Z
    _write_estimate_json(est_dir, 20, ["z_only.py"])

    resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    levels = data["levels"]
    level_of = {}
    for idx, lvl in enumerate(levels):
        for tid in lvl:
            level_of[tid] = idx
    assert "#10" in level_of and "#20" in level_of, f"Both tickets must appear; got {levels}"
    assert level_of["#10"] < level_of["#20"], (
        f"#20 (blocked by #10) must be in a later level; got {levels}"
    )


def test_preview_dag_missing_dep_id_silently_skipped(tmp_path):
    """AC10: preview-dag doesn't crash when depends_on references ID not in sprint."""
    issues = [
        _issue_payload(1, "A", ["sprint-99"]),
        _issue_payload(2, "B", ["sprint-99"]),
    ]
    gen = _make_test_client(tmp_path, issues)
    client, root = next(gen)
    est_dir = root / "repo" / ".commander" / "estimates"
    _write_estimate_json(est_dir, 1, ["a.py"])
    _write_estimate_json(est_dir, 2, ["b.py"], depends_on=[999])  # 999 not in sprint

    resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    flat = [tid for lvl in data["levels"] for tid in lvl]
    assert set(flat) == {"#1", "#2"}


def test_preview_dag_cycle_returns_warning_not_500(tmp_path):
    """AC8: cycle in explicit deps → visible warning in API response body, not 500."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    issues = [
        _issue_payload(1, "A", ["sprint-99"]),
        _issue_payload(2, "B", ["sprint-99"]),
    ]

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    est_dir = tmp_path / "repo" / ".commander" / "estimates"
    _write_estimate_json(est_dir, 1, ["a.py"], depends_on=[2])  # A depends on B
    _write_estimate_json(est_dir, 2, ["b.py"], depends_on=[1])  # B depends on A → cycle

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    # AC8: visible warning must be present
    warning_present = (
        data.get("warning")
        or data.get("cycles")
    )
    assert warning_present, f"Expected a visible cycle warning in response; got {data}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
