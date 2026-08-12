"""Scope the ASSERT_ABSENT lookup to (label, project) in the repair script.

Issue #1481 (follow-up to #1465). Under the composite sprints schema two rows
can share ``label='sprint-66'`` (one per project). The ASSERT_ABSENT phase used
``_get_sprint_row_unscoped`` (``SELECT * FROM sprints WHERE label = ?``, no
scope/LIMIT), so ``fetchone()`` returned an arbitrary row. A real perf-coach
running ghost could be missed if commander's row was returned first.

Each test is anchored to a specific acceptance criterion:

AC1  The ASSERT_ABSENT lookup uses a project-scoped query
     (``WHERE label = ? AND project = ?``) instead of ``_get_sprint_row_unscoped``.
AC2  The scoped lookup detects a ghost when the target project's row exists,
     even when another project shares the same sprint label.
AC3  The scoped lookup reports ASSERT_ABSENT as passing when only a different
     project's row exists for that label.
AC4  No ASSERT_ABSENT call site uses the unscoped lookup.
AC5  Single-project setups (label uniqueness already guaranteed) are unaffected.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
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


@pytest.fixture
def commander_dir(tmp_path):
    d = tmp_path / ".commander"
    (d / "sprints").mkdir(parents=True)
    return d


def _assert_absent_manifest(project: str) -> list:
    return [
        {
            "label": _LABEL,
            "project": project,
            "action": "ASSERT_ABSENT",
            "reason": "test guard",
        }
    ]


# ── AC1: ASSERT_ABSENT phase uses a project-scoped query ─────────────────────

def test_ac1_assert_absent_phase_uses_scoped_query():
    """The ASSERT_ABSENT lookup must use a project-scoped query
    (``WHERE label = ? AND project = ?``), directly or via an equivalent helper.
    """
    # Isolate the ASSERT_ABSENT phase (everything before the RECREATE phase).
    assert_phase = inspect.getsource(_repair.apply).split("RECREATE phase")[0]

    # The phase must perform a lookup scoped by both label and project. Inline
    # that into the SQL it ultimately issues by following the helper it calls.
    scoped_sql = ""
    if "_get_sprint_row_scoped" in assert_phase:
        # The phase delegates to the scoped helper, passing the target project.
        assert re.search(
            r"_get_sprint_row_scoped\(\s*conn\s*,\s*label\s*,\s*project\s*\)",
            assert_phase,
        ), "scoped helper must be called with (conn, label, project)"
        scoped_sql = inspect.getsource(_repair._get_sprint_row_scoped)
    else:
        scoped_sql = assert_phase

    normalized = re.sub(r"\s+", " ", scoped_sql)
    assert re.search(
        r"WHERE\s+label\s*=\s*\?\s+AND\s+project\s*=\s*\?", normalized, re.IGNORECASE
    ), "ASSERT_ABSENT lookup must scope the query to (label, project)"


# ── AC2: scoped lookup detects the ghost despite a shared label ───────────────

def test_ac2_detects_ghost_when_other_project_row_shares_label(fresh_db, commander_dir):
    """A perf-coach running ghost is detected even when commander shares the label.

    Two rows share ``sprint-66``: commander (not running) and perf-coach
    (running ghost). The unscoped lookup could return commander's row first and
    miss the real ghost; the scoped lookup must find perf-coach's running row.
    """
    # commander/sprint-66 — present but NOT running
    fresh_db.record_sprint_start(_LABEL, project=_COMMANDER)
    fresh_db.record_sprint_needs_rework(
        _LABEL, end_reason="orphaned (no live process)", project=_COMMANDER,
    )
    # perf-coach/sprint-66 — the running ghost we must catch
    fresh_db.record_sprint_start(_LABEL, project=_PERF_COACH)

    result = _repair.apply(
        manifest=_assert_absent_manifest(_PERF_COACH), commander_dir=commander_dir,
    )

    assert result["perf_coach_sprint_66"]["running_row_found"] is True
    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is False


# ── AC3: scoped lookup passes when only a different project's row exists ───────

def test_ac3_passes_when_only_other_project_row_exists(fresh_db, commander_dir):
    """ASSERT_ABSENT for perf-coach passes when only commander has a running row."""
    # Only commander has sprint-66, and it is running; perf-coach has no row.
    fresh_db.record_sprint_start(_LABEL, project=_COMMANDER)

    result = _repair.apply(
        manifest=_assert_absent_manifest(_PERF_COACH), commander_dir=commander_dir,
    )

    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is True
    assert result["perf_coach_sprint_66"]["running_row_found"] is False


# ── AC4: no ASSERT_ABSENT call site uses the unscoped lookup ──────────────────

def test_ac4_assert_absent_phase_does_not_call_unscoped_helper():
    """The ASSERT_ABSENT phase must not call ``_get_sprint_row_unscoped``."""
    src = inspect.getsource(_repair.apply)
    assert_phase = src.split("RECREATE phase")[0]
    assert "_get_sprint_row_unscoped" not in assert_phase, (
        "ASSERT_ABSENT lookup must not use the unscoped helper"
    )


# ── AC5: single-project setups are unaffected ────────────────────────────────

def test_ac5_single_project_running_ghost_still_detected(fresh_db, commander_dir):
    """Single-project DB: a running perf-coach row is still flagged as a ghost."""
    fresh_db.record_sprint_start(_LABEL, project=_PERF_COACH)

    result = _repair.apply(
        manifest=_assert_absent_manifest(_PERF_COACH), commander_dir=commander_dir,
    )

    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is False
    assert result["perf_coach_sprint_66"]["running_row_found"] is True


def test_ac5_single_project_no_row_passes(fresh_db, commander_dir):
    """Single-project DB: ASSERT_ABSENT passes when no row exists at all."""
    result = _repair.apply(
        manifest=_assert_absent_manifest(_PERF_COACH), commander_dir=commander_dir,
    )

    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is True
    assert result["perf_coach_sprint_66"]["running_row_found"] is False


def test_ac5_single_project_not_running_passes(fresh_db, commander_dir):
    """Single-project DB: ASSERT_ABSENT passes when the row exists but isn't running."""
    fresh_db.record_sprint_start(_LABEL, project=_PERF_COACH)
    fresh_db.record_sprint_needs_rework(
        _LABEL, end_reason="orphaned (no live process)", project=_PERF_COACH,
    )

    result = _repair.apply(
        manifest=_assert_absent_manifest(_PERF_COACH), commander_dir=commander_dir,
    )

    assert result["perf_coach_sprint_66"]["assert_absent_passed"] is True
    assert result["perf_coach_sprint_66"]["running_row_found"] is False
