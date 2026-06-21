"""Tests for issue #1279: Extract sprint_manager failure handling to failures.py (runs against UAT)"""
import os
import pytest
import subprocess
import sys
from pathlib import Path


# Resolved from UAT .env at runtime; see tester skill Step 0.
# This is a pure unit-test ticket, not HTTP-based; UAT_BASE_URL is resolved
# but tests are filesystem/import based.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")

REPO_ROOT = Path(__file__).parent.parent
FAILURES_PATH = REPO_ROOT / "services" / "sprint_manager" / "failures.py"
SPRINT_MGR_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

REQUIRED_SYMBOLS = [
    "record_failure",
    "_build_failure_suffix",
    "FailureCategory",
    "_generate_gate_failure_analysis",
    "_publish_gate_failure_analyses",
]


# ── AC 1: failures.py exists and contains exactly the required symbols ────────

def test_extract_sprint_manager_failure_handling__failures_exists():
    """AC 1: services/sprint_manager/failures.py exists."""
    assert FAILURES_PATH.exists(), "failures.py was not created"


def test_extract_sprint_manager_failure_handling__contains_required_symbols():
    """AC 1: failures.py contains all 5 required symbols."""
    import importlib
    mod = importlib.import_module("services.sprint_manager.failures")
    for name in REQUIRED_SYMBOLS:
        assert hasattr(mod, name), f"failures.py missing symbol: {name}"


def test_extract_sprint_manager_failure_handling__symbols_defined_not_reexported():
    """AC 1: the 5 symbols are defined in failures.py source (not just imported)."""
    source = FAILURES_PATH.read_text(encoding="utf-8")
    for name in ["record_failure", "_build_failure_suffix",
                 "_generate_gate_failure_analysis", "_publish_gate_failure_analyses"]:
        assert f"def {name}(" in source, (
            f"failures.py does not define '{name}' — must be defined, not merely imported"
        )
    assert "class FailureCategory" in source, (
        "failures.py does not define 'FailureCategory' class"
    )


# ── AC 2: Original module imports from failures.py ──────────────────────────

def test_extract_sprint_manager_failure_handling__sprint_manager_imports():
    """AC 2: sprint_manager.py imports the moved symbols from failures.py."""
    source = SPRINT_MGR_PATH.read_text(encoding="utf-8")
    assert "from services.sprint_manager.failures import" in source, (
        "sprint_manager.py does not import from failures.py"
    )


def test_extract_sprint_manager_failure_handling__symbols_removed_from_sprint_manager():
    """AC 2: The 5 symbols are not re-defined in sprint_manager.py source."""
    source = SPRINT_MGR_PATH.read_text(encoding="utf-8")
    for name in ["record_failure", "_build_failure_suffix",
                 "_generate_gate_failure_analysis", "_publish_gate_failure_analyses"]:
        assert f"def {name}(" not in source, (
            f"sprint_manager.py still defines '{name}' — must be moved to failures.py"
        )
    assert "class FailureCategory" not in source, (
        "sprint_manager.py still defines 'FailureCategory' — must be moved"
    )


# ── AC 3: No logic changes — function bodies byte-for-byte identical ─────────

def test_extract_sprint_manager_failure_handling__failure_category_constants():
    """AC 3: FailureCategory exposes all expected constants unchanged."""
    from services.sprint_manager.failures import FailureCategory
    assert FailureCategory.HANG == "HANG"
    assert FailureCategory.CRASH == "CRASH"
    assert FailureCategory.GATE_FAIL == "GATE_FAIL"
    assert FailureCategory.TESTER_REJECTED == "TESTER_REJECTED"
    assert FailureCategory.RETRY_EXHAUSTED == "RETRY_EXHAUSTED"
    assert FailureCategory.CODER_NO_WORK == "CODER_NO_WORK"
    assert FailureCategory.MERGE_CONFLICT == "MERGE_CONFLICT"
    assert FailureCategory.LINT_FAIL == "LINT_FAIL"
    assert FailureCategory.PYTEST_FAIL == "PYTEST_FAIL"
    assert FailureCategory.REBASE_CONFLICT == "REBASE_CONFLICT"


def test_extract_sprint_manager_failure_handling__record_failure_signature():
    """AC 3: record_failure function signature unchanged."""
    from services.sprint_manager.failures import record_failure
    import inspect
    sig = inspect.signature(record_failure)
    # Should have: issue_num, failure_class, detail, repo_root
    assert "issue_num" in sig.parameters
    assert "failure_class" in sig.parameters
    assert "detail" in sig.parameters
    assert "repo_root" in sig.parameters


def test_extract_sprint_manager_failure_handling__build_failure_suffix_signature():
    """AC 3: _build_failure_suffix signature unchanged."""
    from services.sprint_manager.failures import _build_failure_suffix
    import inspect
    sig = inspect.signature(_build_failure_suffix)
    assert "issue_num" in sig.parameters
    assert "repo_root" in sig.parameters


# ── AC 4: py_compile failures.py exits 0 ────────────────────────────────────

def test_extract_sprint_manager_failure_handling__failures_compiles():
    """AC 4: python -m py_compile services/sprint_manager/failures.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(FAILURES_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed for failures.py:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── AC 5: py_compile sprint_manager.py exits 0 ───────────────────────────────

def test_extract_sprint_manager_failure_handling__sprint_manager_compiles():
    """AC 5: python -m py_compile services/sprint_manager/sprint_manager.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SPRINT_MGR_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed for sprint_manager.py:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── AC 6: Direct import of all 5 symbols succeeds ──────────────────────────

def test_extract_sprint_manager_failure_handling__direct_imports():
    """AC 6: Import all 5 symbols directly from failures module."""
    from services.sprint_manager.failures import (  # noqa: F401
        record_failure,
        FailureCategory,
        _build_failure_suffix,
        _generate_gate_failure_analysis,
        _publish_gate_failure_analyses,
    )
