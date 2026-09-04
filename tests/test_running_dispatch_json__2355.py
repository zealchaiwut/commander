"""Behavioral AC tests for Running view ← dispatch JSON (issue #2355).

No live GitHub: writes fake dispatch-*.json under a temp project root and
asserts GET /api/running surfaces active runs only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


def _write_dispatch(project_root: Path, run: dict) -> Path:
    runtime = project_root / ".commander" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / f"dispatch-{run['run_id']}.json"
    path.write_text(json.dumps(run), encoding="utf-8")
    return path


def test_running_surfaces_active_dispatch_without_pid(tmp_path):
    from routers.running_service import build_running_snapshot

    project = "owner/proj-x"
    _write_dispatch(tmp_path, {
        "run_id": "abc123",
        "sprint_label": "sprint-1030",
        "tickets": [10, 20],
        "repo": project,
        "status": "running",
        "current_issue": 10,
        "current_step": "coder",
        "failed_issue": None,
        "outcomes": [],
        "started_at": "2026-09-02T10:00:00+00:00",
        "finished_at": "",
        "remaining": [10, 20],
    })

    with patch("routers.running_service._server") as mock_srv_fn:
        srv = MagicMock()
        srv._any_sprint_running.return_value = None
        srv._project_root_path.return_value = tmp_path
        srv._commander_dir.side_effect = lambda root: Path(root) / ".commander"
        mock_srv_fn.return_value = srv

        snap = build_running_snapshot(project)

    assert snap is not None
    assert snap["source"] == "dispatch"
    assert snap["sprint_label"] == "sprint-1030"
    assert snap["run_id"] == "abc123"
    assert snap["dispatch"]["current_issue"] == 10
    assert snap["dispatch"]["current_step"] == "coder"
    assert snap["dispatch"]["poll_url"] == "/api/sprints/dispatch/abc123"
    assert snap["current_ticket"]["number"] == 10
    assert [i["number"] for i in snap["issues"]] == [10, 20]


def test_done_dispatch_does_not_appear_as_active(tmp_path):
    from routers.running_service import build_running_snapshot

    project = "owner/proj-x"
    _write_dispatch(tmp_path, {
        "run_id": "done1",
        "sprint_label": "sprint-1030",
        "tickets": [1],
        "repo": project,
        "status": "done",
        "current_issue": None,
        "current_step": None,
        "outcomes": [],
        "started_at": "2026-09-02T10:00:00+00:00",
        "finished_at": "2026-09-02T11:00:00+00:00",
        "remaining": [],
    })

    with patch("routers.running_service._server") as mock_srv_fn:
        srv = MagicMock()
        srv._any_sprint_running.return_value = None
        srv._project_root_path.return_value = tmp_path
        srv._commander_dir.side_effect = lambda root: Path(root) / ".commander"
        mock_srv_fn.return_value = srv

        assert build_running_snapshot(project) is None


def test_list_dispatch_runs_filters_status_and_label(tmp_path):
    from services.sprint_manager.dispatch_runner import list_dispatch_runs

    _write_dispatch(tmp_path, {
        "run_id": "a", "sprint_label": "sprint-1030", "repo": "o/r",
        "status": "running", "tickets": [1],
    })
    _write_dispatch(tmp_path, {
        "run_id": "b", "sprint_label": "sprint-1029", "repo": "o/r",
        "status": "running", "tickets": [2],
    })
    _write_dispatch(tmp_path, {
        "run_id": "c", "sprint_label": "sprint-1030", "repo": "o/r",
        "status": "failed", "tickets": [3],
    })

    active = list_dispatch_runs(tmp_path, sprint_label="sprint-1030", repo="o/r")
    assert [r["run_id"] for r in active] == ["a"]


def test_running_endpoint_200_for_dispatch_json(tmp_path):
    """HTTP path: fake dispatch → GET /api/running returns 200."""
    from fastapi.testclient import TestClient

    project = "zealchaiwut/commander"
    _write_dispatch(tmp_path, {
        "run_id": "http1",
        "sprint_label": "sprint-1030",
        "tickets": [2355],
        "repo": project,
        "status": "queued",
        "current_issue": None,
        "current_step": None,
        "outcomes": [],
        "started_at": "",
        "finished_at": "",
        "remaining": [2355],
    })

    import server as srv

    with (
        patch("routers.running_service._server") as mock_fn,
        patch.object(srv, "_any_sprint_running", return_value=None),
    ):
        # build_running_snapshot uses routers.running_service._server
        mock = MagicMock()
        mock._any_sprint_running.return_value = None
        mock._project_root_path.return_value = tmp_path
        mock._commander_dir.side_effect = lambda root: Path(root) / ".commander"
        mock_fn.return_value = mock

        from routers.running_service import build_running_snapshot
        snap = build_running_snapshot(project)
        assert snap is not None
        assert snap["run_id"] == "http1"
        assert snap["dispatch"]["status"] == "queued"
