"""Tests for #1285: Extract coder dispatch logic to services/sprint_manager/dispatch.py.

AC items verified:
  AC-1  dispatch.py exists and contains _dispatch_coder, _load_agent_persona,
        _agent_identity_env as definitions (not mere imports)
  AC-2  sprint_manager.py no longer defines those three functions (only imports)
  AC-3  All three symbols remain accessible via sprint_manager (call sites unbroken)
  AC-4  python -m py_compile services/sprint_manager/dispatch.py exits 0
  AC-5  python -m py_compile on sprint_manager.py exits 0
  AC-6  No new public API: extracted symbols still have _ prefix
  AC-7  dispatch.py does not import sprint_manager at module level (no circular import)
"""
from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

DISPATCH_MODULE_PATH = REPO_ROOT / "services" / "sprint_manager" / "dispatch.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

THREE_FUNCTIONS = [
    "_dispatch_coder",
    "_load_agent_persona",
    "_agent_identity_env",
]


# ---------------------------------------------------------------------------
# AC-1: dispatch.py exists and defines the three functions
# ---------------------------------------------------------------------------

class TestAC1DispatchModuleExists:
    def test_file_exists(self):
        """services/sprint_manager/dispatch.py must exist."""
        assert DISPATCH_MODULE_PATH.exists(), (
            f"{DISPATCH_MODULE_PATH} does not exist"
        )

    def test_all_three_functions_defined_in_dispatch(self):
        """All three functions must be defined (not just imported) in dispatch.py."""
        source = DISPATCH_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in THREE_FUNCTIONS:
            assert fn in defined, (
                f"{fn} must be defined in dispatch.py, not just re-exported"
            )


# ---------------------------------------------------------------------------
# AC-2: sprint_manager.py no longer defines the three functions
# ---------------------------------------------------------------------------

class TestAC2SprintManagerNoLongerDefines:
    def test_sprint_manager_has_no_def_for_three_functions(self):
        """sprint_manager.py must not contain 'def _dispatch_coder' etc. — only imports."""
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Collect all top-level function definitions in sprint_manager.py
        top_level_defs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in THREE_FUNCTIONS:
            assert fn not in top_level_defs, (
                f"sprint_manager.py must not define {fn} — "
                f"it should only import it from dispatch.py"
            )


# ---------------------------------------------------------------------------
# AC-3: All three symbols accessible via sprint_manager (call sites unbroken)
# ---------------------------------------------------------------------------

class TestAC3SprintManagerReExports:
    def test_sprint_manager_has_all_three_functions(self):
        """All three functions must be importable from sprint_manager."""
        import services.sprint_manager.sprint_manager as sm
        for fn in THREE_FUNCTIONS:
            assert hasattr(sm, fn), (
                f"sprint_manager must expose {fn} (re-export from dispatch.py)"
            )
            assert callable(getattr(sm, fn)), f"{fn} on sprint_manager must be callable"

    def test_dispatch_module_importable(self):
        """services.sprint_manager.dispatch must be importable without error."""
        import services.sprint_manager.dispatch as dp
        for fn in THREE_FUNCTIONS:
            assert hasattr(dp, fn), f"dispatch module must define {fn}"

    def test_sprint_manager_functions_are_dispatch_functions(self):
        """sprint_manager's copies must be the same objects as dispatch's copies."""
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.dispatch as dp
        for fn in THREE_FUNCTIONS:
            sm_fn = getattr(sm, fn)
            dp_fn = getattr(dp, fn)
            assert sm_fn is dp_fn, (
                f"sm.{fn} must be the same object as dispatch.{fn} "
                f"(got {sm_fn!r} vs {dp_fn!r})"
            )


# ---------------------------------------------------------------------------
# AC-4 & AC-5: py_compile exits 0
# ---------------------------------------------------------------------------

class TestAC4AC5PyCompile:
    def test_dispatch_py_compiles(self):
        """python -m py_compile services/sprint_manager/dispatch.py must exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(DISPATCH_MODULE_PATH)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on dispatch.py:\n{result.stderr}"
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
# AC-6: No new public API — symbols keep _ prefix
# ---------------------------------------------------------------------------

class TestAC6PrivatePrefix:
    def test_all_extracted_symbols_are_private(self):
        """All three extracted symbols must retain their _ prefix (no public API added)."""
        for fn in THREE_FUNCTIONS:
            assert fn.startswith("_"), (
                f"{fn} does not have a leading underscore — "
                "the extraction must not change visibility"
            )

    def test_dispatch_module_introduces_no_new_public_names(self):
        """dispatch.py must not introduce new public (non-underscore) names beyond stdlib noise."""
        import services.sprint_manager.dispatch as dp
        public_names = [
            name for name in dir(dp)
            if not name.startswith("_")
        ]
        # Allow standard module-level public names that come from imports
        # (e.g. 'Path', 'Optional', etc.) — but none of our three functions are public.
        for fn in THREE_FUNCTIONS:
            assert fn not in public_names or fn.startswith("_"), (
                f"{fn} appears as a public name in dispatch.py"
            )


# ---------------------------------------------------------------------------
# AC-7: No circular imports — dispatch.py must not import sprint_manager at top level
# ---------------------------------------------------------------------------

class TestAC7NoCircularImports:
    def test_dispatch_does_not_import_sprint_manager_at_top_level(self):
        """dispatch.py must not import sprint_manager at module level (avoids circular import)."""
        source = DISPATCH_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert "sprint_manager.sprint_manager" not in module, (
                        "dispatch.py must not import sprint_manager.sprint_manager "
                        "at the module level (creates circular import)"
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "sprint_manager.sprint_manager" not in alias.name, (
                            "dispatch.py must not import sprint_manager.sprint_manager "
                            "at the module level (creates circular import)"
                        )


# ---------------------------------------------------------------------------
# AC-1 extra: Signatures and docstrings preserved
# ---------------------------------------------------------------------------

class TestSignaturesPreserved:
    def test_parameter_names_dispatch_coder(self):
        """_dispatch_coder must keep its original parameter names."""
        import services.sprint_manager.dispatch as dp
        sig = inspect.signature(dp._dispatch_coder)
        params = list(sig.parameters.keys())
        expected = [
            "issue_num", "alert_modes", "sprint_branch", "repo_name",
            "cfg", "chosen_port", "rate_limit_events", "on_running",
            "sprint_label", "prior_failures", "hang_continuation",
            "attempt_kind", "coder_backend_override", "worktree_override",
        ]
        assert params == expected, (
            f"_dispatch_coder parameter list changed: expected {expected}, got {params}"
        )

    def test_parameter_names_load_agent_persona(self):
        """_load_agent_persona must keep its original parameter names."""
        import services.sprint_manager.dispatch as dp
        sig = inspect.signature(dp._load_agent_persona)
        params = list(sig.parameters.keys())
        assert params == ["role", "base_dir"], (
            f"_load_agent_persona parameter list changed: got {params}"
        )

    def test_parameter_names_agent_identity_env(self):
        """_agent_identity_env must keep its original parameter names."""
        import services.sprint_manager.dispatch as dp
        sig = inspect.signature(dp._agent_identity_env)
        params = list(sig.parameters.keys())
        assert params == ["role", "issue_num"], (
            f"_agent_identity_env parameter list changed: got {params}"
        )

    def test_docstrings_present(self):
        """All three functions must have a non-empty docstring."""
        import services.sprint_manager.dispatch as dp
        for fn_name in THREE_FUNCTIONS:
            fn = getattr(dp, fn_name)
            assert fn.__doc__ and fn.__doc__.strip(), (
                f"{fn_name} must have a docstring after extraction"
            )
