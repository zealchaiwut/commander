"""Behavioral tests for issue #2067 — one UTC timestamp format.

AC1: a shared utc_now() helper exists and every write site uses it.
AC3: readers tolerate all three legacy forms + new Z form.
AC5: round-trip write/read asserts emitted format; parse test covers all shapes.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

# DB_PATH must be set by conftest before db is imported.
import apps.dashboard.db as db


_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ── AC5 / AC1: utc_now() emits the canonical format ──────────────────────────

def test_utc_now_format():
    """utc_now() must return YYYY-MM-DDTHH:MM:SSZ with no microseconds."""
    ts = db.utc_now()
    assert _ISO_Z_RE.match(ts), f"utc_now() returned unexpected format: {ts!r}"


def test_utc_now_is_utc():
    """utc_now() must be within 5 seconds of the real UTC clock."""
    ts = db.utc_now()
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    diff = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert diff < 5, f"utc_now() differs from UTC clock by {diff}s"


# ── AC5: round-trip — write via upsert_agent, read back, check format ────────

def test_upsert_agent_last_seen_format(tmp_path):
    """upsert_agent() must store last_seen in YYYY-MM-DDTHH:MM:SSZ format."""
    db_file = tmp_path / "test_round_trip.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row

    # Bootstrap the agents table (mirrors init_db logic)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            session_id TEXT PRIMARY KEY,
            name TEXT,
            working_dir TEXT,
            status TEXT,
            last_tool TEXT,
            last_seen TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()

    # Point db module at our temp file for the duration of this test
    original_path = db.DB_PATH
    db.DB_PATH = db_file
    try:
        db.upsert_agent("sess-test-2067", "/tmp/test", "working")
        conn2 = sqlite3.connect(str(db_file))
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT last_seen, created_at FROM agents WHERE session_id=?",
            ("sess-test-2067",),
        ).fetchone()
        conn2.close()
        assert row is not None, "upsert_agent did not insert a row"
        assert _ISO_Z_RE.match(row["last_seen"]), (
            f"last_seen format wrong: {row['last_seen']!r}"
        )
        assert _ISO_Z_RE.match(row["created_at"]), (
            f"created_at format wrong: {row['created_at']!r}"
        )
    finally:
        db.DB_PATH = original_path


# ── AC3 / AC5: parse_utc_ts tolerates all three legacy shapes + new form ─────

@pytest.mark.parametrize("raw,expected_hour", [
    # new canonical form
    ("2026-01-15T14:30:00Z", 14),
    # legacy: no offset marker
    ("2026-01-15T14:30:00", 14),
    # legacy: naive with microseconds (from datetime.utcnow().isoformat())
    ("2026-01-15T14:30:00.123456", 14),
    # legacy: explicit UTC offset
    ("2026-01-15T14:30:00+00:00", 14),
])
def test_parse_utc_ts_all_shapes(raw, expected_hour):
    """parse_utc_ts() must parse all four timestamp forms to the same UTC instant."""
    dt = db.parse_utc_ts(raw)
    assert dt.tzinfo is not None, f"parse_utc_ts({raw!r}) returned naive datetime"
    assert dt.utctimetuple().tm_hour == expected_hour, (
        f"Hour mismatch for {raw!r}: got {dt.hour}"
    )


def test_parse_utc_ts_all_shapes_same_instant():
    """All four representations of 2026-01-15T14:30:00 UTC must parse to equal datetimes."""
    shapes = [
        "2026-01-15T14:30:00Z",
        "2026-01-15T14:30:00",
        "2026-01-15T14:30:00.000000",
        "2026-01-15T14:30:00+00:00",
    ]
    parsed = [db.parse_utc_ts(s) for s in shapes]
    reference = parsed[0]
    for ts_str, dt in zip(shapes, parsed):
        assert dt == reference, (
            f"parse_utc_ts({ts_str!r}) → {dt} != reference {reference}"
        )
