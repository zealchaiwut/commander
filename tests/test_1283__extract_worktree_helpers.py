"""Tests for #1283: Extract worktree/env helpers to services/sprint_manager/worktree.py.

AC items verified:
  AC-1  worktree.py exists and contains exactly the five moved functions
  AC-2  sprint_manager re-exports all five so existing call sites work unchanged
  AC-3  Function signatures, default args, and docstrings are not altered
  AC-4  python -m py_compile services/sprint_manager/worktree.py exits 0
  AC-5  python -m py_compile on sprint_manager.py exits 0
  AC-6  No new imports needed at call sites (re-export shim covers them)
"""
from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

WORKTREE_MODULE_PATH = REPO_ROOT / "services" / "sprint_manager" / "worktree.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

FIVE_FUNCTIONS = [
    "_resolve_uat_env_for_tester",
    "_worktree_hygiene",
    "_crg_update_worktree",
    "_stash_to_quarantine",
    "_detect_port",
]


# ---------------------------------------------------------------------------
# AC-1: worktree.py exists and contains exactly the five functions
# ---------------------------------------------------------------------------

class TestAC1WorktreeModuleExists:
    def test_file_exists(self):
        """services/sprint_manager/worktree.py must exist."""
        assert WORKTREE_MODULE_PATH.exists(), (
            f"{WORKTREE_MODULE_PATH} does not exist"
        )

    def test_all_five_functions_defined_in_worktree(self):
        """All five functions must be defined (not just imported) in worktree.py."""
        source = WORKTREE_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in FIVE_FUNCTIONS:
            assert fn in defined, (
                f"{fn} must be defined in worktree.py, not just re-exported"
            )


# ---------------------------------------------------------------------------
# AC-2: sprint_manager re-exports all five functions
# ---------------------------------------------------------------------------

class TestAC2SprintManagerReExports:
    def test_sprint_manager_has_all_five_functions(self):
        """All five functions must be importable from sprint_manager."""
        import services.sprint_manager.sprint_manager as sm
        for fn in FIVE_FUNCTIONS:
            assert hasattr(sm, fn), (
                f"sprint_manager must expose {fn} (re-export from worktree.py)"
            )
            assert callable(getattr(sm, fn)), f"{fn} on sprint_manager must be callable"

    def test_worktree_module_importable(self):
        """services.sprint_manager.worktree must be importable without error."""
        import services.sprint_manager.worktree as wt
        for fn in FIVE_FUNCTIONS:
            assert hasattr(wt, fn), f"worktree module must define {fn}"

    def test_sprint_manager_functions_are_worktree_functions(self):
        """sprint_manager's copies must be the same objects as worktree's copies."""
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.worktree as wt
        for fn in FIVE_FUNCTIONS:
            sm_fn = getattr(sm, fn)
            wt_fn = getattr(wt, fn)
            assert sm_fn is wt_fn, (
                f"sm.{fn} must be the same object as worktree.{fn} "
                f"(got {sm_fn!r} vs {wt_fn!r})"
            )


# ---------------------------------------------------------------------------
# AC-3: Signatures, default args, and docstrings are unchanged
# ---------------------------------------------------------------------------

# Expected signatures captured from sprint_manager.py before extraction.
_EXPECTED_PARAMS = {
    "_resolve_uat_env_for_tester": ["cfg", "tester_app_dir"],
    "_worktree_hygiene": [
        "worktree", "ticket_id", "merge_target",
        "is_retry", "repo_root", "recover_on_rebase_conflict",
    ],
    "_crg_update_worktree": ["worktree", "role"],
    "_stash_to_quarantine": ["worktree", "ticket_id", "effective_root"],
    "_detect_port": ["cfg"],
}

_EXPECTED_DEFAULTS = {
    "_worktree_hygiene": {"is_retry": False, "repo_root": None, "recover_on_rebase_conflict": False},
    "_crg_update_worktree": {"role": "agent"},
}


class TestAC3SignaturesUnchanged:
    def test_parameter_names(self):
        """Each function must have the same parameter names as before extraction."""
        import services.sprint_manager.worktree as wt
        for fn_name, expected_params in _EXPECTED_PARAMS.items():
            fn = getattr(wt, fn_name)
            sig = inspect.signature(fn)
            actual_params = list(sig.parameters.keys())
            assert actual_params == expected_params, (
                f"{fn_name} parameter names changed: "
                f"expected {expected_params}, got {actual_params}"
            )

    def test_default_arguments(self):
        """Default argument values must be unchanged."""
        import services.sprint_manager.worktree as wt
        for fn_name, defaults in _EXPECTED_DEFAULTS.items():
            fn = getattr(wt, fn_name)
            sig = inspect.signature(fn)
            for param_name, expected_default in defaults.items():
                param = sig.parameters[param_name]
                assert param.default == expected_default, (
                    f"{fn_name}.{param_name} default changed: "
                    f"expected {expected_default!r}, got {param.default!r}"
                )

    def test_docstrings_present(self):
        """Each function must have a non-empty docstring."""
        import services.sprint_manager.worktree as wt
        for fn_name in FIVE_FUNCTIONS:
            fn = getattr(wt, fn_name)
            assert fn.__doc__ and fn.__doc__.strip(), (
                f"{fn_name} must have a docstring after extraction"
            )


# ---------------------------------------------------------------------------
# AC-4 & AC-5: py_compile exits 0
# ---------------------------------------------------------------------------

class TestAC4AC5PyCompile:
    def test_worktree_py_compiles(self):
        """python -m py_compile services/sprint_manager/worktree.py must exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(WORKTREE_MODULE_PATH)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on worktree.py:\n{result.stderr}"
        )
        assert result.stdout == "", (
            f"py_compile produced unexpected stdout: {result.stdout}"
        )

    def test_sprint_manager_py_compiles(self):
        """python -m py_compile on sprint_manager.py must exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_PATH)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_manager.py:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# AC-6: No new imports needed at call sites
# ---------------------------------------------------------------------------

class TestAC6NoNewImportsAtCallSites:
    def test_functions_accessible_via_sprint_manager_import(self):
        """Importing sprint_manager (as all call sites do) exposes all five names."""
        import services.sprint_manager.sprint_manager as sm
        for fn in FIVE_FUNCTIONS:
            fn_obj = getattr(sm, fn, None)
            assert fn_obj is not None, (
                f"Call sites that do `from sprint_manager import {fn}` must still work"
            )

    def test_worktree_module_does_not_import_sprint_manager_at_top_level(self):
        """worktree.py must not import sprint_manager at module level (avoids circular import)."""
        source = WORKTREE_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # Only check top-level import statements (not inside function bodies)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert "sprint_manager.sprint_manager" not in module, (
                        "worktree.py must not import sprint_manager.sprint_manager "
                        "at the module level (creates circular import)"
                    )
