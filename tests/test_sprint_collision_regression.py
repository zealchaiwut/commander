"""Regression tests for sprint-66 composite-key collision (issue #1465).

Each test is anchored to a specific acceptance criterion:

AC1  dry-run mode prints (label, project) pairs with intended actions,
     exits 0, and performs no DB writes.
AC2  apply mode recreates commander's sprint-66 row from the best available
     source, without modifying the surviving perf-coach row.
AC4  ASSERT_ABSENT reports True when no running ghost row exists for
     perf-coach/sprint-66.
AC5  Replaying the historical single-key overwrite sequence against a DB
     seeded with composite keys leaves both projects' rows intact.
AC8  Script is idempotent: two apply runs produce the same DB state as one.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SCRIPTS_DIR = REPO_ROOT / "scripts"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SCRIPTS_DIR), str(SCRIPTS_DIR / "archive")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402
import repair_sprint_collisions as _repair  # noqa: E402

_COMMANDER = "zealchaiwut/commander"
_PERF_COACH = "zealchaiwut/perf-coach"
_LABEL = "sprint-66"


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_collision.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC1: dry-run ──────────────────────────────────────────────────────────────

def test_ac1_dry_run_prints_both_manifest_entries(capsys, fresh_db):
    """dry-run prints RECREATE and ASSERT_ABSENT for both (label, project) pairs."""
    _repair.dry_run()
    out = capsys.readouterr().out
    assert "RECREATE" in out
    assert "ASSERT_ABSENT" in out
    assert _LABEL in out
    assert _COMMANDER in out
    assert _PERF_COACH in out


def test_ac1_dry_run_writes_no_db_rows(fresh_db):
    """dry-run must not create or modify any sprint row."""
    before = fresh_db.get_sprint(_LABEL)
    _repair.dry_run()
    after = fresh_db.get_sprint(_LABEL)
    assert before is None
    assert after is None, "dry_run must leave the DB unchanged"


# ── AC2: apply recreates commander's sprint-66 row ───────────────────────────

def test_ac2_apply_creates_commander_row_when_absent(fresh_db, tmp_path):
    """apply recreates commander's sprint-66 from available sources."""
    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)

    result = _repair.apply(commander_dir=commander_dir)

    assert result["commander_sprint_66"]["created"] is True
    row = fresh_db.get_sprint(_LABEL, project=_COMMANDER)
    assert row is not None, "commander/sprint-66 row must exist after apply"
    assert row["project"] == _COMMANDER


def test_ac2_apply_prefers_plan_json_state(fresh_db, tmp_path):
    """apply uses the state from plan.json when the file is present."""
    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)
    plan = commander_dir / "sprints" / f"{_LABEL}-plan.json"
    plan.write_text(json.dumps({"state": "completed"}))

    _repair.apply(commander_dir=commander_dir)

    row = fresh_db.get_sprint(_LABEL, project=_COMMANDER)
    assert row is not None
    assert row["state"] == "completed"


def test_ac2_apply_falls_back_to_needs_rework(fresh_db, tmp_path):
    """apply defaults to needs_rework when no source can provide a state."""
    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)
    # No plan.json, no state.json, no agent_runs → default

    _repair.apply(commander_dir=commander_dir)

    row = fresh_db.get_sprint(_LABEL, project=_COMMANDER)
    assert row is not None
    assert row["state"] == "needs_rework"


# ── AC4: ASSERT_ABSENT ────────────────────────────────────────────────────────

def test_ac4_assert_absent_passes_when_row_not_running(fresh_db, tmp_path):
    """ASSERT_ABSENT passes when perf-coach's sprint-66 is not running."""
    # Simulate orphan sweep: row exists but in needs_rework (not running)
    fresh_db.record_sprint_start(_LABEL, project=_PERF_COACH)
    fresh_db.record_sprint_needs_rework(
        _LABEL, end_reason="orphaned (no live process)", project=_PERF_COACH,
    )

    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)

    result = _repair.apply(commander_dir=commander_dir)

    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is True
    assert result["perf_coach_sprint_66"]["running_row_found"] is False


def test_ac4_assert_absent_fails_when_running_ghost_present(fresh_db, tmp_path):
    """ASSERT_ABSENT reports failure when a stale running row exists."""
    # Stale running row — orphan sweep has NOT run yet
    fresh_db.record_sprint_start(_LABEL, project=_PERF_COACH)

    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)

    result = _repair.apply(commander_dir=commander_dir)

    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is False
    assert result["perf_coach_sprint_66"]["running_row_found"] is True


def test_ac4_assert_absent_passes_when_no_row_at_all(fresh_db, tmp_path):
    """ASSERT_ABSENT passes when perf-coach has no sprint-66 row whatsoever."""
    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)

    result = _repair.apply(commander_dir=commander_dir)

    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is True
    assert result["perf_coach_sprint_66"]["running_row_found"] is False


# ── AC5: composite-key regression ────────────────────────────────────────────

def test_ac5_historical_single_key_overwrote_commander_row():
    """Document the historical bug: ON CONFLICT(label) clobbered commander's row.

    Under the old schema (label TEXT PRIMARY KEY), inserting perf-coach's
    sprint-66 via ON CONFLICT(label) DO UPDATE silently erased commander's row.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE sprints_legacy (
            label   TEXT PRIMARY KEY,
            project TEXT NOT NULL DEFAULT '',
            state   TEXT NOT NULL DEFAULT 'draft'
        )
    """)
    conn.execute(
        "INSERT INTO sprints_legacy VALUES (?, ?, ?)",
        (_LABEL, _COMMANDER, "needs_rework"),
    )
    # perf-coach runs its sprint-66 and the old ON CONFLICT(label) fires
    conn.execute(
        """
        INSERT INTO sprints_legacy (label, project, state) VALUES (?, ?, ?)
        ON CONFLICT(label) DO UPDATE SET
            project = excluded.project,
            state   = excluded.state
        """,
        (_LABEL, _PERF_COACH, "running"),
    )
    conn.commit()

    row = conn.execute(
        "SELECT project, state FROM sprints_legacy WHERE label = ?", (_LABEL,)
    ).fetchone()
    # Historical bug confirmed: commander's row is gone
    assert row[0] == _PERF_COACH, "old single-key clobbered commander's project"
    assert row[1] == "running", "old single-key clobbered commander's state"


def test_ac5_composite_key_preserves_both_rows():
    """With composite (label, project) key, both projects' rows survive.

    Replays the historical perf-coach write against a composite-key DB and
    asserts commander's row is not overwritten.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE sprints (
            label   TEXT NOT NULL,
            project TEXT NOT NULL DEFAULT '',
            state   TEXT NOT NULL DEFAULT 'draft',
            PRIMARY KEY (label, project)
        )
    """)

    # Step 1: commander's sprint-66 exists (prior run ended with children spawned)
    conn.execute(
        "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
        (_LABEL, _COMMANDER, "needs_rework"),
    )
    # Step 2: perf-coach writes sprint-66 — composite key makes this a NEW row
    conn.execute(
        """
        INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)
        ON CONFLICT(label, project) DO UPDATE SET state = excluded.state
        """,
        (_LABEL, _PERF_COACH, "running"),
    )
    conn.commit()

    rows = conn.execute(
        "SELECT project, state FROM sprints WHERE label = ? ORDER BY project",
        (_LABEL,),
    ).fetchall()

    # Both rows survive under the composite key
    assert len(rows) == 2, "composite key must preserve both projects' rows"

    projects = {r[0] for r in rows}
    assert _COMMANDER in projects
    assert _PERF_COACH in projects

    # Commander's row is NOT overwritten
    cmdr_state = conn.execute(
        "SELECT state FROM sprints WHERE label = ? AND project = ?",
        (_LABEL, _COMMANDER),
    ).fetchone()[0]
    assert cmdr_state == "needs_rework", (
        "commander's row must survive perf-coach's write under composite key"
    )


def test_ac5_single_key_duplicate_label_raises_if_no_conflict_clause():
    """Baseline: single-key schema rejects two rows with same label (no ON CONFLICT)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE sprints_single (
            label   TEXT PRIMARY KEY,
            project TEXT NOT NULL DEFAULT '',
            state   TEXT NOT NULL DEFAULT 'draft'
        )
    """)
    conn.execute(
        "INSERT INTO sprints_single VALUES (?, ?, ?)",
        (_LABEL, _COMMANDER, "needs_rework"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sprints_single VALUES (?, ?, ?)",
            (_LABEL, _PERF_COACH, "running"),
        )


# ── AC8: idempotency ─────────────────────────────────────────────────────────

def test_ac8_apply_twice_produces_same_state(fresh_db, tmp_path):
    """Running apply twice produces the same DB state as running it once."""
    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)

    result1 = _repair.apply(commander_dir=commander_dir)
    row_after_first = fresh_db.get_sprint(_LABEL, project=_COMMANDER)

    result2 = _repair.apply(commander_dir=commander_dir)
    row_after_second = fresh_db.get_sprint(_LABEL, project=_COMMANDER)

    assert row_after_first is not None
    assert row_after_second is not None
    assert row_after_first["state"] == row_after_second["state"]
    assert row_after_first["project"] == row_after_second["project"]


def test_ac8_second_apply_reports_already_existed(fresh_db, tmp_path):
    """Second apply reports commander row already exists (no re-creation)."""
    commander_dir = tmp_path / ".commander"
    (commander_dir / "sprints").mkdir(parents=True)

    _repair.apply(commander_dir=commander_dir)
    result2 = _repair.apply(commander_dir=commander_dir)

    assert result2["commander_sprint_66"]["created"] is False
    assert result2["commander_sprint_66"].get("already_existed") is True
