"""Tests for issue #2054 — resync_issues_mirror.py must have argparse.

AC1: -h/--help exits 0 without touching GitHub or the DB.
AC2: No --yes → dry-run summary printed, exit non-zero.
AC3: --repo scopes the would-be resync to one repository.
AC5 (behavioral, per CLAUDE.md #1746): --help and bare invocation make zero
     GitHub calls and zero DB writes — verified by patching the GH client
     with side_effect=AssertionError and asserting DB file mtime is unchanged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"

for _p in (str(_SCRIPTS_DIR), str(_DASHBOARD_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import resync_issues_mirror  # noqa: E402


def _make_mock_ges() -> MagicMock:
    """Return a mock github_events_sync that raises if full_sync_issues_mirror is called."""
    mock = MagicMock()
    mock.full_sync_issues_mirror.side_effect = AssertionError(
        "full_sync_issues_mirror must not be called in dry-run or --help mode"
    )
    return mock


# ── AC1 + AC5: --help ─────────────────────────────────────────────────────────

class TestHelpExitsSafelyWithNoSideEffects:
    """--help must exit 0, make zero GH calls, and leave the DB file untouched."""

    def test_help_exits_zero(self, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": _make_mock_ges()}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                resync_issues_mirror.main()
        assert exc_info.value.code == 0

    def test_help_does_not_write_db(self, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mtime_before = db_file.stat().st_mtime
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": _make_mock_ges()}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--help"]):
            with pytest.raises(SystemExit):
                resync_issues_mirror.main()
        assert db_file.stat().st_mtime == mtime_before

    def test_help_makes_no_github_calls(self, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _make_mock_ges()
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--help"]):
            with pytest.raises(SystemExit):
                resync_issues_mirror.main()
        assert mock_ges.full_sync_issues_mirror.call_count == 0


# ── AC2 + AC5: no --yes ───────────────────────────────────────────────────────

class TestNoYesFlagDryRunWithNoSideEffects:
    """Without --yes, print dry-run summary and exit non-zero — no GH calls, no DB writes."""

    def test_no_yes_exits_nonzero(self, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _make_mock_ges()
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--repo", "test/repo"]):
            result = resync_issues_mirror.main()
        assert result != 0

    def test_no_yes_does_not_write_db(self, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mtime_before = db_file.stat().st_mtime
        mock_ges = _make_mock_ges()
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--repo", "test/repo"]):
            resync_issues_mirror.main()
        assert db_file.stat().st_mtime == mtime_before

    def test_no_yes_makes_no_github_calls(self, tmp_path):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _make_mock_ges()
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--repo", "test/repo"]):
            resync_issues_mirror.main()
        assert mock_ges.full_sync_issues_mirror.call_count == 0

    def test_no_yes_prints_what_would_happen(self, tmp_path, capsys):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _make_mock_ges()
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--repo", "test/repo"]):
            resync_issues_mirror.main()
        out = capsys.readouterr().out
        assert "test/repo" in out
        # Must explain how to perform the resync
        assert "--yes" in out or "--force" in out


# ── AC3: --repo scoping ───────────────────────────────────────────────────────

class TestRepoFlagScoping:
    """--repo limits the dry-run to a single repository."""

    def test_repo_flag_shows_only_specified_repo(self, tmp_path, capsys):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _make_mock_ges()
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(sys, "argv", ["resync_issues_mirror.py", "--repo", "owner/only-this"]):
            resync_issues_mirror.main()
        out = capsys.readouterr().out
        assert "owner/only-this" in out
        assert "1 repo" in out

    def test_repo_flag_scopes_live_sync(self, tmp_path):
        """--yes --repo calls full_sync_issues_mirror for exactly that repo."""
        db_file = tmp_path / "test.db"
        db_file.touch()

        mock_ges = MagicMock()
        mock_ges.full_sync_issues_mirror.return_value = {"total": 5, "rate_limited": False}

        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(resync_issues_mirror, "_sprint_counts", return_value={}), \
             patch.object(sys, "argv", [
                 "resync_issues_mirror.py", "--yes", "--repo", "owner/just-this"
             ]):
            resync_issues_mirror.main()

        calls = [c.args[0] for c in mock_ges.full_sync_issues_mirror.call_args_list]
        assert calls == ["owner/just-this"]
