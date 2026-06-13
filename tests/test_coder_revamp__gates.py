"""Tests for new quality gates: typecheck, design, frontend lint.

Also verifies gate ordering in _run_quality_gates and env-var toggles.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

from sprint_manager import (
    FailureCategory,
    GateResult,
    _LOGIC_FAILURE_CATEGORIES,
    _TimestampedStdout,
    _changed_js_ts_files,
    _changed_py_files,
    _design_error_findings,
    _gate_design,
    _gate_lint,
    _gate_pytest,
    _gate_typecheck,
    _is_test_path,
    _run_quality_gates,
)


class TestTimestampedStdout:
    """Orchestrator stdout is prefixed with a local [HH:MM:SS] stamp, one per
    line, so operators can read when each step started (coder dispatch, etc.)."""

    def test_one_stamp_per_line(self):
        import io
        import re
        buf = io.StringIO()
        ts = _TimestampedStdout(buf)
        ts.write("  Dispatching coder for issue #880 ...\n")
        lines = buf.getvalue().splitlines()
        assert re.match(r"^\[\d{2}:\d{2}:\d{2}\]   Dispatching coder", lines[0])

    def test_partial_writes_coalesce_into_one_line(self):
        import io
        import re
        buf = io.StringIO()
        ts = _TimestampedStdout(buf)
        ts.write("partial")          # no newline yet
        ts.write(" continues\n")     # completes the line — single stamp only
        lines = buf.getvalue().splitlines()
        assert len(lines) == 1
        assert re.match(r"^\[\d{2}:\d{2}:\d{2}\] partial continues$", lines[0])

    def test_passthrough_attrs_and_flush(self):
        import io
        buf = io.StringIO()
        ts = _TimestampedStdout(buf)
        ts.write("x\n")
        ts.flush()  # must not raise
        # __getattr__ delegates unknown attrs to the wrapped stream
        assert ts.getvalue() == buf.getvalue()


class TestDesignFixRoundRouting:
    """A design-gate failure is recoverable: it routes to the coder fix-round
    loop (DESIGN_FAIL is a logic category) instead of terminally dropping."""

    def test_design_fail_is_a_logic_category(self):
        assert FailureCategory.DESIGN_FAIL in _LOGIC_FAILURE_CATEGORIES, (
            "design failures must re-dispatch the coder via the fix-round loop"
        )

    def test_parser_partitions_error_vs_warning(self):
        errs, warns = _design_error_findings(
            '[{"severity": "error"}, {"severity": "warning"}, {"severity": "warning"}]'
        )
        assert len(errs) == 1 and warns == 2

    def test_parser_tolerates_non_json(self):
        assert _design_error_findings("npm warn ...\nno json here") == ([], 0)
        assert _design_error_findings("") == ([], 0)


# ── helpers ────────────────────────────────────────────────────────────────────

def _fake_run(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── _changed_js_ts_files ──────────────────────────────────────────────────────

def _touch(root: Path, *rel_paths: str) -> None:
    """Create empty files under root so the existence filter keeps them."""
    for rel in rel_paths:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")


class TestChangedJsTsFiles:
    """The changed-file helpers return ABSOLUTE paths to files that EXIST in the
    worktree. git diff yields repo-root-relative paths; the helper anchors them to
    the repo root (here mocked to tmp_path) and drops anything missing, so a tool
    run from any cwd can find them (regression: sprint-66.1 stuck loop, #880)."""

    def test_returns_js_ts_files_as_absolute_existing(self, tmp_path):
        _touch(tmp_path, "app.js", "utils.ts", "server.py", "README.md")
        with patch("sprint_manager._git_toplevel", return_value=tmp_path), \
             patch("sprint_manager._run_timed",
                   return_value=(0, "app.js\nutils.ts\nserver.py\nREADME.md\n", "")):
            result = _changed_js_ts_files("develop", tmp_path)
        assert set(result) == {str(tmp_path / "app.js"), str(tmp_path / "utils.ts")}

    def test_returns_empty_on_no_js_ts(self, tmp_path):
        _touch(tmp_path, "server.py", "style.css")
        with patch("sprint_manager._git_toplevel", return_value=tmp_path), \
             patch("sprint_manager._run_timed", return_value=(0, "server.py\nstyle.css\n", "")):
            result = _changed_js_ts_files("develop", tmp_path)
        assert result == []

    def test_returns_empty_on_git_error(self, tmp_path):
        with patch("sprint_manager._run_timed", return_value=(1, "", "fatal: not a git repo")):
            result = _changed_js_ts_files("develop", tmp_path)
        assert result == []

    def test_includes_jsx_tsx_mjs(self, tmp_path):
        _touch(tmp_path, "component.jsx", "page.tsx", "module.mjs")
        with patch("sprint_manager._git_toplevel", return_value=tmp_path), \
             patch("sprint_manager._run_timed",
                   return_value=(0, "component.jsx\npage.tsx\nmodule.mjs\n", "")):
            result = _changed_js_ts_files("develop", tmp_path)
        assert set(result) == {
            str(tmp_path / "component.jsx"),
            str(tmp_path / "page.tsx"),
            str(tmp_path / "module.mjs"),
        }


class TestChangedPyFilesAnchoring:
    """Root cause of the sprint-66.1 stuck loop: git diff (run in the apps/dashboard
    subdir) returns repo-root paths like ``tests/foo.py``; the gate ran pytest from
    the subdir, so the path resolved to apps/dashboard/tests/foo.py -> "file not
    found" -> the gate failed forever for a ticket whose tests actually pass."""

    def test_repo_root_test_resolved_to_absolute(self, tmp_path):
        # File lives at repo-root tests/, NOT under apps/dashboard.
        _touch(tmp_path, "tests/test_880__milestone.py")
        # git diff is invoked in the subdir but returns the repo-root path.
        subdir = tmp_path / "apps" / "dashboard"
        subdir.mkdir(parents=True)
        with patch("sprint_manager._git_toplevel", return_value=tmp_path), \
             patch("sprint_manager._run_timed",
                   return_value=(0, "tests/test_880__milestone.py\n", "")):
            result = _changed_py_files("develop", subdir)
        assert result == [str(tmp_path / "tests" / "test_880__milestone.py")]

    def test_phantom_renamed_away_path_is_dropped(self, tmp_path):
        # git diff lists a path that no longer exists in the worktree (renamed away).
        with patch("sprint_manager._git_toplevel", return_value=tmp_path), \
             patch("sprint_manager._run_timed",
                   return_value=(0, "tests/test_gone.py\nserver.py\n", "")):
            result = _changed_py_files("develop", tmp_path)
        assert result == []  # neither file exists -> both dropped, no phantom path


class TestIsTestPath:
    """The pytest gate must recognise tests in any tests/ dir, not only repo-root
    ``tests/`` (the old startswith('tests/') check silently skipped
    apps/dashboard/tests/)."""

    @pytest.mark.parametrize("path", [
        "tests/test_x.py",
        "apps/dashboard/tests/test_y.py",
        "/abs/repo/tests/test_z.py",
        "pkg/foo_test.py",
        "test_top.py",
    ])
    def test_recognised_as_test(self, path):
        assert _is_test_path(path) is True

    @pytest.mark.parametrize("path", [
        "server.py",
        "apps/dashboard/routers/status.py",
        "tooling/helper.py",
    ])
    def test_not_a_test(self, path):
        assert _is_test_path(path) is False


class TestGatePytestPathRobustness:
    """The pytest gate skips gracefully (never fails) when changed test files do
    not exist, and runs only real test files when they do."""

    def test_skips_when_no_changed_test_files_exist(self, tmp_path):
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/pytest", "")), \
             patch("sprint_manager._changed_py_files", return_value=[]):
            result = _gate_pytest(880, tmp_path, skip=False, base_branch="develop")
        assert result.passed is True
        assert "no test files changed" in (result.output or "")

    def test_runs_only_changed_test_files(self, tmp_path):
        test_abs = str(tmp_path / "tests" / "test_880.py")
        nontest_abs = str(tmp_path / "server.py")
        captured = {}

        def fake_run_timed(*args, **kwargs):
            captured["args"] = args
            return (0, "1 passed", "")

        with patch("sprint_manager._try", return_value=(True, "/usr/bin/pytest", "")), \
             patch("sprint_manager._changed_py_files", return_value=[nontest_abs, test_abs]), \
             patch("sprint_manager._git_toplevel", return_value=tmp_path), \
             patch("sprint_manager._run_timed", side_effect=fake_run_timed):
            result = _gate_pytest(880, tmp_path, skip=False, base_branch="develop")
        assert result.passed is True
        # Only the test file is passed to pytest; the non-test .py is excluded.
        assert test_abs in captured["args"]
        assert nontest_abs not in captured["args"]


# ── _gate_typecheck ───────────────────────────────────────────────────────────

class TestGateTypecheck:
    def test_skip_flag(self, tmp_path):
        result = _gate_typecheck(1, tmp_path, skip=True)
        assert result == GateResult(gate="typecheck", passed=True, skipped=True)

    def test_no_py_no_ts_changed(self, tmp_path):
        with patch("sprint_manager._changed_py_files", return_value=[]):
            with patch("sprint_manager._changed_js_ts_files", return_value=[]):
                result = _gate_typecheck(1, tmp_path, skip=False)
        assert result.passed is True
        assert "no typed files changed" in result.output

    def test_mypy_not_found_skips_gracefully(self, tmp_path):
        with patch("sprint_manager._changed_py_files", return_value=["server.py"]):
            with patch("sprint_manager._try", return_value=(False, "", "")):
                with patch.object(Path, "exists", return_value=False):
                    result = _gate_typecheck(1, tmp_path, skip=False)
        # Should pass (skip gracefully) when mypy not installed
        assert result.passed is True

    def test_mypy_pass(self, tmp_path):
        with patch("sprint_manager._changed_py_files", return_value=["server.py"]):
            with patch("sprint_manager._changed_js_ts_files", return_value=[]):
                with patch("sprint_manager._try") as mock_try:
                    mock_try.return_value = (True, "/usr/bin/mypy", "")
                    with patch("sprint_manager._run_timed") as mock_run:
                        mock_run.return_value = (0, "Success: no issues found", "")
                        result = _gate_typecheck(1, tmp_path, skip=False)
        assert result.passed is True

    def test_mypy_fail_reverts_to_sit(self, tmp_path):
        with patch("sprint_manager._changed_py_files", return_value=["server.py"]):
            with patch("sprint_manager._changed_js_ts_files", return_value=[]):
                with patch("sprint_manager._try") as mock_try:
                    mock_try.return_value = (True, "/usr/bin/mypy", "")
                    with patch("sprint_manager._run_timed") as mock_run:
                        mock_run.return_value = (1, "", "error: found 3 errors")
                        with patch("sprint_manager._revert_to_sit") as mock_revert:
                            result = _gate_typecheck(1, tmp_path, skip=False)
        assert result.passed is False
        mock_revert.assert_called_once_with(1, "typecheck", mock.ANY, repo_name=None)


# ── _gate_design ──────────────────────────────────────────────────────────────

import unittest.mock as mock


class TestGateDesign:
    def test_skip_flag(self, tmp_path):
        result = _gate_design(1, tmp_path, skip=True)
        assert result == GateResult(gate="design", passed=True, skipped=True)

    def test_npx_not_found_skips_gracefully(self, tmp_path):
        with patch("sprint_manager._try", return_value=(False, "", "")):
            result = _gate_design(1, tmp_path, skip=False)
        assert result.passed is True
        assert result.skipped is True

    def test_no_changed_frontend_skips_gracefully(self, tmp_path):
        # Diff-scoped: with no changed frontend files the gate skips (it no longer
        # scans the whole static/ tree, so unrelated tickets aren't failed).
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/npx", "")):
            with patch("sprint_manager._changed_frontend_files", return_value=[]):
                result = _gate_design(1, tmp_path, skip=False)
        assert result.passed is True
        assert result.skipped is True

    def test_design_pass(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/npx", "")):
            with patch("sprint_manager._changed_frontend_files", return_value=["index.html"]):
                with patch("sprint_manager._run_timed", return_value=(0, "[]", "")):
                    result = _gate_design(1, tmp_path, skip=False)
        assert result.passed is True
        assert result.skipped is False

    def test_design_fail_reverts_to_sit_on_error_severity(self, tmp_path):
        # An error-severity finding in a CHANGED file fails the gate.
        (tmp_path / "style.css").write_text("body { color: red; }")
        findings = '[{"antipattern": "gradient-text", "severity": "error"}]'
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/npx", "")):
            with patch("sprint_manager._changed_frontend_files", return_value=["style.css"]):
                with patch("sprint_manager._run_timed", return_value=(1, findings, "")):
                    with patch("sprint_manager._revert_to_sit") as mock_revert:
                        result = _gate_design(1, tmp_path, skip=False)
        assert result.passed is False
        mock_revert.assert_called_once()

    def test_design_warning_only_does_not_fail(self, tmp_path):
        # impeccable exits non-zero on warnings too, but warnings must not block:
        # the gate parses severity and passes when there are no error findings.
        (tmp_path / "style.css").write_text("body { color: #999; }")
        findings = '[{"antipattern": "low-contrast", "severity": "warning"}]'
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/npx", "")):
            with patch("sprint_manager._changed_frontend_files", return_value=["style.css"]):
                with patch("sprint_manager._run_timed", return_value=(1, findings, "")):
                    with patch("sprint_manager._revert_to_sit") as mock_revert:
                        result = _gate_design(1, tmp_path, skip=False)
        assert result.passed is True
        mock_revert.assert_not_called()


# ── _gate_lint (frontend extension) ──────────────────────────────────────────

class TestGateLintFrontend:
    def test_no_js_ts_changed_ruff_passes(self, tmp_path):
        with patch("sprint_manager._changed_py_files", return_value=[]):
            with patch("sprint_manager._changed_js_ts_files", return_value=[]):
                with patch("sprint_manager._try", return_value=(False, "", "")):
                    result = _gate_lint(1, tmp_path, skip=False)
        assert result.passed is True

    def test_js_ts_changed_no_linter_skips_gracefully(self, tmp_path):
        with patch("sprint_manager._changed_py_files", return_value=[]):
            with patch("sprint_manager._changed_js_ts_files", return_value=["app.js"]):
                with patch("sprint_manager._try", return_value=(False, "", "")):
                    result = _gate_lint(1, tmp_path, skip=False)
        # No linter found — should pass gracefully
        assert result.passed is True

    def test_gate_frontend_lint_false_skips_frontend_portion(self, tmp_path):
        """AC: COMMANDER_GATE_FRONTEND_LINT=false skips frontend lint even when JS/TS files changed."""
        fe_lint_called = []

        def fake_run_frontend_lint(*a, **kw):
            fe_lint_called.append(True)
            return (True, "")

        with patch("sprint_manager._changed_py_files", return_value=[]):
            with patch("sprint_manager._changed_js_ts_files", return_value=["app.js"]):
                with patch("sprint_manager._run_frontend_lint", fake_run_frontend_lint):
                    result = _gate_lint(1, tmp_path, skip=False, gate_frontend_lint=False)
        assert result.passed is True
        assert fe_lint_called == [], "frontend lint must not run when gate_frontend_lint=False"

    def test_run_quality_gates_passes_gate_frontend_lint(self, tmp_path):
        """AC: _run_quality_gates passes gate_frontend_lint to _gate_lint."""
        captured = {}

        def fake_lint(issue_num, worktester_dashboard, skip, **kwargs):
            captured["gate_frontend_lint"] = kwargs.get("gate_frontend_lint", True)
            return GateResult(gate="lint", passed=True)

        with patch("sprint_manager._gate_typecheck", return_value=GateResult(gate="typecheck", passed=True)):
            with patch("sprint_manager._gate_lint", fake_lint):
                _run_quality_gates(
                    issue_num=1,
                    feature_branch="feature/1-test",
                    worktester_root=tmp_path,
                    worktester_dashboard=tmp_path,
                    skip_all=False,
                    gate_pytest=False,
                    gate_lint=True,
                    gate_merge_preview=False,
                    gate_typecheck=True,
                    gate_design=False,
                    gate_frontend_lint=False,
                )
        assert captured.get("gate_frontend_lint") is False


# ── _run_quality_gates ordering ───────────────────────────────────────────────

class TestGateOrdering:
    """Verify typecheck runs before lint, design before pytest, pytest before merge-preview."""

    def _make_pass_gate(self, name):
        return GateResult(gate=name, passed=True)

    def test_typecheck_fail_stops_before_lint(self, tmp_path):
        call_order = []

        def fake_typecheck(*a, **kw):
            call_order.append("typecheck")
            return GateResult(gate="typecheck", passed=False, output="type error")

        def fake_lint(*a, **kw):
            call_order.append("lint")
            return GateResult(gate="lint", passed=True)

        def fake_revert(*a, **kw):
            pass

        with patch("sprint_manager._gate_typecheck", fake_typecheck):
            with patch("sprint_manager._gate_lint", fake_lint):
                with patch("sprint_manager._revert_to_sit", fake_revert):
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
                    )

        assert call_order == ["typecheck"]  # stopped before lint
        assert len(results) == 1
        assert results[0].gate == "typecheck"
        assert results[0].passed is False

    def test_all_pass_full_sequence(self, tmp_path):
        gate_calls = []

        def _fake(name):
            def _g(*a, **kw):
                gate_calls.append(name)
                return GateResult(gate=name, passed=True)
            return _g

        with patch("sprint_manager._gate_typecheck", _fake("typecheck")):
            with patch("sprint_manager._gate_lint", _fake("lint")):
                with patch("sprint_manager._gate_design", _fake("design")):
                    with patch("sprint_manager._gate_pytest", _fake("pytest")):
                        with patch("sprint_manager._gate_merge_preview", _fake("merge-preview")):
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
                            )

        assert gate_calls == ["typecheck", "lint", "design", "pytest", "merge-preview"]
        assert all(r.passed for r in results)

    def test_all_gates_skipped_when_skip_all(self, tmp_path):
        results = _run_quality_gates(
            issue_num=1,
            feature_branch="feature/1-test",
            worktester_root=tmp_path,
            worktester_dashboard=tmp_path,
            skip_all=True,
            gate_pytest=True,
            gate_lint=True,
            gate_merge_preview=True,
            gate_typecheck=True,
            gate_design=True,
        )
        assert all(r.skipped for r in results)
        gate_names = [r.gate for r in results]
        assert "typecheck" in gate_names
        assert "design" in gate_names
