"""Tests for issue #1287 — Extract sprint summary generation to dedicated module.

AC coverage:
  AC-1  services/sprint_manager/summary.py exists and contains exactly the four functions.
  AC-2  Original module re-exports all four symbols (backward-compatible import).
  AC-3  python -m py_compile services/sprint_manager/summary.py exits 0.
  AC-4  python -m py_compile on the original module exits 0.
  AC-5  No logic, default arguments, docstrings, or type annotations altered.
  AC-6  All existing tests that invoke summary generation pass without modification
        (verified by running those test files separately; not enforced here).
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

from unittest.mock import MagicMock

if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()

import sprint_manager as sm  # noqa: E402

SUMMARY_MODULE_PATH = SM_DIR / "summary.py"
SPRINT_MANAGER_PATH = SM_DIR / "sprint_manager.py"

_FOUR_SYMBOLS = [
    "generate_sprint_summary",
    "write_sprint_summary",
    "create_summary_github_issue",
    "_prompt_learnings",
]


# ── AC-1: summary.py exists and contains the four functions ─────────────────

class TestAC1SummaryModuleExists:
    """AC-1: summary.py exists and all four callables are defined there."""

    def test_summary_py_file_exists(self):
        assert SUMMARY_MODULE_PATH.exists(), (
            f"services/sprint_manager/summary.py must exist but was not found at {SUMMARY_MODULE_PATH}"
        )

    def test_four_symbols_importable_from_summary(self):
        """All four symbols must be importable directly from the summary module."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("summary", SUMMARY_MODULE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in _FOUR_SYMBOLS:
            assert hasattr(mod, name), f"summary.py must define {name!r}"
            assert callable(getattr(mod, name)), f"{name!r} in summary.py must be callable"

    def test_four_symbols_are_functions_in_summary(self):
        """Each of the four symbols must be a function (not a class or value)."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("summary", SUMMARY_MODULE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in _FOUR_SYMBOLS:
            obj = getattr(mod, name, None)
            assert obj is not None, f"{name!r} missing from summary.py"
            assert inspect.isfunction(obj), f"{name!r} in summary.py must be a function"


# ── AC-2: original module re-exports all four symbols ───────────────────────

class TestAC2BackwardCompatReexport:
    """AC-2: importing from sprint_manager (flat import) still works."""

    def test_importable_from_sprint_manager_flat(self):
        """from sprint_manager import <symbol> must succeed for all four."""
        for name in _FOUR_SYMBOLS:
            assert hasattr(sm, name), (
                f"sprint_manager module must re-export {name!r} for backward compatibility"
            )

    def test_importable_from_package_path(self):
        """from services.sprint_manager.sprint_manager import <symbol> must succeed."""
        import services.sprint_manager.sprint_manager as sm_pkg
        for name in _FOUR_SYMBOLS:
            assert hasattr(sm_pkg, name), (
                f"services.sprint_manager.sprint_manager must re-export {name!r}"
            )

    def test_importable_from_new_module_path(self):
        """from services.sprint_manager.summary import <symbol> must work (AC-2 UAT step 2)."""
        from services.sprint_manager.summary import (  # noqa: F401
            generate_sprint_summary,
            write_sprint_summary,
            create_summary_github_issue,
            _prompt_learnings,
        )

    def test_reexported_symbols_are_same_objects(self):
        """The re-exported names on sprint_manager must point to the same objects as in summary."""
        import services.sprint_manager.summary as summary_mod
        for name in _FOUR_SYMBOLS:
            sm_obj = getattr(sm, name)
            summary_obj = getattr(summary_mod, name)
            assert sm_obj is summary_obj, (
                f"sm.{name} must be the same object as summary.{name} — "
                "re-export must not create a wrapper"
            )


# ── AC-3 / AC-4: py_compile passes on both files ────────────────────────────

class TestAC3AC4PyCompile:
    """AC-3/AC-4: both files must be valid Python (py_compile exits 0)."""

    def test_py_compile_summary_module(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SUMMARY_MODULE_PATH)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on summary.py:\n{result.stderr}"
        )

    def test_py_compile_sprint_manager(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_PATH)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_manager.py:\n{result.stderr}"
        )


# ── AC-5: function signatures, docstrings, annotations unchanged ─────────────

class TestAC5NoLogicOrSignatureChanges:
    """AC-5: the moved functions must have identical signatures, defaults, docstrings."""

    def _get_summary_fn(self, name: str):
        """Load the function from summary.py (not from sprint_manager re-export)."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_summary_fresh", SUMMARY_MODULE_PATH
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, name)

    def _get_sm_sig_before(self, name: str):
        """Read the function from the sprint_manager module (re-exported from summary)."""
        return getattr(sm, name)

    @pytest.mark.parametrize("fn_name", _FOUR_SYMBOLS)
    def test_parameter_names_unchanged(self, fn_name):
        summary_fn = self._get_summary_fn(fn_name)
        sm_fn = self._get_sm_sig_before(fn_name)
        params_summary = list(inspect.signature(summary_fn).parameters.keys())
        params_sm = list(inspect.signature(sm_fn).parameters.keys())
        assert params_summary == params_sm, (
            f"{fn_name}: parameter names differ.\n"
            f"  summary.py: {params_summary}\n"
            f"  sprint_manager: {params_sm}"
        )

    @pytest.mark.parametrize("fn_name", _FOUR_SYMBOLS)
    def test_default_values_unchanged(self, fn_name):
        summary_fn = self._get_summary_fn(fn_name)
        sm_fn = self._get_sm_sig_before(fn_name)
        sig_summary = inspect.signature(summary_fn)
        sig_sm = inspect.signature(sm_fn)
        for pname, param in sig_sm.parameters.items():
            if param.default is inspect.Parameter.empty:
                continue
            summary_default = sig_summary.parameters[pname].default
            assert summary_default == param.default, (
                f"{fn_name}: default for '{pname}' changed.\n"
                f"  summary.py:     {summary_default!r}\n"
                f"  sprint_manager: {param.default!r}"
            )

    @pytest.mark.parametrize("fn_name", _FOUR_SYMBOLS)
    def test_docstring_preserved(self, fn_name):
        summary_fn = self._get_summary_fn(fn_name)
        sm_fn = self._get_sm_sig_before(fn_name)
        assert inspect.getdoc(summary_fn) == inspect.getdoc(sm_fn), (
            f"{fn_name}: docstring altered in the moved function"
        )
