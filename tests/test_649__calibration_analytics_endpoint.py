"""Tests for issue #649 — GET /api/projects/{slug}/analytics/calibration.

AC coverage:
  AC1  — returns HTTP 200 with valid JSON
  AC2  — by_size has all four keys (S, M, L, XL) with required sub-fields
  AC3  — configured_minutes sourced from project estimation settings (not hardcoded)
  AC4  — count/min/avg/max computed from sprint_tickets scoped to the project
  AC5  — actual_elapsed_seconds converted to minutes (÷60)
  AC6  — points array: each element has issue_number, estimated_size, estimated_minutes, actual_minutes
  AC7  — since param filters tickets by completed_at ≥ date
  AC8  — until param filters tickets by completed_at ≤ date
  AC9  — sprint param filters by sprint label
  AC10 — sizes with zero tickets: count=0, min/avg/max=null (not omitted)
  AC11 — unknown slug returns 404
  AC12 — since + until + sprint can be combined
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_SM_ROOT = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_DASHBOARD_ROOT), str(_SM_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import services.sprint_manager.sprint_repo as _sprint_repo_mod
import services.sprint_manager.settings_repo as _settings_repo_mod
from services.sprint_manager.settings_schema import APP_CONFIG_KEY


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_SPRINTS_DDL = """
    CREATE TABLE sprints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT UNIQUE NOT NULL,
        goal TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        started_at TEXT,
        completed_at TEXT,
        cancelled_at TEXT,
        project TEXT NOT NULL
    )
"""

_TICKETS_DDL = """
    CREATE TABLE sprint_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sprint_id INTEGER NOT NULL REFERENCES sprints(id),
        issue_number INTEGER NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'done',
        started_at TEXT,
        completed_at TEXT,
        agent_active TEXT,
        actual_elapsed_seconds INTEGER,
        total_tokens INTEGER,
        estimated_size TEXT,
        UNIQUE(sprint_id, issue_number)
    )
"""

_SETTINGS_DDL = """
    CREATE TABLE settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL,
        project TEXT,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(scope, project, key)
    )
"""


def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text(_SPRINTS_DDL))
        conn.execute(text(_TICKETS_DDL))
        conn.execute(text(_SETTINGS_DDL))
        conn.commit()
    return engine


def _insert_sprint(conn, sprint_id: int, label: str, project: str) -> None:
    conn.execute(text(
        "INSERT INTO sprints (id, label, project) VALUES (:id, :label, :project)"
    ), {"id": sprint_id, "label": label, "project": project})


def _insert_ticket(
    conn,
    sprint_id: int,
    issue_number: int,
    estimated_size: str | None,
    actual_elapsed_seconds: int | None,
    completed_at: str | None = "2026-01-15T10:00:00+00:00",
) -> None:
    conn.execute(text("""
        INSERT INTO sprint_tickets
            (sprint_id, issue_number, estimated_size, actual_elapsed_seconds, completed_at)
        VALUES
            (:sid, :num, :sz, :secs, :cat)
    """), {
        "sid": sprint_id,
        "num": issue_number,
        "sz": estimated_size,
        "secs": actual_elapsed_seconds,
        "cat": completed_at,
    })


@pytest.fixture
def db(monkeypatch):
    """In-memory SQLite wired into sprint_repo and settings_repo."""
    engine = _make_engine()
    SessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(_sprint_repo_mod, "_session_factory", SessionLocal)
    monkeypatch.setattr(_settings_repo_mod, "_session_factory", SessionLocal)
    yield engine


def _call(db_engine, slug: str = "myrepo", project: str = "owner/myrepo", **params):
    """Call GET /api/projects/{slug}/analytics/calibration via TestClient."""
    import server as srv
    from starlette.testclient import TestClient

    with (
        patch("server._resolve_project_slug", return_value=project),
        patch("server._SPRINT_REPO_AVAILABLE", True),
        patch("server._sprint_repo", _sprint_repo_mod),
    ):
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/calibration", params=params)


# ---------------------------------------------------------------------------
# AC1 — HTTP 200, valid JSON
# ---------------------------------------------------------------------------

class TestHttp200:
    def test_returns_200(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        resp = _call(db)
        assert resp.status_code == 200

    def test_response_is_json(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        resp = _call(db)
        data = resp.json()
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# AC2 — by_size has all four size keys with required fields
# ---------------------------------------------------------------------------

class TestBySizeShape:
    def test_by_size_present(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        data = _call(db).json()
        assert "by_size" in data

    def test_all_four_size_keys_present(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        by_size = _call(db).json()["by_size"]
        for sz in ("S", "M", "L", "XL"):
            assert sz in by_size, f"Missing size key: {sz}"

    def test_each_size_has_required_fields(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        by_size = _call(db).json()["by_size"]
        for sz in ("S", "M", "L", "XL"):
            entry = by_size[sz]
            assert "configured_minutes" in entry
            assert "count" in entry
            assert "min_minutes" in entry
            assert "avg_minutes" in entry
            assert "max_minutes" in entry

    def test_points_present(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        data = _call(db).json()
        assert "points" in data
        assert isinstance(data["points"], list)


# ---------------------------------------------------------------------------
# AC3 — configured_minutes from project estimation settings
# ---------------------------------------------------------------------------

class TestConfiguredMinutes:
    def test_configured_minutes_uses_defaults_when_no_override(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        by_size = _call(db).json()["by_size"]
        assert by_size["S"]["configured_minutes"] == 5
        assert by_size["M"]["configured_minutes"] == 15
        assert by_size["L"]["configured_minutes"] == 30
        assert by_size["XL"]["configured_minutes"] == 60

    def test_configured_minutes_reflects_project_override(self, db):
        import json as _json
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            # Write a project-level override for estimation_s_minutes=10
            conn.execute(text(
                "INSERT INTO settings (scope, project, key, value) VALUES"
                " ('project', 'owner/myrepo', :key, :val)"
            ), {"key": APP_CONFIG_KEY, "val": _json.dumps({"estimation_s_minutes": 10})})
            conn.commit()
        by_size = _call(db).json()["by_size"]
        assert by_size["S"]["configured_minutes"] == 10
        # Other sizes unaffected
        assert by_size["M"]["configured_minutes"] == 15


# ---------------------------------------------------------------------------
# AC4 — aggregates computed from sprint_tickets scoped to project
# ---------------------------------------------------------------------------

class TestAggregates:
    def test_count_matches_qualifying_tickets(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "M", 900)   # 15 min
            _insert_ticket(conn, 1, 102, "M", 1800)  # 30 min
            conn.commit()
        by_size = _call(db).json()["by_size"]
        assert by_size["M"]["count"] == 2

    def test_tickets_from_other_project_excluded(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_sprint(conn, 2, "sprint-2", "other/project")
            _insert_ticket(conn, 1, 101, "L", 1800)
            _insert_ticket(conn, 2, 201, "L", 600)   # different project
            conn.commit()
        by_size = _call(db, project="owner/myrepo").json()["by_size"]
        assert by_size["L"]["count"] == 1

    def test_null_estimated_size_excluded(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, None, 900)   # no size
            _insert_ticket(conn, 1, 102, "S", 300)
            conn.commit()
        by_size = _call(db).json()["by_size"]
        assert by_size["S"]["count"] == 1

    def test_null_elapsed_seconds_excluded(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", None)   # no elapsed
            _insert_ticket(conn, 1, 102, "S", 300)
            conn.commit()
        by_size = _call(db).json()["by_size"]
        assert by_size["S"]["count"] == 1

    def test_min_avg_max_correct(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            # 60s, 120s, 180s → 1, 2, 3 min
            _insert_ticket(conn, 1, 101, "M", 60)
            _insert_ticket(conn, 1, 102, "M", 120)
            _insert_ticket(conn, 1, 103, "M", 180)
            conn.commit()
        entry = _call(db).json()["by_size"]["M"]
        assert entry["count"] == 3
        assert abs(entry["min_minutes"] - 1.0) < 0.01
        assert abs(entry["avg_minutes"] - 2.0) < 0.01
        assert abs(entry["max_minutes"] - 3.0) < 0.01


# ---------------------------------------------------------------------------
# AC5 — elapsed_seconds converted to minutes (÷60)
# ---------------------------------------------------------------------------

class TestMinutesConversion:
    def test_seconds_divided_by_60(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "L", 3600)  # 60 minutes
            conn.commit()
        entry = _call(db).json()["by_size"]["L"]
        assert abs(entry["avg_minutes"] - 60.0) < 0.01

    def test_fractional_minutes_preserved(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 90)  # 1.5 minutes
            conn.commit()
        entry = _call(db).json()["by_size"]["S"]
        assert abs(entry["avg_minutes"] - 1.5) < 0.01

    def test_points_actual_minutes_in_minutes(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "M", 1200)  # 20 minutes
            conn.commit()
        points = _call(db).json()["points"]
        assert len(points) == 1
        assert abs(points[0]["actual_minutes"] - 20.0) < 0.01


# ---------------------------------------------------------------------------
# AC6 — points array shape
# ---------------------------------------------------------------------------

class TestPointsShape:
    def test_points_count_equals_qualifying_tickets(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 300)
            _insert_ticket(conn, 1, 102, "M", 900)
            _insert_ticket(conn, 1, 103, None, 600)   # excluded
            _insert_ticket(conn, 1, 104, "L", None)   # excluded
            conn.commit()
        points = _call(db).json()["points"]
        assert len(points) == 2

    def test_point_has_required_fields(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "M", 900)
            conn.commit()
        point = _call(db).json()["points"][0]
        assert "issue_number" in point
        assert "estimated_size" in point
        assert "estimated_minutes" in point
        assert "actual_minutes" in point

    def test_point_issue_number_correct(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 555, "M", 900)
            conn.commit()
        point = _call(db).json()["points"][0]
        assert point["issue_number"] == 555

    def test_point_estimated_size_correct(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "XL", 3600)
            conn.commit()
        point = _call(db).json()["points"][0]
        assert point["estimated_size"] == "XL"

    def test_point_estimated_minutes_matches_configured(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "L", 1800)
            conn.commit()
        point = _call(db).json()["points"][0]
        # Default L = 30 minutes
        assert point["estimated_minutes"] == 30


# ---------------------------------------------------------------------------
# AC7 — since param filters by completed_at
# ---------------------------------------------------------------------------

class TestSinceFilter:
    def test_since_excludes_earlier_tickets(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 300, completed_at="2025-12-31T23:59:59+00:00")
            _insert_ticket(conn, 1, 102, "S", 600, completed_at="2026-01-15T10:00:00+00:00")
            conn.commit()
        data = _call(db, since="2026-01-01").json()
        assert data["by_size"]["S"]["count"] == 1
        assert data["points"][0]["issue_number"] == 102

    def test_since_inclusive_boundary(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 300, completed_at="2026-01-01T00:00:00+00:00")
            conn.commit()
        data = _call(db, since="2026-01-01").json()
        assert data["by_size"]["S"]["count"] == 1


# ---------------------------------------------------------------------------
# AC8 — until param filters by completed_at
# ---------------------------------------------------------------------------

class TestUntilFilter:
    def test_until_excludes_later_tickets(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "M", 900, completed_at="2026-01-15T10:00:00+00:00")
            _insert_ticket(conn, 1, 102, "M", 1800, completed_at="2026-03-01T10:00:00+00:00")
            conn.commit()
        data = _call(db, until="2026-01-31").json()
        assert data["by_size"]["M"]["count"] == 1
        assert data["points"][0]["issue_number"] == 101


# ---------------------------------------------------------------------------
# AC9 — sprint param filters by sprint label
# ---------------------------------------------------------------------------

class TestSprintFilter:
    def test_sprint_filter_returns_only_matching_sprint(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-42", "owner/myrepo")
            _insert_sprint(conn, 2, "sprint-43", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 300)
            _insert_ticket(conn, 2, 201, "S", 600)
            conn.commit()
        data = _call(db, sprint="sprint-42").json()
        assert data["by_size"]["S"]["count"] == 1
        assert data["points"][0]["issue_number"] == 101

    def test_sprint_filter_no_match_gives_empty(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 300)
            conn.commit()
        data = _call(db, sprint="sprint-99").json()
        assert data["by_size"]["S"]["count"] == 0
        assert data["points"] == []


# ---------------------------------------------------------------------------
# AC10 — sizes with zero tickets: count=0, min/avg/max=null
# ---------------------------------------------------------------------------

class TestZeroSizes:
    def test_empty_size_has_count_zero(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 300)
            # No XL tickets
            conn.commit()
        entry = _call(db).json()["by_size"]["XL"]
        assert entry["count"] == 0

    def test_empty_size_has_null_stats(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            _insert_ticket(conn, 1, 101, "S", 300)
            conn.commit()
        entry = _call(db).json()["by_size"]["XL"]
        assert entry["min_minutes"] is None
        assert entry["avg_minutes"] is None
        assert entry["max_minutes"] is None

    def test_all_sizes_present_even_with_no_tickets(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-1", "owner/myrepo")
            conn.commit()
        by_size = _call(db).json()["by_size"]
        for sz in ("S", "M", "L", "XL"):
            assert sz in by_size
            assert by_size[sz]["count"] == 0


# ---------------------------------------------------------------------------
# AC11 — unknown slug returns 404
# ---------------------------------------------------------------------------

class TestNotFound:
    def test_unknown_slug_returns_404(self):
        import server as srv
        from starlette.testclient import TestClient

        # Let _resolve_project_slug raise the real 404
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get("/api/projects/does-not-exist/analytics/calibration")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC12 — combined filters
# ---------------------------------------------------------------------------

class TestCombinedFilters:
    def test_since_until_sprint_combined(self, db):
        with db.connect() as conn:
            _insert_sprint(conn, 1, "sprint-42", "owner/myrepo")
            _insert_sprint(conn, 2, "sprint-43", "owner/myrepo")
            # Matching: sprint-42, in range
            _insert_ticket(conn, 1, 101, "M", 900, completed_at="2026-02-10T10:00:00+00:00")
            # Wrong sprint
            _insert_ticket(conn, 2, 201, "M", 900, completed_at="2026-02-10T10:00:00+00:00")
            # Right sprint, before since
            _insert_ticket(conn, 1, 102, "M", 600, completed_at="2025-12-01T10:00:00+00:00")
            # Right sprint, after until
            _insert_ticket(conn, 1, 103, "M", 1200, completed_at="2026-04-01T10:00:00+00:00")
            conn.commit()
        data = _call(db, since="2026-01-01", until="2026-03-31", sprint="sprint-42").json()
        assert data["by_size"]["M"]["count"] == 1
        assert len(data["points"]) == 1
        assert data["points"][0]["issue_number"] == 101
