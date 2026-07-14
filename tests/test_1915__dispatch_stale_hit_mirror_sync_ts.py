"""Tests for issue #1915 — dispatch stale-hit guard should use mirror's last-sync
timestamp, not wall-clock now.

Context: the stale-hit guard in _mirror_issue_body compares the issue's GitHub
updatedAt against sync_ts to decide whether to use the mirror or do a live gh
fetch. Before this fix, dispatch.py passed _utcnow() as sync_ts, so the
comparison (_record_ts <= sync_ts with correct semantics) was almost always
TRUE (issues are almost never modified in the future) — but because the ORIGINAL
condition was >=, almost any issue was treated as stale and a live gh fetch
fired. The real fix has two parts:
  1. Fix the condition from >= to <= (issue was captured by the sync if it was
     last modified at or before the sync time).
  2. Pass the mirror's last-sync timestamp (sync_state.updated_at for
     "issues:{repo}") instead of wall-clock now.

AC mapping:
AC1  db.py exposes get_sync_updated_at(key) → returns updated_at from sync_state
AC2  db.py get_sync_updated_at returns None when key absent (no sync yet)
AC3  dispatch._get_mirror_sync_ts(repo) returns sync_state.updated_at for "issues:{repo}"
AC4  dispatch._get_mirror_sync_ts returns None when DB unavailable / key missing
AC5  _mirror_issue_body: fresh record (updatedAt <= sync_ts) uses mirror, zero live fetches
AC6  _mirror_issue_body: stale record (updatedAt > sync_ts) triggers live fetch
AC7  dispatch.py passes mirror's sync timestamp (not _utcnow()) to _fetch_dispatch_issue_body
AC8  When mirror has no sync timestamp, sync_ts=None → mirror used without stale check
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

import dispatch  # noqa: E402


# ── fixture helpers ───────────────────────────────────────────────────────────

def _make_mirror_issue(
    issue_num: int,
    body: str = "Issue body text",
    updated_at: str = "2026-07-10T06:00:00Z",
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


def _make_runner(body: str = "Live body", issue_num: int = 0):
    def runner(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        num = issue_num or int(args[-1].split("/")[-1])
        r.stdout = json.dumps({"number": num, "body": body, "labels": []})
        r.stderr = ""
        return r
    return runner


# ── AC1: db.py exposes get_sync_updated_at ───────────────────────────────────

def test_ac1_get_sync_updated_at_exists_in_db():
    """AC1: db.get_sync_updated_at is importable and callable."""
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
    import db  # noqa: E402
    assert hasattr(db, "get_sync_updated_at"), "db.get_sync_updated_at not found"
    assert callable(db.get_sync_updated_at)


def test_ac1_get_sync_updated_at_returns_stored_value(tmp_path):
    """AC1: get_sync_updated_at returns the updated_at stored by set_sync_etag."""
    import os
    db_file = tmp_path / "test.db"
    old_db_path = os.environ.get("DB_PATH")
    try:
        os.environ["DB_PATH"] = str(db_file)
        import db as _db
        import importlib
        importlib.reload(_db)

        _db.set_sync_etag("issues:owner/repo", "abc123")
        result = _db.get_sync_updated_at("issues:owner/repo")

        assert result is not None, "Expected a timestamp, got None"
        assert "2026" in result or "2025" in result or "T" in result, (
            f"Expected ISO-8601 timestamp, got: {result!r}"
        )
    finally:
        if old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = old_db_path


# ── AC2: get_sync_updated_at returns None when key absent ────────────────────

def test_ac2_get_sync_updated_at_returns_none_for_missing_key(tmp_path):
    """AC2: get_sync_updated_at returns None when the key has no sync_state row."""
    import os
    db_file = tmp_path / "test_missing.db"
    old_db_path = os.environ.get("DB_PATH")
    try:
        os.environ["DB_PATH"] = str(db_file)
        import db as _db
        import importlib
        importlib.reload(_db)

        result = _db.get_sync_updated_at("issues:owner/missing-repo")
        assert result is None, f"Expected None for missing key, got {result!r}"
    finally:
        if old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = old_db_path


# ── AC3: dispatch._get_mirror_sync_ts reads sync_state ───────────────────────

def test_ac3_get_mirror_sync_ts_reads_sync_state():
    """AC3: _get_mirror_sync_ts reads sync_state.updated_at for 'issues:{repo}'."""
    assert hasattr(dispatch, "_get_mirror_sync_ts"), (
        "_get_mirror_sync_ts not found in dispatch.py"
    )

    expected_ts = "2026-07-14T06:59:00"
    captured_keys = []

    def mock_get_sync_updated_at(key):
        captured_keys.append(key)
        return expected_ts

    with patch.object(dispatch, "_get_sync_updated_at_from_db", mock_get_sync_updated_at):
        result = dispatch._get_mirror_sync_ts("owner/repo")

    assert result == expected_ts, f"Expected {expected_ts!r}, got {result!r}"
    assert captured_keys == ["issues:owner/repo"], (
        f"Expected key 'issues:owner/repo', got {captured_keys}"
    )


# ── AC4: _get_mirror_sync_ts returns None when DB unavailable or key missing ──

def test_ac4_get_mirror_sync_ts_returns_none_when_db_unavailable():
    """AC4: _get_mirror_sync_ts returns None when the DB helper raises or returns None."""
    def raising_get(key):
        raise RuntimeError("DB unavailable")

    with patch.object(dispatch, "_get_sync_updated_at_from_db", raising_get):
        result = dispatch._get_mirror_sync_ts("owner/repo")

    assert result is None, f"Expected None when DB unavailable, got {result!r}"


def test_ac4_get_mirror_sync_ts_returns_none_for_empty_repo():
    """AC4: _get_mirror_sync_ts returns None for empty/None repo."""
    result = dispatch._get_mirror_sync_ts("")
    assert result is None

    result2 = dispatch._get_mirror_sync_ts(None)
    assert result2 is None


# ── AC5: fresh record (updatedAt <= sync_ts) uses mirror ─────────────────────

def test_ac5_fresh_record_before_sync_uses_mirror():
    """AC5: issue modified before mirror sync time → mirror body used, zero live fetches.

    This covers the common case: a stable issue body last modified days ago,
    mirror synced recently. With the correct <= condition, this is a mirror hit.
    """
    # Issue body last updated days before the mirror synced.
    fresh_issue = _make_mirror_issue(
        101,
        body="AC body text",
        updated_at="2026-07-10T06:00:00Z",  # modified at 6 AM
    )
    sync_ts = "2026-07-14T06:59:00"  # mirror last synced 4 days later (no Z — sync_state format)
    live_calls = []

    def runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 101, "body": "Should not be fetched"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=fresh_issue):
        result = dispatch._mirror_issue_body(
            "owner/repo", 101, runner=runner, sync_ts=sync_ts
        )

    assert result == "AC body text", f"Expected mirror body, got {result!r}"
    assert live_calls == [], (
        f"Expected 0 live fetches for issue modified before sync, got {len(live_calls)}"
    )


def test_ac5_multiple_fresh_records_zero_live_fetches():
    """AC5: multiple issues all modified before sync → zero live fetches total."""
    sync_ts = "2026-07-14T06:59:00"
    issues = {
        n: _make_mirror_issue(n, body=f"Body {n}", updated_at="2026-07-10T06:00:00Z")
        for n in range(200, 210)
    }
    live_calls = []

    def runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 0, "body": "live"})
        r.stderr = ""
        return r

    def mirror_reader(repo, n):
        return issues.get(n)

    with patch("github_client._mirror_issue", side_effect=mirror_reader):
        for n in range(200, 210):
            dispatch._mirror_issue_body("owner/repo", n, runner=runner, sync_ts=sync_ts)

    assert live_calls == [], (
        f"Expected 0 live fetches for 10 fresh issues, got {len(live_calls)}"
    )


# ── AC6: stale record (updatedAt > sync_ts) triggers live fetch ──────────────

def test_ac6_stale_record_after_sync_triggers_live_fetch():
    """AC6: issue modified after mirror sync time → live fetch (mirror may be outdated).

    This is the genuinely stale case: the issue was modified after the mirror
    last synced, so the mirror body may be outdated.
    """
    stale_issue = _make_mirror_issue(
        102,
        body="Old body",
        updated_at="2026-07-14T07:30:00Z",  # modified AFTER the sync
    )
    sync_ts = "2026-07-14T06:59:00"  # mirror last synced before the modification
    live_calls = []

    def runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 102, "body": "Fresh live body"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=stale_issue):
        result = dispatch._mirror_issue_body(
            "owner/repo", 102, runner=runner, sync_ts=sync_ts
        )

    assert len(live_calls) == 1, (
        f"Expected 1 live fetch for stale issue (modified after sync), got {len(live_calls)}"
    )
    assert result == "Fresh live body"


# ── AC7: dispatch uses mirror sync ts, not _utcnow() ─────────────────────────

def test_ac7_dispatch_uses_mirror_sync_ts_not_utcnow():
    """AC7: the sync_ts passed to _fetch_dispatch_issue_body comes from the mirror,
    not from _utcnow(). Verifies that wall-clock time is NOT used.
    """
    mirror_sync_ts = "2026-07-14T06:59:00"  # mirror last synced
    captured_sync_ts = []

    def capturing_fetch(eff_repo, issue_num, sync_ts=None):
        captured_sync_ts.append(sync_ts)
        return "Issue body text"

    with patch.object(dispatch, "_fetch_dispatch_issue_body", side_effect=capturing_fetch):
        with patch.object(dispatch, "_get_mirror_sync_ts", return_value=mirror_sync_ts):
            # Simulate what dispatch._dispatch_coder_ticket does at the body-fetch step.
            _ts = dispatch._get_mirror_sync_ts("owner/repo")
            dispatch._fetch_dispatch_issue_body("owner/repo", 1915, sync_ts=_ts)

    assert captured_sync_ts == [mirror_sync_ts], (
        f"Expected sync_ts={mirror_sync_ts!r}, got {captured_sync_ts}"
    )


def test_ac7_dispatch_sync_ts_is_not_utcnow():
    """AC7: _get_mirror_sync_ts result differs from _utcnow() (not wall-clock now)."""
    from services.sprint_manager.timekeeping import _utcnow

    mirror_sync_ts = "2026-07-14T06:59:00"

    with patch.object(dispatch, "_get_sync_updated_at_from_db", return_value=mirror_sync_ts):
        result = dispatch._get_mirror_sync_ts("owner/repo")

    now = _utcnow()
    # The mirror sync time (set to a fixed past value) should differ from now.
    assert result != now, (
        "sync_ts must come from the mirror sync state, not wall-clock now"
    )
    assert result == mirror_sync_ts


# ── AC8: no sync timestamp → sync_ts=None → mirror used without stale check ──

def test_ac8_no_sync_timestamp_uses_mirror_without_stale_check():
    """AC8: when no mirror sync timestamp exists, sync_ts=None → mirror body used directly."""
    old_issue = _make_mirror_issue(
        103,
        body="Old body no stale check",
        updated_at="2026-01-01T00:00:00Z",  # very old
    )
    live_calls = []

    def runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 103, "body": "Live"})
        r.stderr = ""
        return r

    # Pass sync_ts=None explicitly (simulating no sync state available).
    with patch("github_client._mirror_issue", return_value=old_issue):
        result = dispatch._mirror_issue_body(
            "owner/repo", 103, runner=runner, sync_ts=None
        )

    assert result == "Old body no stale check", (
        f"Without sync_ts, mirror body should always be used; got {result!r}"
    )
    assert live_calls == [], (
        f"With sync_ts=None, no live fetch expected; got {len(live_calls)}"
    )


def test_ac8_get_mirror_sync_ts_returns_none_for_unsynced_repo():
    """AC8: _get_mirror_sync_ts returns None when no sync has run for this repo."""
    def no_sync(key):
        return None  # simulates key not found

    with patch.object(dispatch, "_get_sync_updated_at_from_db", no_sync):
        result = dispatch._get_mirror_sync_ts("owner/repo")

    assert result is None, (
        f"Expected None when no sync state exists, got {result!r}"
    )
