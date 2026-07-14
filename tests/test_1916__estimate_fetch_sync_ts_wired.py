"""Tests for issue #1916 — estimation caller passes sync_ts to fetch_issue.

The stale-hit guard in fetch_issue (estimate_issue.py:140) is inert because the
sole production caller at line 592 did not pass sync_ts.  This test suite
verifies that the caller now wires sync_ts in so the guard is active.

AC mapping:
AC1  main() passes a non-None sync_ts to fetch_issue (guard activated at the call site)
AC2  sync_ts is a valid ISO-8601 UTC timestamp (not empty, not a placeholder)
AC3  stale mirror record during estimation triggers a live fetch (end-to-end guard check)
AC4  fresh mirror record during estimation uses mirror directly (no live fetch)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

import dispatch  # noqa: E402 - imports apps/dashboard/config.py so github_client import resolves correctly
import estimate_issue  # noqa: E402


# ── fixture helpers ───────────────────────────────────────────────────────────

def _make_mirror_issue(
    issue_num: int,
    body: str = "Issue body text",
    updated_at: str = "2026-07-09T10:00:00Z",
) -> dict:
    return {
        "number": issue_num,
        "title": f"Issue #{issue_num}",
        "state": "open",
        "labels": [{"name": "in-progress"}],
        "body": body,
        "updatedAt": updated_at,
        "updated_at": updated_at,
    }


def _make_live_runner(issue_num: int, body: str = "Live body"):
    def runner(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({
            "number": issue_num,
            "title": f"Issue #{issue_num}",
            "body": body,
            "labels": [],
        })
        r.stderr = ""
        return r
    return runner


def _run_main_capture_fetch(tmp_path, issue_num=42):
    """Run estimate_issue.main() with minimal mocking; return captured fetch_issue call kwargs."""
    captured = {}

    def fake_fetch_issue(num, repo, runner=None, sync_ts=None):
        captured["num"] = num
        captured["repo"] = repo
        captured["sync_ts"] = sync_ts
        return {
            "number": num,
            "title": f"Issue #{num}",
            "body": "## What & Why\nTest body.",
            "labels": [],
        }

    fake_estimate = {"size": "S", "confidence": "high", "effort_minutes": 5,
                     "files_likely_affected": [], "risks": [], "reasoning": "ok"}
    estimates_dir = tmp_path / "estimates"
    estimates_dir.mkdir()

    with patch.object(estimate_issue, "fetch_issue", side_effect=fake_fetch_issue), \
         patch.object(estimate_issue, "run_estimator", return_value=(fake_estimate, "")), \
         patch.object(estimate_issue, "load_calibration") as mock_cal, \
         patch.object(estimate_issue, "sqlite_calibration_records", return_value=[]), \
         patch.object(estimate_issue, "db_calibration_records", return_value=[]), \
         patch.object(estimate_issue, "find_commander_dir", return_value=tmp_path), \
         patch.object(estimate_issue, "mint_run_id", return_value="run-test"), \
         patch("sys.argv", ["estimate_issue.py", "--issue", str(issue_num), "--repo", "owner/repo"]):
        mock_cal.return_value = MagicMock(
            warnings=[], calibration_path=None, record_count=0
        )
        estimate_issue.main()

    return captured


# ── AC1: main() passes sync_ts to fetch_issue ────────────────────────────────

def test_ac1_main_passes_sync_ts_to_fetch_issue(tmp_path):
    """AC1: main() passes a non-None sync_ts kwarg to fetch_issue."""
    captured = _run_main_capture_fetch(tmp_path, issue_num=1916)
    assert captured.get("sync_ts") is not None, (
        "main() must pass sync_ts to fetch_issue so the stale-hit guard is active; "
        f"got sync_ts={captured.get('sync_ts')!r}"
    )


# ── AC2: sync_ts is a valid ISO-8601 UTC timestamp ───────────────────────────

_ISO8601_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?$")


def test_ac2_sync_ts_is_valid_iso8601_utc(tmp_path):
    """AC2: the sync_ts passed to fetch_issue is a non-empty ISO-8601 UTC string."""
    captured = _run_main_capture_fetch(tmp_path, issue_num=1916)
    ts = captured.get("sync_ts")
    assert ts and _ISO8601_UTC.match(ts), (
        f"sync_ts must be a valid ISO-8601 UTC timestamp, got: {ts!r}"
    )


# ── AC3: stale mirror record triggers live fetch ─────────────────────────────

def test_ac3_stale_mirror_record_triggers_live_fetch():
    """AC3: when the mirror record predates sync_ts, fetch_issue falls through to a live call."""
    stale_issue = _make_mirror_issue(1916, body="Old body", updated_at="2026-01-01T00:00:00Z")
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

    # Pass a sync_ts well after the stale record's updated_at.
    with patch("github_client._mirror_issue", return_value=stale_issue):
        result = estimate_issue.fetch_issue(
            1916, "owner/repo", runner=counting_runner, sync_ts="2026-07-09T12:00:00Z"
        )

    assert len(live_calls) == 1, (
        f"Stale mirror record + sync_ts must trigger exactly 1 live fetch; got {len(live_calls)}"
    )
    assert result["body"] == "Fresh live body"


# ── AC4: fresh mirror record uses mirror, no live fetch ──────────────────────

def test_ac4_fresh_mirror_record_uses_mirror_no_live_fetch():
    """AC4: when the mirror record is newer than sync_ts, mirror is used with no live call."""
    fresh_issue = _make_mirror_issue(1916, body="Fresh mirror body", updated_at="2026-07-09T12:00:00Z")
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 1916, "body": "Should not be used", "labels": []})
        r.stderr = ""
        return r

    # Pass a sync_ts older than the fresh record's updated_at.
    with patch("github_client._mirror_issue", return_value=fresh_issue):
        result = estimate_issue.fetch_issue(
            1916, "owner/repo", runner=counting_runner, sync_ts="2026-07-09T10:00:00Z"
        )

    assert live_calls == [], (
        f"Fresh mirror record must be served from mirror; got {len(live_calls)} live calls"
    )
    assert result["body"] == "Fresh mirror body"
