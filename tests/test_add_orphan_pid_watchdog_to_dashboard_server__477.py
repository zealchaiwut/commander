"""Tests for issue #477 — orphan PID watchdog on dashboard server startup.

Acceptance Criteria:
  AC1: On startup, server.py scans all .commander/sprints/*-pid files
  AC2: Liveness check via os.kill(pid, 0)
  AC3: Orphan detection emits structured log event with event, pid, file_path, timestamp
  AC4: Orphaned PID files are deleted AFTER the log event is emitted
  AC5: Background task runs same sweep every 5 minutes
  AC6: GET /api/health includes orphans_removed count (cumulative)
  AC7: Non-orphan PID files left untouched
  AC8: Watchdog errors (malformed PID) logged as warnings, server does not crash
"""

import asyncio
import os
import sys
import time
import json
import inspect
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest
import pytest_asyncio
import httpx

# Ensure the dashboard package is on the path
DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pid_file(tmp_path: Path, name: str, content: str) -> Path:
    """Write a PID file and return its path."""
    pid_file = tmp_path / name
    pid_file.write_text(content, encoding="utf-8")
    return pid_file


def _fake_projects(sprints_dir: Path):
    """Return a fake projects list pointing at tmp_path."""
    return [{"repo": "test/fake-project", "_sprints_dir_override": sprints_dir}]


# ---------------------------------------------------------------------------
# Unit-level tests (no live server)
# ---------------------------------------------------------------------------


class TestAC1StartupSweepIsCalledBeforeYield:
    """AC1: _sweep_orphan_pid_files() is called during lifespan before yield."""

    def test_sweep_called_in_lifespan(self):
        import server
        src = inspect.getsource(server.lifespan)
        # sweep must appear before yield
        sweep_pos = src.find("_sweep_orphan_pid_files()")
        yield_pos = src.find("yield")
        assert sweep_pos != -1, "_sweep_orphan_pid_files not found in lifespan"
        assert yield_pos != -1, "yield not found in lifespan"
        assert sweep_pos < yield_pos, (
            "_sweep_orphan_pid_files() must be called before yield in lifespan"
        )


class TestAC2LivenessViaOsKill:
    """AC2: Liveness checked via os.kill(pid, 0)."""

    def test_os_kill_used_for_liveness(self):
        import server
        src = inspect.getsource(server._sweep_orphan_pid_files)
        assert "os.kill(pid, 0)" in src, "os.kill(pid, 0) liveness check not found"

    def test_orphan_removed_when_process_not_found(self, tmp_path):
        import server

        pid_file = _make_pid_file(tmp_path, "sprint-1-pid", "99999999")

        fake_proj = {"repo": "test/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path.parent), \
             patch("server._slog") as mock_slog:
            # Set up sprints dir directly
            sprints_dir = tmp_path.parent / "sprints"
            sprints_dir.mkdir(parents=True, exist_ok=True)
            stale = _make_pid_file(sprints_dir, "sprint-test-pid", "99999999")

            with patch("server._commander_dir", return_value=tmp_path.parent):
                # os.kill(99999999, 0) will raise ProcessLookupError on any real system
                server._sweep_orphan_pid_files()

        assert not stale.exists(), "Orphan PID file should have been removed"

    def test_live_pid_not_removed(self, tmp_path):
        import server

        my_pid = os.getpid()
        fake_proj = {"repo": "test/repo"}

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        live_pid_file = _make_pid_file(sprints_dir, "sprint-live-pid", str(my_pid))

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch("server._psutil", None):
            server._sweep_orphan_pid_files()

        assert live_pid_file.exists(), "Live PID file must not be removed when psutil absent"


class TestAC3StructuredLogEvent:
    """AC3: Orphan emits structured event with event, pid, file_path, timestamp."""

    def test_event_has_required_fields(self, tmp_path):
        import server

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _make_pid_file(sprints_dir, "sprint-orphan-pid", "99999999")

        fake_proj = {"repo": "test/repo"}
        emitted_events = []

        def capture_event(name, **kwargs):
            emitted_events.append({"name": name, **kwargs})

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path):
            with patch.object(server._slog, "event", side_effect=capture_event):
                server._sweep_orphan_pid_files()

        orphan_events = [e for e in emitted_events if e.get("name") == "orphan_pid_detected"]
        assert orphan_events, "No orphan_pid_detected event emitted"

        evt = orphan_events[0]
        assert evt.get("event") == "orphan_pid_detected", "event field missing or wrong"
        assert "pid" in evt, "pid field missing from event"
        assert "file_path" in evt, "file_path field missing from event"
        # timestamp is auto-added by _slog.event internally — verify the method adds it
        import server as srv
        from services.logging import log as slog_instance
        import datetime
        recorded = []
        real_event = slog_instance.__class__.event
        with patch.object(slog_instance, "event", wraps=real_event.__get__(slog_instance)):
            pass  # just confirm event method exists and auto-adds timestamp
        # Verify by inspecting slog.event source
        src = inspect.getsource(slog_instance.event)
        assert '"timestamp"' in src, "timestamp not auto-added by _slog.event"


class TestAC4DeleteAfterLog:
    """AC4: PID file deleted AFTER log event emitted (not before)."""

    def test_log_emitted_before_file_deleted(self, tmp_path):
        import server

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        pid_file = _make_pid_file(sprints_dir, "sprint-orphan-pid", "99999999")

        fake_proj = {"repo": "test/repo"}
        call_order = []

        original_event = server._slog.event

        def track_event(name, **kwargs):
            if name == "orphan_pid_detected":
                call_order.append("log")
            return original_event(name, **kwargs)

        original_unlink = Path.unlink

        def track_unlink(self, missing_ok=False):
            if self.name == "sprint-orphan-pid":
                call_order.append("delete")
            return original_unlink(self, missing_ok=missing_ok)

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch.object(server._slog, "event", side_effect=track_event), \
             patch.object(Path, "unlink", track_unlink):
            server._sweep_orphan_pid_files()

        assert "log" in call_order, "orphan_pid_detected event was never emitted"
        assert "delete" in call_order, "PID file was never deleted"
        assert call_order.index("log") < call_order.index("delete"), (
            f"AC4 FAIL: PID file deleted BEFORE log event. Order: {call_order}"
        )


class TestAC5BackgroundSweepEvery5Min:
    """AC5: Background task runs sweep every 5 minutes (300s)."""

    def test_periodic_loop_uses_300s_sleep(self):
        import server
        src = inspect.getsource(server._periodic_orphan_sweep_loop)
        assert "asyncio.sleep(300)" in src, (
            "Background sweep must use asyncio.sleep(300) for 5-minute interval"
        )

    def test_periodic_loop_calls_sweep(self):
        import server
        src = inspect.getsource(server._periodic_orphan_sweep_loop)
        assert "_sweep_orphan_pid_files()" in src, (
            "_sweep_orphan_pid_files() must be called in _periodic_orphan_sweep_loop"
        )

    def test_periodic_task_created_in_lifespan(self):
        import server
        src = inspect.getsource(server.lifespan)
        assert "_periodic_orphan_sweep_loop()" in src, (
            "_periodic_orphan_sweep_loop task must be created in lifespan"
        )


class TestAC6HealthEndpointOrphansRemoved:
    """AC6: GET /api/health includes orphans_removed count."""

    @pytest.mark.asyncio
    async def test_health_includes_orphans_removed(self):
        import server
        from httpx import AsyncClient, ASGITransport
        async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as client:
            resp = await client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "orphans_removed" in body, "orphans_removed missing from /api/health response"
        assert isinstance(body["orphans_removed"], int), "orphans_removed must be an integer"

    def test_orphans_removed_increments_on_sweep(self, tmp_path):
        import server

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _make_pid_file(sprints_dir, "sprint-a-pid", "99999999")
        _make_pid_file(sprints_dir, "sprint-b-pid", "99999998")

        fake_proj = {"repo": "test/repo"}
        before = server._orphans_removed_total

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch.object(server._slog, "event"):
            server._sweep_orphan_pid_files()

        assert server._orphans_removed_total >= before + 2, (
            f"orphans_removed_total should have incremented by at least 2; "
            f"was {before}, now {server._orphans_removed_total}"
        )


class TestAC7LivePidFilesUntouched:
    """AC7: Non-orphan PID files left untouched."""

    def test_live_process_pid_file_not_deleted(self, tmp_path):
        import server

        my_pid = os.getpid()
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        live_file = _make_pid_file(sprints_dir, "sprint-running-pid", str(my_pid))

        fake_proj = {"repo": "test/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch("server._psutil", None), \
             patch.object(server._slog, "event") as mock_evt:
            server._sweep_orphan_pid_files()

        assert live_file.exists(), "Live process PID file must not be deleted"
        # No orphan event for our PID
        for c in mock_evt.call_args_list:
            if c[0] and c[0][0] == "orphan_pid_detected":
                assert c[1].get("pid") != my_pid, (
                    f"Unexpected orphan_pid_detected event for live PID {my_pid}"
                )


class TestAC8ErrorHandling:
    """AC8: Watchdog errors logged as warnings, server does not crash."""

    def test_malformed_pid_file_logged_as_warning(self, tmp_path):
        import server

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _make_pid_file(sprints_dir, "sprint-bad-pid", "not-a-number")

        fake_proj = {"repo": "test/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch.object(server._slog, "event"), \
             patch("server.logger") as mock_logger:
            # Must not raise
            server._sweep_orphan_pid_files()

        warning_calls = mock_logger.warning.call_args_list
        assert any("malformed" in str(c).lower() or "startup-sweep" in str(c) for c in warning_calls), (
            "Malformed PID file must emit a logger.warning"
        )

    def test_malformed_pid_file_does_not_crash(self, tmp_path):
        import server

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _make_pid_file(sprints_dir, "sprint-crash-pid", "abc")

        fake_proj = {"repo": "test/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch.object(server._slog, "event"):
            try:
                server._sweep_orphan_pid_files()
            except Exception as exc:
                pytest.fail(f"_sweep_orphan_pid_files raised unexpectedly: {exc}")

    def test_permission_error_does_not_crash(self, tmp_path):
        import server

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _make_pid_file(sprints_dir, "sprint-perm-pid", "12345")

        fake_proj = {"repo": "test/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch("server.os.kill", side_effect=PermissionError("EPERM")), \
             patch.object(server._slog, "event"):
            try:
                server._sweep_orphan_pid_files()
            except Exception as exc:
                pytest.fail(f"PermissionError must not propagate: {exc}")

    def test_permission_error_logged_as_warning(self, tmp_path):
        import server

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        pid_file = _make_pid_file(sprints_dir, "sprint-perm2-pid", "12345")

        fake_proj = {"repo": "test/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_proj]), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._commander_dir", return_value=tmp_path), \
             patch("server.os.kill", side_effect=PermissionError("EPERM")), \
             patch.object(server._slog, "event"), \
             patch("server.logger") as mock_logger:
            server._sweep_orphan_pid_files()

        # AC8 explicitly lists "permission denied" as an error that must be logged as a warning
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("perm" in c.lower() or "permission" in c.lower() or "eperm" in c.lower()
                   for c in warning_calls), (
            "AC8: permission denied (PermissionError) must be logged as a warning"
        )
