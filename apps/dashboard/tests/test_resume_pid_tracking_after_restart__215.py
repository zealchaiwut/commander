"""Tests for issue #215 — Resume PID tracking after server restart.

All tests are static (no live server required).

Acceptance Criteria:
  AC-1: On startup, check for existing PID file/process before spawning a new process
  AC-2: Running PID found → attach and resume status tracking without restarting
  AC-3: Stale PID → start fresh (existing _sweep_orphan_pid_files covers this)
  AC-4: Status tracking (uptime, health, metrics) continues from re-attachment point
  AC-5: PID is persisted to well-known location so it survives restarts (existing PID files)
  AC-6: Logs indicate whether restart resulted in re-attachment or fresh start
  AC-7: Concurrent restart attempts safe (atomic writes, no duplicate tracking)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── server.py import ──────────────────────────────────────────────────────────
_DASHBOARD_DIR = Path(__file__).parent.parent
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

import server


# ── shared helpers ────────────────────────────────────────────────────────────

def _make_project(tmp_path, repo="org/repo"):
    """Create a minimal project directory with .commander/sprints/ layout."""
    project_root = tmp_path / repo.replace("/", "_")
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    return project_root, sprints_dir


def _write_status_file(sprints_dir: Path, sprint_label: str, payload: dict) -> Path:
    """Write a sprint-status JSON file and return its path."""
    path = sprints_dir / f"{sprint_label}-status.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _sample_status_payload(sprint_label: str, project: str = "org/repo") -> dict:
    return {
        "project": project,
        "sprint_label": sprint_label,
        "sprint_number": 9,
        "issues": [
            {"number": 42, "status": "done"},
            {"number": 43, "status": "in_progress"},
        ],
        "start_timestamp": "2026-05-28T10:00:00Z",
        "total_tokens_in": 1000,
        "total_tokens_out": 500,
        "wall_clock_secs": 300.0,
        "token_budget": 100000,
        "paused": False,
    }


# ═════════════════════════════════════════════════════════════════════════════
# AC-1 & AC-2: Startup restore attaches to running sprint
# ═════════════════════════════════════════════════════════════════════════════

class TestRestoreRunningSprintOnStartup:

    def test_running_sprint_status_loaded_into_memory(self, tmp_path):
        """AC-2: Status for a running sprint is loaded into _sprint_statuses on startup."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        payload = _sample_status_payload("sprint-9", project="org/repo")
        _write_status_file(sprints_dir, "sprint-9", payload)

        fake_project = {"repo": "org/repo"}

        # Simulate that sprint-9 is running (process alive).
        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", return_value=True):
            # Clear any existing state and run restore.
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        key = ("org/repo", "sprint-9")
        assert key in server._sprint_statuses, (
            "_sprint_statuses must contain the restored status"
        )
        restored = server._sprint_statuses[key]
        assert restored["start_timestamp"] == payload["start_timestamp"]
        assert restored["wall_clock_secs"] == payload["wall_clock_secs"]

    def test_running_sprint_issues_preserved(self, tmp_path):
        """AC-4: Issues list from persisted status is available after re-attachment."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        payload = _sample_status_payload("sprint-9", project="org/repo")
        _write_status_file(sprints_dir, "sprint-9", payload)

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", return_value=True):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        key = ("org/repo", "sprint-9")
        issues = server._sprint_statuses[key]["issues"]
        assert len(issues) == 2
        assert issues[0]["number"] == 42
        assert issues[0]["status"] == "done"


# ═════════════════════════════════════════════════════════════════════════════
# AC-3: Stale/dead sprint processes are NOT restored
# ═════════════════════════════════════════════════════════════════════════════

class TestSkipDeadSprintOnStartup:

    def test_dead_sprint_not_loaded(self, tmp_path):
        """AC-3: Status for a dead sprint process is NOT loaded into _sprint_statuses."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        payload = _sample_status_payload("sprint-9", project="org/repo")
        _write_status_file(sprints_dir, "sprint-9", payload)

        fake_project = {"repo": "org/repo"}

        # Simulate that sprint-9 is NOT running (stale PID).
        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", return_value=False):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        key = ("org/repo", "sprint-9")
        assert key not in server._sprint_statuses, (
            "Dead sprint status must NOT be restored into _sprint_statuses"
        )

    def test_dead_sprint_logged(self, tmp_path, capsys):
        """AC-6: Log clearly indicates that a dead sprint was skipped (not re-attached)."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        payload = _sample_status_payload("sprint-9", project="org/repo")
        _write_status_file(sprints_dir, "sprint-9", payload)

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", return_value=False):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        out = capsys.readouterr().out
        assert "[startup-restore]" in out
        assert "skipped" in out
        assert "sprint-9" in out


# ═════════════════════════════════════════════════════════════════════════════
# AC-6: Logging — re-attached vs fresh start
# ═════════════════════════════════════════════════════════════════════════════

class TestStartupLogs:

    def test_reattachment_logged(self, tmp_path, capsys):
        """AC-6: Log line emitted when successfully re-attached to a running sprint."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        payload = _sample_status_payload("sprint-9", project="org/repo")
        _write_status_file(sprints_dir, "sprint-9", payload)

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", return_value=True):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        out = capsys.readouterr().out
        assert "[startup-restore]" in out
        assert "re-attached" in out
        assert "sprint-9" in out
        assert "org/repo" in out

    def test_completion_summary_logged(self, tmp_path, capsys):
        """AC-6: Completion summary (N attached, N skipped) logged at end of restore."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        # One running sprint and one dead sprint.
        payload_running = _sample_status_payload("sprint-9", project="org/repo")
        payload_dead    = _sample_status_payload("sprint-8", project="org/repo")
        _write_status_file(sprints_dir, "sprint-9", payload_running)
        _write_status_file(sprints_dir, "sprint-8", payload_dead)

        fake_project = {"repo": "org/repo"}

        def _is_running(project_root_arg, label_arg):
            return label_arg == "sprint-9"

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", side_effect=_is_running):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        out = capsys.readouterr().out
        assert "1 sprint(s) re-attached" in out
        assert "1 skipped" in out

    def test_no_status_files_logs_zero(self, tmp_path, capsys):
        """AC-6: When no status files exist, summary shows 0 attached, 0 skipped."""
        project_root, _sprints_dir = _make_project(tmp_path, "org/repo")
        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", return_value=False):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        out = capsys.readouterr().out
        assert "[startup-restore] completed" in out
        assert "0 sprint(s) re-attached" in out


# ═════════════════════════════════════════════════════════════════════════════
# AC-5: Status persisted to well-known location on POST
# ═════════════════════════════════════════════════════════════════════════════

class TestStatusPersistenceOnPost:

    def test_set_sprint_status_writes_file(self, tmp_path):
        """AC-5: POST /api/sprint-status persists status to disk."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        expected_file = sprints_dir / "sprint-9-status.json"

        payload = server.SprintStatusPayload(
            project="org/repo",
            sprint_label="sprint-9",
            sprint_number=9,
            issues=[],
            start_timestamp="2026-05-28T10:00:00Z",
            wall_clock_secs=0.0,
        )

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server.set_sprint_status(payload)

        assert expected_file.exists(), (
            "sprint-status JSON file must be written to .commander/sprints/"
        )

    def test_set_sprint_status_file_content(self, tmp_path):
        """AC-5: Persisted file contains correct sprint label and start timestamp."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")

        payload = server.SprintStatusPayload(
            project="org/repo",
            sprint_label="sprint-9",
            sprint_number=9,
            issues=[],
            start_timestamp="2026-05-28T10:00:00Z",
            wall_clock_secs=42.5,
        )

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server.set_sprint_status(payload)

        status_file = sprints_dir / "sprint-9-status.json"
        data = json.loads(status_file.read_text(encoding="utf-8"))
        assert data["sprint_label"] == "sprint-9"
        assert data["start_timestamp"] == "2026-05-28T10:00:00Z"
        assert data["wall_clock_secs"] == 42.5

    def test_set_sprint_status_no_project_no_file(self, tmp_path):
        """AC-5: When project is empty, no file is written (cannot determine path)."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")

        payload = server.SprintStatusPayload(
            project="",
            sprint_label="sprint-9",
        )

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server.set_sprint_status(payload)

        # No file should be written anywhere under tmp_path
        status_files = list(tmp_path.rglob("*-status.json"))
        assert len(status_files) == 0, (
            "No status file should be written when project is empty"
        )

    def test_set_sprint_status_updates_memory(self, tmp_path):
        """AC-2/4: POST /api/sprint-status always updates in-memory _sprint_statuses."""
        project_root, _sprints_dir = _make_project(tmp_path, "org/repo")

        payload = server.SprintStatusPayload(
            project="org/repo",
            sprint_label="sprint-9",
            sprint_number=9,
            wall_clock_secs=99.9,
        )

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server.set_sprint_status(payload)

        key = ("org/repo", "sprint-9")
        assert key in server._sprint_statuses
        assert server._sprint_statuses[key]["wall_clock_secs"] == 99.9


# ═════════════════════════════════════════════════════════════════════════════
# AC-7: Concurrent safety — atomic writes
# ═════════════════════════════════════════════════════════════════════════════

class TestAtomicWrites:

    def test_status_file_written_atomically(self, tmp_path):
        """AC-7: No .tmp file left behind after successful write."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")

        payload = server.SprintStatusPayload(
            project="org/repo",
            sprint_label="sprint-9",
            sprint_number=9,
        )

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server.set_sprint_status(payload)

        tmp_files = list(sprints_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, (
            "No .tmp files must remain after a successful atomic write"
        )

    def test_multiple_posts_overwrite_file(self, tmp_path):
        """AC-7: Repeated POSTs overwrite the status file; no stale data."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")

        for i in range(3):
            payload = server.SprintStatusPayload(
                project="org/repo",
                sprint_label="sprint-9",
                sprint_number=9,
                wall_clock_secs=float(i * 10),
            )
            with patch("server._project_root_path", return_value=project_root), \
                 patch("server._commander_dir", return_value=project_root / ".commander"):
                server.set_sprint_status(payload)

        status_file = sprints_dir / "sprint-9-status.json"
        assert status_file.exists()
        data = json.loads(status_file.read_text(encoding="utf-8"))
        assert data["wall_clock_secs"] == 20.0, (
            "Last write (wall_clock_secs=20.0) must be what the file contains"
        )


# ═════════════════════════════════════════════════════════════════════════════
# AC-2: _sprint_status_file_path helper
# ═════════════════════════════════════════════════════════════════════════════

class TestSprintStatusFilePath:

    def test_path_in_commander_sprints_dir(self, tmp_path):
        """AC-5: Status file path is inside .commander/sprints/."""
        project_root = tmp_path / "commander"

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            path = server._sprint_status_file_path("org/repo", "sprint-9")

        assert path is not None
        assert path.name == "sprint-9-status.json"
        assert ".commander" in str(path)
        assert "sprints" in str(path)

    def test_empty_project_returns_none(self):
        """AC-5: Empty project string returns None (cannot determine path)."""
        result = server._sprint_status_file_path("", "sprint-9")
        assert result is None

    def test_different_labels_different_paths(self, tmp_path):
        """AC-5: Different sprint labels produce different file paths."""
        project_root = tmp_path / "commander"

        with patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            path5 = server._sprint_status_file_path("org/repo", "sprint-5")
            path6 = server._sprint_status_file_path("org/repo", "sprint-6")

        assert path5 != path6
        assert path5.name == "sprint-5-status.json"
        assert path6.name == "sprint-6-status.json"


# ═════════════════════════════════════════════════════════════════════════════
# Restore robustness — malformed / missing files
# ═════════════════════════════════════════════════════════════════════════════

class TestRestoreRobustness:

    def test_corrupt_status_file_skipped(self, tmp_path, capsys):
        """Restore skips a corrupt (non-JSON) status file without crashing."""
        project_root, sprints_dir = _make_project(tmp_path, "org/repo")
        bad_file = sprints_dir / "sprint-9-status.json"
        bad_file.write_text("NOT VALID JSON }{", encoding="utf-8")

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._is_sprint_running", return_value=True):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()

        # Should not crash and the bad key must not be in memory.
        key = ("org/repo", "sprint-9")
        assert key not in server._sprint_statuses

    def test_no_sprints_dir_is_safe(self, tmp_path, capsys):
        """Restore handles projects with no .commander/sprints/ directory gracefully."""
        project_root = tmp_path / "commander"
        project_root.mkdir()
        # No .commander/sprints/ created.
        (project_root / ".commander").mkdir()

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()  # must not raise

        out = capsys.readouterr().out
        assert "[startup-restore] completed" in out

    def test_load_projects_failure_is_safe(self, tmp_path, capsys):
        """Restore logs and exits cleanly when load_projects raises."""
        with patch("server.projects_module.load_projects",
                   side_effect=RuntimeError("db unavailable")):
            server._sprint_statuses.clear()
            server._restore_sprint_statuses_on_startup()  # must not raise

        out = capsys.readouterr().out
        assert "[startup-restore]" in out
