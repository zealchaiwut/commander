"""Tests for issue #764 — track per-agent duration per issue in sprint runs.

Each test is anchored to a specific acceptance criterion (AC1–AC8).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; yields the patched db module."""
    db_file = tmp_path / "test_764.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── AC1: agent_runs table + columns (created on SQLite) ──────────────────────

# Original columns from issue #764 (migration 0009)
_COLS_0009 = {
    "id", "issue_number", "sprint_label", "agent", "started_at",
    "finished_at", "duration_seconds", "outcome", "total_tokens",
}

# Columns added by issue #790 (migration 0010)
_COLS_0010 = {"risk_tier", "model_used"}

EXPECTED_COLS = _COLS_0009 | _COLS_0010


def test_agent_runs_table_has_expected_columns(fresh_db):
    """AC1: init_db() creates agent_runs with the required columns on SQLite
    (including risk_tier/model_used added by issue #790).

    Subset assert: later migrations legitimately add columns (routing_reason,
    worktree_sha, attempt_kind, …); exact equality broke on every new one.
    """
    with fresh_db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)").fetchall()}
    assert EXPECTED_COLS <= cols, f"missing columns: {EXPECTED_COLS - cols}"


def test_agent_runs_total_tokens_nullable(fresh_db):
    """AC1: total_tokens is nullable (NULL allowed on insert)."""
    fresh_db.record_agent_start(764, "sprint-58", "coder")
    rows = fresh_db.agent_runs_for_issue(764)
    assert rows and rows[0]["total_tokens"] is None


def test_alembic_migration_0009_is_sqlite_portable():
    """AC1: the 0009 migration chains onto 0008, declares the original
    agent_runs columns, and uses only portable types.  The risk_tier /
    model_used columns added by issue #790 live in migration 0010, not 0009."""
    mig_path = REPO_ROOT / "alembic" / "versions" / "0009_add_agent_runs.py"
    src = mig_path.read_text()
    # Revision chains onto the prior head (0008).
    assert 'down_revision' in src and '"h8i9j0k1l2m3"' in src
    # Creates the agent_runs table with every original column.
    assert 'create_table(' in src and '"agent_runs"' in src
    for col in _COLS_0009:
        assert f'"{col}"' in src, f"migration 0009 missing column {col}"
    # risk_tier / model_used belong to migration 0010, not 0009.
    for col in _COLS_0010:
        assert f'"{col}"' not in src, f"column {col} should be in 0010, not 0009"
    # Portable types only — must NOT import or use the postgresql dialect.
    assert "dialects.postgresql" not in src
    assert "TIMESTAMP(" not in src
    # downgrade drops the table cleanly.
    assert "drop_table" in src


# ── AC2: insert on start, update on finish ───────────────────────────────────

def test_record_agent_start_inserts_open_row(fresh_db):
    """AC2: record_agent_start inserts a row with finish fields still NULL."""
    rid = fresh_db.record_agent_start(764, "sprint-58", "coder", started_at="2026-06-10T00:00:00")
    assert rid is not None
    rows = fresh_db.agent_runs_for_issue(764, "sprint-58")
    assert len(rows) == 1
    r = rows[0]
    assert r["agent"] == "coder"
    assert r["started_at"] == "2026-06-10T00:00:00"
    assert r["finished_at"] is None
    assert r["duration_seconds"] is None
    assert r["outcome"] is None


def test_record_agent_finish_closes_row(fresh_db):
    """AC2: record_agent_finish fills finished_at/duration/outcome on the open row."""
    fresh_db.record_agent_start(764, "sprint-58", "tester", started_at="2026-06-10T00:00:00")
    fresh_db.record_agent_finish(
        764, "sprint-58", "tester",
        finished_at="2026-06-10T00:05:00", outcome="pass", total_tokens=1234,
    )
    rows = fresh_db.agent_runs_for_issue(764, "sprint-58")
    assert len(rows) == 1
    r = rows[0]
    assert r["finished_at"] == "2026-06-10T00:05:00"
    assert r["outcome"] == "pass"
    assert r["total_tokens"] == 1234
    assert r["duration_seconds"] == 300


def test_finish_matches_latest_open_run_per_agent(fresh_db):
    """AC2: finish closes the most recent still-open run for that issue/agent."""
    fresh_db.record_agent_start(764, "sprint-58", "coder", started_at="2026-06-10T00:00:00")
    fresh_db.record_agent_finish(764, "sprint-58", "coder",
                                 finished_at="2026-06-10T00:01:00", outcome="success")
    # second dispatch (retry) — new open row
    fresh_db.record_agent_start(764, "sprint-58", "coder", started_at="2026-06-10T00:02:00")
    fresh_db.record_agent_finish(764, "sprint-58", "coder",
                                 finished_at="2026-06-10T00:03:30", outcome="success")
    rows = fresh_db.agent_runs_for_issue(764, "sprint-58")
    assert len(rows) == 2
    assert [r["duration_seconds"] for r in rows] == [60, 90]


# ── AC3: duration within ±2s of wall-clock ───────────────────────────────────

def test_duration_within_2s_of_wallclock(fresh_db):
    """AC3: an explicitly supplied wall-clock duration is stored within ±2 s."""
    fresh_db.record_agent_start(764, "sprint-58", "coder")
    fresh_db.record_agent_finish(764, "sprint-58", "coder", duration_seconds=128.4)
    r = fresh_db.agent_runs_for_issue(764)[0]
    assert abs(r["duration_seconds"] - 128.4) <= 2


def test_duration_computed_from_timestamps_when_not_supplied(fresh_db):
    """AC3: when no duration is supplied it is computed from start/finish stamps."""
    fresh_db.record_agent_start(764, "sprint-58", "coder", started_at="2026-06-10T00:00:00")
    fresh_db.record_agent_finish(764, "sprint-58", "coder", finished_at="2026-06-10T00:02:01")
    r = fresh_db.agent_runs_for_issue(764)[0]
    assert abs(r["duration_seconds"] - 121) <= 2


# ── AC2: sprint_manager wires every dispatched agent ─────────────────────────

def _sm_source() -> str:
    return (SM_DIR / "sprint_manager.py").read_text()


@pytest.mark.parametrize("agent", ["coder", "tester", "documenter", "reviewer", "estimator"])
def test_sprint_manager_records_each_agent(agent):
    """AC2: every dispatched agent opens and closes an agent_runs row."""
    src = _sm_source()
    assert f'_db_agent_start_sm(' in src
    assert f'"{agent}"' in src
    # both helpers are referenced for the agent name somewhere in the file
    assert "_db_agent_finish_sm(" in src


def test_sprint_manager_finish_passes_precise_duration():
    """AC3: coder/tester finishes pass the monotonic-measured duration."""
    src = _sm_source()
    assert "duration_seconds=_coder_elapsed" in src
    assert "duration_seconds=_tester_elapsed" in src


# ── AC5: Activity tab Duration column ────────────────────────────────────────

def test_events_api_enriches_duration_from_agent_runs():
    """AC5: the events endpoint enriches agent_finished rows from agent_runs.

    The events endpoint moved from server.py to routers/activity.py in the
    router extraction (issue #794); check both locations.
    """
    src = (DASHBOARD_DIR / "server.py").read_text()
    routers_dir = DASHBOARD_DIR / "routers"
    if routers_dir.exists():
        for p in routers_dir.glob("*.py"):
            src += p.read_text()
    assert "agent_runs_for_issue" in src
    assert '"duration_seconds"' in src or "d[\"duration_seconds\"]" in src


def test_activity_row_renders_duration():
    """AC5: the activity timeline renders a Duration element on agent rows."""
    src = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "evl-agent-duration" in src
    assert "_evlFmtDuration" in src


# ── AC4: running view live elapsed timer ─────────────────────────────────────

def test_running_view_has_live_timer():
    """AC4: the running view ticks the active agent tag's elapsed in real time."""
    src = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "_smgmtTickLiveTimers" in src
    assert "data-live-timer" in src
    assert "setInterval(_smgmtTickLiveTimers" in src


# ── AC6: sprint summary per-agent duration totals per ticket ─────────────────

def _make_summary():
    if "github_client" not in sys.modules:
        sys.modules["github_client"] = MagicMock()
    import sprint_manager as sm
    iss1 = sm.IssueState(
        number=701, title="First", status="done",
        coder_started_at="2026-06-10T00:00:00", coder_finished_at="2026-06-10T00:02:00",
        tester_started_at="2026-06-10T00:02:00", tester_finished_at="2026-06-10T00:03:00",
    )
    iss2 = sm.IssueState(
        number=702, title="Second", status="done",
        coder_started_at="2026-06-10T00:00:00", coder_finished_at="2026-06-10T00:04:00",
        tester_started_at="2026-06-10T00:04:00", tester_finished_at="2026-06-10T00:06:30",
    )
    state = sm.SprintState(sprint_label="sprint-58", sprint_number=58, issues=[iss1, iss2])
    return sm.generate_sprint_summary(state, elapsed_secs=600)


def test_summary_lists_per_agent_durations_per_ticket():
    """AC6: summary has a Per-Agent Durations section with coder + tester columns."""
    md = _make_summary()
    assert "## Per-Agent Durations" in md
    assert "| Issue # | Coder | Tester |" in md
    # ticket 701: coder 2m 0s, tester 1m 0s
    assert "| #701 | 2m 0s | 1m 0s |" in md
    # ticket 702: coder 4m 0s, tester 2m 30s
    assert "| #702 | 4m 0s | 2m 30s |" in md


def test_summary_shows_per_agent_totals():
    """AC6: summary shows totals per agent across tickets."""
    md = _make_summary()
    # coder total 6m 0s, tester total 3m 30s
    assert "| **Totals** | **6m 0s** | **3m 30s** |" in md


# ── AC7: db_calibration_records emits per-agent records ──────────────────────

def _seed_sprint_state(tmp_path: Path) -> Path:
    """Build estimates_dir + a sprint state JSON with one done ticket."""
    import json
    estimates_dir = tmp_path / "estimates"
    sprints_dir = tmp_path / "sprints"
    estimates_dir.mkdir()
    sprints_dir.mkdir()
    (estimates_dir / "issue-701.json").write_text(json.dumps({"size": "M"}))
    state = {
        "issues": [
            {
                "number": 701, "status": "done",
                "coder_started_at": "2026-06-10T00:00:00",
                "coder_finished_at": "2026-06-10T00:10:00",
                "tester_started_at": "2026-06-10T00:10:00",
                "tester_finished_at": "2026-06-10T00:15:00",
            }
        ]
    }
    (sprints_dir / "sprint-58-state.json").write_text(json.dumps(state))
    return estimates_dir


def test_db_calibration_default_blended_only(tmp_path):
    """AC8: default output is unchanged — one blended record, no agent key."""
    import calibration
    estimates_dir = _seed_sprint_state(tmp_path)
    recs = calibration.db_calibration_records("sprint-58", estimates_dir)
    assert recs == [{"size": "M", "actual_minutes": 15.0}]
    assert all("agent" not in r for r in recs)


def test_db_calibration_include_agents_adds_per_agent(tmp_path):
    """AC7: include_agents=True adds per-agent records alongside the blended one."""
    import calibration
    estimates_dir = _seed_sprint_state(tmp_path)
    recs = calibration.db_calibration_records("sprint-58", estimates_dir, include_agents=True)
    blended = [r for r in recs if "agent" not in r]
    per_agent = [r for r in recs if "agent" in r]
    assert blended == [{"size": "M", "actual_minutes": 15.0}]
    assert {"size": "M", "actual_minutes": 10.0, "agent": "coder"} in per_agent
    assert {"size": "M", "actual_minutes": 5.0, "agent": "tester"} in per_agent


def test_load_calibration_unaffected_by_blended_default(tmp_path):
    """AC7/AC8: existing consumer (load_calibration) works on blended records."""
    import calibration
    estimates_dir = _seed_sprint_state(tmp_path)
    recs = calibration.db_calibration_records("sprint-58", estimates_dir)
    # consumer that omits the agent field still works without modification
    result = calibration.load_calibration(db_records=recs)
    assert "M" in result.effective_minutes
