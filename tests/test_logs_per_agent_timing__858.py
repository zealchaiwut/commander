"""Tester verification for issue #858 — per-agent timing + error detail on the Logs tab.

One test function per acceptance criterion.

The feature is a *data-dependent* read endpoint
(``GET /api/logs/runs/{label}/ticket-stats``) plus a Logs-tab render strip. A
live UAT probe cannot verify it: the running UAT server is on an unrelated
branch and, even when deployed, the endpoint's output depends on seeded
``agent_runs`` rows that a shared server has no predictable copy of. So the
endpoint AC are verified in-process against a seeded temp DB (the codebase's
established pattern for data-backed endpoints, cf. #810), and the UI-only AC are
verified against the static ``project.html`` source the dashboard serves from
disk.
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

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


def _seed(db_module, rows):
    with db_module.get_conn() as conn:
        db_module._create_agent_runs_table(conn)
        for r in rows:
            conn.execute(
                "INSERT INTO agent_runs "
                "(issue_number, sprint_label, agent, started_at, finished_at, "
                " duration_seconds, outcome, total_tokens, attempt_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (r["issue_number"], r["sprint_label"], r["agent"],
                 "2026-06-11T00:00:00", None, r.get("duration_seconds"),
                 r.get("outcome", "success"), r.get("total_tokens"), "initial"),
            )
        conn.commit()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    if "server" in sys.modules:
        del sys.modules["server"]
    import db as db_module
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "tester_858.db")
    import server as srv
    from fastapi.testclient import TestClient
    _seed(db_module, [
        # Passing ticket: coder + tester both recorded, tokens on both.
        {"sprint_label": "sprint-65", "issue_number": 901, "agent": "coder",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 5000},
        {"sprint_label": "sprint-65", "issue_number": 901, "agent": "tester",
         "duration_seconds": 45, "outcome": "pass", "total_tokens": 2000},
        # Ticket with everything NULL — graceful dash degradation (AC5).
        {"sprint_label": "sprint-65", "issue_number": 902, "agent": "coder",
         "duration_seconds": None, "outcome": "success", "total_tokens": None},
        # Failed ticket (no sidecar) — derives a failure class from the stage (AC4).
        {"sprint_label": "sprint-65", "issue_number": 903, "agent": "coder",
         "duration_seconds": 30, "outcome": "success", "total_tokens": 1000},
        {"sprint_label": "sprint-65", "issue_number": 903, "agent": "tester",
         "duration_seconds": 10, "outcome": "fail", "total_tokens": 500},
    ])
    return TestClient(srv.app)


def _stats(client, label="sprint-65"):
    resp = client.get(f"/api/logs/runs/{label}/ticket-stats")
    assert resp.status_code == 200, resp.text
    return {t["issue_number"]: t for t in resp.json()["tickets"]}


# --- Acceptance Criteria ---

def test_logs_per_agent_timing__row_shows_coder_duration(client):
    # AC1: each ticket row shows coder duration from agent_runs.duration_seconds.
    assert _stats(client)[901]["coder_seconds"] == 120


def test_logs_per_agent_timing__row_shows_tester_duration(client):
    # AC2: each ticket row shows tester duration from agent_runs.duration_seconds.
    assert _stats(client)[901]["tester_seconds"] == 45


def test_logs_per_agent_timing__row_shows_total_tokens(client):
    # AC3: total tokens, combined across the coder + tester dispatches.
    assert _stats(client)[901]["total_tokens"] == 7000


def test_logs_per_agent_timing__failure_class_and_message_inline(client):
    # AC4: a failed ticket carries a failure class inline; passing tickets do not.
    by = _stats(client)
    assert by[903]["failed"] is True
    assert by[903]["failure_class"]                 # named (derived TESTER_REJECTED)
    assert by[901]["failed"] is False
    assert by[901]["failure_class"] is None
    # ...and the frontend renders the class + message inline on the row.
    assert "logs-ticket-fail-class" in PROJECT_HTML
    assert "failure_message" in PROJECT_HTML


def test_logs_per_agent_timing__null_degrades_to_dash(client):
    # AC5: NULL duration/tokens come back as JSON null (endpoint) and render as
    # a dash, not a zero/blank/error (frontend).
    null_ticket = _stats(client)[902]
    assert null_ticket["coder_seconds"] is None
    assert null_ticket["tester_seconds"] is None
    assert null_ticket["total_tokens"] is None
    # token formatter guards null -> em dash; durations go through _histFmtSecs.
    assert "if (n == null || isNaN(n)) return '—';" in PROJECT_HTML
    assert "_histFmtSecs(t.coder_seconds)" in PROJECT_HTML
    assert "_histFmtSecs(t.tester_seconds)" in PROJECT_HTML


def test_logs_per_agent_timing__data_first_level_no_drilldown(client):
    # AC6: all fields available from one first-level call — no log file/drill-down.
    required = {"issue_number", "coder_seconds", "tester_seconds",
                "total_tokens", "failed", "failure_class", "failure_message"}
    for t in _stats(client).values():
        assert required <= set(t)
    # The strip is rendered on run expand, fed by the ticket-stats endpoint.
    assert "_logsFetchTicketStats" in PROJECT_HTML
    assert "/api/logs/runs/" in PROJECT_HTML and "/ticket-stats" in PROJECT_HTML


def test_logs_per_agent_timing__visually_consistent_with_batch(client):
    # AC7 (updated by #1850): the Logs Timeline (Runs) view and its per-ticket
    # stats strip were removed in issue #1850 — the ticket-stats API still works
    # (tested above) but the .logs-ticket-* CSS classes no longer appear in the
    # HTML since the run-row renderer was deleted. The /ticket-stats endpoint
    # remains for backward-compatible access.
    assert "_logsFetchTicketStats" in PROJECT_HTML, (
        "_logsFetchTicketStats helper must still exist for the API endpoint"
    )
    assert "/ticket-stats" in PROJECT_HTML, (
        "/ticket-stats API URL must still appear in the JS"
    )
