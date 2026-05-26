"""Tests for issue #48: fix(gates): scope pytest and lint gates to changed files only.

Risk: MEDIUM — up to 2 tests per criterion (happy path + 1 obvious edge).

These tests import sprint_manager directly (no live server needed) and use
temporary git repos or mocks to exercise the changed-file discovery helper
and both gates.
"""
from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
SPRINT_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    """Import sprint_manager.py as a module, stubbing optional deps."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment = lambda *a, **kw: None
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_48_import"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a git repo with an initial commit on 'develop' and a feature branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # git init without -b (compatible with git < 2.28)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo,
                   check=True, capture_output=True)

    # Initial commit on default branch, then rename to 'develop'
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True,
                   capture_output=True)
    # Rename current branch to 'develop' (works on any git version)
    subprocess.run(["git", "branch", "-m", "develop"], cwd=repo, check=True,
                   capture_output=True)

    # Feature branch off develop
    subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo,
                   check=True, capture_output=True)
    return repo


# ---------------------------------------------------------------------------
# AC-1: _changed_py_files helper exists and returns correct files
# ---------------------------------------------------------------------------

class TestChangedPyFilesHelper:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__helper_exists(self):
        """AC-1: _changed_py_files exists in sprint_manager and is callable."""
        sm = _import_sprint_manager()
        assert hasattr(sm, "_changed_py_files"), (
            "_changed_py_files not found in sprint_manager"
        )
        assert callable(sm._changed_py_files)

    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__helper_returns_py_only(self, tmp_path):
        """AC-1: _changed_py_files returns only .py paths relative to base_branch."""
        sm = _import_sprint_manager()
        repo = _make_git_repo(tmp_path)

        # Add a .py file and a .md file on the feature branch
        (repo / "server.py").write_text("x = 1\n")
        (repo / "notes.md").write_text("docs\n")
        subprocess.run(["git", "add", "server.py", "notes.md"], cwd=repo,
                       check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add files"], cwd=repo,
                       check=True, capture_output=True)

        result = sm._changed_py_files("develop", cwd=repo)
        assert "server.py" in result, f"Expected server.py in {result}"
        assert all(f.endswith(".py") for f in result), (
            f"Non-.py file returned: {result}"
        )
        assert "notes.md" not in result, "notes.md should not appear in result"


# ---------------------------------------------------------------------------
# AC-2 & AC-3: Lint gate is scoped; no .py files → immediate pass
# ---------------------------------------------------------------------------

class TestLintGateScoped:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__lint_no_py_passes(self):
        """AC-2 & AC-3: Zero .py files changed → lint gate passes with 'no Python files changed'."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_changed_py_files", return_value=[]):
            result = sm._gate_lint(
                issue_num=9999,
                worktester_dashboard=Path("/tmp"),
                skip=False,
                base_branch="develop",
                gate_scope="changed",
            )
        assert result.passed is True
        assert "no Python files changed" in result.output

    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__lint_skips_untouched_files(self, tmp_path):
        """AC-2: gate only checks the files from _changed_py_files, not the whole repo."""
        sm = _import_sprint_manager()

        # Clean file — ruff should pass on it
        clean_file = tmp_path / "clean.py"
        clean_file.write_text("x = 1\n")

        captured_cmds = []

        original_run_timed = sm._run_timed

        def spy_run_timed(*cmd, cwd=None):
            captured_cmds.append(list(cmd))
            return original_run_timed(*cmd, cwd=cwd)

        with mock.patch.object(sm, "_changed_py_files", return_value=["clean.py"]):
            with mock.patch.object(sm, "_run_timed", side_effect=spy_run_timed):
                with mock.patch.object(sm, "_try", return_value=(True, "ruff", "")):
                    sm._gate_lint(
                        issue_num=9999,
                        worktester_dashboard=tmp_path,
                        skip=False,
                        base_branch="develop",
                        gate_scope="changed",
                    )

        # Should NOT call ruff with "." (full-scope style)
        for cmd in captured_cmds:
            assert "." not in cmd or "check" not in cmd, (
                f"Lint gate used full-scope 'ruff check .' instead of scoped files: {cmd}"
            )
        # Should pass "clean.py" to ruff
        assert any("clean.py" in cmd for cmd in captured_cmds), (
            f"Expected clean.py to be passed to ruff, got: {captured_cmds}"
        )


# ---------------------------------------------------------------------------
# AC-4 & AC-5: Pytest gate is scoped; no test files → immediate pass
# ---------------------------------------------------------------------------

class TestPytestGateScoped:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__pytest_no_tests_passes(self):
        """AC-4 & AC-5: Zero test files changed → pytest gate passes with 'no test files changed'."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_changed_py_files", return_value=[]):
            result = sm._gate_pytest(
                issue_num=9999,
                worktester_dashboard=Path("/tmp"),
                skip=False,
                base_branch="develop",
                gate_scope="changed",
            )
        assert result.passed is True
        assert "no test files changed" in result.output

    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__pytest_filters_to_tests_dir(self):
        """AC-4: Only files under tests/ are passed to pytest; source files are excluded."""
        sm = _import_sprint_manager()

        # Changed files include a source file but NO test file
        changed = ["server.py", "scripts/sprint_manager.py"]

        with mock.patch.object(sm, "_changed_py_files", return_value=changed):
            result = sm._gate_pytest(
                issue_num=9999,
                worktester_dashboard=Path("/tmp"),
                skip=False,
                base_branch="develop",
                gate_scope="changed",
            )
        assert result.passed is True
        assert "no test files changed" in result.output


# ---------------------------------------------------------------------------
# AC-6: New test failure IS caught
# ---------------------------------------------------------------------------

class TestPytestGateCatchesNewFailures:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__new_failure_caught(self, tmp_path):
        """AC-6: A failing test in a changed test file causes GateResult(passed=False)."""
        sm = _import_sprint_manager()

        # Create a test file with a deliberate failure
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        bad_test = tests_dir / "test_deliberate_fail.py"
        bad_test.write_text(textwrap.dedent("""\
            def test_always_fails():
                assert False, "deliberate failure for gate test"
        """))

        changed = ["tests/test_deliberate_fail.py"]

        with mock.patch.object(sm, "_changed_py_files", return_value=changed):
            with mock.patch.object(sm, "_revert_to_sit"):
                result = sm._gate_pytest(
                    issue_num=9999,
                    worktester_dashboard=tmp_path,
                    skip=False,
                    base_branch="develop",
                    gate_scope="changed",
                )

        assert result.passed is False, (
            f"Pytest gate should FAIL on deliberate failure, output: {result.output}"
        )


# ---------------------------------------------------------------------------
# AC-7: New lint error IS caught
# ---------------------------------------------------------------------------

class TestLintGateCatchesNewErrors:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__new_lint_error_caught(self, tmp_path):
        """AC-7: A lint error in a changed file causes GateResult(passed=False)."""
        sm = _import_sprint_manager()

        # Write a file with a deliberate E741 ambiguous variable name
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("l = 1\n")  # E741

        with mock.patch.object(sm, "_changed_py_files", return_value=["bad.py"]):
            with mock.patch.object(sm, "_revert_to_sit"):
                result = sm._gate_lint(
                    issue_num=9999,
                    worktester_dashboard=tmp_path,
                    skip=False,
                    base_branch="develop",
                    gate_scope="changed",
                )
        if "ruff not found" in result.output:
            pytest.skip("ruff not available in this environment")
        assert not result.passed, (
            f"Lint gate should FAIL on E741 in changed file, output: {result.output}"
        )


# ---------------------------------------------------------------------------
# AC-8: Gate output lists checked files
# ---------------------------------------------------------------------------

class TestGateOutputListsFiles:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__lint_logs_file_list(self, tmp_path, capsys):
        """AC-8: _gate_lint prints 'checking N file(s): <names>' to stdout."""
        sm = _import_sprint_manager()

        py_file = tmp_path / "server.py"
        py_file.write_text("x = 1\n")

        with mock.patch.object(sm, "_changed_py_files", return_value=["server.py"]):
            with mock.patch.object(sm, "_revert_to_sit"):
                sm._gate_lint(
                    issue_num=9999,
                    worktester_dashboard=tmp_path,
                    skip=False,
                    base_branch="develop",
                    gate_scope="changed",
                )

        captured = capsys.readouterr()
        assert "checking" in captured.out and "file(s)" in captured.out, (
            f"Expected '[gate:lint] checking N file(s):...' in output, got: {captured.out!r}"
        )
        assert "server.py" in captured.out

    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__pytest_logs_file_list(self, tmp_path, capsys):
        """AC-8: _gate_pytest prints 'checking N file(s): <names>' to stdout."""
        sm = _import_sprint_manager()

        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_example.py"
        test_file.write_text("def test_ok(): pass\n")

        changed = ["tests/test_example.py"]

        with mock.patch.object(sm, "_changed_py_files", return_value=changed):
            with mock.patch.object(sm, "_revert_to_sit"):
                sm._gate_pytest(
                    issue_num=9999,
                    worktester_dashboard=tmp_path,
                    skip=False,
                    base_branch="develop",
                    gate_scope="changed",
                )

        captured = capsys.readouterr()
        assert "checking" in captured.out and "file(s)" in captured.out, (
            f"Expected '[gate:pytest] checking N file(s):...' in output, got: {captured.out!r}"
        )
        assert "test_example.py" in captured.out


# ---------------------------------------------------------------------------
# AC-9: --gate-scope full restores legacy behaviour
# ---------------------------------------------------------------------------

class TestGateScopeFullFlag:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__cli_flag_exists(self):
        """AC-9: --gate-scope with choices 'changed'/'full' is declared in argparse."""
        content = SPRINT_SCRIPT.read_text()
        assert "--gate-scope" in content, (
            "--gate-scope argument not found in sprint_manager.py"
        )
        assert "full" in content, (
            "'full' choice not found for --gate-scope in sprint_manager.py"
        )
        assert "changed" in content, (
            "'changed' choice not found for --gate-scope in sprint_manager.py"
        )

    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__full_scope_runs_ruff_dot(self, tmp_path):
        """AC-9: gate_scope='full' causes _gate_lint to run ruff against '.' (full repo)."""
        sm = _import_sprint_manager()

        captured_cmd = []

        def fake_run_timed(*cmd, cwd=None):
            captured_cmd.append(list(cmd))
            return 0, "", ""

        with mock.patch.object(sm, "_run_timed", side_effect=fake_run_timed):
            with mock.patch.object(sm, "_try", return_value=(True, "ruff", "")):
                sm._gate_lint(
                    issue_num=9999,
                    worktester_dashboard=tmp_path,
                    skip=False,
                    base_branch="develop",
                    gate_scope="full",
                )

        # The 'full' scope should call ruff with "." not a specific file list
        assert any("." in cmd for cmd in captured_cmd), (
            f"Expected 'ruff check .' in full scope, got commands: {captured_cmd}"
        )


# ---------------------------------------------------------------------------
# AC-10: Existing regression tests still pass (structural check)
# ---------------------------------------------------------------------------

class TestRegressionAC10:
    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__regression_file_intact(self):
        """AC-10: test_sprint_manager_failure_handling__24.py still exists and is non-empty."""
        regression_file = (
            Path(__file__).parent
            / "test_sprint_manager_failure_handling__24.py"
        )
        assert regression_file.exists(), (
            "test_sprint_manager_failure_handling__24.py must still exist (AC-10)"
        )
        assert regression_file.stat().st_size > 0, "Regression test file is empty"

    def test_fix_gates_scope_pytest_and_lint_gates_to_changed__core_symbols_intact(self):
        """AC-10: GateResult, _gate_pytest, _gate_lint, _changed_py_files still in module."""
        sm = _import_sprint_manager()
        for symbol in ("GateResult", "_gate_pytest", "_gate_lint", "_changed_py_files"):
            assert hasattr(sm, symbol), (
                f"Symbol {symbol!r} missing from sprint_manager — regression in AC-10"
            )
