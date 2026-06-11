"""Tests for issue #810 — run-stats enrichment endpoint (backend half).

The History run-stats block is fed by a single local-only endpoint
``GET /api/sprints/{label}/run-stats`` whose assembly lives in
``apps/dashboard/routers/run_stats_service.py``. It aggregates the durable
``agent_runs`` rows for one sprint into:

  * per-agent elapsed-time totals (coder / tester / documenter / reviewer),
  * a split-bar percentage breakdown that sums to exactly 100,
  * per-ticket gantt segments carrying sprint-relative start / duration offsets
    and a fix-round flag,
  * a crash-marker position (the offset at the end of the last-reached ticket),
  * wall time, literal token total, fix-round count + ticket refs, the slowest
    ticket, and the parallel-saved time.

Every number is computed directly from the raw rows so it reconciles with them
(AC8/AC9). When no ``agent_runs`` rows exist for the sprint the service reports
``has_runs == False`` and omits the agent-derived payload, so the frontend can
hide the split-bar and gantt while still showing wall-time / token chips (AC7).
"""
from __future__ import annotations

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
                    r.get("outcome", "passed"),
                    r.get("total_tokens"),
                    r.get("attempt_kind", "initial"),
                ),
            )
        conn.commit()


@pytest.fixture()
def stats_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at a fresh temp DB and return (run_stats_service, db)."""
    import db as db_module
    import importlib
    from routers import run_stats_service
    importlib.reload(run_stats_service)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_810.db")
    return run_stats_service, db_module


# A realistic two-ticket sprint with a fix round on the second ticket. Times are
# laid out on an absolute clock; offsets in the result are relative to the
# earliest start (00:00:00) of the sprint.
def _seed_completed_sprint(db_module) -> None:
    _seed_agent_runs(db_module, [
        # ticket 801: coder 0–120s, tester 120–180s
        {"sprint_label": "sprint-60", "issue_number": 801, "agent": "coder",
         "started_at": "2026-06-11T00:00:00", "finished_at": "2026-06-11T00:02:00",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 5000},
        {"sprint_label": "sprint-60", "issue_number": 801, "agent": "tester",
         "started_at": "2026-06-11T00:02:00", "finished_at": "2026-06-11T00:03:00",
         "duration_seconds": 60, "outcome": "pass", "total_tokens": 2000},
        # ticket 802: coder 180–300s, tester 300–360 (fail), coder fix 360–420,
        # tester 420–450 (pass)
        {"sprint_label": "sprint-60", "issue_number": 802, "agent": "coder",
         "started_at": "2026-06-11T00:03:00", "finished_at": "2026-06-11T00:05:00",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 6000},
        {"sprint_label": "sprint-60", "issue_number": 802, "agent": "tester",
         "started_at": "2026-06-11T00:05:00", "finished_at": "2026-06-11T00:06:00",
         "duration_seconds": 60, "outcome": "fail", "total_tokens": 1500},
        {"sprint_label": "sprint-60", "issue_number": 802, "agent": "coder",
         "started_at": "2026-06-11T00:06:00", "finished_at": "2026-06-11T00:07:00",
         "duration_seconds": 60, "outcome": "success", "total_tokens": 3000,
         "attempt_kind": "fix_round"},
        {"sprint_label": "sprint-60", "issue_number": 802, "agent": "tester",
         "started_at": "2026-06-11T00:07:00", "finished_at": "2026-06-11T00:07:30",
         "duration_seconds": 30, "outcome": "pass", "total_tokens": 1000},
        # sprint-level documenter + reviewer runs after the tickets
        {"sprint_label": "sprint-60", "issue_number": 802, "agent": "documenter",
         "started_at": "2026-06-11T00:07:30", "finished_at": "2026-06-11T00:08:00",
         "duration_seconds": 30, "outcome": "succeeded", "total_tokens": 800},
        {"sprint_label": "sprint-60", "issue_number": 802, "agent": "reviewer",
         "started_at": "2026-06-11T00:08:00", "finished_at": "2026-06-11T00:08:10",
         "duration_seconds": 10, "outcome": "succeeded", "total_tokens": 200},
    ])


# ───────── AC8: endpoint returns the agent_runs aggregation ──────────────────

def test_ac8_has_runs_true_when_rows_exist(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    assert s["has_runs"] is True


def test_ac8_per_agent_totals_sum_duration_by_agent(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    # coder: 120 + 120 + 60(fix) = 300; tester: 60 + 60 + 30 = 150
    assert s["agent_seconds"]["coder"] == 300
    assert s["agent_seconds"]["tester"] == 150
    assert s["agent_seconds"]["documenter"] == 30
    assert s["agent_seconds"]["reviewer"] == 10
    assert s["agent_total_seconds"] == 300 + 150 + 30 + 10


def test_ac8_token_total_is_literal_sum(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    assert s["total_tokens"] == 5000 + 2000 + 6000 + 1500 + 3000 + 1000 + 800 + 200


def test_ac8_wall_seconds_is_full_span_of_all_runs(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    # earliest start 00:00:00 → latest finish 00:08:10 == 490s
    assert s["wall_seconds"] == 490


# ───────── AC2: split-bar percentages sum to exactly 100 ─────────────────────

def test_ac2_split_segments_sum_to_100(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    assert sum(seg["pct"] for seg in s["split"]) == 100


def test_ac2_split_covers_the_four_agents_and_is_positive(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    agents = {seg["agent"] for seg in s["split"]}
    assert agents == {"coder", "tester", "documenter", "reviewer"}
    assert all(seg["pct"] > 0 for seg in s["split"]), "all four agents ran → all positive"


def test_ac2_split_pct_tracks_seconds_ratio(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    by = {seg["agent"]: seg for seg in s["split"]}
    # coder is the largest share (300 / 490 ≈ 61%).
    assert by["coder"]["pct"] >= by["tester"]["pct"] >= by["documenter"]["pct"]


# ───────── AC3: per-ticket gantt segments with start/duration offsets ────────

def test_ac3_ticket_rows_carry_sprint_relative_offsets(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    tickets = {t["ticket"]: t for t in s["tickets"]}
    t801 = tickets[801]
    # coder 0–120, tester 120–180 relative to the sprint start
    coder_seg = next(seg for seg in t801["segments"] if seg["agent"] == "coder")
    tester_seg = next(seg for seg in t801["segments"] if seg["agent"] == "tester")
    assert coder_seg["start"] == 0 and coder_seg["duration"] == 120
    assert tester_seg["start"] == 120 and tester_seg["duration"] == 60


def test_ac3_fix_round_segment_is_flagged(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    t802 = next(t for t in s["tickets"] if t["ticket"] == 802)
    fix_segs = [seg for seg in t802["segments"] if seg["fix_round"]]
    assert len(fix_segs) == 1, "the fix-round coder re-run is flagged"
    assert fix_segs[0]["agent"] == "coder"
    # the initial coder run on the same ticket is NOT flagged
    assert any(seg["agent"] == "coder" and not seg["fix_round"] for seg in t802["segments"])


def test_ac3_gantt_is_coder_and_tester_only(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    seg_agents = {seg["agent"] for t in s["tickets"] for seg in t["segments"]}
    assert seg_agents <= {"coder", "tester"}, \
        "gantt rows show the per-ticket coder/tester segments only"


# ───────── AC1: fix-round count + ticket ref, slowest, parallel-saved ────────

def test_ac1_fix_round_count_and_ticket_refs(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    assert s["fix_round_count"] == 1
    assert s["fix_round_tickets"] == [802]


def test_ac1_slowest_ticket_is_the_longest_wall_span(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    # ticket 802 spans 180→450 (270s) vs 801's 0→180 (180s)
    assert s["slowest_ticket"]["ticket"] == 802
    assert s["slowest_ticket"]["seconds"] == 270


def test_ac1_parallel_saved_is_agent_total_minus_wall(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-60")
    assert s["parallel_saved_seconds"] == max(0, s["agent_total_seconds"] - s["wall_seconds"])


# ───────── AC4/AC5: crash marker + unreached tickets absent ──────────────────

def test_ac4_crash_marker_at_end_of_last_reached_ticket(stats_db):
    svc, db = stats_db
    # A failed sprint: ticket 901 completes, ticket 902 crashes mid-coder, and
    # ticket 903 is never reached (no agent_runs rows at all).
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-61", "issue_number": 901, "agent": "coder",
         "started_at": "2026-06-11T00:00:00", "finished_at": "2026-06-11T00:01:00",
         "duration_seconds": 60, "outcome": "success", "total_tokens": 100},
        {"sprint_label": "sprint-61", "issue_number": 901, "agent": "tester",
         "started_at": "2026-06-11T00:01:00", "finished_at": "2026-06-11T00:01:30",
         "duration_seconds": 30, "outcome": "pass", "total_tokens": 50},
        {"sprint_label": "sprint-61", "issue_number": 902, "agent": "coder",
         "started_at": "2026-06-11T00:01:30", "finished_at": "2026-06-11T00:03:30",
         "duration_seconds": 120, "outcome": "failed", "total_tokens": 80},
    ])
    s = svc.sprint_run_stats("sprint-61")
    # crash ticket is the last reached (902); offset is the end of its segment.
    assert s["crash"]["ticket"] == 902
    assert s["crash"]["offset"] == 210  # 00:00:00 → 00:03:30


def test_ac5_unreached_tickets_have_no_gantt_row(stats_db):
    svc, db = stats_db
    _seed_agent_runs(db, [
        {"sprint_label": "sprint-61", "issue_number": 901, "agent": "coder",
         "started_at": "2026-06-11T00:00:00", "finished_at": "2026-06-11T00:01:00",
         "duration_seconds": 60, "outcome": "success"},
        {"sprint_label": "sprint-61", "issue_number": 902, "agent": "coder",
         "started_at": "2026-06-11T00:01:00", "finished_at": "2026-06-11T00:03:00",
         "duration_seconds": 120, "outcome": "failed"},
    ])
    s = svc.sprint_run_stats("sprint-61")
    rendered = {t["ticket"] for t in s["tickets"]}
    assert rendered == {901, 902}, "ticket 903 was never reached → no empty row"


# ───────── AC7: graceful empty when agent_runs rows are absent ───────────────

def test_ac7_has_runs_false_when_no_rows(stats_db):
    svc, db = stats_db
    _seed_agent_runs(db, [])  # table exists, no rows for this sprint
    s = svc.sprint_run_stats("sprint-60")
    assert s["has_runs"] is False
    assert s.get("tickets", []) == []
    assert s.get("split", []) == []


def test_ac7_other_sprints_runs_do_not_leak(stats_db):
    svc, db = stats_db
    _seed_completed_sprint(db)
    s = svc.sprint_run_stats("sprint-99")
    assert s["has_runs"] is False


# ───────── token cost: approximate $ only when a price map is set ────────────

def test_token_cost_present_when_price_map_configured(stats_db, monkeypatch):
    svc, db = stats_db
    _seed_completed_sprint(db)
    monkeypatch.setattr(svc, "_blended_rate", lambda project: 0.000002)
    s = svc.sprint_run_stats("sprint-60", project="owner/repo")
    assert s["token_cost_usd"] == round(s["total_tokens"] * 0.000002, 4)


def test_token_cost_absent_when_no_price_map(stats_db, monkeypatch):
    svc, db = stats_db
    _seed_completed_sprint(db)
    monkeypatch.setattr(svc, "_blended_rate", lambda project: None)
    s = svc.sprint_run_stats("sprint-60", project="owner/repo")
    assert "token_cost_usd" not in s


# ───────── endpoint wiring: route on the History router, not server.py ───────

def test_route_registered_on_history_router():
    from routers import sprint_history
    paths = {r.path for r in sprint_history.router.routes}
    assert "/api/sprints/{label}/run-stats" in paths


def test_no_new_route_added_to_server_py():
    server_src = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
    assert "run-stats" not in server_src, \
        "the run-stats route must live in routers/, never the monolith"
