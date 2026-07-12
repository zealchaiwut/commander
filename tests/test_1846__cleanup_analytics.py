"""Tests for issue #1846 — cleanup: delete retired analytics.html, remove route, remove trend_30d.

AC coverage:
  AC1 — static/analytics.html is deleted
  AC2 — GET /project/{slug}/analytics 301-redirects to /project/{slug}/metrics
  AC3 — stab-analytics-page wiring is removed from project.html
  AC4 — _query_trend_30d() not defined; trend_30d absent from /analytics/cost response
  AC5 — /api/projects/{slug}/analytics/cost still returns by_agent, by_ticket, by_sprint
         (behavioral test via TestClient)
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number INTEGER NOT NULL,
            sprint_label TEXT NOT NULL,
            agent TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds INTEGER,
            outcome TEXT,
            total_tokens INTEGER,
            model_used TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            project TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            agent_role TEXT,
            model_name TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _call_cost(tmp_path: Path, db_path: Path, slug: str = "myrepo", project: str = "owner/myrepo"):
    import server as srv
    from starlette.testclient import TestClient
    import routers.analytics as analytics_mod

    with (
        patch.object(_db, "DB_PATH", str(db_path)),
        patch.object(analytics_mod, "_resolve_project_slug", return_value=project),
        patch.object(analytics_mod, "_project_root_path", return_value=tmp_path),
        patch.object(analytics_mod, "_settings_repo") as mock_sr,
    ):
        mock_sr.get_setting.return_value = {}
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/cost")


def _call_page_redirect(slug: str):
    import server as srv
    from starlette.testclient import TestClient

    client = TestClient(srv.app, follow_redirects=False)
    return client.get(f"/project/{slug}/analytics")


# ---------------------------------------------------------------------------
# AC1 — static/analytics.html is deleted
# ---------------------------------------------------------------------------

class TestAnalyticsHtmlDeleted:
    def test_analytics_html_does_not_exist(self):
        assert not (STATIC_DIR / "analytics.html").exists(), (
            "static/analytics.html should be deleted (AC1)"
        )


# ---------------------------------------------------------------------------
# AC2 — GET /project/{slug}/analytics 301-redirects to /project/{slug}/metrics
# ---------------------------------------------------------------------------

class TestAnalyticsPageRedirect:
    def test_redirect_status_is_301(self):
        resp = _call_page_redirect("commander")
        assert resp.status_code == 301, (
            f"Expected 301 redirect, got {resp.status_code}"
        )

    def test_redirect_location_is_metrics(self):
        resp = _call_page_redirect("commander")
        location = resp.headers.get("location", "")
        assert location.endswith("/project/commander/metrics"), (
            f"Expected redirect to /project/commander/metrics, got {location!r}"
        )


# ---------------------------------------------------------------------------
# AC3 — stab-analytics-page wiring removed from project.html
# ---------------------------------------------------------------------------

class TestStabAnalyticsPageRemoved:
    def test_stab_analytics_page_absent_from_project_html(self):
        project_html = STATIC_DIR / "project.html"
        assert project_html.exists(), "project.html must exist"
        content = project_html.read_text(encoding="utf-8")
        assert "stab-analytics-page" not in content, (
            "stab-analytics-page wiring must be removed from project.html (AC3)"
        )


# ---------------------------------------------------------------------------
# AC4 — _query_trend_30d() removed; trend_30d absent from response
# ---------------------------------------------------------------------------

class TestTrend30dRemoved:
    def test_query_trend_30d_not_on_module(self):
        import routers.analytics as analytics_mod
        assert not hasattr(analytics_mod, "_query_trend_30d"), (
            "_query_trend_30d must be removed from routers/analytics.py (AC4)"
        )

    def test_cost_response_has_no_trend_30d_key(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call_cost(tmp_path, db).json()
        assert "trend_30d" not in data, (
            "trend_30d must be removed from /analytics/cost response (AC4)"
        )


# ---------------------------------------------------------------------------
# AC5 — /analytics/cost still returns the fields the live UI consumes
# ---------------------------------------------------------------------------

class TestCostEndpointStillWorks:
    def test_returns_200(self, tmp_path):
        db = _mk_db(tmp_path)
        assert _call_cost(tmp_path, db).status_code == 200

    def test_by_sprint_present(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call_cost(tmp_path, db).json()
        assert "by_sprint" in data
        assert isinstance(data["by_sprint"], list)

    def test_by_ticket_present(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call_cost(tmp_path, db).json()
        assert "by_ticket" in data
        assert isinstance(data["by_ticket"], list)

    def test_by_agent_present(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call_cost(tmp_path, db).json()
        assert "by_agent" in data
        assert isinstance(data["by_agent"], list)

    def test_by_sprint_aggregates_correctly(self, tmp_path):
        db = _mk_db(tmp_path)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, total_tokens, model_used)"
            " VALUES (1, 'sprint-5', 'coder', '2026-06-01T10:00:00', 2000, 'claude-sonnet-4-6')"
        )
        conn.execute(
            "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, total_tokens, model_used)"
            " VALUES (2, 'sprint-5', 'tester', '2026-06-01T11:00:00', 500, 'claude-haiku-4-5')"
        )
        conn.commit()
        conn.close()
        by_sprint = _call_cost(tmp_path, db).json()["by_sprint"]
        s5 = next(x for x in by_sprint if x["sprint_label"] == "sprint-5")
        assert s5["total_tokens"] == 2500
