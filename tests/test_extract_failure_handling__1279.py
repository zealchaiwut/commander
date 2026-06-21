"""Tests for issue #1279: Extract sprint_manager failure handling to failures.py (runs against UAT)"""
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
MAIN_REPO = REPO_ROOT


def test_extract_failure_handling__failures_py_compiles():
    """AC: `services/sprint_manager/failures.py` exists and compiles without syntax errors"""
    failures_path = MAIN_REPO / "services" / "sprint_manager" / "failures.py"
    assert failures_path.exists(), f"failures.py not found at {failures_path}"

    result = subprocess.run(
        ["python", "-m", "py_compile", str(failures_path)],
        cwd=str(MAIN_REPO),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"py_compile failed: {result.stderr}"


def test_extract_failure_handling__sprint_manager_compiles():
    """AC: sprint_manager.py (original module) compiles without syntax errors"""
    sprint_mgr_path = MAIN_REPO / "services" / "sprint_manager" / "sprint_manager.py"
    assert sprint_mgr_path.exists(), f"sprint_manager.py not found at {sprint_mgr_path}"

    result = subprocess.run(
        ["python", "-m", "py_compile", str(sprint_mgr_path)],
        cwd=str(MAIN_REPO),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"py_compile failed: {result.stderr}"


def test_extract_failure_handling__import_moved_symbols():
    """AC: Moved symbols can be imported directly from failures.py"""
    try:
        from services.sprint_manager.failures import (
            record_failure,
            FailureCategory,
            _build_failure_suffix,
            _generate_gate_failure_analysis,
            _publish_gate_failure_analyses,
        )
        assert callable(record_failure), "record_failure is not callable"
        assert callable(_build_failure_suffix), "_build_failure_suffix is not callable"
        assert hasattr(FailureCategory, "HANG"), "FailureCategory missing HANG constant"
        assert callable(_generate_gate_failure_analysis), "_generate_gate_failure_analysis is not callable"
        assert callable(_publish_gate_failure_analyses), "_publish_gate_failure_analyses is not callable"
    except ImportError as e:
        raise AssertionError(f"Failed to import moved symbols: {e}") from e


def test_extract_failure_handling__sprint_manager_re_exports():
    """AC: sprint_manager imports and re-exports the moved symbols (no broken references)"""
    try:
        from services.sprint_manager.sprint_manager import (
            record_failure,
            FailureCategory,
            _build_failure_suffix,
            _generate_gate_failure_analysis,
            _publish_gate_failure_analyses,
        )
        assert callable(record_failure), "record_failure not re-exported from sprint_manager"
        assert callable(_build_failure_suffix), "_build_failure_suffix not re-exported from sprint_manager"
        assert hasattr(FailureCategory, "HANG"), "FailureCategory not re-exported from sprint_manager"
        assert callable(_generate_gate_failure_analysis), "_generate_gate_failure_analysis not re-exported"
        assert callable(_publish_gate_failure_analyses), "_publish_gate_failure_analyses not re-exported"
    except ImportError as e:
        raise AssertionError(f"Failed to import re-exported symbols from sprint_manager: {e}") from e


def test_extract_failure_handling__failurecategory_constants():
    """AC: FailureCategory has all expected constants post-move"""
    from services.sprint_manager.failures import FailureCategory

    expected_attrs = [
        "HANG", "CRASH", "GATE_FAIL", "TESTER_REJECTED", "RETRY_EXHAUSTED",
        "CODER_NO_WORK", "MERGE_CONFLICT", "LINT_FAIL", "PYTEST_FAIL", "REBASE_CONFLICT"
    ]
    for attr in expected_attrs:
        assert hasattr(FailureCategory, attr), f"FailureCategory missing {attr}"
        value = getattr(FailureCategory, attr)
        assert value == attr, f"FailureCategory.{attr} has unexpected value: {value}"
