"""Tests for issue #1396: GitHub fetch failures in mis-sizing rebuild.

Uses FastAPI TestClient — no live server required.
One test per acceptance criterion (HTTP-layer view).
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

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


def _make_project_tree(
    tmp_path: Path, issue_num: int = 42, estimated_size: str = "M"
) -> Path:
    commander = tmp_path / ".commander"
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"
    sprints_dir.mkdir(parents=True)
    estimates_dir.mkdir(parents=True)
    state = {
        "issues": [
            {
                "number": issue_num,
                "status": "done",
                "coder_started_at": None,
                "tester_finished_at": None,
                "status_changed_at": None,
            }
        ]
    }
    (sprints_dir / "sprint-1-state.json").write_text(json.dumps(state))
    (estimates_dir / f"issue-{issue_num}.json").write_text(
        json.dumps({"size": estimated_size})
    )
    return tmp_path


def _make_test_client():
    """Minimal FastAPI app containing only the mis_sizing router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mod = importlib.import_module("routers.mis_sizing")
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), mod


def test_rebuild_labels_fetch_failure__labels_fetched_field_in_response(
    tmp_path,
):
    """AC3: POST /api/mis-sizing/rebuild always includes a 'labels_fetched' bool."""
    _make_project_tree(tmp_path)
    client, mod = _make_test_client()

    proc = MagicMock(returncode=1, stderr="auth error", stdout="")
    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc
    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    with patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "github_client", github_client_mock), \
         patch.object(mod, "subprocess", subprocess_mock):
        r = client.post("/api/mis-sizing/rebuild?project=test")

    assert r.status_code == 200
    data = r.json()
    assert "labels_fetched" in data
    assert isinstance(data["labels_fetched"], bool)


def test_rebuild_labels_fetch_failure__labels_fetched_true_on_success(
    tmp_path,
):
    """AC5: When labels are fetched successfully, 'labels_fetched' is True."""
    issue_num = 42
    _make_project_tree(tmp_path, issue_num=issue_num)
    client, mod = _make_test_client()

    gh_response = json.dumps([
        {"number": issue_num, "labels": [{"name": "size-M"}, {"name": "backend"}]},
    ])
    proc = MagicMock(returncode=0, stdout=gh_response, stderr="")
    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc
    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    with patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "github_client", github_client_mock), \
         patch.object(mod, "subprocess", subprocess_mock):
        r = client.post("/api/mis-sizing/rebuild?project=test")

    assert r.status_code == 200
    data = r.json()
    assert "message" in data
    assert isinstance(data["labels_fetched"], bool)
    assert data["labels_fetched"] is True


def test_rebuild_labels_fetch_failure__http_200_with_rebuild_message_on_any_outcome(
    tmp_path,
):
    """AC4: HTTP 200 with 'History rebuilt:' message regardless of label fetch result."""
    _make_project_tree(tmp_path)
    client, mod = _make_test_client()

    proc = MagicMock(returncode=2, stderr="rate limit exceeded", stdout="")
    subprocess_mock = MagicMock()
    subprocess_mock.run.return_value = proc
    github_client_mock = MagicMock()
    github_client_mock.get_repo_for_operation.return_value = "owner/repo"

    with patch.object(mod, "_project_root_path", return_value=tmp_path), \
         patch.object(mod, "github_client", github_client_mock), \
         patch.object(mod, "subprocess", subprocess_mock):
        r = client.post("/api/mis-sizing/rebuild?project=test")

    assert r.status_code == 200
    data = r.json()
    assert "message" in data
    assert "labels_fetched" in data
    assert isinstance(data["labels_fetched"], bool)
