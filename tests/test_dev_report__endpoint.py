"""Tests for issue #1960: GET /api/dev-report endpoint.

AC1: GET /api/dev-report returns stored artifact for today (Bangkok tz) by default
AC2: GET /api/dev-report?date=YYYY-MM-DD returns artifact for specified date
AC3: GET /api/dev-report?force=1 regenerates inline and stores before returning
AC4: GET /api/dev-report?date=YYYY-MM-DD&force=1 regenerates for given date
AC5: No artifact + no force → HTTP 404 with {"error": "<descriptive message>"}
AC6: assemble_dev_report() is importable from routers.dev_report_service
AC7: Script (export_hermes_report) continues to work after refactor
AC8: Tests seed data via shared assembly function, not raw fixture insertion
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db as _db_module
import routers.dev_report_service as svc
from routers.dev_report import router as dev_report_router


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; patches db.DB_PATH for the duration of the test."""
    db_file = tmp_path / "test_1960.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def client(fresh_db):
    """TestClient wired to the dev_report router with a fresh DB."""
    app = FastAPI()
    app.include_router(dev_report_router)
    return TestClient(app)


# ── AC6: assemble_dev_report() is importable ──────────────────────────────────

def test_assemble_dev_report_is_importable():
    """AC6: assemble_dev_report() exists in routers.dev_report_service and is callable."""
    assert callable(svc.assemble_dev_report)


# ── AC1: GET /api/dev-report returns today's stored artifact ─────────────────

def test_get_dev_report_returns_today_artifact(client, fresh_db, monkeypatch):
    """AC1: default date is Bangkok today; stored artifact is returned as JSON."""
    today = "2026-07-17"
    monkeypatch.setattr(svc, "_bkk_today", lambda: today)

    # Seed via shared assembly function (AC8)
    contract = svc.assemble_dev_report(today)
    fresh_db.set_brief_artifact("dev_report", "", today, contract)

    r = client.get("/api/dev-report")
    assert r.status_code == 200
    data = r.json()
    assert data["for_date"] == today


# ── AC2: GET /api/dev-report?date=YYYY-MM-DD ─────────────────────────────────

def test_get_dev_report_specified_date(client, fresh_db):
    """AC2: returns stored artifact for the specified date."""
    date = "2026-07-10"

    # Seed via shared assembly function (AC8)
    contract = svc.assemble_dev_report(date)
    fresh_db.set_brief_artifact("dev_report", "", date, contract)

    r = client.get(f"/api/dev-report?date={date}")
    assert r.status_code == 200
    data = r.json()
    assert data["for_date"] == date


# ── AC3: GET /api/dev-report?force=1 ─────────────────────────────────────────

def test_get_dev_report_force_regenerates(client, fresh_db, monkeypatch):
    """AC3: force=1 regenerates inline, stores, and returns the payload."""
    today = "2026-07-17"
    monkeypatch.setattr(svc, "_bkk_today", lambda: today)

    # No pre-stored artifact
    assert fresh_db.get_brief_artifact("dev_report", "", today) is None

    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    data = r.json()
    assert data["for_date"] == today

    # Verify it was persisted so a subsequent non-force call also returns 200
    stored = fresh_db.get_brief_artifact("dev_report", "", today)
    assert stored is not None
    assert stored["payload"] is not None


# ── AC4: GET /api/dev-report?date=YYYY-MM-DD&force=1 ─────────────────────────

def test_get_dev_report_force_given_date(client, fresh_db):
    """AC4: force=1 with explicit date regenerates and stores for that date."""
    date = "2026-07-15"

    # No pre-stored artifact
    assert fresh_db.get_brief_artifact("dev_report", "", date) is None

    r = client.get(f"/api/dev-report?date={date}&force=1")
    assert r.status_code == 200
    data = r.json()
    assert data["for_date"] == date

    # Verify persisted
    stored = fresh_db.get_brief_artifact("dev_report", "", date)
    assert stored is not None


# ── AC5: 404 when no artifact and force not set ───────────────────────────────

def test_get_dev_report_missing_returns_404(client, fresh_db):
    """AC5: no artifact + no force flag → HTTP 404 with {"error": "..."}."""
    r = client.get("/api/dev-report?date=2099-01-01")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body
    assert "2099-01-01" in body["error"]


def test_get_dev_report_missing_today_returns_404(client, fresh_db, monkeypatch):
    """AC5: no artifact for today (default date) + no force → 404."""
    monkeypatch.setattr(svc, "_bkk_today", lambda: "2099-12-31")
    r = client.get("/api/dev-report")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body


# ── AC3 continued: subsequent call without force uses stored artifact ─────────

def test_get_dev_report_force_then_get_no_force(client, fresh_db, monkeypatch):
    """AC3: after force=1 stores the artifact, a non-force call returns 200."""
    today = "2026-07-17"
    monkeypatch.setattr(svc, "_bkk_today", lambda: today)

    r1 = client.get("/api/dev-report?force=1")
    assert r1.status_code == 200

    r2 = client.get("/api/dev-report")
    assert r2.status_code == 200
    assert r2.json()["for_date"] == today


# ── AC7: script continues to work after refactor ─────────────────────────────

def test_export_hermes_report_script_still_works(tmp_path):
    """AC7: export_hermes_report.run() completes without error after refactor."""
    import export_hermes_report as script

    output_path = tmp_path / "report.json"
    state_path = tmp_path / ".state.json"

    # A non-existent DB path triggers graceful degradation (run logs to stderr, exits 0)
    script.run(
        db_path=str(tmp_path / "nonexistent.db"),
        output_path=output_path,
        state_path=state_path,
        dry_run=False,
        projects_list=[],
        price_map=None,
        stale_blocked_days=3,
        stale_waiting_days=2,
        stale_backlog_days=7,
    )
    # If run() raised, the test would fail. The script degrades gracefully.
