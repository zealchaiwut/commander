"""Tests for issue #1279: Extract sprint_manager failure handling to failures.py"""
import subprocess
import sys
from pathlib import Path


def test_failures_py_compiles():
    """AC1: failures.py exists and compiles without syntax errors."""
    failures_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "failures.py"
    assert failures_path.exists(), f"{failures_path} does not exist"

    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(failures_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"failures.py compile failed: {result.stderr}"


def test_failures_py_contains_required_symbols():
    """AC1: failures.py contains exactly the five required symbols."""
    failures_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "failures.py"
    content = failures_path.read_text(encoding="utf-8")

    required = [
        "class FailureCategory:",
        "def record_failure(",
        "def _build_failure_suffix(",
        "def _generate_gate_failure_analysis(",
        "def _publish_gate_failure_analyses(",
    ]
    for symbol in required:
        assert symbol in content, f"Missing symbol in failures.py: {symbol}"


def test_sprint_manager_imports_from_failures():
    """AC2: sprint_manager.py re-imports moved symbols from failures.py."""
    sm_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sm_path.read_text(encoding="utf-8")

    # Check that the import block exists
    assert "from services.sprint_manager.failures import" in content, \
        "sprint_manager.py does not import from failures.py"

    # Check that all five main symbols are imported
    required = [
        "FailureCategory",
        "record_failure",
        "_build_failure_suffix",
        "_generate_gate_failure_analysis",
        "_publish_gate_failure_analyses",
    ]
    for symbol in required:
        assert symbol in content, f"sprint_manager.py does not import {symbol}"


def test_sprint_manager_does_not_redefine_moved_functions():
    """AC3: The five moved functions are not redefined in sprint_manager.py."""
    sm_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    content = sm_path.read_text(encoding="utf-8")

    # These functions should NOT be defined in sprint_manager.py after the move
    disallowed_defs = [
        r"^def record_failure\(",
        r"^def _build_failure_suffix\(",
        r"^class FailureCategory:",
        r"^def _generate_gate_failure_analysis\(",
        r"^def _publish_gate_failure_analyses\(",
    ]

    import re
    for pattern in disallowed_defs:
        matches = re.findall(pattern, content, re.MULTILINE)
        assert not matches, f"Function redefined in sprint_manager.py: {pattern}"


def test_sprint_manager_py_compiles():
    """AC4: sprint_manager.py compiles without syntax errors."""
    sm_path = Path(__file__).parent.parent / "services" / "sprint_manager" / "sprint_manager.py"
    assert sm_path.exists(), f"{sm_path} does not exist"

    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(sm_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"sprint_manager.py compile failed: {result.stderr}"


def test_direct_import_from_failures():
    """AC3 (UAT step 3): Import the moved symbols directly from failures.py."""
    try:
        from services.sprint_manager.failures import (
            record_failure,
            FailureCategory,
            _build_failure_suffix,
            _generate_gate_failure_analysis,
            _publish_gate_failure_analyses,
        )
    except ImportError as e:
        raise AssertionError(f"Failed to import from failures.py: {e}") from e

    # Verify they are callable/accessible
    assert callable(record_failure), "record_failure is not callable"
    assert callable(_build_failure_suffix), "_build_failure_suffix is not callable"
    assert callable(_generate_gate_failure_analysis), "_generate_gate_failure_analysis is not callable"
    assert callable(_publish_gate_failure_analyses), "_publish_gate_failure_analyses is not callable"
    assert hasattr(FailureCategory, "HANG"), "FailureCategory missing HANG attribute"


def test_reexport_from_sprint_manager():
    """AC2 (UAT step 3): Symbols should be importable via re-export from sprint_manager."""
    try:
        from services.sprint_manager.sprint_manager import (
            record_failure,
            FailureCategory,
            _build_failure_suffix,
            _generate_gate_failure_analysis,
            _publish_gate_failure_analyses,
        )
    except ImportError as e:
        raise AssertionError(f"Failed to import re-exports from sprint_manager.py: {e}") from e

    # Verify they are callable/accessible
    assert callable(record_failure), "record_failure is not callable"
    assert callable(_build_failure_suffix), "_build_failure_suffix is not callable"
    assert callable(_generate_gate_failure_analysis), "_generate_gate_failure_analysis is not callable"
    assert callable(_publish_gate_failure_analyses), "_publish_gate_failure_analyses is not callable"
    assert hasattr(FailureCategory, "HANG"), "FailureCategory missing HANG attribute"


def test_failure_category_constants():
    """AC1 (verification): FailureCategory has all expected constants."""
    from services.sprint_manager.failures import FailureCategory

    expected_constants = [
        "HANG",
        "CRASH",
        "GATE_FAIL",
        "TESTER_REJECTED",
        "RETRY_EXHAUSTED",
        "CODER_NO_WORK",
        "MERGE_CONFLICT",
        "LINT_FAIL",
        "PYTEST_FAIL",
        "REBASE_CONFLICT",
    ]

    for const in expected_constants:
        assert hasattr(FailureCategory, const), f"FailureCategory missing {const}"
