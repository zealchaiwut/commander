"""Tests for issue #809 — GET /api/sprints/{sprint_label}/preview-dag endpoint.

Anchored to the Acceptance Criteria:
- AC1: returns levels[], conflicts, cycles, unestimated[] built with dag_builder
- AC2: endpoint uses only cached data — zero additional GitHub API calls
- AC5: unestimated tickets produce a partial-preview warning, not an error
- AC4 (backend): cycles reported by dag_builder surface in the cycles field
- AC7: preview execution order matches what sprint_manager would produce
       (same dag_builder layering) for the same ticket set
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


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


# ── AC1: response shape ──────────────────────────────────────────────────────

def test_response_has_levels_conflicts_cycles_unestimated(tmp_path):
    issues = [
        _issue(1, "Base", ["sprint-99"]),
        _issue(2, "Dependent", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)
    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/core.py"])
    _write_estimate(est, 2, ["src/core.py"])

    resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("levels", "conflicts", "cycles", "unestimated"):
        assert key in data, f"missing {key} in preview-dag payload"
    assert isinstance(data["levels"], list)
    assert isinstance(data["conflicts"], list)
    assert isinstance(data["cycles"], list)
    assert isinstance(data["unestimated"], list)


# ── AC1/AC7: dependent ticket lands in a later level than its dependency ──────

def test_dependent_ticket_in_later_level(tmp_path):
    issues = [
        _issue(1, "Owns core", ["sprint-99"]),
        _issue(2, "Shares core", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)
    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/core.py"])
    _write_estimate(est, 2, ["src/core.py"])

    data = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo").json()
    levels = data["levels"]
    # Flatten to find the level index for each ticket id.
    level_of = {}
    for idx, lvl in enumerate(levels):
        for tid in lvl:
            level_of[tid] = idx
    assert level_of["#1"] < level_of["#2"], (
        f"dependent #2 should be in a later level than #1; got {levels}"
    )


# ── AC7: order matches what sprint_manager / dag_builder would produce ────────

def test_order_matches_dag_builder(tmp_path):
    # Diamond: 1 owns shared.py and util.py; 2 and 3 each share one; 4 shares none.
    issues = [
        _issue(1, "Root", ["sprint-99"]),
        _issue(2, "Left", ["sprint-99"]),
        _issue(3, "Right", ["sprint-99"]),
        _issue(4, "Island", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)
    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/shared.py", "src/util.py"])
    _write_estimate(est, 2, ["src/shared.py"])
    _write_estimate(est, 3, ["src/util.py"])
    _write_estimate(est, 4, ["src/island.py"])

    data = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo").json()

    # Build the expected layering directly with the same dag_builder the
    # sprint_manager uses, over the same ticket set & input order.
    from services.sprint_manager.dag_builder import build_dag
    tickets = [
        {"id": "#1", "files_touched": ["src/shared.py", "src/util.py"]},
        {"id": "#2", "files_touched": ["src/shared.py"]},
        {"id": "#3", "files_touched": ["src/util.py"]},
        {"id": "#4", "files_touched": ["src/island.py"]},
    ]
    expected = build_dag(tickets)
    # Compare as sets-per-level (within-level order is a display detail).
    got_sets = [set(lvl) for lvl in data["levels"]]
    exp_sets = [set(lvl) for lvl in expected.layers]
    assert got_sets == exp_sets, f"levels {data['levels']} != dag layers {expected.layers}"


# ── AC2: zero additional GitHub API calls — uses only cached data ────────────

def test_zero_github_api_calls(tmp_path):
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv
    gh = srv.github_client

    issues = [
        _issue(1, "A", ["sprint-99"]),
        _issue(2, "B", ["sprint-99"]),
    ]

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    est = tmp_path / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/a.py"])
    _write_estimate(est, 2, ["src/b.py"])

    # Prime the github_client cache so the endpoint can read issues without
    # touching the network, then make any gh subprocess call explode.
    r = gh._r("test/repo")
    gh._cache[f"open_issues_body:{r}"] = (time.monotonic(), issues)

    def _boom(*a, **k):
        raise AssertionError("preview-dag made a GitHub API call — must be cache-only")

    with (
        patch.object(gh, "_mirror_issues", return_value=None),
        patch.object(gh, "_json", side_effect=_boom),
        patch.object(gh, "_run", side_effect=_boom),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    flat = [tid for lvl in data["levels"] for tid in lvl]
    assert set(flat) == {"#1", "#2"}


# ── AC5: unestimated tickets → partial warning, not an error ─────────────────

def test_unestimated_produces_partial_warning_not_error(tmp_path):
    issues = [
        _issue(1, "Estimated", ["sprint-99"]),
        _issue(2, "Unestimated", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)
    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/a.py"])
    # No estimate for #2 → unestimated.

    resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 200, "unestimated must not error"
    data = resp.json()
    assert "#2" in data["unestimated"]
    assert "#1" not in data["unestimated"]
    assert data.get("partial") is True
    # Existing level groups remain present.
    assert any("#1" in lvl for lvl in data["levels"])


# ── AC4 (backend): a cycle reported by dag_builder surfaces in cycles ────────

def test_cycle_surfaces_in_cycles_field(tmp_path):
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    issues = [
        _issue(1, "A", ["sprint-99"]),
        _issue(2, "B", ["sprint-99"]),
    ]

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    est = tmp_path / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/a.py"])
    _write_estimate(est, 2, ["src/a.py"])

    # build_dag's star topology can't naturally cycle; stub it to exercise the
    # defensive cycle-reporting path that AC4 requires.
    fake_cycle = srv._CycleError(cycles=[["#1", "#2"]])

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
        patch.object(srv, "_build_dag", return_value=fake_cycle),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cycles"] == [["#1", "#2"]]


# ── AC1: conflicts (pairwise shared files) are listed ────────────────────────

def test_conflicts_listed_for_shared_files(tmp_path):
    issues = [
        _issue(1, "A", ["sprint-99"]),
        _issue(2, "B", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)
    est = root / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/shared.py"])
    _write_estimate(est, 2, ["src/shared.py"])

    data = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo").json()
    assert len(data["conflicts"]) == 1
    c = data["conflicts"][0]
    assert {c["ticket1_id"], c["ticket2_id"]} == {1, 2}
    assert c["shared_files"] == ["src/shared.py"]


# ── validation ───────────────────────────────────────────────────────────────

def test_invalid_sprint_label_400(tmp_path):
    gen = _make_client(tmp_path, [])
    client, _ = next(gen)
    resp = client.get("/api/sprints/not-a-sprint/preview-dag?project=test/repo")
    assert resp.status_code == 400


def test_404_when_sprint_has_no_issues(tmp_path):
    gen = _make_client(tmp_path, [])
    client, _ = next(gen)
    resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")
    assert resp.status_code == 404
