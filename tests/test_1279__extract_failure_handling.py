"""Tests for issue #1279 — extract sprint_manager failure handling to failures.py.

Each test is anchored to a specific acceptance criterion.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT        = Path(__file__).parent.parent
FAILURES_PATH    = REPO_ROOT / "services" / "sprint_manager" / "failures.py"
SPRINT_MGR_PATH  = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

REQUIRED_SYMBOLS = [
    "record_failure",
    "_build_failure_suffix",
    "FailureCategory",
    "_generate_gate_failure_analysis",
    "_publish_gate_failure_analyses",
]


# ── AC1: failures.py exists and contains exactly the required symbols ─────────

def test_failures_module_exists():
    """AC1: services/sprint_manager/failures.py exists."""
    assert FAILURES_PATH.exists(), "failures.py was not created"


def test_failures_contains_required_symbols():
    """AC1: failures.py contains all 5 required symbols."""
    mod = importlib.import_module("services.sprint_manager.failures")
    for name in REQUIRED_SYMBOLS:
        assert hasattr(mod, name), f"failures.py missing symbol: {name}"


def test_failures_required_symbols_are_defined_not_just_re_exported():
    """AC1: the 5 symbols are *defined* in failures.py source (not just names)."""
    source = FAILURES_PATH.read_text(encoding="utf-8")
    for name in ["record_failure", "_build_failure_suffix",
                 "_generate_gate_failure_analysis", "_publish_gate_failure_analyses"]:
        assert f"def {name}(" in source, (
            f"failures.py does not define '{name}' — it must be defined here, not merely imported"
        )
    assert "class FailureCategory" in source, (
        "failures.py does not define 'FailureCategory' class"
    )


# ── AC2: Original module imports the moved symbols from failures.py ───────────

def test_sprint_manager_imports_from_failures():
    """AC2: sprint_manager.py contains an import from .failures."""
    source = SPRINT_MGR_PATH.read_text(encoding="utf-8")
    assert "failures" in source, (
        "sprint_manager.py does not import from failures"
    )


def test_moved_symbols_not_defined_in_sprint_manager():
    """AC2: The 5 symbols are NOT re-defined in sprint_manager.py source.

    sprint_manager.py imports and re-exports them so existing call sites work,
    but must not have a second definition.
    """
    source = SPRINT_MGR_PATH.read_text(encoding="utf-8")
    for name in ["record_failure", "_build_failure_suffix",
                 "_generate_gate_failure_analysis", "_publish_gate_failure_analyses"]:
        assert f"def {name}(" not in source, (
            f"sprint_manager.py still defines '{name}' — it must be removed (moved to failures.py)"
        )
    assert "class FailureCategory" not in source, (
        "sprint_manager.py still defines 'FailureCategory' — it must be removed"
    )


# ── AC3: No logic changes — callables and FailureCategory constants ───────────

def test_failure_category_constants():
    """AC3: FailureCategory still exposes all expected string constants."""
    from services.sprint_manager.failures import FailureCategory
    assert FailureCategory.HANG            == "HANG"
    assert FailureCategory.CRASH           == "CRASH"
    assert FailureCategory.GATE_FAIL       == "GATE_FAIL"
    assert FailureCategory.TESTER_REJECTED == "TESTER_REJECTED"
    assert FailureCategory.RETRY_EXHAUSTED == "RETRY_EXHAUSTED"
    assert FailureCategory.CODER_NO_WORK   == "CODER_NO_WORK"
    assert FailureCategory.MERGE_CONFLICT  == "MERGE_CONFLICT"
    assert FailureCategory.LINT_FAIL       == "LINT_FAIL"
    assert FailureCategory.PYTEST_FAIL     == "PYTEST_FAIL"
    assert FailureCategory.REBASE_CONFLICT == "REBASE_CONFLICT"


def test_record_failure_writes_sidecar(tmp_path):
    """AC3: record_failure writes a JSON sidecar with expected keys."""
    from services.sprint_manager.failures import record_failure
    result = record_failure(
        issue_num=9999,
        failure_class="CRASH",
        detail="test detail",
        repo_root=tmp_path,
    )
    assert result is not None
    sc_path = tmp_path / ".commander" / "runtime" / "last-failure-9999.json"
    assert sc_path.exists(), "sidecar file was not written"
    import json
    data = json.loads(sc_path.read_text())
    assert data["issue"] == 9999
    assert data["failure_class"] == "CRASH"
    assert data["detail"] == "test detail"


def test_build_failure_suffix_returns_empty_when_no_sidecar(tmp_path):
    """AC3: _build_failure_suffix returns '' when no sidecar exists."""
    from services.sprint_manager.failures import _build_failure_suffix
    result = _build_failure_suffix(issue_num=8888, repo_root=tmp_path)
    assert result == "", (
        "_build_failure_suffix should return empty string when sidecar is absent"
    )


def test_build_failure_suffix_returns_string_with_sidecar(tmp_path):
    """AC3: _build_failure_suffix returns non-empty string when sidecar exists."""
    from services.sprint_manager.failures import record_failure, _build_failure_suffix
    record_failure(7777, "GATE_FAIL", "lint errors found", repo_root=tmp_path)
    result = _build_failure_suffix(issue_num=7777, repo_root=tmp_path)
    assert isinstance(result, str) and len(result) > 0, (
        "_build_failure_suffix should return a non-empty string when sidecar exists"
    )


def test_generate_gate_failure_analysis_returns_dict():
    """AC3: _generate_gate_failure_analysis returns dict with root_cause and prevention."""
    from services.sprint_manager.failures import _generate_gate_failure_analysis
    result = _generate_gate_failure_analysis("pytest", "E   AssertionError: assert 1 == 2")
    assert isinstance(result, dict), "_generate_gate_failure_analysis must return a dict"
    assert "root_cause" in result, "result must have 'root_cause' key"
    assert "prevention" in result, "result must have 'prevention' key"
    assert isinstance(result["root_cause"], str)
    assert isinstance(result["prevention"], str)


# ── AC4: py_compile failures.py exits 0 ──────────────────────────────────────

def test_failures_compiles_clean():
    """AC4: python -m py_compile services/sprint_manager/failures.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(FAILURES_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed for failures.py:\n{result.stderr}"
    )


# ── AC5: py_compile sprint_manager.py exits 0 ────────────────────────────────

def test_sprint_manager_compiles_clean():
    """AC5: python -m py_compile services/sprint_manager/sprint_manager.py exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SPRINT_MGR_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"py_compile failed for sprint_manager.py:\n{result.stderr}"
    )


# ── AC6: All existing tests that exercise failure handling pass ───────────────

def test_failures_importable_directly():
    """AC6: Direct import from services.sprint_manager.failures succeeds."""
    from services.sprint_manager.failures import (  # noqa: F401
        record_failure,
        FailureCategory,
        _build_failure_suffix,
        _generate_gate_failure_analysis,
        _publish_gate_failure_analyses,
    )
