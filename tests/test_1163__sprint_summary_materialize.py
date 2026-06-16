"""Tests for issue #1163 — Materialize sprint_summary row on run finish.

AC1  ingest_sprint_run_artifact writes summary columns on completion
AC2  written row has fixed shape: settled_done, uat_count, failure_count, tokens — all non-null
AC3  settled_done matches _settled_done_from_columns / canonical formula on same data
AC4  outcome reads prefer materialized row; inline re-derivation is fallback when absent
AC5  history reads prefer materialized row when present
AC6  automated test: stable read immediately after ingest returns values from materialized row
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD))


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_1163.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    for mod in list(sys.modules.keys()):
        if mod in ("db", "server") or mod.startswith("routers"):
            del sys.modules[mod]
    import db as db_module
    db_module.init_db()
    return db_module


# ── AC1: ingest writes summary columns ─────────────────────────────────────────

def test_ingest_writes_summary_columns(fresh_db):
    """ingest_sprint_run_artifact must write the three new summary columns."""
    state = {
        "sprint_label": "sprint-10",
        "issues": [
            {"number": 101, "title": "A", "status": "done", "agent_status": "completed"},
            {"number": 102, "title": "B", "status": "done", "agent_status": "failed",
             "failure_reason": "lint error"},
        ],
        "total_tokens_in": 100,
        "total_tokens_out": 50,
    }
    fresh_db.record_sprint_ready_to_merge("sprint-10", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-10", state, project="o/r")

    row = fresh_db.get_sprint("sprint-10")
    assert row is not None
    assert row["summary_settled_done"] is not None, "summary_settled_done not written"
    assert row["summary_uat_count"] is not None, "summary_uat_count not written"
    assert row["summary_failure_count"] is not None, "summary_failure_count not written"
    assert row["tokens"] is not None, "tokens not written"


# ── AC2: fixed shape with non-null values ──────────────────────────────────────

def test_summary_row_shape_non_null_int(fresh_db):
    """All four summary fields must be non-null integers after ingest."""
    state = {
        "sprint_label": "sprint-11",
        "issues": [
            {"number": 201, "status": "done", "agent_status": "completed"},
            {"number": 202, "status": "uat"},
            {"number": 203, "status": "done", "agent_status": "failed",
             "failure_reason": "crash"},
            {"number": 204, "status": "pending"},
        ],
        "total_tokens_in": 200,
        "total_tokens_out": 80,
    }
    fresh_db.record_sprint_ready_to_merge("sprint-11", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-11", state, project="o/r")

    row = fresh_db.get_sprint("sprint-11")
    assert isinstance(row["summary_settled_done"], int)
    assert isinstance(row["summary_uat_count"], int)
    assert isinstance(row["summary_failure_count"], int)
    assert isinstance(row["tokens"], int)
    # tokens = 200 + 80
    assert row["tokens"] == 280


# ── AC3: settled_done matches canonical formula ────────────────────────────────

def test_settled_done_matches_canonical_formula(fresh_db):
    """summary_settled_done must equal _settled_done_from_columns applied to same run data.

    5 issues: 2 done, 1 uat, 1 failed, 1 pending.
    settled = total - backlog(pending) - in_progress(0) - sit(0) = 5 - 1 = 4
    """
    state = {
        "sprint_label": "sprint-12",
        "issues": [
            {"number": 301, "status": "done", "agent_status": "completed"},
            {"number": 302, "status": "done", "agent_status": "completed"},
            {"number": 303, "status": "uat"},
            {"number": 304, "agent_status": "failed", "failure_reason": "timeout"},
            {"number": 305, "status": "pending"},
        ],
    }
    fresh_db.record_sprint_ready_to_merge("sprint-12", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-12", state, project="o/r")

    row = fresh_db.get_sprint("sprint-12")

    # Canonical formula: total - backlog(pending) - in_progress - sit
    total = 5
    pending = 1
    expected_settled = max(0, total - pending)  # 4
    assert row["summary_settled_done"] == expected_settled, (
        f"settled_done mismatch: got {row['summary_settled_done']}, "
        f"expected {expected_settled} from canonical formula"
    )
    assert row["summary_uat_count"] == 1
    assert row["summary_failure_count"] == 1  # issue 304 failed


def test_settled_done_all_pending(fresh_db):
    """When all issues are pending, settled_done = 0."""
    state = {
        "sprint_label": "sprint-13",
        "issues": [
            {"number": 401, "status": "pending"},
            {"number": 402, "status": "pending"},
        ],
    }
    fresh_db.ingest_sprint_run_artifact("sprint-13", state, project="o/r")
    row = fresh_db.get_sprint("sprint-13")
    assert row["summary_settled_done"] == 0


def test_settled_done_all_completed(fresh_db):
    """When all issues are done, settled_done = total."""
    state = {
        "sprint_label": "sprint-14",
        "issues": [
            {"number": 501, "status": "done", "agent_status": "completed"},
            {"number": 502, "status": "done", "agent_status": "completed"},
            {"number": 503, "status": "uat"},
        ],
    }
    fresh_db.ingest_sprint_run_artifact("sprint-14", state, project="o/r")
    row = fresh_db.get_sprint("sprint-14")
    assert row["summary_settled_done"] == 3
    assert row["summary_uat_count"] == 1
    assert row["summary_failure_count"] == 0


# ── AC4: outcome reads prefer materialized row ─────────────────────────────────

def test_outcome_reads_prefer_materialized_failure_count(fresh_db):
    """_outcome_from_ingested_row must prefer summary_failure_count from the DB row.

    Proof: after ingest, corrupt issues_json to empty — the failure count reported
    by the outcome function must still match the materialized value, not re-derive 0.
    """
    state = {
        "sprint_label": "sprint-20",
        "issues": [
            {"number": 601, "status": "done", "agent_status": "completed"},
            {"number": 602, "agent_status": "failed", "failure_reason": "lint"},
            {"number": 603, "agent_status": "failed", "failure_reason": "timeout"},
        ],
        "total_tokens_in": 300,
        "total_tokens_out": 150,
    }
    fresh_db.record_sprint_ready_to_merge("sprint-20", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-20", state, project="o/r")

    row = fresh_db.get_sprint("sprint-20")
    materialized_failure_count = row["summary_failure_count"]
    assert materialized_failure_count == 2, "pre-condition: 2 failures ingested"

    # Corrupt issues_json — if outcome re-derives from issues_json it would return 0
    with fresh_db.get_conn() as conn:
        conn.execute("UPDATE sprints SET issues_json = '[]' WHERE label = 'sprint-20'")
        conn.commit()

    row_corrupted = fresh_db.get_sprint("sprint-20")
    for mod in list(sys.modules.keys()):
        if mod == "server" or mod.startswith("routers"):
            del sys.modules[mod]
    import server as srv

    with (
        patch.object(srv, "_has_rework_tickets", return_value=False),
        patch("routers.sprint_history_service._issues_from_agent_runs", return_value=[]),
        patch.object(srv.github_client, "get_repo_for_operation", return_value="o/r"),
    ):
        out = srv._outcome_from_ingested_row(row_corrupted, "sprint-20", "o/r")

    # When materialized counts are present, outcome must use them, not re-derive 0
    assert out["counts"]["failed"] == materialized_failure_count, (
        f"outcome failed count {out['counts']['failed']} != "
        f"materialized {materialized_failure_count}; "
        "outcome is re-deriving from issues_json instead of using materialized row"
    )


# ── AC5: history reads prefer materialized row ─────────────────────────────────

def test_history_enrichment_exposes_summary_counts(fresh_db):
    """enrichment_from_db_row must return summary counts from the materialized columns.

    After ingest, the enrichment helper used by history reads must surface the
    materialized summary values from the DB row (not re-derive from issues_json).
    """
    state = {
        "sprint_label": "sprint-30",
        "issues": [
            {"number": 701, "status": "done", "agent_status": "completed"},
            {"number": 702, "status": "uat"},
            {"number": 703, "agent_status": "failed", "failure_reason": "crash"},
        ],
        "total_tokens_in": 400,
        "total_tokens_out": 200,
    }
    fresh_db.record_sprint_ready_to_merge("sprint-30", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-30", state, project="o/r")

    row = fresh_db.get_sprint("sprint-30")

    for mod in list(sys.modules.keys()):
        if mod.startswith("routers"):
            del sys.modules[mod]
    from routers import sprint_artifact_service

    enrich = sprint_artifact_service.enrichment_from_db_row(row)

    assert enrich.get("summary_settled_done") == row["summary_settled_done"], \
        "enrichment_from_db_row did not surface summary_settled_done"
    assert enrich.get("summary_uat_count") == row["summary_uat_count"], \
        "enrichment_from_db_row did not surface summary_uat_count"
    assert enrich.get("summary_failure_count") == row["summary_failure_count"], \
        "enrichment_from_db_row did not surface summary_failure_count"


# ── AC6: stable read immediately after ingest uses materialized values ─────────

def test_stable_read_after_ingest(fresh_db):
    """Stable read: immediately after ingest, outcome counts come from materialized row.

    The test ingests a state, then corrupts issues_json to prove the outcome
    failure count is read from the materialized summary_failure_count column,
    not re-derived from issues_json.  Tokens likewise come from the stored column.
    """
    state = {
        "sprint_label": "sprint-40",
        "issues": [
            {"number": 801, "status": "done", "agent_status": "completed"},
            {"number": 802, "status": "done", "agent_status": "completed"},
            {"number": 803, "agent_status": "failed", "failure_reason": "design_docs_missing"},
        ],
        "total_tokens_in": 500,
        "total_tokens_out": 250,
    }
    fresh_db.record_sprint_ready_to_merge("sprint-40", project="o/r")
    fresh_db.ingest_sprint_run_artifact("sprint-40", state, project="o/r")

    # Verify summary columns are written correctly
    row = fresh_db.get_sprint("sprint-40")
    assert row["summary_settled_done"] == 3, "all 3 issues settled (none pending)"
    assert row["summary_uat_count"] == 0
    assert row["summary_failure_count"] == 1
    assert row["tokens"] == 750

    # Corrupt issues_json — stable read must not regress to re-derivation
    with fresh_db.get_conn() as conn:
        conn.execute("UPDATE sprints SET issues_json = '[]' WHERE label = 'sprint-40'")
        conn.commit()

    row_after = fresh_db.get_sprint("sprint-40")
    for mod in list(sys.modules.keys()):
        if mod == "server" or mod.startswith("routers"):
            del sys.modules[mod]
    import server as srv

    with (
        patch.object(srv, "_has_rework_tickets", return_value=False),
        patch("routers.sprint_history_service._issues_from_agent_runs", return_value=[]),
        patch.object(srv.github_client, "get_repo_for_operation", return_value="o/r"),
    ):
        out = srv._outcome_from_ingested_row(row_after, "sprint-40", "o/r")

    # AC6 assertion: counts sourced from materialized row, not re-derived from empty issues_json
    assert out["counts"]["failed"] == 1, (
        f"stable read failed: counts.failed={out['counts']['failed']} "
        "but expected 1 from materialized row (issues_json was cleared)"
    )


def test_ingest_on_fresh_insert_writes_summary_columns(fresh_db):
    """INSERT path (no existing row) must also write summary columns."""
    state = {
        "sprint_label": "sprint-50",
        "issues": [
            {"number": 901, "status": "uat"},
            {"number": 902, "status": "uat"},
        ],
        "total_tokens_in": 50,
        "total_tokens_out": 25,
    }
    # Do NOT call record_sprint_* first — test the INSERT path in ingest
    fresh_db.ingest_sprint_run_artifact("sprint-50", state, project="o/r")

    row = fresh_db.get_sprint("sprint-50")
    assert row is not None
    assert row["summary_settled_done"] == 2
    assert row["summary_uat_count"] == 2
    assert row["summary_failure_count"] == 0
    assert row["tokens"] == 75
