"""Tests for issue #1949 — coder-no-test-edits gate must catch deleted and renamed test files.

AC coverage:
  AC1  deleted test file (diff-filter D) causes gate to fail
  AC2  renamed test file (old path in tests/) causes gate to fail
  AC3  file renamed INTO tests/ (new path in tests/) causes gate to fail
  AC4  rename of file entirely outside tests/ passes
  AC5  allowlist exempts a renamed-from test path
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
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _acmd_only(paths: str) -> list[tuple[int, str, str]]:
    """side_effect for two _run_timed calls: first has ACMD paths, second (rename) is empty."""
    return [(0, paths, ""), (0, "", "")]


def _rename_only(name_status_line: str) -> list[tuple[int, str, str]]:
    """side_effect for two _run_timed calls: first (ACMD) is empty, second has rename."""
    return [(0, "", ""), (0, name_status_line, "")]


def _both_empty() -> list[tuple[int, str, str]]:
    return [(0, "", ""), (0, "", "")]


# ── AC1: deleted test file is blocked ────────────────────────────────────────

class TestDeletedTestFile:
    def test_deleted_test_file_fails_gate(self, tmp_path):
        """Gate must fail when a file in tests/ is deleted by the coder."""
        with patch("sprint_manager._run_timed", side_effect=_acmd_only("tests/test_grading.py\n")), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is False

    def test_deleted_test_file_named_in_output(self, tmp_path):
        """Deleted test file must appear in the gate failure message."""
        with patch("sprint_manager._run_timed", side_effect=_acmd_only("tests/test_grading.py\n")), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert "tests/test_grading.py" in result.output

    def test_deleted_non_test_file_passes(self, tmp_path):
        """A deleted file outside tests/ must not trigger the gate."""
        with patch("sprint_manager._run_timed", side_effect=_acmd_only("services/old_module.py\n")), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is True


# ── AC2: renamed-from test file (old path in tests/) is blocked ───────────────

class TestRenamedFromTestFile:
    def test_renamed_out_of_tests_fails_gate(self, tmp_path):
        """Renaming a test file out of tests/ (old path blocked) must fail."""
        rename_line = "R100\ttests/test_grading.py\tsrc/grading_moved.py\n"
        with patch("sprint_manager._run_timed", side_effect=_rename_only(rename_line)), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is False

    def test_renamed_out_of_tests_old_path_in_output(self, tmp_path):
        """Old (blocked) path must appear in the gate failure message."""
        rename_line = "R100\ttests/test_grading.py\tsrc/grading_moved.py\n"
        with patch("sprint_manager._run_timed", side_effect=_rename_only(rename_line)), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert "tests/test_grading.py" in result.output


# ── AC3: renamed-into tests/ (new path in tests/) is blocked ─────────────────

class TestRenamedIntoTestDir:
    def test_renamed_into_tests_fails_gate(self, tmp_path):
        """Moving a file INTO tests/ (new path blocked) must fail."""
        rename_line = "R100\tsrc/sneaky.py\ttests/test_sneaked_in.py\n"
        with patch("sprint_manager._run_timed", side_effect=_rename_only(rename_line)), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is False

    def test_renamed_into_tests_new_path_in_output(self, tmp_path):
        """New (blocked) path must appear in the gate failure message."""
        rename_line = "R100\tsrc/sneaky.py\ttests/test_sneaked_in.py\n"
        with patch("sprint_manager._run_timed", side_effect=_rename_only(rename_line)), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert "tests/test_sneaked_in.py" in result.output


# ── AC4: rename entirely outside tests/ passes ────────────────────────────────

class TestRenameOutsideTests:
    def test_rename_non_test_files_passes(self, tmp_path):
        """Renaming a file between two non-test paths must pass."""
        rename_line = "R100\tservices/old_name.py\tservices/new_name.py\n"
        with patch("sprint_manager._run_timed", side_effect=_rename_only(rename_line)), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is True

    def test_empty_rename_output_passes(self, tmp_path):
        """When the rename diff is empty, gate passes (no renames at all)."""
        with patch("sprint_manager._run_timed", side_effect=_both_empty()), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(1, tmp_path, skip=False)
        assert result.passed is True


# ── AC5: allowlist exempts renamed-from test path ────────────────────────────

class TestAllowlistWithRename:
    def test_allowlisted_old_rename_path_passes(self, tmp_path):
        """If the old rename path is allowlisted, gate passes."""
        rename_line = "R100\ttests/conftest.py\tsrc/conftest_moved.py\n"
        with patch("sprint_manager._run_timed", side_effect=_rename_only(rename_line)), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(
                1, tmp_path, skip=False,
                allowlist=["tests/conftest.py"],
            )
        assert result.passed is True

    def test_allowlisted_new_rename_path_passes(self, tmp_path):
        """If the new rename path (into tests/) is allowlisted, gate passes."""
        rename_line = "R100\tsrc/conftest.py\ttests/conftest.py\n"
        with patch("sprint_manager._run_timed", side_effect=_rename_only(rename_line)), \
             patch("sprint_manager._revert_to_sit"):
            result = _gate_coder_no_test_edits(
                1, tmp_path, skip=False,
                allowlist=["tests/conftest.py"],
            )
        assert result.passed is True
