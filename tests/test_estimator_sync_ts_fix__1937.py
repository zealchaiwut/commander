"""Tests for issue #1937: [follow-up] estimator CLI passes mirror sync_ts, not now().

AC mapping:
AC1  estimate_issue.py main() passes the mirror's actual sync_ts (same helper as dispatch.py)
AC2  AC3/AC4 assertions in test_1916__estimate_fetch_sync_ts_wired.py corrected to
     fresh = updatedAt <= sync_ts; test suite passes
AC3  Source-regex test file test_fetch_issue_stale_guard__1916.py removed/consolidated
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

import dispatch  # noqa: E402 — side-effect: loads config
import estimate_issue  # noqa: E402


# --- AC1: main() passes mirror's actual sync_ts (same helper as dispatch) ---

def test_ac1_main_passes_mirror_sync_ts_not_datetime_now(tmp_path, monkeypatch):
    """AC1: main() must call _get_mirror_sync_ts and pass its result to fetch_issue,
    not datetime.now(). The guard is inert when sync_ts is the current time."""

    # Capture the sync_ts passed to fetch_issue
    captured = {}

    def fake_fetch_issue(num, repo, runner=None, sync_ts=None):
        captured["sync_ts"] = sync_ts
        return {
            "number": num,
            "title": f"Issue #{num}",
            "body": "## What\nTest",
            "labels": [],
        }

    fake_estimate = {
        "size": "S", "confidence": "high", "effort_minutes": 5,
        "files_likely_affected": [], "risks": [], "reasoning": "ok"
    }
    estimates_dir = tmp_path / "estimates"
    estimates_dir.mkdir()

    # Mock _get_mirror_sync_ts to return a known value (simulating the real
    # mirror's last-sync timestamp, which is older than now()).
    mock_sync_ts = "2026-07-09T10:00:00Z"

    with patch.object(estimate_issue, "fetch_issue", side_effect=fake_fetch_issue), \
         patch.object(estimate_issue, "run_estimator", return_value=(fake_estimate, "")), \
         patch.object(estimate_issue, "load_calibration") as mock_cal, \
         patch.object(estimate_issue, "sqlite_calibration_records", return_value=[]), \
         patch.object(estimate_issue, "db_calibration_records", return_value=[]), \
         patch.object(estimate_issue, "find_commander_dir", return_value=tmp_path), \
         patch.object(estimate_issue, "mint_run_id", return_value="run-test"), \
         patch("dispatch._get_mirror_sync_ts", return_value=mock_sync_ts), \
         patch("sys.argv", ["estimate_issue.py", "--issue", "1937", "--repo", "owner/repo"]):
        mock_cal.return_value = MagicMock(
            warnings=[], calibration_path=None, record_count=0
        )
        estimate_issue.main()

    # The sync_ts passed to fetch_issue must be the mirror's sync_ts,
    # not datetime.now() (which would be far in the future relative to the mock).
    assert captured.get("sync_ts") == mock_sync_ts, (
        f"main() must pass dispatch._get_mirror_sync_ts() result to fetch_issue, "
        f"not datetime.now(). Expected {mock_sync_ts!r}, got {captured.get('sync_ts')!r}"
    )


# --- AC2: test_1916 AC3/AC4 assertions corrected (stale/fresh logic) ---

def test_ac2_ac3_inverted_assertion_fixed():
    """AC2a: test_1916 AC3 assertion corrected.

    The bug: AC3 asserted stale.updated_at < sync_ts triggers live fetch.
    The fix: stale = updated_at > sync_ts (mirror is NEWER than last sync, so stale).
             fresh = updated_at <= sync_ts (mirror is old or same as last sync, so fresh/reliable).

    When mirror.updatedAt='2026-01-01' and sync_ts='2026-07-09T12:00:00Z':
    - updatedAt <= sync_ts → True → mirror is fresh (no live fetch needed)
    - Before the fix, the inverted test expected a live fetch; now it should NOT.
    """
    stale_issue = {
        "number": 1916,
        "title": "Issue #1916",
        "body": "Old body",
        "updatedAt": "2026-01-01T00:00:00Z",
    }
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({
            "number": 1916, "title": "Issue #1916", "body": "Fresh live body", "labels": []
        })
        r.stderr = ""
        return r

    # sync_ts is AFTER the old record's updated_at.
    # Since updated_at (2026-01-01) <= sync_ts (2026-07-09), mirror is FRESH.
    # The corrected logic: NO live fetch should happen.
    with patch("github_client._mirror_issue", return_value=stale_issue):
        result = estimate_issue.fetch_issue(
            1916, "owner/repo", runner=counting_runner, sync_ts="2026-07-09T12:00:00Z"
        )

    assert live_calls == [], (
        f"When mirror.updatedAt <= sync_ts, mirror is FRESH and should be used; "
        f"got {len(live_calls)} live fetch calls (should be 0). "
        f"Result body should be 'Old body', got {result['body']!r}"
    )
    assert result["body"] == "Old body"


def test_ac2_ac4_inverted_assertion_fixed():
    """AC2b: test_1916 AC4 assertion corrected.

    The bug: AC4 asserted fresh.updated_at > sync_ts uses mirror (inverted).
    The fix: stale = updated_at > sync_ts (mirror NEWER than last sync, so stale).
             fresh = updated_at <= sync_ts (mirror OLDER than or same as sync, so safe).

    When mirror.updatedAt='2026-07-09T12:00:00Z' and sync_ts='2026-07-09T10:00:00Z':
    - updatedAt > sync_ts → True → mirror is STALE (live fetch needed).
    - Before the fix, the inverted test expected no live fetch; now it SHOULD fetch.
    """
    stale_issue = {
        "number": 1916,
        "title": "Issue #1916",
        "body": "Stale mirror body",
        "updatedAt": "2026-07-09T12:00:00Z",  # AFTER sync_ts
    }
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({
            "number": 1916, "title": "Issue #1916", "body": "Fresh live body", "labels": []
        })
        r.stderr = ""
        return r

    # sync_ts is BEFORE the record's updated_at.
    # Since updated_at (2026-07-09T12:00) > sync_ts (2026-07-09T10:00), mirror is STALE.
    # The corrected logic: live fetch SHOULD happen.
    with patch("github_client._mirror_issue", return_value=stale_issue):
        result = estimate_issue.fetch_issue(
            1916, "owner/repo", runner=counting_runner, sync_ts="2026-07-09T10:00:00Z"
        )

    assert len(live_calls) == 1, (
        f"When mirror.updatedAt > sync_ts, mirror is STALE and live fetch is needed; "
        f"got {len(live_calls)} live calls (should be 1)"
    )
    assert result["body"] == "Fresh live body"


# --- AC3: source-regex test file deletion ---

def test_ac3_stale_guard_test_file_deleted():
    """AC3: test_fetch_issue_stale_guard__1916.py (source-regex-only test) is deleted.

    The file violates issue #1746 (AC tests must exercise behavior, not source text).
    This test verifies it no longer exists.
    """
    stale_test_path = Path(__file__).parent / "test_fetch_issue_stale_guard__1916.py"
    assert not stale_test_path.exists(), (
        f"Source-regex test file must be deleted per #1746 and #1937 AC3. "
        f"Found at {stale_test_path}"
    )
