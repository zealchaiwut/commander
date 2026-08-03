"""Tests for issue #2155: _feature_branch_diff_files must use the sprint branch
as the merge-base in sprint mode (COMMANDER_MERGE_TARGET set to a sprint branch).

Without this fix, merge-base HEAD develop resolves to the pre-sprint fork point,
so sibling sprint tickets' files pollute the "own diff" set, causing cross-ticket
scope contamination to go undetected.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from sprint_manager import _feature_branch_diff_files  # noqa: E402


def _make_run(returncode: int, stdout: str = ""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


class TestSprintModeDiffBase:
    """AC: sprint branch is tried first when COMMANDER_MERGE_TARGET is a sprint branch."""

    def test_sprint_branch_tried_before_develop(self, tmp_path, monkeypatch):
        """When COMMANDER_MERGE_TARGET=sprint/sprint-N, origin/sprint/sprint-N is
        the first merge-base candidate — not origin/develop."""
        monkeypatch.setenv("COMMANDER_MERGE_TARGET", "sprint/sprint-1013")

        merge_base_sha = "abc123def456"
        diff_output = "services/sprint_manager/sprint_manager.py\n"

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "merge-base" in cmd:
                base = cmd[-1]
                if base == "origin/sprint/sprint-1013":
                    return _make_run(0, merge_base_sha)
                return _make_run(1)  # all other bases fail
            if "diff" in cmd and "--name-only" in cmd:
                return _make_run(0, diff_output)
            return _make_run(1)

        with patch("subprocess.run", side_effect=fake_run):
            result = _feature_branch_diff_files(tmp_path)

        assert result == frozenset({"services/sprint_manager/sprint_manager.py"})

        # origin/sprint/sprint-1013 must appear before origin/develop in the call list
        merge_base_calls = [c for c in calls if "merge-base" in c]
        bases_tried = [c[-1] for c in merge_base_calls]
        assert "origin/sprint/sprint-1013" in bases_tried, \
            "origin/<sprint-branch> must be in base candidates"
        sprint_idx = bases_tried.index("origin/sprint/sprint-1013")
        develop_idx = bases_tried.index("origin/develop") if "origin/develop" in bases_tried else len(bases_tried)
        assert sprint_idx < develop_idx, \
            "sprint branch must be tried before origin/develop"

    def test_local_sprint_branch_tried_as_fallback(self, tmp_path, monkeypatch):
        """When origin/<sprint> is unreachable, local sprint/<N> is tried next."""
        monkeypatch.setenv("COMMANDER_MERGE_TARGET", "sprint/sprint-1013")

        merge_base_sha = "deadbeef"
        diff_output = "apps/dashboard/server.py\n"

        def fake_run(cmd, **kwargs):
            if "merge-base" in cmd:
                base = cmd[-1]
                if base == "sprint/sprint-1013":
                    return _make_run(0, merge_base_sha)
                return _make_run(1)
            if "diff" in cmd and "--name-only" in cmd:
                return _make_run(0, diff_output)
            return _make_run(1)

        with patch("subprocess.run", side_effect=fake_run):
            result = _feature_branch_diff_files(tmp_path)

        assert result == frozenset({"apps/dashboard/server.py"})

    def test_develop_mode_unaffected(self, tmp_path, monkeypatch):
        """When COMMANDER_MERGE_TARGET=develop, origin/develop is still first."""
        monkeypatch.setenv("COMMANDER_MERGE_TARGET", "develop")

        merge_base_sha = "cafebabe"
        diff_output = "scripts/update_ticket.py\n"

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "merge-base" in cmd:
                base = cmd[-1]
                if base == "origin/develop":
                    return _make_run(0, merge_base_sha)
                return _make_run(1)
            if "diff" in cmd and "--name-only" in cmd:
                return _make_run(0, diff_output)
            return _make_run(1)

        with patch("subprocess.run", side_effect=fake_run):
            result = _feature_branch_diff_files(tmp_path)

        assert result == frozenset({"scripts/update_ticket.py"})

        merge_base_calls = [c for c in calls if "merge-base" in c]
        bases_tried = [c[-1] for c in merge_base_calls]
        # develop branch itself must not be inserted as a sprint candidate
        assert bases_tried[0] == "origin/develop", \
            "origin/develop must remain the first candidate when COMMANDER_MERGE_TARGET=develop"

    def test_no_env_var_unaffected(self, tmp_path, monkeypatch):
        """When COMMANDER_MERGE_TARGET is unset, behavior is unchanged."""
        monkeypatch.delenv("COMMANDER_MERGE_TARGET", raising=False)

        merge_base_sha = "feedface"
        diff_output = "tests/test_foo__123.py\n"

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "merge-base" in cmd:
                base = cmd[-1]
                if base == "origin/develop":
                    return _make_run(0, merge_base_sha)
                return _make_run(1)
            if "diff" in cmd and "--name-only" in cmd:
                return _make_run(0, diff_output)
            return _make_run(1)

        with patch("subprocess.run", side_effect=fake_run):
            result = _feature_branch_diff_files(tmp_path)

        assert result == frozenset({"tests/test_foo__123.py"})

        merge_base_calls = [c for c in calls if "merge-base" in c]
        bases_tried = [c[-1] for c in merge_base_calls]
        assert "origin/develop" in bases_tried
        assert bases_tried[0] == "origin/develop", \
            "Without COMMANDER_MERGE_TARGET, origin/develop must be the first candidate"

    def test_sprint_branch_success_skips_develop(self, tmp_path, monkeypatch):
        """When sprint branch merge-base succeeds, develop is never consulted."""
        monkeypatch.setenv("COMMANDER_MERGE_TARGET", "sprint/sprint-42")

        merge_base_sha = "11223344"

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "merge-base" in cmd:
                base = cmd[-1]
                if base in ("origin/sprint/sprint-42", "sprint/sprint-42"):
                    return _make_run(0, merge_base_sha)
                return _make_run(1)
            if "diff" in cmd and "--name-only" in cmd:
                return _make_run(0, "some/file.py\n")
            return _make_run(1)

        with patch("subprocess.run", side_effect=fake_run):
            _feature_branch_diff_files(tmp_path)

        merge_base_calls = [c for c in calls if "merge-base" in c]
        bases_tried = [c[-1] for c in merge_base_calls]
        assert "origin/develop" not in bases_tried, \
            "develop must not be consulted when sprint branch succeeds"
        assert "develop" not in bases_tried, \
            "develop must not be consulted when sprint branch succeeds"
