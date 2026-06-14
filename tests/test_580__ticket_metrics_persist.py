"""Tests for issue #580: Persist ticket elapsed time and token totals.

AC coverage:
  AC1 - actual_elapsed_seconds and total_tokens stored on ticket close
  AC2 - both fields queryable via get_sprint_rollup (SQL WHERE / GROUP BY)
  AC3 - calibration load_calibration() reads from persistent store via db_records
  AC4 - sprint rollup aggregates sum/avg without data loss across sessions
  AC5 - pre-existing NULL values cause no errors
  AC6 - reopen/re-close accumulates rather than duplicates
  AC7 - fields non-null for tickets closed after feature ships
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from conftest import make_sprint_db

import sys
_SM = Path(__file__).parent.parent / "services" / "sprint_manager"
if str(_SM) not in sys.path:
    sys.path.insert(0, str(_SM))

import services.sprint_manager.sprint_repo as sprint_repo  # noqa: E402
from services.sprint_manager.sprint_repo import (  # noqa: E402
    add_ticket,
    create_sprint,
    get_sprint_rollup,
    list_tickets,
    update_ticket_status,
)
from calibration import load_calibration  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)
    monkeypatch.setattr(sprint_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


def _sprint_and_running_ticket(sprint_label: str, issue_number: int) -> None:
    """Create sprint + ticket already in running state."""
    create_sprint(label=sprint_label, goal="test", project="test/repo")
    add_ticket(sprint_label=sprint_label, issue_number=issue_number, position=0)
    update_ticket_status(sprint_label, issue_number, "running")


# ── AC1: fields stored on ticket close ────────────────────────────────────────

def test_actual_elapsed_seconds_set_on_done():
    _sprint_and_running_ticket("s1", 10)
    ticket = update_ticket_status("s1", 10, "done", total_tokens=500)
    assert ticket.actual_elapsed_seconds is not None
    assert ticket.actual_elapsed_seconds >= 0
    assert ticket.total_tokens == 500


def test_total_tokens_set_on_failed():
    _sprint_and_running_ticket("s1", 11)
    ticket = update_ticket_status("s1", 11, "failed", total_tokens=200)
    assert ticket.total_tokens == 200


def test_total_tokens_zero_default():
    _sprint_and_running_ticket("s1", 12)
    ticket = update_ticket_status("s1", 12, "skipped")
    assert ticket.total_tokens == 0


def test_total_tokens_set_on_skipped():
    _sprint_and_running_ticket("s1", 13)
    ticket = update_ticket_status("s1", 13, "skipped", total_tokens=50)
    assert ticket.total_tokens == 50


# ── AC2: queryable via rollup ─────────────────────────────────────────────────

def test_rollup_returns_per_ticket_data():
    create_sprint(label="s2", goal="g", project="p")
    for i, num in enumerate([20, 21]):
        add_ticket(sprint_label="s2", issue_number=num, position=i)
        update_ticket_status("s2", num, "running")
    update_ticket_status("s2", 20, "done", total_tokens=1000)
    update_ticket_status("s2", 21, "done", total_tokens=2000)
    rollup = get_sprint_rollup("s2")
    nums = {r["issue_number"] for r in rollup["tickets"]}
    assert nums == {20, 21}
    assert rollup["sum_tokens"] == 3000
    assert rollup["avg_tokens"] == 1500.0


def test_rollup_sum_elapsed_seconds():
    create_sprint(label="s3", goal="g", project="p")
    for i, num in enumerate([30, 31]):
        add_ticket(sprint_label="s3", issue_number=num, position=i)
        update_ticket_status("s3", num, "running")
        update_ticket_status("s3", num, "done", total_tokens=0)
    rollup = get_sprint_rollup("s3")
    assert rollup["sum_elapsed_seconds"] >= 0
    assert rollup["avg_elapsed_seconds"] is not None


def test_rollup_unknown_sprint_returns_empty():
    rollup = get_sprint_rollup("does-not-exist")
    assert rollup["tickets"] == []
    assert rollup["sum_tokens"] == 0
    assert rollup["avg_tokens"] is None


# ── AC3: calibration reads from persistent store ─────────────────────────────

def test_load_calibration_accepts_db_records():
    db_recs = (
        [{"size": "M", "actual_minutes": 10}] * 3
        + [{"size": "L", "actual_minutes": 28}] * 3
    )
    result = load_calibration(db_records=db_recs)
    assert result.sources["M"] == "calibrated"
    assert result.effective_minutes["M"] == 10
    assert result.sources["L"] == "calibrated"
    assert result.record_count == 6


def test_load_calibration_db_records_merged_with_file(tmp_path):
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"records": [{"size": "S", "actual_minutes": v} for v in [3, 4, 5]]}))
    db_recs = [{"size": "M", "actual_minutes": m} for m in [12, 15, 18]]
    result = load_calibration(commander_dir=tmp_path, db_records=db_recs)
    assert result.sources["S"] == "calibrated"
    assert result.sources["M"] == "calibrated"


def test_load_calibration_db_takes_priority_when_sufficient(tmp_path):
    # File has 3 S records at 5 min; DB has 3 S records at 10 min — DB wins.
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"records": [{"size": "S", "actual_minutes": 5}] * 3}))
    db_recs = [{"size": "S", "actual_minutes": 10}] * 3
    result = load_calibration(commander_dir=tmp_path, db_records=db_recs)
    assert result.effective_minutes["S"] == 10


def test_load_calibration_db_and_file_combined_when_insufficient(tmp_path):
    # 2 S from DB + 2 S from file = 4 total → still < MIN_SAMPLES=3? No, 4>=3, should calibrate.
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"records": [{"size": "S", "actual_minutes": 6}] * 2}))
    db_recs = [{"size": "S", "actual_minutes": 4}] * 2
    result = load_calibration(commander_dir=tmp_path, db_records=db_recs)
    # 4 combined records → calibrated
    assert result.sources["S"] == "calibrated"


# ── AC4: rollup stable (sum/avg queryable from DB) ────────────────────────────

def test_rollup_aggregates_match_individual_tickets():
    create_sprint(label="s4", goal="g", project="p")
    tokens = [100, 200, 300]
    for i, (num, tok) in enumerate(zip([40, 41, 42], tokens)):
        add_ticket(sprint_label="s4", issue_number=num, position=i)
        update_ticket_status("s4", num, "running")
        update_ticket_status("s4", num, "done", total_tokens=tok)
    rollup = get_sprint_rollup("s4")
    assert rollup["sum_tokens"] == sum(tokens)
    assert abs(rollup["avg_tokens"] - sum(tokens) / len(tokens)) < 0.01


# ── AC5: pre-existing NULL values cause no errors ────────────────────────────

def test_null_fields_on_pending_ticket_no_error():
    create_sprint(label="s5", goal="g", project="p")
    add_ticket(sprint_label="s5", issue_number=50, position=0)
    add_ticket(sprint_label="s5", issue_number=51, position=1)
    update_ticket_status("s5", 50, "running")
    update_ticket_status("s5", 50, "done", total_tokens=100)
    # ticket 51 never started → NULL elapsed/tokens
    rollup = get_sprint_rollup("s5")
    assert rollup["sum_tokens"] == 100
    ticket_51 = next(r for r in rollup["tickets"] if r["issue_number"] == 51)
    assert ticket_51["total_tokens"] is None
    assert ticket_51["actual_elapsed_seconds"] is None


# ── AC6: reopen/re-close accumulates ─────────────────────────────────────────

def test_reopen_reclose_accumulates_tokens():
    _sprint_and_running_ticket("s6", 60)
    update_ticket_status("s6", 60, "done", total_tokens=300)
    update_ticket_status("s6", 60, "running")
    ticket = update_ticket_status("s6", 60, "done", total_tokens=200)
    assert ticket.total_tokens == 500  # 300 + 200, not reset


def test_reopen_reclose_accumulates_elapsed():
    _sprint_and_running_ticket("s7", 70)
    update_ticket_status("s7", 70, "done", total_tokens=0)
    first_elapsed = list_tickets("s7")[0].actual_elapsed_seconds or 0
    update_ticket_status("s7", 70, "running")
    update_ticket_status("s7", 70, "done", total_tokens=0)
    second_elapsed = list_tickets("s7")[0].actual_elapsed_seconds or 0
    assert second_elapsed >= first_elapsed


# ── AC7: non-null for newly closed tickets ────────────────────────────────────

def test_newly_closed_ticket_has_non_null_fields():
    _sprint_and_running_ticket("s8", 80)
    ticket = update_ticket_status("s8", 80, "done", total_tokens=42)
    assert ticket.actual_elapsed_seconds is not None
    assert ticket.total_tokens is not None
    assert ticket.total_tokens == 42
