"""Tests for issue #156: Surface sprint dispatch failures and sweep orphan PID files.

A. Surface dispatch failures to the UI:
   - Subprocess polled for ~2 s; fast exit → HTTPException(502) with log tail (last 30
     lines, detail truncated to ≤1500 chars) and PID file removed.
   - Subprocess still running after window → 202 dict response as before.

B. Server-startup orphan PID sweep:
   - Dead PID (ProcessLookupError) → file deleted, correct log line emitted.
   - Alive PID with unrelated argv → file deleted, "PID reuse" log line emitted.
   - Alive sprint_manager.py process for correct label → file untouched.
   - Sweep duration logged (under 100 ms for normal inputs).
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# ── server.py import ──────────────────────────────────────────────────────────
_DASHBOARD_DIR = Path(__file__).parent.parent
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

import server

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

_REAL_SM_PATH = (
    _DASHBOARD_DIR.parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
)


def _make_proc_mock(exit_code=None, pid=12345):
    """Return a Popen-like mock.

    If exit_code is None, proc.poll() always returns None (still running).
    Otherwise the first call to proc.poll() returns exit_code (fast crash).
    """
    proc = MagicMock()
    proc.pid = pid
    if exit_code is None:
        proc.poll.return_value = None
    else:
        proc.poll.return_value = exit_code
    return proc


def _setup_tmp_project(tmp_path):
    """Create a minimal project tree under tmp_path and return (project_root, commander_dir)."""
    project_root = tmp_path / "project"
    coder_clone = project_root / "coder"
    coder_clone.mkdir(parents=True)
    commander_dir = project_root / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)
    (commander_dir / "logs").mkdir(parents=True)
    return project_root, commander_dir, coder_clone


def _call_run_sprint_managed(tmp_path, proc_mock, sprint_label="sprint-9"):
    """Invoke run_sprint_managed with all infrastructure mocked. Returns the result."""
    project_root, commander_dir, coder_clone = _setup_tmp_project(tmp_path)

    with patch("server.subprocess.Popen", return_value=proc_mock), \
         patch("server._project_root_path", return_value=project_root), \
         patch("server._coder_clone_path", return_value=coder_clone), \
         patch("server._commander_dir", return_value=commander_dir), \
         patch("server._is_sprint_running", return_value=False), \
         patch("server.SPRINT_MANAGER_PATH", _REAL_SM_PATH), \
         patch("server._sprint_goal_path", return_value=tmp_path / "no-goal"):
        body = server.SprintMgmtRunBody(
            project="zealchaiwut/commander", sprint_label=sprint_label
        )
        return server.run_sprint_managed(body), commander_dir


# ═════════════════════════════════════════════════════════════════════════════
# AC-A: Surface dispatch failures
# ═════════════════════════════════════════════════════════════════════════════

class TestDispatchFailureSurfacing:

    def test_fast_crash_raises_502(self, tmp_path):
        """AC-A2: When subprocess exits immediately, HTTPException 502 is raised."""
        from fastapi import HTTPException
        proc = _make_proc_mock(exit_code=1)

        with pytest.raises(HTTPException) as exc_info:
            _call_run_sprint_managed(tmp_path, proc)

        assert exc_info.value.status_code == 502

    def test_fast_crash_detail_contains_exit_code(self, tmp_path):
        """AC-A2: 502 detail includes the subprocess exit code."""
        from fastapi import HTTPException
        proc = _make_proc_mock(exit_code=2)

        with pytest.raises(HTTPException) as exc_info:
            _call_run_sprint_managed(tmp_path, proc)

        assert "2" in exc_info.value.detail

    def test_fast_crash_detail_contains_log_tail(self, tmp_path):
        """AC-A2: 502 detail includes text from the subprocess log output."""
        from fastapi import HTTPException

        log_output = "Stub crash: missing dependency 'foo'"

        # Popen factory that writes stub output to the log file handle (stdout=log_fh).
        def fake_popen(cmd, *, stdout=None, stderr=None, **kwargs):
            if stdout is not None:
                stdout.write(log_output)
                stdout.flush()
            proc = MagicMock()
            proc.pid = 12345
            proc.poll.return_value = 1
            return proc

        _project_root, commander_dir, _coder = _setup_tmp_project(tmp_path)

        with patch("server.subprocess.Popen", side_effect=fake_popen), \
             patch("server._project_root_path", return_value=_project_root), \
             patch("server._coder_clone_path", return_value=_coder), \
             patch("server._commander_dir", return_value=commander_dir), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server.SPRINT_MANAGER_PATH", _REAL_SM_PATH), \
             patch("server._sprint_goal_path", return_value=tmp_path / "no-goal"):
            body = server.SprintMgmtRunBody(
                project="zealchaiwut/commander", sprint_label="sprint-9"
            )
            with pytest.raises(HTTPException) as exc_info:
                server.run_sprint_managed(body)

        assert log_output in exc_info.value.detail

    def test_fast_crash_detail_last_30_lines(self, tmp_path):
        """AC-A2: detail includes only the last 30 lines of the log."""
        from fastapi import HTTPException

        lines = [f"line{i}" for i in range(50)]
        log_content = "\n".join(lines)

        def fake_popen(cmd, *, stdout=None, stderr=None, **kwargs):
            if stdout is not None:
                stdout.write(log_content)
                stdout.flush()
            proc = MagicMock()
            proc.pid = 12346
            proc.poll.return_value = 1
            return proc

        _project_root, commander_dir, _coder = _setup_tmp_project(tmp_path)

        with patch("server.subprocess.Popen", side_effect=fake_popen), \
             patch("server._project_root_path", return_value=_project_root), \
             patch("server._coder_clone_path", return_value=_coder), \
             patch("server._commander_dir", return_value=commander_dir), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server.SPRINT_MANAGER_PATH", _REAL_SM_PATH), \
             patch("server._sprint_goal_path", return_value=tmp_path / "no-goal"):
            body = server.SprintMgmtRunBody(
                project="zealchaiwut/commander", sprint_label="sprint-9"
            )
            with pytest.raises(HTTPException) as exc_info:
                server.run_sprint_managed(body)

        detail = exc_info.value.detail
        # Last line of the file must be in detail.
        assert "line49" in detail
        # Line 0 through 19 (first 20 lines) must NOT appear (only last 30 kept).
        assert "line0" not in detail

    def test_fast_crash_detail_truncated_to_1500_chars(self, tmp_path):
        """AC-A2: detail is at most 1500 characters long."""
        from fastapi import HTTPException

        long_log = "x" * 5000
        proc = _make_proc_mock(exit_code=1)

        _project_root, commander_dir, _coder = _setup_tmp_project(tmp_path)
        log_path = commander_dir / "logs" / "sprint-9.log"
        log_path.write_text(long_log, encoding="utf-8")

        with patch("server.subprocess.Popen", return_value=proc), \
             patch("server._project_root_path", return_value=_project_root), \
             patch("server._coder_clone_path", return_value=_coder), \
             patch("server._commander_dir", return_value=commander_dir), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server.SPRINT_MANAGER_PATH", _REAL_SM_PATH), \
             patch("server._sprint_goal_path", return_value=tmp_path / "no-goal"):
            body = server.SprintMgmtRunBody(
                project="zealchaiwut/commander", sprint_label="sprint-9"
            )
            with pytest.raises(HTTPException) as exc_info:
                server.run_sprint_managed(body)

        assert len(exc_info.value.detail) <= 1500

    def test_fast_crash_pid_file_deleted(self, tmp_path):
        """AC-A4: PID file is removed before the 502 response is returned."""
        from fastapi import HTTPException

        proc = _make_proc_mock(exit_code=1, pid=54321)
        _project_root, commander_dir, _coder = _setup_tmp_project(tmp_path)
        pid_path = commander_dir / "sprints" / "sprint-9-pid"

        with patch("server.subprocess.Popen", return_value=proc), \
             patch("server._project_root_path", return_value=_project_root), \
             patch("server._coder_clone_path", return_value=_coder), \
             patch("server._commander_dir", return_value=commander_dir), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server.SPRINT_MANAGER_PATH", _REAL_SM_PATH), \
             patch("server._sprint_goal_path", return_value=tmp_path / "no-goal"):
            body = server.SprintMgmtRunBody(
                project="zealchaiwut/commander", sprint_label="sprint-9"
            )
            with pytest.raises(HTTPException):
                server.run_sprint_managed(body)

        assert not pid_path.exists(), (
            "PID file must be deleted when subprocess exits immediately"
        )

    def test_success_path_returns_202_dict(self, tmp_path):
        """AC-A3: When subprocess keeps running, return 202-style dict (ok=True)."""
        proc = _make_proc_mock(exit_code=None, pid=99001)
        result, _ = _call_run_sprint_managed(tmp_path, proc)

        assert result["ok"] is True
        assert result["sprint_label"] == "sprint-9"
        assert result["pid"] == 99001

    def test_success_path_pid_file_kept(self, tmp_path):
        """AC-A3: PID file remains when the subprocess is still running."""
        proc = _make_proc_mock(exit_code=None, pid=88123)
        _project_root, commander_dir, _coder = _setup_tmp_project(tmp_path)
        pid_path = commander_dir / "sprints" / "sprint-9-pid"

        with patch("server.subprocess.Popen", return_value=proc), \
             patch("server._project_root_path", return_value=_project_root), \
             patch("server._coder_clone_path", return_value=_coder), \
             patch("server._commander_dir", return_value=commander_dir), \
             patch("server._is_sprint_running", return_value=False), \
             patch("server.SPRINT_MANAGER_PATH", _REAL_SM_PATH), \
             patch("server._sprint_goal_path", return_value=tmp_path / "no-goal"):
            body = server.SprintMgmtRunBody(
                project="zealchaiwut/commander", sprint_label="sprint-9"
            )
            server.run_sprint_managed(body)

        assert pid_path.exists(), "PID file must exist when subprocess is still running"
        assert pid_path.read_text(encoding="utf-8").strip() == "88123"


# ═════════════════════════════════════════════════════════════════════════════
# AC-B: Server-startup orphan PID sweep
# ═════════════════════════════════════════════════════════════════════════════

def _make_project(tmp_path, repo="org/repo"):
    """Create a minimal project directory with a sprints/ sub-directory."""
    project_root = tmp_path / repo.replace("/", "_")
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    return project_root, sprints_dir


class TestOrphanPIDSweep:

    def test_dead_pid_file_is_removed(self, tmp_path, capsys):
        """AC-B2: PID file with a non-existent PID is deleted on startup sweep."""
        project_root, sprints_dir = _make_project(tmp_path)
        pid_file = sprints_dir / "sprint-9-pid"
        pid_file.write_text("9999999", encoding="utf-8")

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server._sweep_orphan_pid_files()

        assert not pid_file.exists(), (
            "PID file for a non-existent PID must be deleted by the startup sweep"
        )

    def test_dead_pid_log_line(self, tmp_path, capsys):
        """AC-B2: Correct log line emitted when dead-PID orphan is cleaned."""
        project_root, sprints_dir = _make_project(tmp_path)
        pid_file = sprints_dir / "sprint-9-pid"
        pid_file.write_text("9999999", encoding="utf-8")

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server._sweep_orphan_pid_files()

        out = capsys.readouterr().out
        assert "[startup-sweep]" in out
        assert "9999999 not running" in out

    def test_pid_reuse_file_is_removed(self, tmp_path, capsys):
        """AC-B3: PID file whose process doesn't have sprint_manager.py in argv is deleted."""
        project_root, sprints_dir = _make_project(tmp_path)
        live_pid = os.getpid()  # current process — definitely alive
        pid_file = sprints_dir / "sprint-9-pid"
        pid_file.write_text(str(live_pid), encoding="utf-8")

        fake_project = {"repo": "org/repo"}
        # Mock psutil to return an argv that does NOT contain sprint_manager.py
        mock_psutil = MagicMock()
        mock_proc_info = MagicMock()
        mock_proc_info.cmdline.return_value = ["/usr/bin/python3", "unrelated_script.py"]
        mock_psutil.Process.return_value = mock_proc_info

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._psutil", mock_psutil):
            server._sweep_orphan_pid_files()

        assert not pid_file.exists(), (
            "PID file for an unrelated process (PID reuse) must be deleted"
        )

    def test_pid_reuse_log_line(self, tmp_path, capsys):
        """AC-B3: 'reused by unrelated process' log line emitted for PID-reuse orphan."""
        project_root, sprints_dir = _make_project(tmp_path)
        live_pid = os.getpid()
        pid_file = sprints_dir / "sprint-9-pid"
        pid_file.write_text(str(live_pid), encoding="utf-8")

        fake_project = {"repo": "org/repo"}
        mock_psutil = MagicMock()
        mock_proc_info = MagicMock()
        mock_proc_info.cmdline.return_value = ["/usr/bin/python3", "unrelated_script.py"]
        mock_psutil.Process.return_value = mock_proc_info

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._psutil", mock_psutil):
            server._sweep_orphan_pid_files()

        out = capsys.readouterr().out
        assert "reused by unrelated process" in out

    def test_live_sprint_manager_pid_file_untouched(self, tmp_path, capsys):
        """AC-B4: PID file for a live sprint_manager.py process is left alone."""
        project_root, sprints_dir = _make_project(tmp_path)
        live_pid = os.getpid()
        pid_file = sprints_dir / "sprint-9-pid"
        pid_file.write_text(str(live_pid), encoding="utf-8")

        fake_project = {"repo": "org/repo"}
        mock_psutil = MagicMock()
        mock_proc_info = MagicMock()
        # argv: sprint_manager.py followed by the matching sprint label
        mock_proc_info.cmdline.return_value = [
            "/usr/bin/python3",
            "/path/to/sprint_manager.py",
            "sprint-9",
            "--skip-gates",
        ]
        mock_psutil.Process.return_value = mock_proc_info

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"), \
             patch("server._psutil", mock_psutil):
            server._sweep_orphan_pid_files()

        assert pid_file.exists(), (
            "PID file for a live, correctly labeled sprint_manager.py must NOT be deleted"
        )
        assert pid_file.read_text(encoding="utf-8").strip() == str(live_pid)

    def test_sweep_duration_logged(self, tmp_path, capsys):
        """AC-B5: Sweep duration is logged as '[startup-sweep] completed in X.Yms'."""
        fake_project = {"repo": "org/repo"}
        _project_root, sprints_dir = _make_project(tmp_path)

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=_project_root), \
             patch("server._commander_dir", return_value=_project_root / ".commander"):
            server._sweep_orphan_pid_files()

        out = capsys.readouterr().out
        assert "[startup-sweep] completed in" in out
        assert "ms" in out

    def test_sweep_completes_under_100ms(self, tmp_path):
        """AC-B5: Sweep with up to 10 projects × 10 PID files each finishes in <100ms."""
        projects = []
        project_paths = {}

        for i in range(10):
            repo = f"org/project{i}"
            p_root = tmp_path / f"project{i}"
            sprints_dir = p_root / ".commander" / "sprints"
            sprints_dir.mkdir(parents=True)
            for j in range(10):
                pid_file = sprints_dir / f"sprint-{j}-pid"
                pid_file.write_text("9999999", encoding="utf-8")  # dead PIDs
            projects.append({"repo": repo})
            project_paths[repo] = p_root

        def _mock_project_root(repo):
            return project_paths[repo]

        def _mock_commander_dir(root):
            return root / ".commander"

        start = time.monotonic()
        with patch("server.projects_module.load_projects", return_value=projects), \
             patch("server._project_root_path", side_effect=_mock_project_root), \
             patch("server._commander_dir", side_effect=_mock_commander_dir):
            server._sweep_orphan_pid_files()
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 100, (
            f"Sweep took {elapsed_ms:.1f}ms — must complete under 100ms"
        )

    def test_sweep_ignores_load_projects_error(self, tmp_path, capsys):
        """AC-B1: If load_projects() raises, sweep logs and returns without crashing."""
        with patch("server.projects_module.load_projects", side_effect=RuntimeError("db down")):
            server._sweep_orphan_pid_files()  # must not raise

        out = capsys.readouterr().out
        assert "[startup-sweep]" in out

    def test_corrupt_pid_file_cleaned(self, tmp_path, capsys):
        """Corrupt/unreadable PID file (non-integer content) is removed gracefully."""
        project_root, sprints_dir = _make_project(tmp_path)
        pid_file = sprints_dir / "sprint-9-pid"
        pid_file.write_text("not-a-number", encoding="utf-8")

        fake_project = {"repo": "org/repo"}

        with patch("server.projects_module.load_projects", return_value=[fake_project]), \
             patch("server._project_root_path", return_value=project_root), \
             patch("server._commander_dir", return_value=project_root / ".commander"):
            server._sweep_orphan_pid_files()

        assert not pid_file.exists(), (
            "Corrupt PID file (non-integer) must be deleted by the startup sweep"
        )
