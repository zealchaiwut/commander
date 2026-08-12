"""Tests for #1289: Extract sprint_manager pipeline dispatch functions to
services/sprint_manager/pipeline.py.

AC items verified:
  AC-1  pipeline.py exists and contains definitions (not just imports) of all
        five functions: _run_pipeline_dispatch, _compute_dispatch_levels,
        _build_sprint_dag_layers, _warn_file_conflicts, list_backlog_issues
  AC-2  sprint_manager.py imports the five symbols from pipeline.py (no logic
        duplicated in sprint_manager — they are re-exports only)
  AC-3  Pure moves: no signature changes for any of the five functions
  AC-4  python -m py_compile services/sprint_manager/pipeline.py exits 0
  AC-5  python -m py_compile on sprint_manager.py exits 0
  AC-6  Pipeline dispatch behavior is unchanged (list_backlog_issues importable
        from pipeline and callable without error; _compute_dispatch_levels
        returns correct layer grouping)
  AC-7  No dead imports remain in sprint_manager.py after extraction (pipeline.py
        re-import covers all five removed definitions)
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

PIPELINE_PATH = REPO_ROOT / "services" / "sprint_manager" / "pipeline.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

# list_backlog_issues moved to backlog.py (issue #2245); four remain in pipeline.py.
FOUR_FUNCTIONS = [
    "_run_pipeline_dispatch",
    "_compute_dispatch_levels",
    "_build_sprint_dag_layers",
    "_warn_file_conflicts",
]
FIVE_FUNCTIONS = FOUR_FUNCTIONS + ["list_backlog_issues"]


# ---------------------------------------------------------------------------
# AC-1: pipeline.py defines all five functions
# ---------------------------------------------------------------------------

class TestAC1PipelineModuleContainsFiveFunctions:
    def test_pipeline_file_exists(self):
        assert PIPELINE_PATH.exists(), f"{PIPELINE_PATH} does not exist"

    def test_all_five_functions_defined_in_pipeline(self):
        """Four dispatch functions defined in pipeline.py; list_backlog_issues in backlog.py."""
        source = PIPELINE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in FOUR_FUNCTIONS:
            assert fn in defined, (
                f"{fn} must be defined in pipeline.py, not just re-exported"
            )
        # list_backlog_issues extracted to backlog.py (issue #2245)
        backlog_src = (REPO_ROOT / "services/sprint_manager/backlog.py").read_text()
        backlog_tree = ast.parse(backlog_src)
        backlog_defs = {n.name for n in ast.walk(backlog_tree) if isinstance(n, ast.FunctionDef)}
        assert "list_backlog_issues" in backlog_defs, (
            "list_backlog_issues must be defined in backlog.py (issue #2245)"
        )


# ---------------------------------------------------------------------------
# AC-2: sprint_manager.py no longer defines the five functions (only imports)
# ---------------------------------------------------------------------------

class TestAC2SprintManagerNoLongerDefinesFive:
    def test_sprint_manager_has_no_def_for_five_functions(self):
        """sprint_manager.py must not contain 'def <fn>' for any of the five
        functions — only the import from pipeline.py."""
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_defs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in FIVE_FUNCTIONS:
            assert fn not in top_level_defs, (
                f"sprint_manager.py must not define {fn} — "
                "it should only import it from pipeline.py"
            )

    def test_sprint_manager_imports_four_functions_from_pipeline(self):
        """sprint_manager.py must import the four dispatch symbols from pipeline.py.

        list_backlog_issues moved to backlog.py (issue #2245) so it is imported from there.
        """
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_from_pipeline: set[str] = set()
        imported_from_backlog: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "pipeline" in module:
                    for alias in node.names:
                        imported_from_pipeline.add(alias.asname or alias.name)
                if "backlog" in module:
                    for alias in node.names:
                        imported_from_backlog.add(alias.asname or alias.name)
        for fn in FOUR_FUNCTIONS:
            assert fn in imported_from_pipeline, (
                f"sprint_manager.py must import {fn} from pipeline.py"
            )
        assert "list_backlog_issues" in imported_from_backlog, (
            "sprint_manager.py must import list_backlog_issues from backlog.py (issue #2245)"
        )


# ---------------------------------------------------------------------------
# AC-3: Pure moves — signatures unchanged
# ---------------------------------------------------------------------------

class TestAC3SignaturesUnchanged:
    def test_compute_dispatch_levels_signature(self):
        """_compute_dispatch_levels must keep its original parameter names."""
        import services.sprint_manager.pipeline as pl
        sig = inspect.signature(pl._compute_dispatch_levels)
        params = list(sig.parameters.keys())
        assert params == ["issues", "plan_order", "dag_layers"], (
            f"_compute_dispatch_levels signature changed: {params}"
        )

    def test_build_sprint_dag_layers_signature(self):
        """_build_sprint_dag_layers must keep its original parameter names."""
        import services.sprint_manager.pipeline as pl
        sig = inspect.signature(pl._build_sprint_dag_layers)
        params = list(sig.parameters.keys())
        assert params == ["issues"], (
            f"_build_sprint_dag_layers signature changed: {params}"
        )

    def test_warn_file_conflicts_signature(self):
        """_warn_file_conflicts must keep its original parameter names."""
        import services.sprint_manager.pipeline as pl
        sig = inspect.signature(pl._warn_file_conflicts)
        params = list(sig.parameters.keys())
        assert params == ["issues"], (
            f"_warn_file_conflicts signature changed: {params}"
        )

    def test_list_backlog_issues_signature(self):
        """list_backlog_issues must keep its original parameter names."""
        import services.sprint_manager.pipeline as pl
        sig = inspect.signature(pl.list_backlog_issues)
        params = list(sig.parameters.keys())
        assert params == ["label", "repo_name"], (
            f"list_backlog_issues signature changed: {params}"
        )

    def test_run_pipeline_dispatch_is_keyword_only(self):
        """_run_pipeline_dispatch must remain keyword-only (no positional params)."""
        import services.sprint_manager.pipeline as pl
        sig = inspect.signature(pl._run_pipeline_dispatch)
        for name, param in sig.parameters.items():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"_run_pipeline_dispatch param '{name}' must be keyword-only"
            )

    def test_five_functions_accessible_via_sprint_manager(self):
        """All five symbols must be importable from sprint_manager (call sites unbroken)."""
        import services.sprint_manager.sprint_manager as sm
        for fn in FIVE_FUNCTIONS:
            assert hasattr(sm, fn), f"sprint_manager must expose {fn}"
            assert callable(getattr(sm, fn)), f"{fn} on sprint_manager must be callable"

    def test_sprint_manager_functions_are_pipeline_functions(self):
        """sprint_manager's copies must be the same objects as pipeline's copies."""
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.pipeline as pl
        for fn in FIVE_FUNCTIONS:
            sm_fn = getattr(sm, fn)
            pl_fn = getattr(pl, fn)
            assert sm_fn is pl_fn, (
                f"sm.{fn} must be the same object as pipeline.{fn} "
                f"(got {sm_fn!r} vs {pl_fn!r})"
            )


# ---------------------------------------------------------------------------
# AC-4 & AC-5: py_compile exits 0
# ---------------------------------------------------------------------------

class TestAC4AC5PyCompile:
    def test_pipeline_py_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(PIPELINE_PATH)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on pipeline.py:\n{result.stderr}"
        )
        assert result.stdout == "", (
            f"py_compile produced unexpected stdout: {result.stdout}"
        )

    def test_sprint_manager_py_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_PATH)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_manager.py:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# AC-6: Pipeline dispatch behavior unchanged
# ---------------------------------------------------------------------------

class TestAC6BehaviorUnchanged:
    def test_list_backlog_issues_callable_from_pipeline(self):
        """list_backlog_issues must be importable and callable from pipeline.py."""
        from services.sprint_manager.pipeline import list_backlog_issues
        assert callable(list_backlog_issues)

    def test_compute_dispatch_levels_single_level_no_dag(self):
        """_compute_dispatch_levels with no DAG layers returns one level with all issues."""
        from services.sprint_manager.pipeline import _compute_dispatch_levels

        class FakeIssue:
            def __init__(self, number):
                self.number = number

        issues = [FakeIssue(1), FakeIssue(2), FakeIssue(3)]
        result = _compute_dispatch_levels(issues, plan_order=None, dag_layers=None)
        assert len(result) == 1
        assert [i.number for i in result[0]] == [1, 2, 3]

    def test_compute_dispatch_levels_respects_dag_layers(self):
        """_compute_dispatch_levels respects dag_layers grouping."""
        from services.sprint_manager.pipeline import _compute_dispatch_levels

        class FakeIssue:
            def __init__(self, number):
                self.number = number

        issues = [FakeIssue(1), FakeIssue(2), FakeIssue(3)]
        dag_layers = [[1, 2], [3]]
        result = _compute_dispatch_levels(issues, plan_order=None, dag_layers=dag_layers)
        assert len(result) == 2
        assert {i.number for i in result[0]} == {1, 2}
        assert [i.number for i in result[1]] == [3]

    def test_compute_dispatch_levels_respects_plan_order(self):
        """_compute_dispatch_levels sorts by plan_order within a level."""
        from services.sprint_manager.pipeline import _compute_dispatch_levels

        class FakeIssue:
            def __init__(self, number):
                self.number = number

        issues = [FakeIssue(10), FakeIssue(5), FakeIssue(1)]
        result = _compute_dispatch_levels(issues, plan_order=[5, 10, 1], dag_layers=None)
        assert len(result) == 1
        assert [i.number for i in result[0]] == [5, 10, 1]

    def test_build_sprint_dag_layers_returns_none_when_unavailable(self):
        """_build_sprint_dag_layers returns None when dag_builder is unavailable."""
        import services.sprint_manager.pipeline as pl
        original = pl._DAG_BUILDER_AVAILABLE
        try:
            pl._DAG_BUILDER_AVAILABLE = False
            result = pl._build_sprint_dag_layers([])
            assert result is None
        finally:
            pl._DAG_BUILDER_AVAILABLE = original

    def test_warn_file_conflicts_runs_without_error(self):
        """_warn_file_conflicts must run without raising on empty input."""
        from services.sprint_manager.pipeline import _warn_file_conflicts

        class FakeIssue:
            def __init__(self, number, status="pending"):
                self.number = number
                self.status = status

        _warn_file_conflicts([FakeIssue(1), FakeIssue(2)])


# ---------------------------------------------------------------------------
# AC-7: No dead imports in sprint_manager.py (functions are re-imported)
# ---------------------------------------------------------------------------

class TestAC7NoDeadImports:
    def test_pipeline_does_not_import_sprint_manager_at_module_level(self):
        """pipeline.py must not import sprint_manager at the module level
        (avoids circular imports)."""
        source = PIPELINE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # Only top-level imports (col_offset == 0) count as module-level
                if node.col_offset == 0:
                    assert "sprint_manager.sprint_manager" not in module, (
                        "pipeline.py must not import sprint_manager.sprint_manager "
                        "at the module level (creates circular import)"
                    )
            elif isinstance(node, ast.Import) and node.col_offset == 0:
                for alias in node.names:
                    assert "sprint_manager.sprint_manager" not in alias.name, (
                        "pipeline.py must not import sprint_manager.sprint_manager "
                        "at the module level (creates circular import)"
                    )

    def test_five_functions_absent_from_sprint_manager_source(self):
        """None of the five def statements should appear in sprint_manager.py."""
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        for fn in FIVE_FUNCTIONS:
            assert f"def {fn}(" not in source, (
                f"sprint_manager.py still contains 'def {fn}(' — "
                "the definition was not removed"
            )
