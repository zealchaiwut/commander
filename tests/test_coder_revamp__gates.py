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
    GateResult,
    _changed_js_ts_files,
    _gate_design,
    _gate_lint,
    _gate_typecheck,
    _run_quality_gates,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _fake_run(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ── _changed_js_ts_files ──────────────────────────────────────────────────────

class TestChangedJsTsFiles:
    def test_returns_js_ts_files(self, tmp_path):
        with patch("sprint_manager._run_timed") as mock_run:
            mock_run.return_value = (0, "app.js\nutils.ts\nserver.py\nREADME.md\n", "")
            result = _changed_js_ts_files("develop", tmp_path)
        assert result == ["app.js", "utils.ts"]

    def test_returns_empty_on_no_js_ts(self, tmp_path):
        with patch("sprint_manager._run_timed") as mock_run:
            mock_run.return_value = (0, "server.py\nstyle.css\n", "")
            result = _changed_js_ts_files("develop", tmp_path)
        assert result == []

    def test_returns_empty_on_git_error(self, tmp_path):
        with patch("sprint_manager._run_timed") as mock_run:
            mock_run.return_value = (1, "", "fatal: not a git repo")
            result = _changed_js_ts_files("develop", tmp_path)
        assert result == []

    def test_includes_jsx_tsx_mjs(self, tmp_path):
        with patch("sprint_manager._run_timed") as mock_run:
            mock_run.return_value = (0, "component.jsx\npage.tsx\nmodule.mjs\n", "")
            result = _changed_js_ts_files("develop", tmp_path)
        assert set(result) == {"component.jsx", "page.tsx", "module.mjs"}


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

    def test_no_frontend_skips_gracefully(self, tmp_path):
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/npx", "")):
            result = _gate_design(1, tmp_path, skip=False)
        # tmp_path has no HTML/CSS/JSX files
        assert result.passed is True
        assert result.skipped is True

    def test_design_pass(self, tmp_path):
        # Create a fake HTML file so has_frontend = True
        (tmp_path / "index.html").write_text("<html></html>")
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/npx", "")):
            with patch("sprint_manager._run_timed") as mock_run:
                mock_run.return_value = (0, "[]", "")
                result = _gate_design(1, tmp_path, skip=False)
        assert result.passed is True

    def test_design_fail_reverts_to_sit(self, tmp_path):
        (tmp_path / "style.css").write_text("body { color: red; }")
        with patch("sprint_manager._try", return_value=(True, "/usr/bin/npx", "")):
            with patch("sprint_manager._run_timed") as mock_run:
                mock_run.return_value = (1, '[{"antipattern": "low-contrast"}]', "")
                with patch("sprint_manager._revert_to_sit") as mock_revert:
                    result = _gate_design(1, tmp_path, skip=False)
        assert result.passed is False
        mock_revert.assert_called_once()


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
