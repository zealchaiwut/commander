"""Tests for issue #1944 — gate blocking coder from editing test files.

AC coverage:
  AC1  gate function exists in gates.py, returns GateResult failure when test paths touched
  AC2  blocked path pattern configurable via CODER_BLOCKED_PATH_PATTERNS
  AC3  allowlist via CODER_TEST_PATH_ALLOWLIST exempts specific paths
  AC4  gate is registered and invoked inside _run_quality_gates
  AC5  error message names offending file(s) and states coders may not modify grading tests
  AC6  passes when all changed paths are outside tests/ (or on allowlist)
  AC7  unit tests: all-clear, blocked, allowlisted, empty diff
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from sprint_manager import (  # noqa: E402
    GateResult,
    _gate_coder_no_test_edits,
    _coder_no_test_edits_gate_enabled,
    _get_coder_blocked_patterns,
    _get_coder_test_allowlist,
    _run_quality_gates,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_diff_output(*paths: str) -> tuple[int, str, str]:
    """Return a fake (rc, stdout, stderr) from git diff --name-only."""
    return 0, "\n".join(paths) + "\n", ""


def _empty_diff() -> tuple[int, str, str]:
    return 0, "", ""


# ── AC1: gate function exists, returns GateResult ────────────────────────────

class TestGateExists:
    def test_returns_gateresult_on_skip(self, tmp_path):
        result = _gate_coder_no_test_edits(1, tmp_path, skip=True)
        assert isinstance(result, GateResult)
        assert result.gate == "coder-no-test-edits"

    def test_skip_passes(self, tmp_path):
        result = _gate_coder_no_test_edits(1, tmp_path, skip=True)
        assert result.passed is True
        assert result.skipped is True


# ── AC7: empty diff → gate passes ────────────────────────────────────────────

class TestEmptyDiff:
    def test_empty_diff_passes(self, tmp_path):
        with patch("sprint_manager._run_timed", return_value=_empty_diff()):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is True

    def test_failed_git_cmd_passes(self, tmp_path):
        """A non-zero git exit (e.g. not a git repo) is treated as empty diff."""
        with patch("sprint_manager._run_timed", return_value=(1, "", "not a repo")):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is True


# ── AC6: all-clear path — files outside tests/ → passes ─────────────────────

class TestAllClearPath:
    def test_source_files_only_pass(self, tmp_path):
        diff = _make_diff_output(
            "services/sprint_manager/gates.py",
            "apps/dashboard/server.py",
            "requirements.txt",
        )
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is True

    def test_test_like_name_not_in_tests_dir_passes(self, tmp_path):
        """A file named test_foo.py outside tests/ should not be blocked."""
        diff = _make_diff_output("services/test_helpers.py", "conftest.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(
                1, tmp_path, skip=False,
                blocked_patterns=["tests/**"],
            )
        assert result.passed is True


# ── AC1 + AC5: blocked path → gate fails, message names offending files ──────

class TestBlockedPath:
    def test_test_file_blocked(self, tmp_path):
        diff = _make_diff_output("tests/test_foo.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is False

    def test_error_message_names_file(self, tmp_path):
        """AC5: error message must name the offending file."""
        diff = _make_diff_output("tests/test_my_feature.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert "tests/test_my_feature.py" in result.output

    def test_error_message_states_coders_may_not_modify(self, tmp_path):
        """AC5: error message states coders may not modify grading tests."""
        diff = _make_diff_output("tests/test_bar.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert "coders may not modify grading tests" in result.output.lower() or \
               "coders may not modify" in result.output

    def test_multiple_blocked_files_all_named(self, tmp_path):
        diff = _make_diff_output(
            "tests/test_a.py",
            "services/good.py",
            "tests/test_b.py",
        )
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is False
        assert "tests/test_a.py" in result.output
        assert "tests/test_b.py" in result.output

    def test_revert_called_on_failure(self, tmp_path):
        diff = _make_diff_output("tests/test_blocked.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit") as mock_revert:
            _gate_coder_no_test_edits(1, tmp_path, skip=False)
        mock_revert.assert_called_once()


# ── AC3: allowlisted path → gate passes ──────────────────────────────────────

class TestAllowlistedPath:
    def test_allowlisted_file_passes(self, tmp_path):
        diff = _make_diff_output("tests/conftest.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(
                1, tmp_path, skip=False,
                allowlist=["tests/conftest.py"],
            )
        assert result.passed is True

    def test_allowlist_only_exempts_exact_path(self, tmp_path):
        """Allowlist is an exact match; other files in tests/ still fail."""
        diff = _make_diff_output("tests/conftest.py", "tests/test_other.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(
                1, tmp_path, skip=False,
                allowlist=["tests/conftest.py"],
            )
        assert result.passed is False
        assert "tests/test_other.py" in result.output

    def test_allowlist_from_env(self, tmp_path, monkeypatch):
        """AC3: CODER_TEST_PATH_ALLOWLIST env var is read."""
        monkeypatch.setenv("CODER_TEST_PATH_ALLOWLIST", "tests/fixtures/stub.py")
        diff = _make_diff_output("tests/fixtures/stub.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is True


# ── AC2: configurable blocked patterns ───────────────────────────────────────

class TestConfigurableBlockedPatterns:
    def test_custom_pattern_via_arg(self, tmp_path):
        """Passing blocked_patterns=['fixtures/**'] blocks files under fixtures/."""
        diff = _make_diff_output("fixtures/my_fixture.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(
                1, tmp_path, skip=False,
                blocked_patterns=["fixtures/**"],
            )
        assert result.passed is False

    def test_custom_pattern_from_env(self, tmp_path, monkeypatch):
        """AC2: CODER_BLOCKED_PATH_PATTERNS env var is read."""
        monkeypatch.setenv("CODER_BLOCKED_PATH_PATTERNS", "fixtures/**")
        diff = _make_diff_output("fixtures/my_fixture.py")
        with patch("sprint_manager._run_timed", return_value=diff), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is False

    def test_default_pattern_is_tests_glob(self, monkeypatch):
        """AC2: when CODER_BLOCKED_PATH_PATTERNS is unset, default is tests/**."""
        monkeypatch.delenv("CODER_BLOCKED_PATH_PATTERNS", raising=False)
        patterns = _get_coder_blocked_patterns()
        assert any("tests" in p for p in patterns)

    def test_env_pattern_overrides_default(self, monkeypatch):
        monkeypatch.setenv("CODER_BLOCKED_PATH_PATTERNS", "custom/**,other/**")
        patterns = _get_coder_blocked_patterns()
        assert "custom/**" in patterns
        assert "other/**" in patterns
        assert "tests/**" not in patterns


# ── AC3: allowlist env helpers ────────────────────────────────────────────────

class TestAllowlistHelper:
    def test_empty_env_returns_empty_list(self, monkeypatch):
        monkeypatch.delenv("CODER_TEST_PATH_ALLOWLIST", raising=False)
        assert _get_coder_test_allowlist() == []

    def test_env_parsed_correctly(self, monkeypatch):
        monkeypatch.setenv("CODER_TEST_PATH_ALLOWLIST", "tests/conftest.py, tests/fixtures/base.py")
        result = _get_coder_test_allowlist()
        assert "tests/conftest.py" in result
        assert "tests/fixtures/base.py" in result


# ── AC4: gate registered in _run_quality_gates ───────────────────────────────

class TestGateRegisteredInPipeline:
    def test_gate_appears_in_results(self, tmp_path):
        """AC4: _run_quality_gates returns a result for coder-no-test-edits."""
        with patch("sprint_manager._gate_coder_no_test_edits",
                   return_value=GateResult(gate="coder-no-test-edits", passed=True)), \
             patch("sprint_manager._gate_typecheck",
                   return_value=GateResult(gate="typecheck", passed=True, skipped=True)), \
             patch("sprint_manager._gate_lint",
                   return_value=GateResult(gate="lint", passed=True, skipped=True)), \
             patch("sprint_manager._gate_design",
                   return_value=GateResult(gate="design", passed=True, skipped=True)), \
             patch("sprint_manager._gate_pytest",
                   return_value=GateResult(gate="pytest", passed=True, skipped=True)), \
             patch("sprint_manager._gate_merge_preview",
                   return_value=GateResult(gate="merge-preview", passed=True, skipped=True)), \
             patch("sprint_manager._gate_monolith",
                   return_value=GateResult(gate="monolith", passed=True, skipped=True)):
            results = _run_quality_gates(
                issue_num=99,
                feature_branch="feature/99-test",
                worktester_root=tmp_path,
                worktester_dashboard=tmp_path,
                skip_all=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                gate_coder_no_test_edits=True,
            )

        gate_names = [r.gate for r in results]
        assert "coder-no-test-edits" in gate_names

    def test_gate_skipped_when_disabled(self, tmp_path):
        """When gate_coder_no_test_edits=False the result is skipped=True."""
        with patch("sprint_manager._gate_typecheck",
                   return_value=GateResult(gate="typecheck", passed=True, skipped=True)), \
             patch("sprint_manager._gate_lint",
                   return_value=GateResult(gate="lint", passed=True, skipped=True)), \
             patch("sprint_manager._gate_design",
                   return_value=GateResult(gate="design", passed=True, skipped=True)), \
             patch("sprint_manager._gate_pytest",
                   return_value=GateResult(gate="pytest", passed=True, skipped=True)), \
             patch("sprint_manager._gate_merge_preview",
                   return_value=GateResult(gate="merge-preview", passed=True, skipped=True)), \
             patch("sprint_manager._gate_monolith",
                   return_value=GateResult(gate="monolith", passed=True, skipped=True)):
            results = _run_quality_gates(
                issue_num=99,
                feature_branch="feature/99-test",
                worktester_root=tmp_path,
                worktester_dashboard=tmp_path,
                skip_all=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                gate_coder_no_test_edits=False,
            )

        coder_result = next((r for r in results if r.gate == "coder-no-test-edits"), None)
        assert coder_result is not None
        assert coder_result.skipped is True


# ── enable/disable toggle ─────────────────────────────────────────────────────

class TestGateEnvToggle:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("COMMANDER_GATE_CODER_NO_TEST_EDITS", raising=False)
        assert _coder_no_test_edits_gate_enabled() is True

    def test_false_disables(self, monkeypatch):
        monkeypatch.setenv("COMMANDER_GATE_CODER_NO_TEST_EDITS", "false")
        assert _coder_no_test_edits_gate_enabled() is False

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("COMMANDER_GATE_CODER_NO_TEST_EDITS", "0")
        assert _coder_no_test_edits_gate_enabled() is False

    def test_one_enables(self, monkeypatch):
        monkeypatch.setenv("COMMANDER_GATE_CODER_NO_TEST_EDITS", "1")
        assert _coder_no_test_edits_gate_enabled() is True
