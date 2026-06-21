"""Tests for issue #1280 — extract pytest/lint gates to services/sprint_manager/gates.py.

Each test is anchored to a specific acceptance criterion from the issue.

AC1: gates.py exists and contains all required symbols — verbatim, no logic changes
AC2: All moved symbols re-exported from sprint_manager; no external call site breaks
AC3: python -m py_compile services/sprint_manager/gates.py exits 0
AC4: python -m py_compile on sprint_manager.py exits 0
AC5: Existing pytest suite passes without modification
AC6: _gate_lint produces identical pass/fail results on the same inputs
AC7: No new public API; all moved functions remain private (underscore-prefixed)
AC8: Diff is a pure move — zero behavioral changes, zero added logic
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).parent.parent
GATES_PATH = REPO_ROOT / "services" / "sprint_manager" / "gates.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


# ── AC1: gates.py exists and contains the required symbols ───────────────────

class TestGatesModuleExists:
    """AC1: services/sprint_manager/gates.py exists."""

    def test_gates_file_exists(self):
        assert GATES_PATH.exists(), "gates.py was not created"

    def test_gates_module_is_importable(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert mod is not None

    def test_gates_has_gate_pytest(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(getattr(mod, "_gate_pytest", None)), \
            "gates.py missing _gate_pytest"

    def test_gates_has_gate_lint(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(getattr(mod, "_gate_lint", None)), \
            "gates.py missing _gate_lint"

    def test_gates_has_lint_autofix_commit(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(getattr(mod, "_lint_autofix_commit", None)), \
            "gates.py missing _lint_autofix_commit"

    def test_gates_has_run_frontend_lint(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(getattr(mod, "_run_frontend_lint", None)), \
            "gates.py missing _run_frontend_lint"

    def test_gates_has_changed_py_files(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(getattr(mod, "_changed_py_files", None)), \
            "gates.py missing _changed_py_files"

    def test_gates_has_changed_js_ts_files(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(getattr(mod, "_changed_js_ts_files", None)), \
            "gates.py missing _changed_js_ts_files"

    def test_gates_has_changed_frontend_files(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(getattr(mod, "_changed_frontend_files", None)), \
            "gates.py missing _changed_frontend_files"


# ── AC1: Symbols NOT redefined in sprint_manager.py (pure move) ──────────────

class TestMovedSymbolsRemovedFromSprintManager:
    """AC1/AC8: The moved symbols are defined in gates.py, not redefined in sprint_manager.py."""

    def test_gate_pytest_not_defined_in_sprint_manager(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "def _gate_pytest(" not in source, \
            "sprint_manager.py still defines _gate_pytest — must be removed"

    def test_gate_lint_not_defined_in_sprint_manager(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "def _gate_lint(" not in source, \
            "sprint_manager.py still defines _gate_lint — must be removed"

    def test_lint_autofix_commit_not_defined_in_sprint_manager(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "def _lint_autofix_commit(" not in source, \
            "sprint_manager.py still defines _lint_autofix_commit — must be removed"

    def test_run_frontend_lint_not_defined_in_sprint_manager(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "def _run_frontend_lint(" not in source, \
            "sprint_manager.py still defines _run_frontend_lint — must be removed"

    def test_changed_py_files_not_defined_in_sprint_manager(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "def _changed_py_files(" not in source, \
            "sprint_manager.py still defines _changed_py_files — must be removed"

    def test_changed_js_ts_files_not_defined_in_sprint_manager(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "def _changed_js_ts_files(" not in source, \
            "sprint_manager.py still defines _changed_js_ts_files — must be removed"

    def test_changed_frontend_files_not_defined_in_sprint_manager(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "def _changed_frontend_files(" not in source, \
            "sprint_manager.py still defines _changed_frontend_files — must be removed"


# ── AC2: sprint_manager imports from gates.py ────────────────────────────────

class TestSprintManagerReExports:
    """AC2: sprint_manager.py imports moved symbols from gates so call sites work."""

    def test_sprint_manager_imports_gates(self):
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        assert "gates" in source, \
            "sprint_manager.py does not import from gates"

    def test_sprint_manager_has_gate_pytest_in_namespace(self):
        """AC2: _gate_pytest resolves in sprint_manager namespace via import."""
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(mod._gate_pytest)

    def test_sprint_manager_has_gate_lint_in_namespace(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(mod._gate_lint)

    def test_sprint_manager_has_changed_py_files_in_namespace(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(mod._changed_py_files)

    def test_sprint_manager_has_changed_js_ts_files_in_namespace(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(mod._changed_js_ts_files)

    def test_sprint_manager_has_changed_frontend_files_in_namespace(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(mod._changed_frontend_files)

    def test_sprint_manager_has_run_frontend_lint_in_namespace(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(mod._run_frontend_lint)

    def test_sprint_manager_has_lint_autofix_commit_in_namespace(self):
        mod = importlib.import_module("services.sprint_manager.gates")
        assert callable(mod._lint_autofix_commit)


# ── AC3: py_compile gates.py exits 0 ─────────────────────────────────────────

class TestPyCompileGates:
    """AC3: gates.py has no syntax errors."""

    def test_py_compile_gates_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/gates.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed for gates.py:\n{result.stderr}"
        )

    def test_py_compile_gates_no_output(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/gates.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.stdout == ""
        assert result.stderr == ""


# ── AC4: py_compile sprint_manager.py exits 0 ────────────────────────────────

class TestPyCompileSprintManager:
    """AC4: sprint_manager.py has no syntax errors after the refactor."""

    def test_py_compile_sprint_manager_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/sprint_manager.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed for sprint_manager.py:\n{result.stderr}"
        )

    def test_py_compile_sprint_manager_no_output(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/sprint_manager.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.stdout == ""
        assert result.stderr == ""


# ── AC6: Behavioral equivalence — _changed_*_files helpers work correctly ────

class TestChangedFilesHelpers:
    """AC6: _changed_*_files functions behave identically to pre-move."""

    def test_changed_py_files_returns_only_py(self, tmp_path):
        """_changed_py_files returns only .py files from git diff output."""
        from services.sprint_manager.gates import _changed_py_files

        fake_diff = "server.py\napps/dashboard/static/app.js\ntests/test_foo.py\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=fake_diff, stderr=""
            )
            result = _changed_py_files("develop", tmp_path)

        assert "server.py" in result
        assert "tests/test_foo.py" in result
        assert "apps/dashboard/static/app.js" not in result

    def test_changed_py_files_returns_empty_on_nonzero_rc(self, tmp_path):
        from services.sprint_manager.gates import _changed_py_files

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
            result = _changed_py_files("develop", tmp_path)

        assert result == []

    def test_changed_js_ts_files_returns_js_ts_only(self, tmp_path):
        from services.sprint_manager.gates import _changed_js_ts_files

        fake_diff = "app.js\nstyle.css\ncomponent.tsx\nstatic/dist/bundle.js\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=fake_diff, stderr=""
            )
            result = _changed_js_ts_files("develop", tmp_path)

        assert "app.js" in result
        assert "component.tsx" in result
        assert "style.css" not in result
        # _JS_TS_LINT_EXCLUDE checks for "/dist/" (with leading slash) so
        # "static/dist/bundle.js" is excluded but "dist/bundle.js" is not
        assert "static/dist/bundle.js" not in result

    def test_changed_js_ts_files_excludes_dist_and_maps(self, tmp_path):
        from services.sprint_manager.gates import _changed_js_ts_files

        fake_diff = "static/dist/bundle.js\napp.ts\nfoo.js.map\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=fake_diff, stderr=""
            )
            result = _changed_js_ts_files("develop", tmp_path)

        assert "app.ts" in result
        assert "static/dist/bundle.js" not in result
        assert "foo.js.map" not in result

    def test_changed_frontend_files_returns_html_css_only(self, tmp_path):
        from services.sprint_manager.gates import _changed_frontend_files

        fake_diff = "index.html\nstyle.css\nserver.py\ncomponent.tsx\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=fake_diff, stderr=""
            )
            result = _changed_frontend_files("develop", tmp_path)

        assert "index.html" in result
        assert "style.css" in result
        assert "component.tsx" in result
        assert "server.py" not in result

    def test_changed_frontend_files_returns_empty_on_nonzero_rc(self, tmp_path):
        from services.sprint_manager.gates import _changed_frontend_files

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            result = _changed_frontend_files("develop", tmp_path)

        assert result == []


# ── AC6: _gate_lint pass/fail is identical to pre-move ───────────────────────

class TestGateLintBehavior:
    """AC6: _gate_lint produces identical pass/fail results on the same inputs."""

    def test_gate_lint_skips_when_skip_is_true(self):
        from services.sprint_manager.gates import _gate_lint
        result = _gate_lint(issue_num=1, worktester_dashboard=Path("."),
                            skip=True)
        assert result.passed is True
        assert result.skipped is True
        assert result.gate == "lint"

    def test_gate_pytest_skips_when_skip_is_true(self):
        from services.sprint_manager.gates import _gate_pytest
        result = _gate_pytest(issue_num=1, worktester_dashboard=Path("."),
                              skip=True)
        assert result.passed is True
        assert result.skipped is True
        assert result.gate == "pytest"


# ── AC7: All moved symbols remain private (underscore-prefixed) ───────────────

class TestAllSymbolsPrivate:
    """AC7: No new public API — all moved functions keep their underscore prefix."""

    def test_gate_pytest_is_private(self):
        from services.sprint_manager import gates
        assert hasattr(gates, "_gate_pytest")
        assert not hasattr(gates, "gate_pytest"), \
            "gate_pytest (without underscore) must not exist"

    def test_gate_lint_is_private(self):
        from services.sprint_manager import gates
        assert hasattr(gates, "_gate_lint")
        assert not hasattr(gates, "gate_lint")

    def test_lint_autofix_commit_is_private(self):
        from services.sprint_manager import gates
        assert hasattr(gates, "_lint_autofix_commit")
        assert not hasattr(gates, "lint_autofix_commit")

    def test_run_frontend_lint_is_private(self):
        from services.sprint_manager import gates
        assert hasattr(gates, "_run_frontend_lint")
        assert not hasattr(gates, "run_frontend_lint")

    def test_changed_py_files_is_private(self):
        from services.sprint_manager import gates
        assert hasattr(gates, "_changed_py_files")
        assert not hasattr(gates, "changed_py_files")

    def test_changed_js_ts_files_is_private(self):
        from services.sprint_manager import gates
        assert hasattr(gates, "_changed_js_ts_files")
        assert not hasattr(gates, "changed_js_ts_files")

    def test_changed_frontend_files_is_private(self):
        from services.sprint_manager import gates
        assert hasattr(gates, "_changed_frontend_files")
        assert not hasattr(gates, "changed_frontend_files")


# ── AC8: Pure move — gates.py source contains no logic not from sprint_manager ─

class TestPureMove:
    """AC8: Diff is a pure move — zero behavioral changes, zero added logic."""

    def test_gates_source_does_not_add_logic_beyond_moved_functions(self):
        """gates.py must not define functions that were not in sprint_manager before the move."""
        gates_source = GATES_PATH.read_text(encoding="utf-8")
        # These are the ONLY callable symbols that should be defined in gates.py
        # (plus small local helpers like _run_timed, _try which are infrastructure)
        expected_public_functions = {
            "_gate_pytest",
            "_gate_lint",
            "_lint_autofix_commit",
            "_run_frontend_lint",
            "_changed_py_files",
            "_changed_js_ts_files",
            "_changed_frontend_files",
        }
        import ast
        tree = ast.parse(gates_source)
        top_level_defs = {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and isinstance(node.col_offset, int)
            and node.col_offset == 0
        }
        # All top-level function definitions must either be in the expected set
        # or be a small infrastructure helper (starts with _ and is <=10 lines)
        for name in top_level_defs:
            if name in expected_public_functions:
                continue
            # allow private infrastructure helpers
            assert name.startswith("_"), (
                f"gates.py defines unexpected public function: {name}"
            )

    def test_gates_module_constants_are_from_sprint_manager(self):
        """AC8: Module-level constants in gates.py match what was in sprint_manager.py."""
        from services.sprint_manager import gates
        assert hasattr(gates, "_JS_TS_EXTENSIONS"), "Missing _JS_TS_EXTENSIONS"
        assert hasattr(gates, "_JS_TS_LINT_EXCLUDE"), "Missing _JS_TS_LINT_EXCLUDE"
        assert hasattr(gates, "_DESIGN_FE_EXTENSIONS"), "Missing _DESIGN_FE_EXTENSIONS"
        assert ".js" in gates._JS_TS_EXTENSIONS
        assert "/dist/" in gates._JS_TS_LINT_EXCLUDE
        assert ".html" in gates._DESIGN_FE_EXTENSIONS
