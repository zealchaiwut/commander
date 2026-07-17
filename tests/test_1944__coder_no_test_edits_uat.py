"""UAT tests for issue #1944 — gate blocking coder from editing test files (runs against UAT).

This test file validates the acceptance criteria by invoking the sprint manager gates
and examining the behavior end-to-end.
"""
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add services path for sprint_manager imports
import sys
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from sprint_manager import (
    GateResult,
    _gate_coder_no_test_edits,
    _run_quality_gates,
    _coder_no_test_edits_gate_enabled,
    _get_coder_blocked_patterns,
    _get_coder_test_allowlist,
)


# ── UAT Step 1: Coder touching tests/ → gate fails ──────────────────────────

def test_uat_step_1_coder_touches_test_file_blocked(tmp_path):
    """UAT Step 1: Coder agent submission includes a change to a file under tests/.

    Expected: The sprint gate fails with an error message naming the offending file(s)
    and blocking the submission.
    """
    # Simulate a coder diff that touches tests/test_feature.py
    mock_diff_output = (0, "tests/test_feature.py\nservices/sprint_manager/gates.py\n", "")

    with patch("sprint_manager._run_timed", return_value=mock_diff_output), \
         patch("sprint_manager._revert_to_sit") as mock_revert:
        result = _gate_coder_no_test_edits(1944, tmp_path, skip=False)

    # Gate should FAIL
    assert result.passed is False, "Gate should fail when coder touches test files"

    # Error message must name the offending file
    assert "tests/test_feature.py" in result.output, \
        f"Output should name the blocked file. Got: {result.output}"

    # Error message must state coders may not modify grading tests
    assert "coders may not modify" in result.output.lower(), \
        f"Output should state coders may not modify tests. Got: {result.output}"

    # _revert_to_sit should have been called
    mock_revert.assert_called_once()


# ── UAT Step 2: Coder touching only non-test files → gate passes ────────────

def test_uat_step_2_coder_touches_only_source_files_passes(tmp_path):
    """UAT Step 2: Coder agent submission that touches only files outside tests/.

    Expected: The gate passes and the submission proceeds normally.
    """
    # Simulate a coder diff that touches only non-test files
    mock_diff_output = (
        0,
        "services/sprint_manager/gates.py\n"
        "apps/dashboard/server.py\n"
        "requirements.txt\n",
        ""
    )

    with patch("sprint_manager._run_timed", return_value=mock_diff_output), \
         patch("sprint_manager._revert_to_sit"):
        result = _gate_coder_no_test_edits(1944, tmp_path, skip=False)

    # Gate should PASS
    assert result.passed is True, "Gate should pass when coder only touches non-test files"


# ── UAT Step 3: Allowlist exemption works ────────────────────────────────────

def test_uat_step_3_allowlisted_test_file_passes(tmp_path):
    """UAT Step 3: Add a specific test file path to CODER_TEST_PATH_ALLOWLIST
    and resubmit a coder diff that touches only that file.

    Expected: The gate passes, acknowledging the explicit allowlist exemption.
    """
    # Simulate a coder diff that touches only tests/conftest.py (an allowlisted file)
    mock_diff_output = (0, "tests/conftest.py\n", "")

    with patch("sprint_manager._run_timed", return_value=mock_diff_output), \
         patch("sprint_manager._revert_to_sit"):
        result = _gate_coder_no_test_edits(
            1944, tmp_path, skip=False,
            allowlist=["tests/conftest.py"]
        )

    # Gate should PASS because tests/conftest.py is allowlisted
    assert result.passed is True, \
        "Gate should pass when blocked file is on the allowlist"


# ── UAT Step 4: Custom pattern blocking ──────────────────────────────────────

def test_uat_step_4_custom_blocked_pattern(tmp_path):
    """UAT Step 4: Add a custom pattern to CODER_BLOCKED_PATH_PATTERNS
    (e.g. fixtures/) and submit a coder diff that touches a file matching that pattern.

    Expected: The gate fails and identifies the matching file as blocked.
    """
    # Simulate a coder diff that touches fixtures/stub.py
    mock_diff_output = (0, "fixtures/stub.py\nservices/good.py\n", "")

    with patch("sprint_manager._run_timed", return_value=mock_diff_output), \
         patch("sprint_manager._revert_to_sit") as mock_revert:
        result = _gate_coder_no_test_edits(
            1944, tmp_path, skip=False,
            blocked_patterns=["fixtures/**"]
        )

    # Gate should FAIL
    assert result.passed is False, \
        "Gate should fail when coder touches a file matching custom blocked pattern"

    # Error message must name the offending file
    assert "fixtures/stub.py" in result.output, \
        f"Output should name the blocked file. Got: {result.output}"

    # _revert_to_sit should have been called
    mock_revert.assert_called_once()


# ── UAT Step 5: Empty changeset → gate passes ────────────────────────────────

def test_uat_step_5_empty_changeset_passes(tmp_path):
    """UAT Step 5: Submit a coder diff with an empty changeset (no files modified).

    Expected: The gate passes without error.
    """
    # Simulate an empty diff
    mock_diff_output = (0, "", "")

    with patch("sprint_manager._run_timed", return_value=mock_diff_output), \
         patch("sprint_manager._revert_to_sit"):
        result = _gate_coder_no_test_edits(1944, tmp_path, skip=False)

    # Gate should PASS
    assert result.passed is True, "Gate should pass on empty diff"


# ── Integration: gate appears in _run_quality_gates pipeline ────────────────

def test_uat_gate_integration_in_pipeline(tmp_path):
    """Verify that the coder-no-test-edits gate is registered and runs as part of
    _run_quality_gates.
    """
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
            issue_num=1944,
            feature_branch="feature/1944-test",
            worktester_root=tmp_path,
            worktester_dashboard=tmp_path,
            skip_all=False,
            gate_pytest=True,
            gate_lint=True,
            gate_merge_preview=True,
            gate_coder_no_test_edits=True,
        )

    gate_names = [r.gate for r in results]
    assert "coder-no-test-edits" in gate_names, \
        "Gate should be registered and appear in results"

    # It should be the first gate (Gate 0)
    assert results[0].gate == "coder-no-test-edits", \
        "coder-no-test-edits should be the first gate (Gate 0, cheapest)"


# ── Configuration: environment variables work ────────────────────────────────

def test_uat_config_blocked_patterns_from_env(tmp_path, monkeypatch):
    """Verify that CODER_BLOCKED_PATH_PATTERNS env var is read correctly."""
    monkeypatch.setenv("CODER_BLOCKED_PATH_PATTERNS", "fixtures/**,stubs/**")

    patterns = _get_coder_blocked_patterns()

    assert "fixtures/**" in patterns
    assert "stubs/**" in patterns
    assert patterns != ["tests/**"], "Custom patterns should override defaults"


def test_uat_config_allowlist_from_env(tmp_path, monkeypatch):
    """Verify that CODER_TEST_PATH_ALLOWLIST env var is read correctly."""
    monkeypatch.setenv("CODER_TEST_PATH_ALLOWLIST", "tests/conftest.py, tests/fixtures/base.py")

    allowlist = _get_coder_test_allowlist()

    assert "tests/conftest.py" in allowlist
    assert "tests/fixtures/base.py" in allowlist


def test_uat_config_gate_can_be_disabled(tmp_path, monkeypatch):
    """Verify that COMMANDER_GATE_CODER_NO_TEST_EDITS=false disables the gate."""
    monkeypatch.setenv("COMMANDER_GATE_CODER_NO_TEST_EDITS", "false")

    assert _coder_no_test_edits_gate_enabled() is False


def test_uat_config_gate_enabled_by_default(tmp_path, monkeypatch):
    """Verify that the gate is enabled by default."""
    monkeypatch.delenv("COMMANDER_GATE_CODER_NO_TEST_EDITS", raising=False)

    assert _coder_no_test_edits_gate_enabled() is True
