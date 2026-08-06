"""Tests for issue #2000: freshness guard must normalize Z/no-Z before comparing.

Bug: `_is_fresh = _record_ts <= sync_ts` is a raw string compare. When
`_record_ts` carries a trailing Z (from GitHub API) and `sync_ts` does not
(old DB rows written before utc_now() was updated), equal-second records are
misjudged as stale and trigger a needless live gh REST call.

AC1: Parse both operands to datetime before comparing; equal-second Z vs no-Z
     is treated as FRESH (no live fetch).
AC2: Real `_get_mirror_sync_ts` runs against a seeded sync_state row; the
     returned value participates correctly in the datetime comparison.
"""
from __future__ import annotations

import os
import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

# Default DB_PATH so db import doesn't crash in isolation.
os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2000.db")

import db as _db_module  # noqa: E402
import estimate_issue  # noqa: E402


# ── AC1: Z vs no-Z normalization in freshness comparison ─────────────────────

def test_ac1_equal_second_z_record_vs_no_z_sync_ts_is_fresh():
    """AC1: mirror record with Z-suffix == sync_ts without Z → FRESH, no live fetch.

    Old string compare: "...00Z" > "...00" → is_fresh=False (stale, wrong).
    Fixed datetime compare: equal instants → is_fresh=True (fresh, correct).
    """
    # _record_ts has Z (from GitHub API mirror), sync_ts has no Z (old DB row)
    record_ts = "2026-01-15T08:30:00Z"
    sync_ts_no_z = "2026-01-15T08:30:00"  # same instant, no Z

    # Reproduce the comparison exactly as fetch_issue does it post-fix.
    from estimate_issue import _parse_ts_for_compare
    is_fresh = _parse_ts_for_compare(record_ts) <= _parse_ts_for_compare(sync_ts_no_z)

    assert is_fresh, (
        f"record_ts={record_ts!r} == sync_ts={sync_ts_no_z!r} (same instant) "
        f"must be FRESH; old string compare gave False because Z sorts after digits"
    )


def test_ac1_record_z_after_sync_ts_is_stale():
    """AC1: record updated AFTER sync_ts is still correctly detected as stale."""
    record_ts = "2026-01-15T09:00:00Z"   # later
    sync_ts = "2026-01-15T08:00:00Z"     # earlier

    from estimate_issue import _parse_ts_for_compare
    is_fresh = _parse_ts_for_compare(record_ts) <= _parse_ts_for_compare(sync_ts)

    assert not is_fresh, (
        f"record_ts={record_ts!r} > sync_ts={sync_ts!r} must be STALE"
    )


def test_ac1_record_before_sync_ts_is_fresh():
    """AC1: record updated BEFORE sync_ts is correctly detected as fresh."""
    record_ts = "2026-01-15T07:00:00Z"
    sync_ts = "2026-01-15T08:00:00"  # no Z

    from estimate_issue import _parse_ts_for_compare
    is_fresh = _parse_ts_for_compare(record_ts) <= _parse_ts_for_compare(sync_ts)

    assert is_fresh, (
        f"record_ts={record_ts!r} < sync_ts={sync_ts!r} must be FRESH"
    )


def test_ac1_fetch_issue_no_live_fetch_on_equal_second_z_no_z(monkeypatch):
    """AC1: fetch_issue returns mirror (no live gh call) when Z/no-Z timestamps are equal."""
    mirror_record = {
        "number": 42,
        "title": "Test",
        "body": "Mirror body",
        "updatedAt": "2026-03-10T12:00:00Z",  # Z-suffixed (from GitHub API)
        "state": "open",
        "labels": [],
    }
    live_calls = []

    def mock_runner(args, **kwargs):
        live_calls.append(args)
        r = MagicMock()
        r.stdout = '{"number":42,"title":"Test","body":"Live","labels":[]}'
        r.returncode = 0
        r.stderr = ""
        return r

    # sync_ts has no Z — same instant as mirror's updatedAt
    sync_ts_no_z = "2026-03-10T12:00:00"

    with patch("github_client._mirror_issue", return_value=mirror_record):
        result = estimate_issue.fetch_issue(
            42, "owner/repo",
            runner=mock_runner,
            sync_ts=sync_ts_no_z,
        )

    assert len(live_calls) == 0, (
        f"Z-suffixed record_ts equal to no-Z sync_ts must use mirror (no live fetch); "
        f"live_calls={live_calls}"
    )
    assert result["body"] == "Mirror body"


# ── AC2: real _get_mirror_sync_ts against a seeded sync_state row ─────────────

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated SQLite; yields the db module pointed at a temp file."""
    db_file = tmp_path / "test_2000.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def test_ac2_real_get_mirror_sync_ts_reads_seeded_row(fresh_db):
    """AC2: _get_mirror_sync_ts returns the stored timestamp from a real sync_state row.

    Seeds sync_state via db.set_sync_etag, then calls the real _get_mirror_sync_ts
    (no mock) to verify it reads back the DB value correctly.
    """
    repo = "owner/repo"
    fresh_db.set_sync_etag(f"issues:{repo}", '"etag-abc"')

    # Point estimate_issue's lazy db import to the same temp DB.
    with patch.dict(os.environ, {"DB_PATH": str(fresh_db.DB_PATH)}):
        # Patch DB_PATH on the already-imported db module so _get_mirror_sync_ts finds it.
        with patch.object(_db_module, "DB_PATH", fresh_db.DB_PATH):
            ts = estimate_issue._get_mirror_sync_ts(repo)

    assert ts is not None, "_get_mirror_sync_ts must return a non-None string after seeding"
    assert isinstance(ts, str), f"_get_mirror_sync_ts must return str; got {type(ts)}"
    # The stored timestamp should be parseable (no ValueError).
    from estimate_issue import _parse_ts_for_compare
    parsed = _parse_ts_for_compare(ts)
    from datetime import datetime
    assert isinstance(parsed, datetime), (
        f"_get_mirror_sync_ts returned {ts!r} which is not parseable to datetime"
    )


def test_ac2_real_sync_ts_freshness_comparison_correct(fresh_db):
    """AC2: freshness comparison with real sync_ts (from DB) and Z-suffixed mirror record.

    Seeds the DB with a timestamp, retrieves it via the real helper, then
    checks that a mirror record with the *same* instant but explicit Z suffix
    is judged FRESH (not stale) by the datetime comparison.
    """
    repo = "owner/repo-freshness"
    fresh_db.set_sync_etag(f"issues:{repo}", '"etag-xyz"')

    with patch.object(_db_module, "DB_PATH", fresh_db.DB_PATH):
        real_sync_ts = estimate_issue._get_mirror_sync_ts(repo)

    assert real_sync_ts is not None

    # Simulate a GitHub mirror record with the same instant but +00:00 suffix
    # (or convert the stored value to the other format for cross-format comparison).
    from estimate_issue import _parse_ts_for_compare
    from datetime import timezone

    parsed_sync = _parse_ts_for_compare(real_sync_ts)

    # Build a "same instant" record_ts in a different format.
    if real_sync_ts.endswith("Z"):
        # Stored with Z; simulate a no-Z record
        record_ts_alt = real_sync_ts[:-1]  # strip Z
    else:
        # Stored without Z; simulate a Z-suffixed record
        record_ts_alt = real_sync_ts + "Z"

    is_fresh = _parse_ts_for_compare(record_ts_alt) <= parsed_sync

    assert is_fresh, (
        f"Same-instant comparison across Z/no-Z formats must be FRESH; "
        f"real_sync_ts={real_sync_ts!r}, record_ts_alt={record_ts_alt!r}"
    )
