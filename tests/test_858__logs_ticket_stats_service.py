"""Tests for issue #858 — per-agent timing and error detail on the Logs tab (service half).

The Logs tab expanded-run view gains a per-ticket stats strip fed by a single
local-only endpoint ``GET /api/logs/runs/{label}/ticket-stats`` whose assembly
lives in ``apps/dashboard/routers/logs_stats_service.py``. It aggregates the
durable ``agent_runs`` rows for one sprint into one row per ticket carrying:

  * coder duration  — summed ``duration_seconds`` of the coder dispatch(es),
  * tester duration — summed ``duration_seconds`` of the tester dispatch(es),
  * total tokens    — summed ``total_tokens`` across the ticket's runs,
  * failed flag + failure class + failure message for failed tickets (the
    class/message sourced from the ``last-failure-<issue>.json`` sidecar).

AC coverage (service):
  AC1 — coder_seconds sourced from agent_runs.duration_seconds (coder dispatch).
  AC2 — tester_seconds sourced from agent_runs.duration_seconds (tester dispatch).
  AC3 — total_tokens sourced from agent_runs.total_tokens.
  AC4 — a failed ticket carries failure_class + failure_message inline.
  AC5 — NULL duration / tokens degrade to None (the frontend renders a dash).
        A passing ticket carries no failure class / message.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _seed_agent_runs(db_module, rows: list[dict]) -> None:
    """Create the agent_runs table in the test DB and insert the given rows."""
    with db_module.get_conn() as conn:
        db_module._create_agent_runs_table(conn)
        for r in rows:
            conn.execute(
                "INSERT INTO agent_runs "
                "(issue_number, sprint_label, agent, started_at, finished_at, "
                " duration_seconds, outcome, total_tokens, attempt_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    r.get("issue_number", 1),
                    r["sprint_label"],
                    r["agent"],
                    r.get("started_at", "2026-06-11T00:00:00"),
                    r.get("finished_at"),
                    r.get("duration_seconds"),
                    r.get("outcome", "success"),
                    r.get("total_tokens"),
                    r.get("attempt_kind", "initial"),
                ),
            )
        conn.commit()


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    """Point db.DB_PATH at a fresh temp DB and return (logs_stats_service, db)."""
    import db as db_module
    import importlib
    from routers import logs_stats_service
    importlib.reload(logs_stats_service)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_858.db")
    return logs_stats_service, db_module


def _by_ticket(result: dict) -> dict[int, dict]:
    return {t["issue_number"]: t for t in result["tickets"]}


# ── AC1 + AC2: per-agent durations sourced from duration_seconds ─────────────

def test_coder_and_tester_durations_from_duration_seconds(svc):
    service, db = svc
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-65", "issue_number": 801, "agent": "coder",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 5000},
        {"sprint_label": "sprint-65", "issue_number": 801, "agent": "tester",
         "duration_seconds": 45, "outcome": "pass", "total_tokens": 2000},
    ])
    tickets = _by_ticket(service.ticket_stats("sprint-65"))
    assert tickets[801]["coder_seconds"] == 120      # AC1
    assert tickets[801]["tester_seconds"] == 45      # AC2


def test_fix_round_durations_sum_across_dispatches(svc):
    """A ticket with a coder fix round reports the combined coder time."""
    service, db = svc
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-65", "issue_number": 802, "agent": "coder",
         "duration_seconds": 100, "outcome": "success"},
        {"sprint_label": "sprint-65", "issue_number": 802, "agent": "tester",
         "duration_seconds": 30, "outcome": "fail"},
        {"sprint_label": "sprint-65", "issue_number": 802, "agent": "coder",
         "duration_seconds": 50, "outcome": "success", "attempt_kind": "fix_round"},
        {"sprint_label": "sprint-65", "issue_number": 802, "agent": "tester",
         "duration_seconds": 20, "outcome": "pass", "attempt_kind": "fix_round"},
    ])
    tickets = _by_ticket(service.ticket_stats("sprint-65"))
    assert tickets[802]["coder_seconds"] == 150      # 100 + 50
    assert tickets[802]["tester_seconds"] == 50      # 30 + 20


# ── AC3: total tokens sourced from agent_runs.total_tokens ────────────────────

def test_total_tokens_summed_across_runs(svc):
    service, db = svc
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-65", "issue_number": 801, "agent": "coder",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 5000},
        {"sprint_label": "sprint-65", "issue_number": 801, "agent": "tester",
         "duration_seconds": 45, "outcome": "pass", "total_tokens": 2000},
    ])
    tickets = _by_ticket(service.ticket_stats("sprint-65"))
    assert tickets[801]["total_tokens"] == 7000      # AC3


# ── AC5: NULL duration / tokens degrade to None (dash on the frontend) ────────

def test_null_duration_and_tokens_become_none(svc):
    service, db = svc
    _seed_agent_runs(db, [
        # coder ran but its duration / tokens were never recorded
        {"sprint_label": "sprint-65", "issue_number": 803, "agent": "coder",
         "duration_seconds": None, "outcome": "success", "total_tokens": None},
    ])
    tickets = _by_ticket(service.ticket_stats("sprint-65"))
    assert tickets[803]["coder_seconds"] is None     # AC5
    assert tickets[803]["total_tokens"] is None      # AC5
    # tester never ran at all → also None, not an error
    assert tickets[803]["tester_seconds"] is None


def test_zero_duration_is_preserved_not_nulled(svc):
    """A recorded duration of 0 is a real value, distinct from NULL."""
    service, db = svc
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-65", "issue_number": 804, "agent": "coder",
         "duration_seconds": 0, "outcome": "success", "total_tokens": 0},
    ])
    tickets = _by_ticket(service.ticket_stats("sprint-65"))
    assert tickets[804]["coder_seconds"] == 0
    assert tickets[804]["total_tokens"] == 0


# ── AC4: failed ticket carries failure class + message inline ─────────────────

def _write_sidecar(runtime_dir: Path, issue: int, failure_class: str, summary: str) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / f"last-failure-{issue}.json").write_text(
        json.dumps({
            "issue": issue,
            "failure_class": failure_class,
            "summary": summary,
            "detail": "full detail body",
        }),
        encoding="utf-8",
    )


def test_failed_ticket_surfaces_failure_class_and_message(svc, tmp_path):
    service, db = svc
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-65", "issue_number": 805, "agent": "coder",
         "duration_seconds": 90, "outcome": "success"},
        {"sprint_label": "sprint-65", "issue_number": 805, "agent": "tester",
         "duration_seconds": 40, "outcome": "fail"},
    ])
    runtime = tmp_path / "rt"
    _write_sidecar(runtime, 805, "BUILD_ERROR", "pytest collection failed: 3 errors")

    tickets = _by_ticket(service.ticket_stats("sprint-65", runtime_dirs=[runtime]))
    t = tickets[805]
    assert t["failed"] is True                                  # AC4
    assert t["failure_class"] == "BUILD_ERROR"                  # AC4
    assert "pytest collection failed" in t["failure_message"]   # AC4


def test_passing_ticket_has_no_failure_fields(svc, tmp_path):
    """A run where the ticket passed shows no failure class / message (UAT step 4)."""
    service, db = svc
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-65", "issue_number": 806, "agent": "coder",
         "duration_seconds": 90, "outcome": "success"},
        {"sprint_label": "sprint-65", "issue_number": 806, "agent": "tester",
         "duration_seconds": 40, "outcome": "pass"},
    ])
    runtime = tmp_path / "rt"  # no sidecar written → ticket passed
    tickets = _by_ticket(service.ticket_stats("sprint-65", runtime_dirs=[runtime]))
    t = tickets[806]
    assert t["failed"] is False
    assert t["failure_class"] is None
    assert t["failure_message"] is None


def test_failed_outcome_without_sidecar_still_marks_failed(svc, tmp_path):
    """A failing agent outcome marks the ticket failed even if the sidecar is gone."""
    service, db = svc
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-65", "issue_number": 807, "agent": "coder",
         "duration_seconds": 10, "outcome": "timeout"},
    ])
    runtime = tmp_path / "rt"
    tickets = _by_ticket(service.ticket_stats("sprint-65", runtime_dirs=[runtime]))
    assert tickets[807]["failed"] is True
    # class is derived from the failing stage when no sidecar is present
    assert tickets[807]["failure_class"]


# ── No rows / graceful empty ──────────────────────────────────────────────────

def test_sprint_with_no_runs_returns_empty_tickets(svc):
    service, db = svc
    result = service.ticket_stats("sprint-does-not-exist")
    assert result["tickets"] == []
