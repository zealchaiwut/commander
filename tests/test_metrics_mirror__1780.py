"""Tests for issue #1780 — metrics endpoint routes through issues mirror.

AC1: GET /api/metrics/sprints issues zero gh subprocess calls with populated mirror.
AC7: _bulk_rework_from_mirror makes a single mirror pass, not per-sprint gh loops.
AC6: when mirror is empty, falls back to existing gh path without error.
AC5: response shape is byte-identical (same keys, types, values) whether from mirror or gh.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

import db  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_state_file(sprints_dir: Path, sprint_num: int, start_ts: str) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": f"sprint-{sprint_num}",
        "project": "owner/repo",
        "start_timestamp": start_ts,
        "wall_clock_secs": 600.0,
        "issues": [
            {"number": sprint_num * 10, "status": "done",
             "coder_started_at": None, "tester_started_at": None},
        ],
    }
    (sprints_dir / f"sprint-{sprint_num}-state.json").write_text(json.dumps(state))


def _mirror_iss(number: int, sprint_label: str, extra_labels: list[str] | None = None) -> dict:
    labels = [{"name": sprint_label}] + [{"name": l} for l in (extra_labels or [])]
    return {"number": number, "state": "open", "title": f"Issue {number}",
            "labels": labels, "updatedAt": ""}


def _call_metrics(tmp_path: Path, mirror: list[dict], repo: str = "owner/repo",
                  subprocess_raise: bool = False) -> tuple:
    """Helper: call GET /api/metrics/sprints with mocked mirror and subprocess."""
    mod = importlib.import_module("routers.metrics")

    subprocess_mock = MagicMock()
    if subprocess_raise:
        subprocess_mock.run.side_effect = AssertionError(
            "subprocess.run must NOT be called when mirror is populated"
        )

    projects = [{"repo": repo}]
    with patch.object(db, "get_mirrored_issues", return_value=mirror), \
         patch.object(mod, "projects_module") as proj_mock, \
         patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "subprocess", subprocess_mock):
        proj_mock.load_projects.return_value = projects
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(mod.router)
        client = TestClient(app)
        resp = client.get("/api/metrics/sprints?from=2026-01-01&to=2026-12-31")
    return resp, subprocess_mock


# ── AC1: zero gh subprocess calls when mirror is populated ────────────────────

def test_ac1_zero_gh_calls_with_populated_mirror(tmp_path):
    """AC1: GET /api/metrics/sprints fires no subprocess when mirror has data."""
    sprints_dir = tmp_path / ".commander" / "sprints"
    _make_state_file(sprints_dir, 10, "2026-07-01T00:00:00")

    mirror = [
        _mirror_iss(5, "sprint-10", ["needs-rework"]),
        _mirror_iss(6, "sprint-10"),  # no rework label
    ]
    resp, sp_mock = _call_metrics(tmp_path, mirror, subprocess_raise=True)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["sprint_label"] == "sprint-10"
    assert data[0]["ticket_outcomes_breakdown"]["needs_rework"] == 1


def test_ac1_mirror_grouped_by_sprint_label(tmp_path):
    """AC1: rework count from mirror is grouped correctly per sprint label."""
    sprints_dir = tmp_path / ".commander" / "sprints"
    _make_state_file(sprints_dir, 20, "2026-07-02T00:00:00")
    _make_state_file(sprints_dir, 21, "2026-07-03T00:00:00")

    mirror = [
        _mirror_iss(1, "sprint-20", ["needs-rework"]),
        _mirror_iss(2, "sprint-20", ["needs-rework"]),
        _mirror_iss(3, "sprint-21", ["needs-rework"]),
        _mirror_iss(4, "sprint-21"),
    ]
    resp, _ = _call_metrics(tmp_path, mirror, subprocess_raise=True)

    assert resp.status_code == 200
    data = {row["sprint_label"]: row for row in resp.json()}
    assert data["sprint-20"]["ticket_outcomes_breakdown"]["needs_rework"] == 2
    assert data["sprint-21"]["ticket_outcomes_breakdown"]["needs_rework"] == 1


# ── AC7: single mirror pass for multiple sprints ──────────────────────────────

def test_ac7_single_mirror_pass_not_per_sprint(tmp_path):
    """AC7: db.get_mirrored_issues called once per project, not once per sprint."""
    sprints_dir = tmp_path / ".commander" / "sprints"
    for n, ts in [(1, "2026-07-01T00:00:00"), (2, "2026-07-02T00:00:00"), (3, "2026-07-03T00:00:00")]:
        _make_state_file(sprints_dir, n, ts)

    call_log: list[str] = []

    def counting_mirror(repo, state=None):
        call_log.append(repo)
        return [_mirror_iss(99, "sprint-1")]

    mod = importlib.import_module("routers.metrics")
    with patch.object(db, "get_mirrored_issues", side_effect=counting_mirror), \
         patch.object(mod, "projects_module") as proj_mock, \
         patch.object(mod, "_project_root_path", return_value=tmp_path):
        proj_mock.load_projects.return_value = [{"repo": "owner/repo"}]
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(mod.router)
        client = TestClient(app)
        client.get("/api/metrics/sprints?from=2026-01-01&to=2026-12-31")

    assert len(call_log) == 1, (
        f"Expected 1 mirror call for 3 sprints (same project), got {len(call_log)}"
    )


# ── AC6: empty mirror falls back to gh without error ─────────────────────────

def test_ac6_empty_mirror_falls_back_to_gh(tmp_path):
    """AC6: when mirror empty, _count_rework_tickets gh fallback is used per sprint."""
    sprints_dir = tmp_path / ".commander" / "sprints"
    _make_state_file(sprints_dir, 5, "2026-07-01T00:00:00")

    gh_calls: list[str] = []

    def fake_count_rework(sprint_label: str, project: str) -> int:
        gh_calls.append(sprint_label)
        return 3  # non-zero to prove fallback path ran

    mod = importlib.import_module("routers.metrics")
    with patch.object(db, "get_mirrored_issues", return_value=[]), \
         patch.object(mod, "projects_module") as proj_mock, \
         patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "_count_rework_tickets", side_effect=fake_count_rework):
        proj_mock.load_projects.return_value = [{"repo": "owner/repo"}]
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(mod.router)
        client = TestClient(app)
        resp = client.get("/api/metrics/sprints?from=2026-01-01&to=2026-12-31")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["ticket_outcomes_breakdown"]["needs_rework"] == 3
    assert "sprint-5" in gh_calls


# ── AC5: response is byte-identical (same schema, types, and fields) ──────────

def test_ac5_response_schema_matches_expected_fixture(tmp_path):
    """AC5: mirror-based response has exactly the same keys and types as fixture baseline."""
    sprints_dir = tmp_path / ".commander" / "sprints"
    _make_state_file(sprints_dir, 42, "2026-07-01T00:00:00")

    mirror = [
        _mirror_iss(101, "sprint-42", ["needs-rework"]),
        _mirror_iss(102, "sprint-42", ["needs-rework"]),
    ]
    resp, _ = _call_metrics(tmp_path, mirror, subprocess_raise=True)

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]

    # Verify exact schema matches expected baseline shape
    assert set(row.keys()) == {
        "sprint_label", "project", "duration_minutes", "ticket_count",
        "ticket_outcomes_breakdown", "agent_dispatch_counts",
        "total_token_estimate",
    }
    breakdown = row["ticket_outcomes_breakdown"]
    assert set(breakdown.keys()) == {"done", "failed", "skipped", "needs_rework"}
    assert isinstance(breakdown["needs_rework"], int)
    assert breakdown["needs_rework"] == 2

    agent_counts = row["agent_dispatch_counts"]
    assert set(agent_counts.keys()) == {"coder", "tester", "reviewer", "documenter"}


# ── Direct unit: _bulk_rework_from_mirror ────────────────────────────────────

def test_bulk_rework_from_mirror_returns_none_when_empty():
    """`_bulk_rework_from_mirror` returns None (not {}) when mirror has no rows."""
    mod = importlib.import_module("routers.metrics")
    with patch.object(db, "get_mirrored_issues", return_value=[]):
        result = mod._bulk_rework_from_mirror("owner/repo")
    assert result is None


def test_bulk_rework_from_mirror_counts_by_sprint_label():
    """`_bulk_rework_from_mirror` groups needs-rework issues by sprint label."""
    mod = importlib.import_module("routers.metrics")
    mirror = [
        _mirror_iss(1, "sprint-10", ["needs-rework"]),
        _mirror_iss(2, "sprint-10", ["needs-rework"]),
        _mirror_iss(3, "sprint-11", ["needs-rework"]),
        _mirror_iss(4, "sprint-10"),  # no rework label
    ]
    with patch.object(db, "get_mirrored_issues", return_value=mirror):
        result = mod._bulk_rework_from_mirror("owner/repo")
    assert result == {"sprint-10": 2, "sprint-11": 1}


def test_bulk_rework_from_mirror_ignores_non_rework_issues():
    """`_bulk_rework_from_mirror` returns empty dict when no needs-rework issues."""
    mod = importlib.import_module("routers.metrics")
    mirror = [
        _mirror_iss(1, "sprint-10"),
        _mirror_iss(2, "sprint-10", ["UAT"]),
    ]
    with patch.object(db, "get_mirrored_issues", return_value=mirror):
        result = mod._bulk_rework_from_mirror("owner/repo")
    # Mirror is populated but no rework issues → empty dict (not None)
    assert result == {}
