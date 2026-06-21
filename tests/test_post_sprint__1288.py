"""TDD tests for issue #1288: Extract post-sprint agents to post_sprint.py.

Each test class maps to one AC item.
"""
from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SM_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
POST_SPRINT_PATH = REPO_ROOT / "services" / "sprint_manager" / "post_sprint.py"

SIX_METHODS = [
    "_create_sprint_pr",
    "_dispatch_documenter",
    "_dispatch_reviewer",
    "_dispatch_ba_for_followup",
    "_dispatch_estimator_for_followup",
    "_enrich_followup_tickets",
]


def _top_level_funcs(path: Path) -> set[str]:
    """Return names of all top-level function definitions in a Python file."""
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.col_offset == 0
    }


class TestAC1PostSprintModuleContainsSixMethods:
    """AC1: post_sprint.py exists and contains exactly the six methods."""

    def test_file_exists(self):
        assert POST_SPRINT_PATH.exists(), (
            f"post_sprint.py not found at {POST_SPRINT_PATH}"
        )

    def test_contains_all_six_methods(self):
        defined = _top_level_funcs(POST_SPRINT_PATH)
        for method in SIX_METHODS:
            assert method in defined, (
                f"{method} not defined in post_sprint.py"
            )


class TestAC2MethodsRemovedFromOriginal:
    """AC2: All six methods are removed from sprint_manager.py (no `def` remains)."""

    def test_no_def_in_sprint_manager(self):
        defined = _top_level_funcs(SM_PATH)
        for method in SIX_METHODS:
            assert method not in defined, (
                f"{method} still has a top-level `def` in sprint_manager.py — "
                "it should be removed and imported from post_sprint.py"
            )


class TestAC3CallSitesCanInvokeMovedMethods:
    """AC3: Original call sites import and invoke the moved methods without modification."""

    def test_sprint_manager_re_exports_all_six(self):
        import services.sprint_manager.sprint_manager as sm
        for method in SIX_METHODS:
            assert hasattr(sm, method), (
                f"sm.{method} missing — sprint_manager.py must re-export it from post_sprint.py"
            )

    def test_re_exported_attributes_are_callable(self):
        import services.sprint_manager.sprint_manager as sm
        for method in SIX_METHODS:
            fn = getattr(sm, method)
            assert callable(fn), f"sm.{method} is not callable"

    def test_default_reviewer_prompt_still_accessible_on_sm(self):
        """Tests access sm.DEFAULT_REVIEWER_PROMPT; it must remain accessible."""
        import services.sprint_manager.sprint_manager as sm
        assert hasattr(sm, "DEFAULT_REVIEWER_PROMPT"), (
            "sm.DEFAULT_REVIEWER_PROMPT missing after refactor — re-export it from post_sprint.py"
        )
        assert isinstance(sm.DEFAULT_REVIEWER_PROMPT, str)
        assert len(sm.DEFAULT_REVIEWER_PROMPT) > 100


class TestAC4PostSprintCompiles:
    """AC4: `python -m py_compile services/sprint_manager/post_sprint.py` exits 0."""

    def test_post_sprint_py_compile(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(POST_SPRINT_PATH)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on post_sprint.py:\n{result.stderr}"
        )


class TestAC5AllModifiedFilesCompile:
    """AC5: `python -m py_compile` on all modified files exits 0."""

    def _compile(self, path: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on {path.name}:\n{result.stderr}"
        )

    def test_sprint_manager_compiles(self):
        self._compile(SM_PATH)

    def test_post_sprint_compiles(self):
        self._compile(POST_SPRINT_PATH)


class TestAC6SignaturesPreserved:
    """AC6: No argument signatures altered during the move."""

    _EXPECTED_PARAMS = {
        "_create_sprint_pr": [
            "sprint_branch", "sprint_label", "sprint_number", "state",
            "repo_name", "pr_base", "merge_target",
        ],
        "_dispatch_documenter": [
            "state", "sprint_branch", "base_sha", "head_sha",
            "cfg", "repo_name", "timeout_secs", "merge_target",
        ],
        "_dispatch_reviewer": [
            "state", "summary_issue_num", "sprint_branch", "base_sha",
            "head_sha", "cfg", "repo_name", "merge_target",
        ],
        "_dispatch_ba_for_followup": ["issue_num", "eff_repo", "cfg", "state"],
        "_dispatch_estimator_for_followup": ["issue_num", "eff_repo", "cfg"],
        "_enrich_followup_tickets": ["follow_up_tickets", "eff_repo", "cfg", "state"],
    }

    def test_signatures_unchanged(self):
        import services.sprint_manager.post_sprint as ps
        for method_name, expected in self._EXPECTED_PARAMS.items():
            fn = getattr(ps, method_name)
            actual = list(inspect.signature(fn).parameters.keys())
            assert actual == expected, (
                f"{method_name} signature changed: expected {expected}, got {actual}"
            )
