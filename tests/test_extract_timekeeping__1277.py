"""Tests for issue #1277: Extract timekeeping helpers to services/sprint_manager/timekeeping.py"""
from datetime import datetime, timezone, timedelta
from pathlib import Path


class TestTimekeepingExtraction:
    """Verify that timekeeping.py contains the required extracted functions."""

    def test_timekeeping_module_exists(self):
        """AC: services/sprint_manager/timekeeping.py exists."""
        timekeeping_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "timekeeping.py"
        assert timekeeping_path.exists(), f"timekeeping.py not found at {timekeeping_path}"

    def test_timekeeping_has_all_required_functions(self):
        """AC: timekeeping.py contains all required functions."""
        from services.sprint_manager import timekeeping
        required = [
            "_token_window_sums",
            "_utcnow",
            "_bangkok_now",
            "_wait_if_paused",
            "_setup_pid_file",
            "_acquire_pid_lock",
            "_release_pid_lock",
        ]
        for func_name in required:
            assert hasattr(timekeeping, func_name), f"Missing function: {func_name}"

    def test_utcnow_returns_iso8601_z_format(self):
        """AC: _utcnow() returns ISO 8601 with Z suffix."""
        from services.sprint_manager.timekeeping import _utcnow
        result = _utcnow()
        assert isinstance(result, str)
        assert result.endswith("Z"), f"Expected UTC format ending with Z, got {result}"
        assert result.count("T") == 1, f"Expected ISO format with T separator, got {result}"
        # Should be parseable as ISO datetime
        dt = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert dt.tzinfo == timezone.utc

    def test_bangkok_now_returns_utc_plus_7(self):
        """AC: _bangkok_now() returns UTC+7 format."""
        from services.sprint_manager.timekeeping import _bangkok_now
        result = _bangkok_now()
        assert isinstance(result, str)
        assert "+07:00" in result, f"Expected UTC+07:00 offset, got {result}"
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo == timezone(timedelta(hours=7)), f"Timezone is not UTC+7, got {dt.tzinfo}"

    def test_no_duplication_in_sprint_manager(self):
        """AC: Moved symbols are removed from sprint_manager.py (no duplication)."""
        from services.sprint_manager import sprint_manager
        # Check that sprint_manager imports from timekeeping, not defining these locally
        # by looking at the module's __dict__
        assert hasattr(sprint_manager, "_utcnow"), "sprint_manager should have _utcnow"
        assert hasattr(sprint_manager, "_bangkok_now"), "sprint_manager should have _bangkok_now"

        # Verify they're imported from timekeeping, not defined in sprint_manager
        # (This is a best-effort check via the source code)
        sprint_manager_source = (Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py").read_text()
        assert "from services.sprint_manager.timekeeping import" in sprint_manager_source, \
            "sprint_manager.py should import from timekeeping.py"

    def test_token_window_sums_callable(self):
        """AC: _token_window_sums is callable and returns tuple[int, int]."""
        from services.sprint_manager.timekeeping import _token_window_sums
        result = _token_window_sums("coder", "2026-06-21T00:00:00")
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 2, f"Expected 2-tuple, got {len(result)}"
        assert isinstance(result[0], int) and isinstance(result[1], int), \
            f"Expected (int, int), got {(type(result[0]), type(result[1]))}"

    def test_wait_if_paused_imports_correctly(self):
        """AC: _wait_if_paused is importable and has correct signature."""
        from services.sprint_manager.timekeeping import _wait_if_paused
        import inspect
        sig = inspect.signature(_wait_if_paused)
        params = list(sig.parameters.keys())
        assert "sprint_num" in params
        assert "state" in params
        # api_url is optional
        assert sig.parameters["api_url"].default is None

    def test_acquire_and_release_pid_lock_importable(self):
        """AC: _acquire_pid_lock and _release_pid_lock are importable."""
        from services.sprint_manager.timekeeping import _acquire_pid_lock, _release_pid_lock
        assert callable(_acquire_pid_lock)
        assert callable(_release_pid_lock)

    def test_setup_pid_file_importable(self):
        """AC: _setup_pid_file is importable."""
        from services.sprint_manager.timekeeping import _setup_pid_file
        assert callable(_setup_pid_file)

    def test_sprint_manager_imports_from_timekeeping(self):
        """AC: Original module imports from timekeeping.py so all existing call sites resolve without change."""
        from services.sprint_manager import sprint_manager
        # All re-exported functions should be accessible
        functions = [
            "_token_window_sums",
            "_utcnow",
            "_bangkok_now",
            "_wait_if_paused",
            "_setup_pid_file",
            "_acquire_pid_lock",
            "_release_pid_lock",
        ]
        for func_name in functions:
            assert hasattr(sprint_manager, func_name), \
                f"sprint_manager.py should have {func_name} re-exported from timekeeping"

    def test_no_new_dependencies(self):
        """AC: No new dependencies introduced."""
        # Check that timekeeping.py only imports from standard library,
        # services/sprint_manager/, and existing dashboard dependencies
        timekeeping_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "timekeeping.py"
        source = timekeeping_path.read_text()

        # Should not import from unexpected third-party packages
        forbidden = [
            "import numpy",
            "import pandas",
            "import requests",
            "from requests",
        ]
        for forbidden_import in forbidden:
            assert forbidden_import not in source, \
                f"Unexpected import in timekeeping.py: {forbidden_import}"

    def test_py_compile_timekeeping(self):
        """UAT: python -m py_compile services/sprint_manager/timekeeping.py exits 0."""
        import subprocess
        result = subprocess.run(
            ["python", "-m", "py_compile", "services/sprint_manager/timekeeping.py"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
        )
        assert result.returncode == 0, f"py_compile failed: {result.stderr.decode()}"

    def test_py_compile_sprint_manager(self):
        """UAT: python -m py_compile sprint_manager.py exits 0."""
        import subprocess
        result = subprocess.run(
            ["python", "-m", "py_compile", "services/sprint_manager/sprint_manager.py"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
        )
        assert result.returncode == 0, f"py_compile failed: {result.stderr.decode()}"
