"""Tests for issue #1420 — Apply DAG Order button on sprint planning board.

Anchored to Acceptance Criteria:
- AC5:  ordering logic — lower DAG levels before higher, within-level relative order preserved
- AC6:  persistence — new order written via POST /api/sprints/{sprint_label}/plan
- AC7:  _load_sprint_plan consistency — plan.json sequence matches DAG order after apply
- AC10: no-op on identical order — is_noop=True, no write made
- AC3:  partial preview flag surfaces in dag-order-preview response
- AC2:  dag-order-preview endpoint returns correct structure
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── helpers ───────────────────────────────────────────────────────────────────

def _issue(number: int, title: str, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "url": f"https://github.com/test/repo/issues/{number}",
        "body": "## Acceptance Criteria\n- [ ] do it",
        "labels": [{"name": l} for l in labels],
    }


def _write_estimate(estimates_dir: Path, issue_num: int, files: list[str], size: str = "S") -> None:
    estimates_dir.mkdir(parents=True, exist_ok=True)
    est = {
        "issue_number": issue_num,
        "size": size,
        "confidence": "high",
        "files_likely_affected": files,
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
    }
    (estimates_dir / f"issue-{issue_num}.json").write_text(json.dumps(est), encoding="utf-8")


def _write_plan(sprints_dir: Path, sprint_label: str, ticket_numbers: list[int]) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    plan = {"state": "planned", "tickets": ticket_numbers}
    (sprints_dir / f"{sprint_label}-plan.json").write_text(json.dumps(plan), encoding="utf-8")


def _make_client(tmp_path: Path, issues: list[dict]):
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


# ── AC5: compute_dag_order ordering logic ─────────────────────────────────────

def test_compute_dag_order_reorders_by_level():
    """AC5: tickets are reordered so lower DAG levels appear before higher levels."""
    from routers.sprints_service import compute_dag_order

    # Current board order: [2, 1] (ticket 2 first, but DAG says 1 blocks 2)
    # DAG levels: level 0 = [#1], level 1 = [#2]
    current_order = [2, 1]
    levels = [["#1"], ["#2"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["main.py"]}]

    result = compute_dag_order(current_order, levels, conflicts)

    assert result["new_order"] == [1, 2], (
        f"Expected [1, 2] (DAG order), got {result['new_order']}"
    )
    assert result["is_noop"] is False


def test_compute_dag_order_preserves_relative_order_within_level():
    """AC5: within each DAG level, the existing relative order of tickets is preserved."""
    from routers.sprints_service import compute_dag_order

    # Current order: [3, 1, 5] all in level 0 — should stay as [3, 1, 5]
    # level 1: [2, 4] — should preserve their relative order [2, 4]
    current_order = [3, 1, 5, 2, 4]
    levels = [["#1", "#3", "#5"], ["#2", "#4"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["a.py"]}]

    result = compute_dag_order(current_order, levels, conflicts)

    new_order = result["new_order"]
    # Level 0 tickets [3, 1, 5] must all appear before level 1 tickets [2, 4]
    level0_positions = [new_order.index(n) for n in [3, 1, 5]]
    level1_positions = [new_order.index(n) for n in [2, 4]]
    assert max(level0_positions) < min(level1_positions), (
        f"All level-0 tickets must precede level-1 tickets; got order {new_order}"
    )
    # Within level 0: relative order 3 < 1 < 5 (from current_order) preserved
    assert new_order.index(3) < new_order.index(1) < new_order.index(5), (
        f"Level-0 relative order must be preserved; got {new_order}"
    )
    # Within level 1: relative order 2 < 4 (from current_order) preserved
    assert new_order.index(2) < new_order.index(4), (
        f"Level-1 relative order must be preserved; got {new_order}"
    )


def test_compute_dag_order_no_op_when_already_ordered():
    """AC10: is_noop is True when current order already matches DAG order."""
    from routers.sprints_service import compute_dag_order

    current_order = [1, 2]
    levels = [["#1"], ["#2"]]
    conflicts = [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["a.py"]}]

    result = compute_dag_order(current_order, levels, conflicts)

    assert result["is_noop"] is True
    assert result["new_order"] == [1, 2]


def test_compute_dag_order_no_op_diff_is_empty():
    """AC10: when no-op, diff is empty list."""
    from routers.sprints_service import compute_dag_order

    result = compute_dag_order([1, 2], [["#1"], ["#2"]],
                               [{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["a.py"]}])
    assert result["diff"] == [], f"No-op should have empty diff; got {result['diff']}"


def test_compute_dag_order_diff_describes_moved_tickets():
    """AC4/AC5: diff lines describe which tickets change position."""
    from routers.sprints_service import compute_dag_order

    # Ticket 2 moves from position 0 to behind ticket 1
    result = compute_dag_order(
        current_order=[2, 1],
        levels=[["#1"], ["#2"]],
        conflicts=[{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["main.py"]}],
    )

    diff = result["diff"]
    assert len(diff) >= 1, f"Expected at least one diff line; got {diff}"
    # The diff should mention the move
    full_diff = " ".join(diff)
    assert "#1" in full_diff or "#2" in full_diff, (
        f"Diff should mention moved tickets; got {diff}"
    )


def test_compute_dag_order_includes_dep_edge_hint():
    """AC4: diff line mentions dep edge when tickets share files."""
    from routers.sprints_service import compute_dag_order

    result = compute_dag_order(
        current_order=[2, 1],
        levels=[["#1"], ["#2"]],
        conflicts=[{"ticket1_id": 1, "ticket2_id": 2, "shared_files": ["main.py"]}],
    )

    diff = result["diff"]
    # At least one line should reference a dep edge
    has_dep_hint = any("dep edge" in line or "blocks" in line for line in diff)
    assert has_dep_hint, f"Expected dep edge hint in diff; got {diff}"


def test_compute_dag_order_tickets_not_in_dag_appended_last():
    """AC5: tickets absent from DAG levels are placed after all leveled tickets."""
    from routers.sprints_service import compute_dag_order

    # Ticket 99 is in the current order but not in DAG levels
    current_order = [99, 1, 2]
    levels = [["#1"], ["#2"]]
    conflicts = []

    result = compute_dag_order(current_order, levels, conflicts)

    new_order = result["new_order"]
    assert new_order[-1] == 99, (
        f"Ticket absent from DAG should be last; got order {new_order}"
    )


def test_compute_dag_order_empty_levels_preserves_current():
    """AC5: when levels is empty, current_order is returned unchanged."""
    from routers.sprints_service import compute_dag_order

    current_order = [3, 1, 2]
    result = compute_dag_order(current_order, [], [])

    assert result["new_order"] == [3, 1, 2]
    assert result["is_noop"] is True


# ── AC2: dag-order-preview endpoint structure ─────────────────────────────────

def test_dag_order_preview_endpoint_returns_required_fields(tmp_path):
    """AC2: GET /api/sprints/{label}/dag-order-preview returns new_order, diff, is_noop, partial."""
    issues = [
        _issue(1, "Base ticket", ["sprint-10"]),
        _issue(2, "Dependent ticket", ["sprint-10"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/core.py"])
    _write_estimate(est, 2, ["src/core.py"])

    sprints_dir = root / "repo" / ".commander" / "sprints"
    _write_plan(sprints_dir, "sprint-10", [2, 1])

    resp = client.get("/api/sprints/sprint-10/dag-order-preview?project=test/repo")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    for key in ("new_order", "diff", "is_noop", "partial"):
        assert key in data, f"Missing key {key!r} in response: {data}"


def test_dag_order_preview_reorders_out_of_order_plan(tmp_path):
    """AC5 via endpoint: endpoint returns DAG-ordered new_order for a mis-ordered plan."""
    issues = [
        _issue(1, "Base", ["sprint-10"]),
        _issue(2, "Depends on 1", ["sprint-10"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/core.py"])
    _write_estimate(est, 2, ["src/core.py"])

    sprints_dir = root / "repo" / ".commander" / "sprints"
    _write_plan(sprints_dir, "sprint-10", [2, 1])  # wrong order

    resp = client.get("/api/sprints/sprint-10/dag-order-preview?project=test/repo")
    data = resp.json()
    # DAG says #1 blocks #2, so new_order should be [1, 2]
    assert data["new_order"] == [1, 2], (
        f"Expected DAG order [1, 2]; got {data['new_order']}"
    )
    assert data["is_noop"] is False


def test_dag_order_preview_is_noop_when_order_matches(tmp_path):
    """AC10 via endpoint: is_noop=True when current plan already matches DAG order."""
    issues = [
        _issue(1, "Base", ["sprint-10"]),
        _issue(2, "Depends on 1", ["sprint-10"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/core.py"])
    _write_estimate(est, 2, ["src/core.py"])

    sprints_dir = root / "repo" / ".commander" / "sprints"
    _write_plan(sprints_dir, "sprint-10", [1, 2])  # already correct DAG order

    resp = client.get("/api/sprints/sprint-10/dag-order-preview?project=test/repo")
    data = resp.json()
    assert data["is_noop"] is True, (
        f"Expected is_noop=True for already-ordered plan; got {data}"
    )


def test_dag_order_preview_propagates_partial_flag(tmp_path):
    """AC3: partial=True surfaces in dag-order-preview when some tickets are unestimated."""
    issues = [
        _issue(1, "Estimated", ["sprint-10"]),
        _issue(2, "Unestimated", ["sprint-10"]),  # no estimate file written
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/core.py"])
    # Deliberately no estimate for issue 2

    sprints_dir = root / "repo" / ".commander" / "sprints"
    _write_plan(sprints_dir, "sprint-10", [1, 2])

    resp = client.get("/api/sprints/sprint-10/dag-order-preview?project=test/repo")
    data = resp.json()
    assert data["partial"] is True, (
        f"Expected partial=True when unestimated tickets present; got {data}"
    )


def test_dag_order_preview_404_for_unknown_sprint(tmp_path):
    """AC2: endpoint returns 404 when sprint has no tickets."""
    issues = []
    gen = _make_client(tmp_path, issues)
    client, _ = next(gen)

    resp = client.get("/api/sprints/sprint-99/dag-order-preview?project=test/repo")
    assert resp.status_code == 404, f"Expected 404 for unknown sprint; got {resp.status_code}"


# ── AC6 + AC7: persistence via POST /api/sprints/{label}/plan ─────────────────

def test_plan_endpoint_writes_ticket_order(tmp_path):
    """AC6: POST /api/sprints/{label}/plan persists the ticket order to plan.json."""
    issues = []
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    project_root = root / "repo"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    plan_path = sprints_dir / "sprint-10-plan.json"
    plan_path.write_text(json.dumps({"state": "planned", "tickets": [1, 2, 3]}), encoding="utf-8")

    new_order = [3, 1, 2]
    with patch("server._project_root_path", return_value=project_root):
        resp = client.post(
            "/api/sprints/sprint-10/plan?project=test/repo",
            content=json.dumps(new_order),
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 200, f"POST plan failed: {resp.text}"

    written = json.loads(plan_path.read_text(encoding="utf-8"))
    assert written["tickets"] == new_order, (
        f"Expected plan.json tickets={new_order}; got {written['tickets']}"
    )


def test_load_sprint_plan_consistent_after_apply(tmp_path):
    """AC7: _load_sprint_plan returns the DAG-ordered sequence after applying via POST plan."""
    from services.sprint_manager.sprint_manager import _load_sprint_plan

    issues = []
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    project_root = root / "repo"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    plan_path = sprints_dir / "sprint-10-plan.json"
    plan_path.write_text(json.dumps({"state": "planned", "tickets": [1, 2, 3]}), encoding="utf-8")

    dag_order = [2, 3, 1]  # simulated DAG-ordered sequence
    with patch("server._project_root_path", return_value=project_root):
        client.post(
            "/api/sprints/sprint-10/plan?project=test/repo",
            content=json.dumps(dag_order),
            headers={"Content-Type": "application/json"},
        )

    loaded = _load_sprint_plan(sprints_dir, "sprint-10")
    assert loaded == dag_order, (
        f"_load_sprint_plan should return {dag_order} after apply; got {loaded}"
    )
