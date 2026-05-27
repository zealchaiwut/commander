"""Tests for issue #122 — Sprint manager: per-project PID files,
allow concurrent sprints across projects.

All tests are static (no live server required).

Acceptance Criteria:
  AC-1: No global concurrency guard in sprint_manager.py or finish_feature.py
  AC-2: PID file path scoped to (project, sprint_label)
  AC-3: On startup, only checks PID file at own (project, label) path
  AC-4: Alive PID → refuse with clear error message
  AC-5: Stale PID → log warning, clean up, proceed
  AC-6: Clean shutdown (atexit + SIGTERM) removes PID file
  AC-7: Crash leaves stale file; next run cleans it up (covered by AC-5)
  AC-8: Different (project, label) keys can run simultaneously
  AC-9: Subprocesses inherit COMMANDER_PROJECT=<repo>
  AC-10: Log file paths are per-project via cfg.logs_dir
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import os
import signal
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── path setup ────────────────────────────────────────────────────────────────

SPRINT_SCRIPT = (
    Path(__file__).parent.parent.parent.parent
    / "services" / "sprint_manager" / "sprint_manager.py"
)
FINISH_FEATURE_SCRIPT = (
    Path(__file__).parent.parent.parent.parent
    / "scripts" / "finish_feature.py"
)


def _import_sprint_manager():
    """Import sprint_manager.py with stubs for external deps."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo          = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    gc_stub.create_issue  = lambda **kw: (999, "https://github.com/zealchaiwut/commander/issues/999")
    sys.modules.setdefault("github_client", gc_stub)

    ptp_stub = types.ModuleType("post_test_report")
    ptp_stub.parse_failures   = lambda *a, **kw: []
    ptp_stub.build_failure_block = lambda *a, **kw: ""
    ptp_stub.write_sidecar    = lambda *a, **kw: None
    ptp_stub.sidecar_path     = lambda *a, **kw: Path("/tmp/sidecar")
    sys.modules.setdefault("post_test_report", ptp_stub)

    mod_name = "sprint_manager_122_import"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── AC-1: No global lock ──────────────────────────────────────────────────────

class TestNoGlobalLock:
    """AC-1: sprint_manager.py and finish_feature.py have no global concurrency guard."""

    def test_sprint_manager_no_global_lock(self):
        content = SPRINT_SCRIPT.read_text()
        # The old pattern was a single global .lock file or threading.Lock at module level.
        # Check there is no global lock file path (e.g. sprint_manager.lock or manager.lock).
        assert "sprint_manager.lock" not in content
        assert "manager.lock" not in content

    def test_sprint_manager_pid_functions_exist(self):
        content = SPRINT_SCRIPT.read_text()
        assert "_pid_file_path" in content
        assert "_acquire_pid_lock" in content
        assert "_release_pid_lock" in content

    def test_finish_feature_no_lock_references(self):
        content = FINISH_FEATURE_SCRIPT.read_text()
        assert "sprint_manager.lock" not in content
        assert "_acquire_pid_lock" not in content


# ── AC-2: PID file path ────────────────────────────────────────────────────────

class TestPidFilePath:
    """AC-2: PID file is scoped per (project, sprint_label)."""

    def test_pid_path_uses_sprints_dir_and_label(self, tmp_path):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig(
            sprints_dir=tmp_path / "sprints",
        )
        path = sm._pid_file_path("sprint-5", cfg=cfg)
        assert path.name == "sprint-5-pid"
        assert path.parent == tmp_path / "sprints"

    def test_pid_path_different_labels_are_different(self, tmp_path):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig(sprints_dir=tmp_path / "sprints")
        p1 = sm._pid_file_path("sprint-5", cfg=cfg)
        p2 = sm._pid_file_path("sprint-6", cfg=cfg)
        assert p1 != p2

    def test_pid_path_creates_sprints_dir(self, tmp_path):
        sm = _import_sprint_manager()
        sprints_dir = tmp_path / ".commander" / "sprints"
        assert not sprints_dir.exists()
        cfg = sm.SprintConfig(sprints_dir=sprints_dir)
        sm._pid_file_path("sprint-5", cfg=cfg)
        assert sprints_dir.exists()

    def test_pid_path_no_cfg_uses_repo_root(self):
        sm = _import_sprint_manager()
        path = sm._pid_file_path("sprint-test-ac2")
        assert path.name == "sprint-test-ac2-pid"
        assert ".commander" in str(path)
        assert "sprints" in str(path)


# ── AC-3 & AC-4: Alive process → refuse ──────────────────────────────────────

class TestAcquireAliveProcess:
    """AC-3 & AC-4: If PID file exists and process is alive, exit with clear message."""

    def test_refuses_when_process_alive(self, tmp_path):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig(sprints_dir=tmp_path / "sprints")
        pid_path = sm._pid_file_path("sprint-5", cfg=cfg)
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        # Use the parent process PID — alive but NOT our own PID.
        # (Using os.getpid() would trigger the server-dispatch self-recognition
        # introduced in issue #155 and would not raise SystemExit.)
        other_live_pid = os.getppid()
        pid_path.write_text(str(other_live_pid))

        with pytest.raises(SystemExit) as exc_info:
            sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander", cfg=cfg)

        # Exit message must include sprint label, project, and PID
        msg = str(exc_info.value)
        assert "sprint-5" in msg
        assert "zealchaiwut/commander" in msg
        assert str(other_live_pid) in msg

    def test_error_message_format(self, tmp_path):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig(sprints_dir=tmp_path / "sprints")
        pid_path = sm._pid_file_path("sprint-5", cfg=cfg)
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        # Use the parent process PID — alive but NOT our own PID.
        other_live_pid = os.getppid()
        pid_path.write_text(str(other_live_pid))

        with pytest.raises(SystemExit) as exc_info:
            sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander", cfg=cfg)

        # Exact expected format: "Sprint <label> is already running on <project> (PID <N>)"
        msg = str(exc_info.value)
        assert "is already running on" in msg
        assert "(PID" in msg


# ── AC-5: Stale PID → warn, clean up, proceed ────────────────────────────────

class TestStalePidCleanup:
    """AC-5 & AC-7: If PID file exists but process is dead, log warning and proceed."""

    def test_stale_pid_is_cleaned_up(self, tmp_path):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig(sprints_dir=tmp_path / "sprints")
        pid_path = sm._pid_file_path("sprint-5", cfg=cfg)
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        # Write a PID that cannot exist (very high number)
        pid_path.write_text("99999999")

        captured = io.StringIO()
        with patch("sys.stderr", captured):
            result_path = sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander", cfg=cfg)

        # Should succeed and return the PID path
        assert result_path == pid_path
        # PID file should now contain current process's PID
        assert pid_path.read_text().strip() == str(os.getpid())

    def test_stale_pid_logs_warning(self, tmp_path):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig(sprints_dir=tmp_path / "sprints")
        pid_path = sm._pid_file_path("sprint-5", cfg=cfg)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("99999999")

        captured = io.StringIO()
        with patch("sys.stderr", captured):
            sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander", cfg=cfg)

        output = captured.getvalue()
        assert "Stale lock" in output or "stale" in output.lower()

    def test_no_pid_file_acquires_normally(self, tmp_path):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig(sprints_dir=tmp_path / "sprints")
        pid_path = sm._pid_file_path("sprint-5", cfg=cfg)
        pid_path.parent.mkdir(parents=True, exist_ok=True)

        result = sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander", cfg=cfg)

        assert result == pid_path
        assert pid_path.exists()
        assert pid_path.read_text().strip() == str(os.getpid())


# ── AC-6: Clean shutdown removes PID file ─────────────────────────────────────

class TestCleanShutdown:
    """AC-6: PID file is removed on clean shutdown (atexit + SIGTERM)."""

    def test_release_pid_lock_removes_file(self, tmp_path):
        sm = _import_sprint_manager()
        pid_path = tmp_path / "sprint-5-pid"
        pid_path.write_text(str(os.getpid()))

        sm._release_pid_lock(pid_path)

        assert not pid_path.exists()

    def test_release_pid_lock_idempotent(self, tmp_path):
        sm = _import_sprint_manager()
        pid_path = tmp_path / "sprint-5-pid"
        # File does not exist — should not raise
        sm._release_pid_lock(pid_path)

    def test_atexit_registered_in_main(self):
        """AC-6: Main registers atexit and SIGTERM handler."""
        content = SPRINT_SCRIPT.read_text()
        assert "atexit.register" in content
        assert "SIGTERM" in content
        assert "_cleanup_pid" in content or "_release_pid_lock" in content

    def test_sigterm_handler_registered(self):
        content = SPRINT_SCRIPT.read_text()
        assert "signal.signal(signal.SIGTERM" in content


# ── AC-8: Different (project, label) keys don't interfere ────────────────────

class TestConcurrentSprints:
    """AC-8: Two different (project, label) pairs can run simultaneously."""

    def test_different_labels_independent(self, tmp_path):
        sm = _import_sprint_manager()
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()

        cfg1 = sm.SprintConfig(sprints_dir=sprints_dir)
        cfg2 = sm.SprintConfig(sprints_dir=sprints_dir)

        # Acquire lock for sprint-5
        path1 = sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander", cfg=cfg1)

        # Acquiring sprint-6 on same project should succeed (different label)
        path2 = sm._acquire_pid_lock("sprint-6", "zealchaiwut/commander", cfg=cfg2)

        assert path1 != path2
        assert path1.exists()
        assert path2.exists()

        sm._release_pid_lock(path1)
        sm._release_pid_lock(path2)

    def test_different_projects_independent(self, tmp_path):
        sm = _import_sprint_manager()
        sprints_dir_a = tmp_path / "commander" / "sprints"
        sprints_dir_b = tmp_path / "perf-coach" / "sprints"
        sprints_dir_a.mkdir(parents=True)
        sprints_dir_b.mkdir(parents=True)

        cfg_a = sm.SprintConfig(sprints_dir=sprints_dir_a)
        cfg_b = sm.SprintConfig(sprints_dir=sprints_dir_b)

        # Both sprints with same label on different projects should coexist
        path_a = sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander",  cfg=cfg_a)
        path_b = sm._acquire_pid_lock("sprint-5", "zealchaiwut/perf-coach", cfg=cfg_b)

        assert path_a != path_b
        assert path_a.exists()
        assert path_b.exists()

        sm._release_pid_lock(path_a)
        sm._release_pid_lock(path_b)

    def test_same_project_same_label_blocked(self, tmp_path):
        sm = _import_sprint_manager()
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        cfg = sm.SprintConfig(sprints_dir=sprints_dir)

        # Write the parent PID — a live process that is NOT our own PID.
        # This simulates a different process already holding the lock.
        # (Using our own PID would trigger the server-dispatch self-recognition
        # path introduced in issue #155 and the call would succeed instead of
        # raising SystemExit.)
        pid_path = sm._pid_file_path("sprint-5", cfg=cfg)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        other_live_pid = os.getppid()
        pid_path.write_text(str(other_live_pid))

        # Acquire on same (project, label) should fail — another live process holds it.
        with pytest.raises(SystemExit):
            sm._acquire_pid_lock("sprint-5", "zealchaiwut/commander", cfg=cfg)

        # Clean up
        sm._release_pid_lock(pid_path)


# ── AC-9: COMMANDER_PROJECT propagated to subprocesses ───────────────────────

class TestCommanderProjectEnv:
    """AC-9: Subprocesses inherit COMMANDER_PROJECT=<repo>."""

    def test_commander_project_set_in_coder_env(self):
        content = SPRINT_SCRIPT.read_text()
        assert "COMMANDER_PROJECT" in content

    def test_commander_project_set_to_eff_repo(self):
        """The coder subprocess env sets COMMANDER_PROJECT when eff_repo is available."""
        content = SPRINT_SCRIPT.read_text()
        # Both coder and tester blocks should set COMMANDER_PROJECT
        count = content.count('sub_env["COMMANDER_PROJECT"] = eff_repo')
        assert count >= 2, (
            f"Expected COMMANDER_PROJECT set in both coder and tester blocks, "
            f"found {count} occurrences"
        )

    def test_commander_project_comment_references_issue_122(self):
        content = SPRINT_SCRIPT.read_text()
        assert "122" in content and "COMMANDER_PROJECT" in content


# ── AC-10: Log file paths are per-project ─────────────────────────────────────

class TestPerProjectLogs:
    """AC-10: Log files are scoped per-project via cfg.logs_dir."""

    def test_sprint_config_has_logs_dir(self):
        sm = _import_sprint_manager()
        cfg = sm.SprintConfig()
        assert hasattr(cfg, "logs_dir")
        assert cfg.logs_dir is not None

    def test_load_config_sets_logs_dir(self, tmp_path):
        sm = _import_sprint_manager()
        coder_dir  = tmp_path / "coder"
        tester_dir = tmp_path / "tester"
        logs_dir   = tmp_path / "my-logs"
        coder_dir.mkdir()
        tester_dir.mkdir()
        logs_dir.mkdir()

        yaml_content = f"""
repo_name: zealchaiwut/commander
worktrees:
  coder: {coder_dir}
  tester: {tester_dir}
paths:
  logs_dir: {logs_dir}
"""
        yaml_path = tmp_path / "sprint.yaml"
        yaml_path.write_text(yaml_content)

        cfg = sm.load_config(yaml_path)
        assert cfg.logs_dir == logs_dir

    def test_different_projects_have_separate_logs_dirs(self, tmp_path):
        sm = _import_sprint_manager()
        logs_a = tmp_path / "commander" / "logs"
        logs_b = tmp_path / "perf-coach" / "logs"
        logs_a.mkdir(parents=True)
        logs_b.mkdir(parents=True)

        cfg_a = sm.SprintConfig(logs_dir=logs_a)
        cfg_b = sm.SprintConfig(logs_dir=logs_b)

        assert cfg_a.logs_dir != cfg_b.logs_dir
        assert "commander" in str(cfg_a.logs_dir)
        assert "perf-coach" in str(cfg_b.logs_dir)
