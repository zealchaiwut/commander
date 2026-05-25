"""Tests for issue #48 — scope pytest and lint gates to changed files only.

All tests are static (no live server required) and exercise the new
_changed_py_files(), _gate_pytest(), and _gate_lint() helpers plus the
--gate-scope CLI flag.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR   = Path(__file__).parent.parent / "scripts"
SPRINT_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    """Import sprint_manager.py as a module, mocking dotenv and github_client."""
    import importlib, importlib.util

    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_48_test_import"
    spec     = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod      = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-1: _changed_py_files helper exists and works
# ---------------------------------------------------------------------------

class TestChangedPyFilesHelper:
    def test_function_exists_in_sprint_manager(self):
        content = SPRINT_SCRIPT.read_text()
        assert "_changed_py_files" in content, \
            "_changed_py_files must be defined in sprint_manager.py"

    def test_function_is_callable(self):
        sm = _import_sprint_manager()
        assert callable(sm._changed_py_files)

    def test_function_signature_accepts_base_branch_and_cwd(self, tmp_path):
        """_changed_py_files(base_branch, cwd) must be callable with those args."""
        sm = _import_sprint_manager()
        # In a non-git dir, git diff will fail (rc!=0) → returns []
        result = sm._changed_py_files("develop", cwd=tmp_path)
        assert isinstance(result, list)

    def test_returns_only_py_files(self, tmp_path):
        """Only .py files should be returned, not .md or .yaml files."""
        sm = _import_sprint_manager()

        fake_output = "server.py\nREADME.md\ntests/test_foo.py\nconfig.yaml\n"

        with mock.patch.object(sm, "_run_timed", return_value=(0, fake_output, "")) as m:
            result = sm._changed_py_files("develop", cwd=tmp_path)

        assert "server.py" in result
        assert "tests/test_foo.py" in result
        assert "README.md" not in result
        assert "config.yaml" not in result

    def test_returns_empty_list_on_git_error(self, tmp_path):
        """If git diff fails (rc != 0), return empty list."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_run_timed", return_value=(1, "", "error")):
            result = sm._changed_py_files("develop", cwd=tmp_path)

        assert result == []

    def test_uses_diff_filter_acm(self):
        """git diff command must include --diff-filter=ACM."""
        content = SPRINT_SCRIPT.read_text()
        assert "--diff-filter=ACM" in content, \
            "_changed_py_files must use --diff-filter=ACM to find added/modified files"

    def test_uses_name_only_flag(self):
        """git diff command must include --name-only."""
        content = SPRINT_SCRIPT.read_text()
        assert "--name-only" in content, \
            "_changed_py_files must use --name-only to get file paths"


# ---------------------------------------------------------------------------
# AC-2 & AC-3: Lint gate scoped / passes when no .py files changed
# ---------------------------------------------------------------------------

class TestLintGateScoped:
    def test_gate_lint_accepts_base_branch_and_gate_scope(self):
        """_gate_lint() must accept base_branch and gate_scope parameters."""
        import inspect
        sm = _import_sprint_manager()
        sig = inspect.signature(sm._gate_lint)
        assert "base_branch" in sig.parameters, \
            "_gate_lint must have a base_branch parameter"
        assert "gate_scope" in sig.parameters, \
            "_gate_lint must have a gate_scope parameter"

    def test_lint_gate_passes_when_no_py_files_changed(self, tmp_path):
        """AC-3: If no .py files changed, gate passes immediately without ruff."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_changed_py_files", return_value=[]) as mock_changed, \
             mock.patch.object(sm, "_run_timed") as mock_run, \
             mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/ruff", "")):
            result = sm._gate_lint(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="changed",
                base_branch="develop",
            )

        assert result.passed is True
        assert "no Python files changed" in result.output
        # ruff must NOT have been invoked
        mock_run.assert_not_called()

    def test_lint_gate_logs_checked_files(self, tmp_path, capsys):
        """AC-8: Gate output must list the files being checked."""
        sm = _import_sprint_manager()
        py_files = ["server.py", "api/routes.py"]

        with mock.patch.object(sm, "_changed_py_files", return_value=py_files), \
             mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/ruff", "")), \
             mock.patch.object(sm, "_run_timed", return_value=(0, "", "")), \
             mock.patch.object(sm, "_post_agent_event"):
            result = sm._gate_lint(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="changed",
                base_branch="develop",
            )

        captured = capsys.readouterr()
        assert "server.py" in captured.out
        assert "api/routes.py" in captured.out
        assert result.passed is True

    def test_lint_gate_full_scope_uses_dot(self, tmp_path):
        """AC-9: --gate-scope full must invoke ruff check ."""
        sm = _import_sprint_manager()
        captured_args = []

        def fake_run_timed(*args, **kwargs):
            captured_args.extend(args)
            return (0, "", "")

        with mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/ruff", "")), \
             mock.patch.object(sm, "_run_timed", side_effect=fake_run_timed), \
             mock.patch.object(sm, "_post_agent_event"):
            result = sm._gate_lint(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="full",
            )

        assert result.passed is True
        # "." should be the target, not a file list
        assert "." in captured_args

    def test_lint_gate_catches_new_error_in_changed_file(self, tmp_path):
        """AC-7: If coder introduces a lint error, gate must fail."""
        sm = _import_sprint_manager()
        py_files = ["server.py"]
        ruff_output = "server.py:10:5: E741 Ambiguous variable name: `l`"

        with mock.patch.object(sm, "_changed_py_files", return_value=py_files), \
             mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/ruff", "")), \
             mock.patch.object(sm, "_run_timed", return_value=(1, ruff_output, "")), \
             mock.patch.object(sm, "_revert_to_sit"), \
             mock.patch.object(sm, "_post_agent_event"):
            result = sm._gate_lint(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="changed",
                base_branch="develop",
            )

        assert result.passed is False
        assert "E741" in result.output

    def test_lint_gate_skipped_when_skip_true(self, tmp_path):
        """If skip=True, gate returns passed=True with skipped=True."""
        sm = _import_sprint_manager()
        result = sm._gate_lint(
            issue_num=1,
            worktester_dashboard=tmp_path,
            skip=True,
        )
        assert result.passed is True
        assert result.skipped is True


# ---------------------------------------------------------------------------
# AC-4 & AC-5: Pytest gate scoped / passes when no test files changed
# ---------------------------------------------------------------------------

class TestPytestGateScoped:
    def test_gate_pytest_accepts_base_branch_and_gate_scope(self):
        """_gate_pytest() must accept base_branch and gate_scope parameters."""
        import inspect
        sm = _import_sprint_manager()
        sig = inspect.signature(sm._gate_pytest)
        assert "base_branch" in sig.parameters, \
            "_gate_pytest must have a base_branch parameter"
        assert "gate_scope" in sig.parameters, \
            "_gate_pytest must have a gate_scope parameter"

    def test_pytest_gate_passes_when_no_test_files_changed(self, tmp_path):
        """AC-5: If no test files changed, gate passes without invoking pytest."""
        sm = _import_sprint_manager()
        # Changed files contain only non-test python files
        changed_files = ["server.py", "api/routes.py"]

        with mock.patch.object(sm, "_changed_py_files", return_value=changed_files) as mock_changed, \
             mock.patch.object(sm, "_run_timed") as mock_run, \
             mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/pytest", "")):
            result = sm._gate_pytest(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="changed",
                base_branch="develop",
            )

        assert result.passed is True
        assert "no test files changed" in result.output
        mock_run.assert_not_called()

    def test_pytest_gate_only_runs_test_files(self, tmp_path):
        """AC-4: Only test files under tests/ should be passed to pytest."""
        sm = _import_sprint_manager()
        changed_files = ["server.py", "tests/test_foo__1.py", "tests/test_bar__2.py"]
        captured_args = []

        def fake_run_timed(*args, **kwargs):
            captured_args.extend(args)
            return (0, "", "")

        with mock.patch.object(sm, "_changed_py_files", return_value=changed_files), \
             mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/pytest", "")), \
             mock.patch.object(sm, "_run_timed", side_effect=fake_run_timed), \
             mock.patch.object(sm, "_post_agent_event"):
            result = sm._gate_pytest(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="changed",
                base_branch="develop",
            )

        assert result.passed is True
        # server.py must NOT be in the pytest args
        assert "server.py" not in captured_args
        # test files must be included
        assert "tests/test_foo__1.py" in captured_args
        assert "tests/test_bar__2.py" in captured_args

    def test_pytest_gate_logs_checked_files(self, tmp_path, capsys):
        """AC-8: Gate output must list the test files being checked."""
        sm = _import_sprint_manager()
        changed_files = ["tests/test_my_feature__48.py"]

        with mock.patch.object(sm, "_changed_py_files", return_value=changed_files), \
             mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/pytest", "")), \
             mock.patch.object(sm, "_run_timed", return_value=(0, "", "")), \
             mock.patch.object(sm, "_post_agent_event"):
            result = sm._gate_pytest(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="changed",
                base_branch="develop",
            )

        captured = capsys.readouterr()
        assert "tests/test_my_feature__48.py" in captured.out
        assert result.passed is True

    def test_pytest_gate_full_scope_runs_all(self, tmp_path):
        """AC-9: --gate-scope full must invoke pytest -x without a file list."""
        sm = _import_sprint_manager()
        captured_args = []

        def fake_run_timed(*args, **kwargs):
            captured_args.extend(args)
            return (0, "", "")

        with mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/pytest", "")), \
             mock.patch.object(sm, "_run_timed", side_effect=fake_run_timed), \
             mock.patch.object(sm, "_post_agent_event"):
            result = sm._gate_pytest(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="full",
            )

        assert result.passed is True
        # In full mode, no extra file args — just "-x"
        # (pytest_bin and "-x" are the only positional args)
        assert "-x" in captured_args
        # No "tests/..." path should be appended
        test_paths = [a for a in captured_args if a.startswith("tests/")]
        assert test_paths == [], f"full scope should not pass test file paths: {test_paths}"

    def test_pytest_gate_catches_new_failure_in_changed_test(self, tmp_path):
        """AC-6: If coder introduces a failing test, gate must fail."""
        sm = _import_sprint_manager()
        changed_files = ["tests/test_new_feature__48.py"]
        pytest_output = "FAILED tests/test_new_feature__48.py::test_something - AssertionError"

        with mock.patch.object(sm, "_changed_py_files", return_value=changed_files), \
             mock.patch.object(sm, "_try", return_value=(True, "/usr/bin/pytest", "")), \
             mock.patch.object(sm, "_run_timed", return_value=(1, pytest_output, "")), \
             mock.patch.object(sm, "_revert_to_sit"), \
             mock.patch.object(sm, "_post_agent_event"):
            result = sm._gate_pytest(
                issue_num=1,
                worktester_dashboard=tmp_path,
                skip=False,
                gate_scope="changed",
                base_branch="develop",
            )

        assert result.passed is False
        assert "AssertionError" in result.output

    def test_pytest_gate_skipped_when_skip_true(self, tmp_path):
        """If skip=True, gate returns passed=True with skipped=True."""
        sm = _import_sprint_manager()
        result = sm._gate_pytest(
            issue_num=1,
            worktester_dashboard=tmp_path,
            skip=True,
        )
        assert result.passed is True
        assert result.skipped is True


# ---------------------------------------------------------------------------
# AC-9: --gate-scope CLI flag
# ---------------------------------------------------------------------------

class TestGateScopeCLIFlag:
    def test_gate_scope_flag_present_in_source(self):
        content = SPRINT_SCRIPT.read_text()
        assert "--gate-scope" in content, \
            "--gate-scope flag must be defined in sprint_manager.py CLI"

    def test_gate_scope_choices_are_changed_and_full(self):
        content = SPRINT_SCRIPT.read_text()
        assert "changed" in content
        assert "full" in content

    def test_gate_scope_default_is_changed(self):
        content = SPRINT_SCRIPT.read_text()
        # The default value should be 'changed'
        assert '"changed"' in content or "'changed'" in content

    def test_run_sprint_accepts_gate_scope(self):
        """run_sprint() must accept a gate_scope parameter."""
        import inspect
        sm = _import_sprint_manager()
        sig = inspect.signature(sm.run_sprint)
        assert "gate_scope" in sig.parameters, \
            "run_sprint() must have a gate_scope parameter"

    def test_handle_post_tester_accepts_gate_scope(self):
        """handle_post_tester() must accept gate_scope and base_branch parameters."""
        import inspect
        sm = _import_sprint_manager()
        sig = inspect.signature(sm.handle_post_tester)
        assert "gate_scope" in sig.parameters, \
            "handle_post_tester() must have a gate_scope parameter"
        assert "base_branch" in sig.parameters, \
            "handle_post_tester() must have a base_branch parameter"

    def test_run_quality_gates_accepts_gate_scope(self):
        """_run_quality_gates() must accept gate_scope and base_branch parameters."""
        import inspect
        sm = _import_sprint_manager()
        sig = inspect.signature(sm._run_quality_gates)
        assert "gate_scope" in sig.parameters, \
            "_run_quality_gates() must have a gate_scope parameter"
        assert "base_branch" in sig.parameters, \
            "_run_quality_gates() must have a base_branch parameter"


# ---------------------------------------------------------------------------
# AC-10: Regression — existing tests in __24 still pass (smoke check)
# ---------------------------------------------------------------------------

class TestRegressionSmoke:
    def test_gate_result_dataclass_unchanged(self):
        """GateResult must still have gate, passed, skipped, output fields."""
        sm = _import_sprint_manager()
        gr = sm.GateResult(gate="pytest", passed=True)
        assert gr.gate == "pytest"
        assert gr.passed is True
        assert gr.skipped is False
        assert gr.output == ""

    def test_failure_categories_unchanged(self):
        sm = _import_sprint_manager()
        fc = sm.FailureCategory
        assert fc.HANG            == "HANG"
        assert fc.CRASH           == "CRASH"
        assert fc.GATE_FAIL       == "GATE_FAIL"
        assert fc.TESTER_REJECTED == "TESTER_REJECTED"
        assert fc.RETRY_EXHAUSTED == "RETRY_EXHAUSTED"

    def test_gate_lint_still_skips_when_skip_true(self, tmp_path):
        sm = _import_sprint_manager()
        result = sm._gate_lint(
            issue_num=99,
            worktester_dashboard=tmp_path,
            skip=True,
        )
        assert result.passed is True
        assert result.skipped is True

    def test_gate_pytest_still_skips_when_skip_true(self, tmp_path):
        sm = _import_sprint_manager()
        result = sm._gate_pytest(
            issue_num=99,
            worktester_dashboard=tmp_path,
            skip=True,
        )
        assert result.passed is True
        assert result.skipped is True
