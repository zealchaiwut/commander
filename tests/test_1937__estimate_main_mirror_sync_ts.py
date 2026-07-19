"""Tests for issue #1937 AC1 — estimate_issue.main() uses mirror's actual sync_ts.

The root bug: main() used datetime.now(utc) as sync_ts, making updatedAt <= sync_ts
always true and the stale-hit guard a no-op on the CLI path.

AC1: main() calls _get_mirror_sync_ts(repo) so the real mirror last-sync
     timestamp gates the stale check, not wall-clock now.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

import dispatch  # noqa: E402 — side-effect import: loads config so github_client resolves
import estimate_issue  # noqa: E402


_FIXED_MIRROR_TS = "2026-06-01T08:00:00Z"


def _run_main_with_mirror_ts(tmp_path, mirror_ts=_FIXED_MIRROR_TS):
    """Run estimate_issue.main(), patching _get_mirror_sync_ts; return captured values."""
    captured = {}

    def fake_fetch_issue(num, repo, runner=None, sync_ts=None):
        captured["sync_ts"] = sync_ts
        captured["repo"] = repo
        return {"number": num, "title": "x", "body": "## What\nOk.", "labels": []}

    fake_estimate = {
        "size": "S", "confidence": "high", "effort_minutes": 5,
        "files_likely_affected": [], "risks": [], "reasoning": "ok",
    }
    (tmp_path / "estimates").mkdir()

    with patch.object(estimate_issue, "fetch_issue", side_effect=fake_fetch_issue), \
         patch.object(estimate_issue, "_get_mirror_sync_ts", return_value=mirror_ts) as mock_ts, \
         patch.object(estimate_issue, "run_estimator", return_value=(fake_estimate, "")), \
         patch.object(estimate_issue, "load_calibration") as mock_cal, \
         patch.object(estimate_issue, "sqlite_calibration_records", return_value=[]), \
         patch.object(estimate_issue, "db_calibration_records", return_value=[]), \
         patch.object(estimate_issue, "find_commander_dir", return_value=tmp_path), \
         patch.object(estimate_issue, "mint_run_id", return_value="run-test"), \
         patch("sys.argv", ["estimate_issue.py", "--issue", "1937", "--repo", "owner/repo"]):
        mock_cal.return_value = MagicMock(
            warnings=[], calibration_path=None, record_count=0
        )
        captured["mock_ts"] = mock_ts
        estimate_issue.main()

    return captured


# ── AC1a: _get_mirror_sync_ts is called with the resolved repo ────────────────

def test_ac1a_main_calls_get_mirror_sync_ts(tmp_path):
    """AC1: main() must call _get_mirror_sync_ts(repo) to get the actual mirror timestamp."""
    captured = _run_main_with_mirror_ts(tmp_path)
    captured["mock_ts"].assert_called_once_with("owner/repo")


# ── AC1b: the mirror timestamp is forwarded to fetch_issue ────────────────────

def test_ac1b_mirror_ts_forwarded_to_fetch_issue(tmp_path):
    """AC1: the sync_ts passed to fetch_issue must equal _get_mirror_sync_ts's return value."""
    captured = _run_main_with_mirror_ts(tmp_path, mirror_ts=_FIXED_MIRROR_TS)
    assert captured.get("sync_ts") == _FIXED_MIRROR_TS, (
        f"main() must forward mirror sync_ts to fetch_issue; "
        f"got {captured.get('sync_ts')!r}, want {_FIXED_MIRROR_TS!r}"
    )


# ── AC1c: None propagates when mirror has no record (guard skipped) ───────────

def test_ac1c_none_propagates_when_mirror_has_no_record(tmp_path):
    """AC1: when mirror has no sync record, None propagates so the guard is skipped."""
    captured = _run_main_with_mirror_ts(tmp_path, mirror_ts=None)
    assert captured.get("sync_ts") is None, (
        "When _get_mirror_sync_ts returns None, fetch_issue must receive sync_ts=None "
        f"(guard skipped); got {captured.get('sync_ts')!r}"
    )


# ── AC1d: _get_mirror_sync_ts helper exists and is patchable ─────────────────

def test_ac1d_get_mirror_sync_ts_helper_is_defined():
    """AC1: estimate_issue must expose _get_mirror_sync_ts so callers can patch it."""
    assert hasattr(estimate_issue, "_get_mirror_sync_ts"), (
        "estimate_issue must define _get_mirror_sync_ts for main() and tests to use"
    )
    assert callable(estimate_issue._get_mirror_sync_ts)
