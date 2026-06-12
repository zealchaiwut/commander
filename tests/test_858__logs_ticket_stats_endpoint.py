"""Tests for issue #858 — per-agent timing and error detail on the Logs tab (endpoint half).

The per-ticket stats strip is fed by ``GET /api/logs/runs/{label}/ticket-stats``,
mounted on the already-included ``/api/logs`` router (log_search router) so the
new route never touches the server.py monolith (COMMANDER_GATE_MONOLITH).

AC coverage (endpoint):
  AC1/AC2/AC3 — each ticket item carries coder_seconds, tester_seconds, total_tokens.
  AC5         — NULL values come back as JSON null (frontend renders a dash).
  AC6         — the data is available at the first level (one call, no drill-down).
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

REQUIRED_FIELDS = {
    "issue_number", "coder_seconds", "tester_seconds", "total_tokens",
    "failed", "failure_class", "failure_message",
}


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
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "ep_858.db")
    import server as srv  # noqa: F401
    from fastapi.testclient import TestClient
    _seed(db_module, [
        {"sprint_label": "sprint-65", "issue_number": 801, "agent": "coder",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 5000},
        {"sprint_label": "sprint-65", "issue_number": 801, "agent": "tester",
         "duration_seconds": 45, "outcome": "pass", "total_tokens": 2000},
        {"sprint_label": "sprint-65", "issue_number": 802, "agent": "coder",
         "duration_seconds": None, "outcome": "success", "total_tokens": None},
    ])
    return TestClient(srv.app)


def test_endpoint_returns_200_with_ticket_items(client):
    resp = client.get("/api/logs/runs/sprint-65/ticket-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "tickets" in data
    by = {t["issue_number"]: t for t in data["tickets"]}
    assert set(by) == {801, 802}


def test_each_item_has_required_fields(client):
    data = client.get("/api/logs/runs/sprint-65/ticket-stats").json()
    for t in data["tickets"]:
        assert REQUIRED_FIELDS <= set(t)  # AC6: all first-level, no drill-down


def test_durations_and_tokens_present(client):
    data = client.get("/api/logs/runs/sprint-65/ticket-stats").json()
    by = {t["issue_number"]: t for t in data["tickets"]}
    assert by[801]["coder_seconds"] == 120     # AC1
    assert by[801]["tester_seconds"] == 45      # AC2
    assert by[801]["total_tokens"] == 7000      # AC3


def test_null_values_serialize_as_json_null(client):
    data = client.get("/api/logs/runs/sprint-65/ticket-stats").json()
    by = {t["issue_number"]: t for t in data["tickets"]}
    assert by[802]["coder_seconds"] is None     # AC5
    assert by[802]["total_tokens"] is None      # AC5
    assert by[802]["tester_seconds"] is None     # AC5 (tester never ran)


def test_unknown_sprint_returns_200_empty(client):
    resp = client.get("/api/logs/runs/sprint-none/ticket-stats")
    assert resp.status_code == 200
    assert resp.json()["tickets"] == []
