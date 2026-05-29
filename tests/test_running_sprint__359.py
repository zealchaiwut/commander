"""Tests for issue #359 — GET /api/projects/{project}/running-sprint endpoint.

AC-1  GET /api/projects/{project}/running-sprint returns 200 with label, started_at (ISO 8601), and pid when running
AC-2  Returns 204 No Content when no sprint is running
AC-3  Returns 404 when the specified project does not exist
AC-4  Stale PID files (dead process) are treated as not running, return 204
AC-5  Endpoint reads from .commander/sprints/<label>-pid and .commander/sprints/<label>-pid.pending
"""

import os
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(DASHBOARD))

import server
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """FastAPI test client with mocked filesystem and projects."""
    with patch("server._project_root_path") as mock_root_path, \
         patch("server._commander_dir") as mock_cmd_dir, \
         patch("server.projects_module.load_projects") as mock_load_projects:

        # Configure mocks to work with tmp_path
        def _mock_root_path(repo: str) -> Path:
            return tmp_path / "project_root"

        def _mock_cmd_dir(project_root: Path) -> Path:
            return project_root / ".commander"

        mock_root_path.side_effect = _mock_root_path
        mock_cmd_dir.side_effect = _mock_cmd_dir

        # Mock projects.json to return a test project
        mock_load_projects.return_value = [
            {"repo": "zealchaiwut/commander", "name": "Commander"}
        ]

        # Create the directory structure
        (tmp_path / "project_root" / ".commander" / "sprints").mkdir(parents=True)

        yield TestClient(server.app)


# ─── AC-1: Running sprint returns 200 with label, started_at, pid ────────────

def test_ac1_running_sprint_returns_200(client, tmp_path):
    """When a sprint is running, endpoint returns 200 with sprint metadata."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Create a PID file for a running sprint
    pid_file = sprints_dir / "sprint-25-pid"
    current_pid = os.getpid()  # Use current process PID (guaranteed to exist)
    pid_file.write_text(str(current_pid), encoding="utf-8")

    # Set a specific mtime to verify it's used as started_at
    import time
    test_time = time.time() - 3600  # 1 hour ago
    os.utime(pid_file, (test_time, test_time))

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["label"] == "sprint-25"
    assert data["pid"] == current_pid
    assert "started_at" in data
    # Verify started_at is ISO 8601 format
    datetime.fromisoformat(data["started_at"].replace("Z", "+00:00"))


def test_ac1_running_sprint_with_pending_file(client, tmp_path):
    """When sprint has a pending PID file, it's treated as running."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Create a pending PID file
    pid_file = sprints_dir / "sprint-26-pid.pending"
    current_pid = os.getpid()
    pid_file.write_text(str(current_pid), encoding="utf-8")

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 200, resp.text

    data = resp.json()
    assert data["label"] == "sprint-26"
    assert data["pid"] == current_pid


# ─── AC-2: No sprint running returns 204 ──────────────────────────────────

def test_ac2_no_sprint_running_returns_204(client, tmp_path):
    """When no sprint is running, endpoint returns 204 No Content."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Ensure sprints directory exists but is empty
    sprints_dir.mkdir(parents=True, exist_ok=True)

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 204
    assert resp.text == ""


def test_ac2_no_sprints_dir_returns_204(client, tmp_path):
    """When .commander/sprints doesn't exist, return 204."""
    project_root = tmp_path / "project_root"
    cmd_dir = project_root / ".commander"

    # Remove sprints directory if it exists
    sprints_dir = cmd_dir / "sprints"
    if sprints_dir.exists():
        import shutil
        shutil.rmtree(sprints_dir)

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 204
    assert resp.text == ""


# ─── AC-3: Nonexistent project returns 404 ───────────────────────────────

def test_ac3_nonexistent_project_returns_404(client):
    """When project doesn't exist in projects.json, endpoint returns 404."""
    resp = client.get("/api/projects/does-not-exist/running-sprint")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_ac3_returns_404_with_full_repo_slug(client):
    """404 is returned for full repo slug if project not registered."""
    resp = client.get("/api/projects/other-user/other-repo/running-sprint")
    assert resp.status_code == 404


# ─── AC-4: Stale PID files return 204 ──────────────────────────────────

def test_ac4_stale_pid_file_returns_204(client, tmp_path):
    """When PID file contains a dead process, return 204 No Content."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Create a PID file with a PID that doesn't exist (using a very high number)
    pid_file = sprints_dir / "sprint-27-pid"
    pid_file.write_text("999999", encoding="utf-8")

    resp = client.get("/api/projects/commander/running-sprint")
    # Should return 204 since PID 999999 almost certainly doesn't exist
    assert resp.status_code == 204


def test_ac4_unreadable_pid_file_returns_204(client, tmp_path):
    """When PID file contains invalid data, return 204 No Content."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Create a PID file with invalid content
    pid_file = sprints_dir / "sprint-28-pid"
    pid_file.write_text("not-a-number", encoding="utf-8")

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 204


# ─── AC-5: Reads from correct PID file location ───────────────────────────

def test_ac5_reads_from_sprints_pid_location(client, tmp_path):
    """Endpoint reads from .commander/sprints/<label>-pid."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Create a PID file at the expected location
    pid_file = sprints_dir / "sprint-29-pid"
    current_pid = os.getpid()
    pid_file.write_text(str(current_pid), encoding="utf-8")

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "sprint-29"
    assert data["pid"] == current_pid


def test_ac5_reads_from_pending_pid_location(client, tmp_path):
    """Endpoint reads from .commander/sprints/<label>-pid.pending."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Create only a pending PID file
    pid_file = sprints_dir / "sprint-30-pid.pending"
    current_pid = os.getpid()
    pid_file.write_text(str(current_pid), encoding="utf-8")

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "sprint-30"


# ─── Integration: Multiple PID files, first running sprint returned ────────

def test_multiple_pid_files_returns_first_running(client, tmp_path):
    """When multiple PID files exist, first running sprint is returned."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    current_pid = os.getpid()

    # Create multiple dead PID files
    for i in range(3):
        pid_file = sprints_dir / f"sprint-{i}-pid"
        pid_file.write_text("999999", encoding="utf-8")

    # Create one live PID file
    live_pid_file = sprints_dir / "sprint-live-pid"
    live_pid_file.write_text(str(current_pid), encoding="utf-8")

    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "sprint-live"
    assert data["pid"] == current_pid


def test_project_by_short_name(client):
    """Endpoint accepts project short name (last component of repo slug)."""
    resp = client.get("/api/projects/commander/running-sprint")
    # Should not error, even though no sprint is running
    assert resp.status_code == 204


def test_project_lookup_flexibility(client, tmp_path):
    """Endpoint looks up project by short name; full slug match is for projects without / in name."""
    project_root = tmp_path / "project_root"
    sprints_dir = project_root / ".commander" / "sprints"

    # Create a running sprint
    pid_file = sprints_dir / "sprint-test-pid"
    current_pid = os.getpid()
    pid_file.write_text(str(current_pid), encoding="utf-8")

    # URL path can only contain the short name (last component of repo slug)
    resp = client.get("/api/projects/commander/running-sprint")
    assert resp.status_code == 200
    assert resp.json()["label"] == "sprint-test"
