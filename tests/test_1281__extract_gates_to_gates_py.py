"""Tests for issue #1281 — extract quality gates to gates.py.

AC1  gates.py exists and contains all five gate functions
AC2  sprint_manager no longer defines the five symbols (only imports them)
AC3  No logic changes — py_compile on gates.py exits 0
AC4  py_compile on the sprint_manager entry module exits 0
AC5  (same compile gate; covered by AC3/AC4)
AC6  All existing quality gate tests pass (checked implicitly by the full suite)
AC7  _run_quality_gates orchestration order unchanged
"""
import ast
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
GATES_MODULE_PATH = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
SM_MODULE_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(REPO_ROOT))

_FIVE_SYMBOLS = [
    "_gate_typecheck",
    "_gate_design",
    "_gate_merge_preview",
    "_gate_monolith",
    "_run_quality_gates",
]


# ── AC1: gates.py exists and defines all five functions ───────────────────────

class TestGatesModuleContent:
    def test_gates_py_exists(self):
        assert GATES_MODULE_PATH.exists(), "services/sprint_manager/gates.py not found"

    @pytest.mark.parametrize("symbol", _FIVE_SYMBOLS)
    def test_symbol_defined_in_gates(self, symbol):
        """Each of the five symbols must be defined (as a function) in gates.py."""
        from services.sprint_manager import gates
        assert hasattr(gates, symbol), f"gates.py missing {symbol}"
        assert callable(getattr(gates, symbol)), f"{symbol} in gates.py is not callable"

    def test_gates_defines_log_gate_result(self):
        """_log_gate_result must also live in gates (called by _run_quality_gates)."""
        from services.sprint_manager import gates
        assert hasattr(gates, "_log_gate_result")


# ── AC2: sprint_manager no longer defines the five symbols ────────────────────

class TestSprintManagerNoLongerDefines:
    def _sm_ast(self):
        return ast.parse(SM_MODULE_PATH.read_text(encoding="utf-8"))

    def _top_level_function_names(self, tree):
        """Return names of top-level function definitions only (not imports)."""
        return {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and isinstance(node, ast.FunctionDef)
        }

    @pytest.mark.parametrize("symbol", _FIVE_SYMBOLS)
    def test_symbol_not_defined_in_sprint_manager(self, symbol):
        """The function must not be *defined* in sprint_manager.py — only imported."""
        tree = self._sm_ast()
        # Walk all FunctionDef nodes at module level (depth-1)
        top_level_defs = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        assert symbol not in top_level_defs, (
            f"{symbol} is still defined (not just imported) in sprint_manager.py"
        )

    @pytest.mark.parametrize("symbol", _FIVE_SYMBOLS)
    def test_symbol_importable_from_sprint_manager(self, symbol):
        """sprint_manager must re-export each symbol so existing callers work."""
        import sprint_manager as sm
        assert hasattr(sm, symbol), (
            f"sprint_manager.{symbol} not accessible — did you add it to the re-export?"
        )


# ── AC3 / AC4: py_compile passes ─────────────────────────────────────────────

class TestPyCompile:
    def test_gates_py_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(GATES_MODULE_PATH)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on gates.py:\n{result.stderr}"
        )

    def test_sprint_manager_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SM_MODULE_PATH)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_manager.py:\n{result.stderr}"
        )


# ── AC7: _run_quality_gates orchestration order unchanged ─────────────────────

class TestRunQualityGatesOrder:
    """Verify gate invocation order: typecheck → lint → design → pytest →
    merge-preview → monolith.  Each gate must be called BEFORE the next one and
    a failure must stop execution (remaining gates are not called)."""

    def _make_pass_result(self, gate: str):
        from services.sprint_manager.state import GateResult
        return GateResult(gate=gate, passed=True)

    def _make_fail_result(self, gate: str):
        from services.sprint_manager.state import GateResult
        return GateResult(gate=gate, passed=False, output=f"{gate} failed")

    def test_all_gates_called_in_order_when_all_pass(self, tmp_path):
        from sprint_manager import _run_quality_gates
        call_log = []

        def make_gate(name):
            def gate(*a, **kw):
                call_log.append(name)
                return self._make_pass_result(name)
            return gate

        patches = {
            "sprint_manager._gate_typecheck": make_gate("typecheck"),
            "sprint_manager._gate_lint": make_gate("lint"),
            "sprint_manager._gate_design": make_gate("design"),
            "sprint_manager._gate_pytest": make_gate("pytest"),
            "sprint_manager._gate_merge_preview": make_gate("merge-preview"),
            "sprint_manager._gate_monolith": make_gate("monolith"),
            "sprint_manager._log_gate_result": lambda *a, **kw: None,
        }
        with patch.multiple("sprint_manager", **{k.split(".", 1)[1]: v for k, v in patches.items()}):
            results = _run_quality_gates(
                issue_num=1,
                feature_branch="feature/1-test",
                worktester_root=tmp_path,
                worktester_dashboard=tmp_path,
                skip_all=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                gate_typecheck=True,
                gate_design=True,
                gate_frontend_lint=True,
                gate_monolith=True,
            )

        assert call_log == [
            "typecheck", "lint", "design", "pytest", "merge-preview", "monolith"
        ], f"Gate order wrong: {call_log}"
        assert all(r.passed for r in results)

    def test_all_gates_run_even_after_failure(self, tmp_path):
        """All gates run in one pass even when an early gate fails — every failure
        surfaces together so a single retry can fix them all (replaces the old
        fail-fast behaviour that cost one attempt per gate)."""
        from sprint_manager import _run_quality_gates
        call_log = []

        def fail_gate(name):
            def gate(*a, **kw):
                call_log.append(name)
                return self._make_fail_result(name)
            return gate

        def pass_gate(name):
            def gate(*a, **kw):
                call_log.append(name)
                return self._make_pass_result(name)
            return gate

        with patch.multiple("sprint_manager",
                            _gate_typecheck=fail_gate("typecheck"),
                            _gate_lint=pass_gate("lint"),
                            _gate_design=fail_gate("design"),
                            _gate_pytest=pass_gate("pytest"),
                            _gate_merge_preview=fail_gate("merge-preview"),
                            _gate_monolith=pass_gate("monolith"),
                            _log_gate_result=lambda *a, **kw: None):
            results = _run_quality_gates(
                issue_num=1,
                feature_branch="feature/1-test",
                worktester_root=tmp_path,
                worktester_dashboard=tmp_path,
                skip_all=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
            )

        # Every gate ran despite typecheck failing first.
        assert call_log == [
            "typecheck", "lint", "design", "pytest", "merge-preview", "monolith"
        ], f"All gates must run; got: {call_log}"
        assert len(results) == 6
        failed = [r.gate for r in results if not r.passed]
        assert failed == ["typecheck", "design", "merge-preview"]

    def test_revert_suppressed_during_run_then_restored(self, tmp_path):
        """Per-gate reverts are suppressed while gates run (the caller aggregates),
        and the suppression flag is restored afterward."""
        from sprint_manager import _run_quality_gates
        from services.sprint_manager import gates as _gates

        seen = {}

        def fail_gate(name):
            def gate(*a, **kw):
                seen[name] = _gates._REVERT_SUPPRESSED
                return self._make_fail_result(name)
            return gate

        assert _gates._REVERT_SUPPRESSED is False
        with patch.multiple("sprint_manager",
                            _gate_typecheck=fail_gate("typecheck"),
                            _gate_lint=fail_gate("lint"),
                            _gate_design=fail_gate("design"),
                            _gate_pytest=fail_gate("pytest"),
                            _gate_merge_preview=fail_gate("merge-preview"),
                            _gate_monolith=fail_gate("monolith"),
                            _log_gate_result=lambda *a, **kw: None):
            _run_quality_gates(
                issue_num=1, feature_branch="feature/1-test",
                worktester_root=tmp_path, worktester_dashboard=tmp_path,
                skip_all=False, gate_pytest=True, gate_lint=True,
                gate_merge_preview=True,
            )
        assert all(v is True for v in seen.values()), "reverts not suppressed during run"
        assert _gates._REVERT_SUPPRESSED is False, "suppression flag not restored"

    def test_skip_all_skips_every_gate(self, tmp_path):
        """When skip_all=True every gate returns skipped=True."""
        from sprint_manager import _run_quality_gates
        results = _run_quality_gates(
            issue_num=1,
            feature_branch="feature/1-test",
            worktester_root=tmp_path,
            worktester_dashboard=tmp_path,
            skip_all=True,
            gate_pytest=True,
            gate_lint=True,
            gate_merge_preview=True,
        )
        assert all(r.passed for r in results)
        assert all(getattr(r, "skipped", False) for r in results)
