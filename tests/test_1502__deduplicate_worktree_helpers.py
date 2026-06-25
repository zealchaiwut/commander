"""Tests for #1502: Deduplicate worktree helper copies to prevent silent drift.

AC items verified:
  AC-1  _git_worktree_root, _parse_dotenv_value, _find_crg_bin are NOT defined
        (as ast.FunctionDef) in sprint_manager.py — worktree.py is the single source
  AC-2  All three helpers are defined in worktree.py
  AC-3  sprint_manager.py exposes all three helpers (importable / re-exported)
  AC-4  sprint_manager's copies are the same objects as worktree's copies
  AC-5  Both files compile cleanly (py_compile exits 0)
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

WORKTREE_MODULE_PATH = REPO_ROOT / "services" / "sprint_manager" / "worktree.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

THREE_HELPERS = [
    "_git_worktree_root",
    "_parse_dotenv_value",
    "_find_crg_bin",
]


# ---------------------------------------------------------------------------
# AC-1: sprint_manager.py must NOT define these functions (no duplicate defs)
# ---------------------------------------------------------------------------

class TestAC1NoDuplicateDefinitions:
    def test_helpers_not_defined_in_sprint_manager(self):
        """_git_worktree_root, _parse_dotenv_value, _find_crg_bin must not be
        defined as FunctionDef nodes in sprint_manager.py.
        worktree.py is the authoritative source — no duplicates allowed.
        """
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined_in_sm = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in THREE_HELPERS:
            assert fn not in defined_in_sm, (
                f"{fn} must NOT be defined in sprint_manager.py — "
                "remove the duplicate and import from worktree.py instead"
            )


# ---------------------------------------------------------------------------
# AC-2: All three helpers are defined in worktree.py
# ---------------------------------------------------------------------------

class TestAC2DefinedInWorktree:
    def test_helpers_defined_in_worktree(self):
        """All three helpers must be defined (ast.FunctionDef) in worktree.py."""
        source = WORKTREE_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in THREE_HELPERS:
            assert fn in defined, (
                f"{fn} must be defined in worktree.py as the single source of truth"
            )


# ---------------------------------------------------------------------------
# AC-3: sprint_manager.py exposes all three helpers
# ---------------------------------------------------------------------------

class TestAC3SprintManagerExposesHelpers:
    def test_helpers_importable_from_sprint_manager(self):
        """All three helpers must be importable from sprint_manager module."""
        import services.sprint_manager.sprint_manager as sm
        for fn in THREE_HELPERS:
            assert hasattr(sm, fn), (
                f"sprint_manager must expose {fn} (re-exported from worktree.py)"
            )
            assert callable(getattr(sm, fn)), f"sm.{fn} must be callable"


# ---------------------------------------------------------------------------
# AC-4: sprint_manager's copies ARE worktree's copies (same object)
# ---------------------------------------------------------------------------

class TestAC4SameObjects:
    def test_sprint_manager_helpers_are_worktree_helpers(self):
        """sm._helper must be the same object as worktree._helper (not a copy)."""
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.worktree as wt
        for fn in THREE_HELPERS:
            sm_fn = getattr(sm, fn)
            wt_fn = getattr(wt, fn)
            assert sm_fn is wt_fn, (
                f"sm.{fn} must be the same object as worktree.{fn} "
                f"(got {sm_fn!r} vs {wt_fn!r})"
            )


# ---------------------------------------------------------------------------
# AC-5: Both files compile cleanly
# ---------------------------------------------------------------------------

class TestAC5PyCompile:
    def test_worktree_py_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(WORKTREE_MODULE_PATH)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on worktree.py:\n{result.stderr}"
        )

    def test_sprint_manager_py_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_PATH)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_manager.py:\n{result.stderr}"
        )
