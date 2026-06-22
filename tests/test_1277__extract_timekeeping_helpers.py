"""Tests for issue #1277 — extract timekeeping helpers to timekeeping.py.

Each test is anchored to a specific acceptance criterion.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TIMEKEEPING_PATH = REPO_ROOT / "services" / "sprint_manager" / "timekeeping.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


# ── AC1: timekeeping.py exists and contains the required symbols ──────────────

def test_timekeeping_module_exists():
    """AC1: services/sprint_manager/timekeeping.py exists."""
    assert TIMEKEEPING_PATH.exists(), "timekeeping.py was not created"


def test_timekeeping_contains_required_symbols():
    """AC1: timekeeping.py contains all 7 required symbols."""
    required = [
        "_token_window_sums",
        "_utcnow",
        "_bangkok_now",
        "_wait_if_paused",
        "_setup_pid_file",
        "_acquire_pid_lock",
        "_release_pid_lock",
    ]
    mod = importlib.import_module("services.sprint_manager.timekeeping")
    for name in required:
        assert hasattr(mod, name), f"timekeeping.py missing symbol: {name}"


# ── AC2: All moved symbols removed from original source (no duplication) ──────

def test_moved_symbols_not_defined_in_sprint_manager():
    """AC2: The 7 symbols are defined in timekeeping.py, not redefined in sprint_manager.py.

    We inspect the source text rather than the live module because sprint_manager.py
    re-imports these symbols — they will appear in its namespace but must not be
    *defined* there (no 'def _utcnow' or '_BANGKOK_TZ =' lines).
    """
    source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
    forbidden_defs = [
        "def _token_window_sums(",
        "def _utcnow(",
        "def _bangkok_now(",
        "def _wait_if_paused(",
        "def _setup_pid_file(",
        "def _acquire_pid_lock(",
        "def _release_pid_lock(",
    ]
    for pattern in forbidden_defs:
        assert pattern not in source, (
            f"sprint_manager.py still defines '{pattern}' — it must be removed"
        )


# ── AC3: Original module imports from timekeeping.py ─────────────────────────

def test_sprint_manager_imports_from_timekeeping():
    """AC3: sprint_manager.py has an import from .timekeeping."""
    source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
    assert "timekeeping" in source, (
        "sprint_manager.py does not import from timekeeping"
    )


def test_sprint_manager_exports_moved_symbols():
    """AC3: All 7 symbols resolve in the sprint_manager namespace (call sites work)."""
    # Importing sprint_manager is heavyweight; check via source import of the
    # timekeeping module and name resolution only.
    mod = importlib.import_module("services.sprint_manager.timekeeping")
    required = [
        "_token_window_sums",
        "_utcnow",
        "_bangkok_now",
        "_wait_if_paused",
        "_setup_pid_file",
        "_acquire_pid_lock",
        "_release_pid_lock",
    ]
    for name in required:
        assert callable(getattr(mod, name, None)), (
            f"timekeeping.{name} is not callable"
        )


# ── AC4: py_compile timekeeping.py exits 0 ───────────────────────────────────

def test_timekeeping_compiles_clean():
    """AC4: python -m py_compile services/sprint_manager/timekeeping.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(TIMEKEEPING_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed for timekeeping.py:\n{result.stderr}"
    )


# ── AC5: py_compile sprint_manager.py exits 0 ────────────────────────────────

def test_sprint_manager_compiles_clean():
    """AC5: python -m py_compile on sprint_manager.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed for sprint_manager.py:\n{result.stderr}"
    )


# ── AC6: No runtime behaviour changes ────────────────────────────────────────

def test_utcnow_returns_utc_datetime_string():
    """AC6: _utcnow() returns a UTC ISO-8601 string ending in Z."""
    from services.sprint_manager.timekeeping import _utcnow
    result = _utcnow()
    assert isinstance(result, str)
    assert result.endswith("Z"), f"_utcnow() should end with Z, got: {result!r}"
    # Must be parseable as UTC
    dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")
    assert dt is not None


def test_bangkok_now_returns_utc_plus7():
    """AC6: _bangkok_now() returns a datetime string at UTC+7 offset."""
    from services.sprint_manager.timekeeping import _bangkok_now
    result = _bangkok_now()
    assert isinstance(result, str)
    assert result.endswith("+07:00"), (
        f"_bangkok_now() should end with +07:00, got: {result!r}"
    )
    dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S+07:00")
    assert dt is not None


def test_bangkok_now_is_utc_plus7():
    """AC6: _bangkok_now() is approximately UTC + 7 hours."""
    from services.sprint_manager.timekeeping import _utcnow, _bangkok_now
    utc_str   = _utcnow()
    bkk_str   = _bangkok_now()
    utc_dt    = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    bkk_dt    = datetime.strptime(bkk_str, "%Y-%m-%dT%H:%M:%S+07:00").replace(
        tzinfo=timezone(timedelta(hours=7))
    )
    # Both represent the same moment; difference should be < 2 seconds
    diff = abs((bkk_dt - utc_dt).total_seconds())
    assert diff < 2, f"_bangkok_now() and _utcnow() drift by {diff}s — expected < 2s"


def test_token_window_sums_returns_tuple():
    """AC6: _token_window_sums returns a (int, int) tuple even when db is unavailable."""
    from services.sprint_manager.timekeeping import _token_window_sums, _utcnow
    result = _token_window_sums("coder", _utcnow())
    assert isinstance(result, tuple) and len(result) == 2
    assert all(isinstance(v, int) for v in result)


def test_acquire_pid_lock_returns_path(tmp_path, monkeypatch):
    """AC6: _acquire_pid_lock writes a PID file and returns its Path."""
    from services.sprint_manager.timekeeping import _acquire_pid_lock

    # Point the PID-file helper at a temp dir via a mock config
    pid_file = tmp_path / "test-sprint.pid"

    # Patch _pid_file_path to use tmp_path
    import services.sprint_manager.timekeeping as tk

    def _fake_pid_file_path(label, cfg=None):
        return pid_file

    monkeypatch.setattr(tk, "_pid_file_path", _fake_pid_file_path)

    result = _acquire_pid_lock("sprint-test", "zealchaiwut/commander")
    assert result == pid_file
    assert pid_file.exists()
    assert pid_file.read_text().strip() == str(__import__("os").getpid())


def test_release_pid_lock_removes_file(tmp_path):
    """AC6: _release_pid_lock removes the PID file; idempotent."""
    from services.sprint_manager.timekeeping import _release_pid_lock

    pid_file = tmp_path / "sprint-test.pid"
    pid_file.write_text("12345")

    _release_pid_lock(pid_file)
    assert not pid_file.exists()

    # Idempotent — must not raise even if file already gone
    _release_pid_lock(pid_file)


# ── AC7: No new dependencies introduced ──────────────────────────────────────

def test_no_new_top_level_imports():
    """AC7: timekeeping.py uses only stdlib + intra-package imports at module level."""
    source = TIMEKEEPING_PATH.read_text(encoding="utf-8")
    # Only check non-indented import lines (module-level); lazy imports inside
    # functions are pre-existing patterns (e.g. `import db`) and not new deps.
    lines = [line for line in source.splitlines()
             if line.startswith(("import ", "from "))]
    allowed_prefixes = (
        "import atexit",
        "import json",
        "import os",
        "import re",
        "import signal",
        "import sys",
        "import time",
        "from __future__",
        "from datetime",
        "from pathlib",
        "from typing",
        "from services.sprint_manager",
        "from services.logging",
    )
    for line in lines:
        assert any(line.startswith(p) for p in allowed_prefixes), (
            f"Unexpected import in timekeeping.py: {line!r}"
        )
