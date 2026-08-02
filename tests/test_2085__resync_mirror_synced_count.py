"""Tests for issue #2085 — resync_issues_mirror.py summary always reports '0 rows changed'.

Root cause: result.get("total", 0) on line 147, but full_sync_issues_mirror()
returns {"status", "synced", "rate_limited"} — no "total" key — so rows is always 0.
Fix: use result.get("synced", 0) instead.

AC: When full_sync_issues_mirror returns {"synced": N, ...}, the per-repo line
and the final summary line must reflect N, not 0.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"

for _p in (str(_SCRIPTS_DIR), str(_DASHBOARD_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import resync_issues_mirror  # noqa: E402


def _mock_ges(synced: int = 42) -> MagicMock:
    """Return a mock github_events_sync whose full_sync_issues_mirror returns
    the real dict shape: {status, synced, rate_limited}."""
    mock = MagicMock()
    mock.full_sync_issues_mirror.return_value = {
        "status": 200,
        "synced": synced,
        "rate_limited": False,
    }
    return mock


class TestResyncSyncedCountReported:
    """full_sync_issues_mirror returns 'synced' key; summary must reflect that count."""

    def test_per_repo_line_shows_synced_count(self, tmp_path, capsys):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _mock_ges(synced=42)
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(resync_issues_mirror, "_sprint_counts", return_value={}), \
             patch.object(sys, "argv", [
                 "resync_issues_mirror.py", "--yes", "--repo", "owner/repo"
             ]):
            resync_issues_mirror.main()
        out = capsys.readouterr().out
        assert "42 rows" in out, (
            f"Per-repo line must show the actual synced count (42), got: {out!r}"
        )

    def test_summary_line_shows_synced_count(self, tmp_path, capsys):
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _mock_ges(synced=7)
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(resync_issues_mirror, "_sprint_counts", return_value={}), \
             patch.object(sys, "argv", [
                 "resync_issues_mirror.py", "--yes", "--repo", "owner/repo"
             ]):
            resync_issues_mirror.main()
        out = capsys.readouterr().out
        assert "7 rows changed" in out, (
            f"Summary line must report the actual rows changed (7), got: {out!r}"
        )

    def test_summary_not_always_zero(self, tmp_path, capsys):
        """The bug: result.get('total', 0) → 0 always. After fix this must be nonzero."""
        db_file = tmp_path / "test.db"
        db_file.touch()
        mock_ges = _mock_ges(synced=99)
        with patch.dict(os.environ, {"DB_PATH": str(db_file)}), \
             patch.dict(sys.modules, {"github_events_sync": mock_ges}), \
             patch.object(resync_issues_mirror, "_sprint_counts", return_value={}), \
             patch.object(sys, "argv", [
                 "resync_issues_mirror.py", "--yes", "--repo", "owner/repo"
             ]):
            resync_issues_mirror.main()
        out = capsys.readouterr().out
        assert "0 rows changed" not in out, (
            f"Summary must not show '0 rows changed' when 99 issues were synced, got: {out!r}"
        )
