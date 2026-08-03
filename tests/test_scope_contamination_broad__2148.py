"""Tests for issue #2148: _gate_failure_scope_contaminated must detect non-test
source file leakage, not just test files with __<N>.py suffixes.

The real-world contamination in #2072 involved ~30 non-test advisor*.py /
roadmap*.py files; the existing __<N>.py-only regex would have missed all of
them.  This suite verifies the broadened detection via the new diff_files
parameter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from sprint_manager import _gate_failure_scope_contaminated  # noqa: E402


def _write_sidecar(tmp_path: Path, issue_num: int, detail: str) -> Path:
    runtime_dir = tmp_path / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    sc = runtime_dir / f"last-failure-{issue_num}.json"
    sc.write_text(
        json.dumps({"issue": issue_num, "failure_class": "gate", "detail": detail}),
        encoding="utf-8",
    )
    return sc


# ── Tests for non-test source file detection (new in #2148) ──────────────────

class TestNonTestSourceFileDetection:
    """Scope contamination from non-test source files (no __<N>.py suffix)."""

    def test_single_foreign_source_file_detected(self, tmp_path):
        """Single non-test source file NOT in diff → contaminated (the missing case)."""
        _write_sidecar(
            tmp_path, 2073,
            "FAILED services/sprint_manager/advisor.py::SomeTest - ImportError\n",
        )
        diff_files = frozenset({"services/sprint_manager/config.py"})
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=diff_files) is True

    def test_own_source_file_not_contaminated(self, tmp_path):
        """Source file that IS in the current ticket's diff → not contaminated."""
        _write_sidecar(
            tmp_path, 2073,
            "FAILED services/sprint_manager/advisor.py::SomeTest - ImportError\n",
        )
        diff_files = frozenset({"services/sprint_manager/advisor.py"})
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=diff_files) is False

    def test_multiple_foreign_source_files_all_detected(self, tmp_path):
        """30-file contamination from advisor.py + roadmap.py (neither in diff) → contaminated."""
        _write_sidecar(
            tmp_path, 2073,
            "services/sprint_manager/advisor.py\n"
            "services/sprint_manager/roadmap.py\n",
        )
        diff_files = frozenset({"services/sprint_manager/config.py"})
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=diff_files) is True

    def test_short_basename_matched_via_suffix(self, tmp_path):
        """Short filename like 'advisor.py' matched against full diff path."""
        _write_sidecar(
            tmp_path, 2073,
            "  advisor.py imported from elsewhere\n",
        )
        # diff_files has the full path; advisor.py should match as suffix
        diff_files = frozenset({"services/sprint_manager/advisor.py"})
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=diff_files) is False


# ── diff_files=None / empty: fall back to test-only logic ────────────────────

class TestDiffFilesAbsenceFallback:
    """When diff_files is None or empty, source files are ignored (backward compat)."""

    def test_no_diff_files_source_files_ignored(self, tmp_path):
        """Source file in detail, diff_files=None → fall back to test-only → False."""
        _write_sidecar(
            tmp_path, 2073,
            "FAILED services/sprint_manager/advisor.py::SomeTest - ImportError\n",
        )
        # diff_files not provided — existing behavior must be preserved
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path) is False

    def test_empty_diff_files_source_files_ignored(self, tmp_path):
        """Source file in detail, diff_files=frozenset() → cannot check → False."""
        _write_sidecar(
            tmp_path, 2073,
            "FAILED services/sprint_manager/advisor.py::SomeTest - ImportError\n",
        )
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=frozenset()) is False

    def test_no_diff_foreign_test_file_still_detected(self, tmp_path):
        """diff_files=None does not regress test-file detection (existing AC4)."""
        _write_sidecar(
            tmp_path, 2073,
            "tests/test_milestone_prod_pollution__2074.py\n",
        )
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path) is True


# ── Combined test + source scenarios ─────────────────────────────────────────

class TestCombinedTestAndSourceFiles:
    """Mix of test-file and source-file references in the same sidecar."""

    def test_own_test_file_plus_foreign_source_not_contaminated(self, tmp_path):
        """Own test file present → not contaminated even if source file is foreign."""
        _write_sidecar(
            tmp_path, 2073,
            "tests/test_failures_table__2073.py\n"
            "services/sprint_manager/advisor.py\n",
        )
        diff_files = frozenset({"services/sprint_manager/config.py"})
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=diff_files) is False

    def test_foreign_test_file_plus_foreign_source_contaminated(self, tmp_path):
        """Both test and source files are foreign → contaminated."""
        _write_sidecar(
            tmp_path, 2073,
            "tests/test_milestone_prod_pollution__2074.py\n"
            "services/sprint_manager/advisor.py\n",
        )
        diff_files = frozenset({"services/sprint_manager/config.py"})
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=diff_files) is True

    def test_foreign_test_plus_own_source_not_contaminated(self, tmp_path):
        """Foreign test but own source file → not contaminated."""
        _write_sidecar(
            tmp_path, 2073,
            "tests/test_milestone_prod_pollution__2074.py\n"
            "services/sprint_manager/advisor.py\n",
        )
        # advisor.py IS in our diff → own
        diff_files = frozenset({"services/sprint_manager/advisor.py"})
        assert _gate_failure_scope_contaminated(2073, repo_root=tmp_path, diff_files=diff_files) is False
