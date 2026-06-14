"""
Tests for issue #507: Introduce explicit per-sprint state field in plan.json.

Acceptance criteria covered:
- plan.json is written with state=planning on sprint create
- plan.json transitions to state=running on sprint run
- _all_sprints_running reads plan.json state, not PID files
- A stale PID + state=completed/cancelled does not appear as running
- _is_sprint_running reconciles plan.json=running with dead PID → cancelled
- GET /api/sprints/{label}/state returns plan.json payload
- save_sprint_plan preserves state when updating tickets
- sprint rename also renames plan.json
- _load_sprint_plan handles new dict format
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

from fastapi.testclient import TestClient

from apps.dashboard import server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project_root(tmp_path: Path) -> tuple[Path, Path]:
    """Return (project_root, sprints_dir) with .commander/sprints/ created."""
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    return tmp_path, sprints_dir


# ---------------------------------------------------------------------------
# _read_plan_json / _write_plan_json / _plan_json_set_state
# ---------------------------------------------------------------------------

def test_read_plan_json_missing(tmp_path):
    root, _ = _make_project_root(tmp_path)
    result = server._read_plan_json(root, "sprint-5")
    assert result is None


def test_read_plan_json_new_dict_format(tmp_path):
    root, sprints = _make_project_root(tmp_path)
    data = {"state": "running", "created_at": "2026-01-01", "tickets": [1, 2, 3]}
    (sprints / "sprint-5-plan.json").write_text(json.dumps(data), encoding="utf-8")
    result = server._read_plan_json(root, "sprint-5")
    assert result == data


def test_read_plan_json_old_list_format(tmp_path):
    root, sprints = _make_project_root(tmp_path)
    (sprints / "sprint-5-plan.json").write_text(json.dumps([3, 1, 2]), encoding="utf-8")
    result = server._read_plan_json(root, "sprint-5")
    assert result == {"tickets": [3, 1, 2]}


def test_write_plan_json_atomic(tmp_path):
    root, _ = _make_project_root(tmp_path)
    server._write_plan_json(root, "sprint-5", {"state": "planning", "tickets": []})
    result = server._read_plan_json(root, "sprint-5")
    assert result["state"] == "planning"


def test_plan_json_set_state_creates_file(tmp_path):
    root, _ = _make_project_root(tmp_path)
    server._plan_json_set_state(root, "sprint-5", "planning",
                                created_at="2026-01-01T00:00:00+00:00")
    result = server._read_plan_json(root, "sprint-5")
    assert result["state"] == "planning"
    assert result["created_at"] == "2026-01-01T00:00:00+00:00"


def test_plan_json_set_state_preserves_existing_fields(tmp_path):
    root, _ = _make_project_root(tmp_path)
    server._plan_json_set_state(root, "sprint-5", "planning",
                                created_at="2026-01-01", tickets=[1, 2])
    server._plan_json_set_state(root, "sprint-5", "running",
                                started_at="2026-01-02")
    result = server._read_plan_json(root, "sprint-5")
    assert result["state"] == "running"
    assert result["created_at"] == "2026-01-01"
    assert result["tickets"] == [1, 2]
    assert result["started_at"] == "2026-01-02"


# ---------------------------------------------------------------------------
# _is_sprint_running — plan.json as authoritative source
# ---------------------------------------------------------------------------

def test_is_sprint_running_planning_state(tmp_path):
    root, _ = _make_project_root(tmp_path)
    server._plan_json_set_state(root, "sprint-5", "planning")
    assert server._is_sprint_running(root, "sprint-5") is False


def test_is_sprint_running_completed_state(tmp_path):
    root, _ = _make_project_root(tmp_path)
    server._plan_json_set_state(root, "sprint-5", "completed")
    assert server._is_sprint_running(root, "sprint-5") is False


def test_is_sprint_running_cancelled_state(tmp_path):
    root, _ = _make_project_root(tmp_path)
    server._plan_json_set_state(root, "sprint-5", "cancelled")
    assert server._is_sprint_running(root, "sprint-5") is False


def test_is_sprint_running_stale_pid_but_state_completed(tmp_path):
    """Stale PID file + state=completed → not running (AC: stale PID ignored)."""
    root, sprints = _make_project_root(tmp_path)
    server._plan_json_set_state(root, "sprint-5", "completed")
    # Write a fake stale PID
    (sprints / "sprint-5-pid").write_text("99999", encoding="utf-8")
    assert server._is_sprint_running(root, "sprint-5") is False


def test_is_sprint_running_dead_pid_does_not_mutate_plan_json(tmp_path):
    """plan.json=running but PID is dead → returns False; plan.json must be unchanged.

    Issue #1096: _is_sprint_running is called from GET endpoints and must NOT
    write plan.json.  The startup sweep (_sweep_plan_json_states) reconciles
    stale plan.json files at boot time instead.
    """
    root, sprints = _make_project_root(tmp_path)
    server._plan_json_set_state(root, "sprint-5", "running")
    original_bytes = (sprints / "sprint-5-plan.json").read_bytes()
    # Write a clearly dead PID
    (sprints / "sprint-5-pid").write_text("999999999", encoding="utf-8")
    with patch("server.db.get_sprint", return_value=None):
        result = server._is_sprint_running(root, "sprint-5")
    assert result is False
    after_bytes = (sprints / "sprint-5-plan.json").read_bytes()
    assert after_bytes == original_bytes, (
        "_is_sprint_running must not rewrite plan.json (issue #1096)"
    )


def test_is_sprint_running_no_plan_no_pid_does_not_create_plan(tmp_path):
    """No plan.json, no PID file → returns False; plan.json must NOT be created.

    Issue #1096: _is_sprint_running is called from GET endpoints and must not
    write plan.json.  Legacy plan.json creation moved to the sprint manager.
    """
    root, sprints = _make_project_root(tmp_path)
    plan_path = sprints / "sprint-99-plan.json"
    assert not plan_path.exists()
    with patch("server.db.get_sprint", return_value=None):
        result = server._is_sprint_running(root, "sprint-99")
    assert result is False
    assert not plan_path.exists(), (
        "_is_sprint_running must not create plan.json for legacy sprints (issue #1096)"
    )


# ---------------------------------------------------------------------------
# GET /api/sprints/{label}/state endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with a mocked project root pointing to tmp_path."""
    root, _ = _make_project_root(tmp_path)
    monkeypatch.setattr(server, "_project_root_path", lambda repo: root)
    return TestClient(server.app)


def test_get_sprint_state_returns_plan_json(client, tmp_path):
    root = tmp_path
    server._plan_json_set_state(root, "sprint-5", "planning",
                                created_at="2026-01-01T00:00:00+00:00")
    resp = client.get("/api/sprints/sprint-5/state?project=myrepo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "planning"
    assert "created_at" in data


def test_get_sprint_state_missing_plan_returns_404(client, tmp_path):
    """No plan.json → returns 404 (not lazy-created).

    Issue #1096: GET endpoints must not write plan.json.  The sprint manager
    is the sole writer, so missing plan.json returns 404 instead of creating it.
    """
    resp = client.get("/api/sprints/sprint-42/state?project=myrepo")
    assert resp.status_code == 404, (
        f"GET /state with no plan.json must return 404 (issue #1096); got {resp.status_code}"
    )


def test_get_sprint_state_invalid_label(client):
    resp = client.get("/api/sprints/not-a-sprint/state?project=myrepo")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# save_sprint_plan preserves state fields (issue #507)
# ---------------------------------------------------------------------------

def test_save_sprint_plan_preserves_state(client, tmp_path):
    """POST /api/sprints/{label}/plan preserves existing state field."""
    root = tmp_path
    server._plan_json_set_state(root, "sprint-5", "planning",
                                created_at="2026-01-01T00:00:00+00:00")
    resp = client.post(
        "/api/sprints/sprint-5/plan?project=myrepo",
        content=json.dumps([10, 20, 30]),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    plan = server._read_plan_json(root, "sprint-5")
    assert plan["state"] == "planning"
    assert plan["created_at"] == "2026-01-01T00:00:00+00:00"
    assert plan["tickets"] == [10, 20, 30]
