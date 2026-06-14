"""Tests for issue #638 — settings KV table and sprint_tickets.estimated_size.

AC coverage:
  AC1  — settings table has required columns + unique constraint
  AC2  — sprint_tickets gains nullable estimated_size column
  AC4  — migration seeds global estimation row (fixture mirrors this)
  AC5  — get_setting('estimation') returns seeded global value
  AC6  — get_setting('estimation', project=...) returns global when no override
  AC7  — get_setting('estimation', project=...) deep-merges project override
  AC8  — set_setting('global', ...) upserts global row
  AC9  — set_setting('project', ...) upserts project-scoped row
  AC10 — helpers live in services/sprint_manager/; SQLite dashboard DB untouched
  AC11 — SCHEMA.md documents settings table and estimated_size column
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from conftest import make_sprint_db

import services.sprint_manager.settings_repo as settings_repo
from services.sprint_manager.settings_repo import get_setting, set_setting

SEED_VALUE = {
    "size_minutes": {"S": 5, "M": 15, "L": 30, "XL": 60},
    "buffer_pct": 20,
    "thin_ac_buffer_pct": 30,
}


@pytest.fixture(autouse=True)
def sqlite_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO settings (scope, project, key, value) VALUES ('global', NULL, 'estimation', :val)"),
            {"val": json.dumps(SEED_VALUE)},
        )
        conn.commit()
    SessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(settings_repo, "_session_factory", SessionLocal)
    yield engine


# AC1 — settings table columns and unique constraint
def test_settings_table_has_required_columns(sqlite_db):
    insp = inspect(sqlite_db)
    cols = {c["name"] for c in insp.get_columns("settings")}
    assert {"id", "scope", "project", "key", "value", "updated_at"} <= cols


def test_settings_table_unique_constraint(sqlite_db):
    # Use non-NULL project to avoid SQLite NULL-distinct behaviour
    with sqlite_db.connect() as conn:
        conn.execute(
            text("INSERT INTO settings (scope, project, key, value) VALUES ('project', 'proj-X', 'estimation', '{}')"),
        )
        conn.commit()
        with pytest.raises(Exception):
            conn.execute(
                text("INSERT INTO settings (scope, project, key, value) VALUES ('project', 'proj-X', 'estimation', '{}')"),
            )
            conn.commit()


# AC2 — sprint_tickets has nullable estimated_size column
def test_sprint_tickets_has_estimated_size(sqlite_db):
    insp = inspect(sqlite_db)
    cols = {c["name"] for c in insp.get_columns("sprint_tickets")}
    assert "estimated_size" in cols


def test_sprint_tickets_estimated_size_is_nullable(sqlite_db):
    with sqlite_db.connect() as conn:
        conn.execute(
            text("INSERT INTO sprint_tickets (sprint_id, issue_number, position) VALUES (1, 99, 0)"),
        )
        conn.commit()
        row = conn.execute(text("SELECT estimated_size FROM sprint_tickets WHERE issue_number=99")).fetchone()
        assert row[0] is None


# AC5 — get_setting returns seeded global value when no project override
def test_get_setting_returns_global_default():
    result = get_setting("estimation")
    assert result == SEED_VALUE


# AC6 — get_setting with project returns global value when no project row exists
def test_get_setting_with_project_returns_global_when_no_override():
    result = get_setting("estimation", project="proj-A")
    assert result == SEED_VALUE


# AC7 — get_setting deep-merges project override on top of global default
def test_get_setting_deep_merges_project_override():
    set_setting("project", "estimation", {"buffer_pct": 40}, project="proj-A")
    result = get_setting("estimation", project="proj-A")
    assert result["buffer_pct"] == 40
    assert result["size_minutes"] == SEED_VALUE["size_minutes"]
    assert result["thin_ac_buffer_pct"] == 30


def test_get_setting_project_fields_win_over_global():
    set_setting("project", "estimation", {"buffer_pct": 99, "extra": "yes"}, project="proj-B")
    result = get_setting("estimation", project="proj-B")
    assert result["buffer_pct"] == 99
    assert result["extra"] == "yes"
    assert result["size_minutes"] == SEED_VALUE["size_minutes"]


# AC8 — set_setting upserts global row
def test_set_setting_upserts_global():
    new_val = {**SEED_VALUE, "buffer_pct": 25}
    set_setting("global", "estimation", new_val)
    result = get_setting("estimation")
    assert result["buffer_pct"] == 25


def test_set_setting_global_idempotent():
    set_setting("global", "estimation", SEED_VALUE)
    set_setting("global", "estimation", {**SEED_VALUE, "buffer_pct": 35})
    result = get_setting("estimation")
    assert result["buffer_pct"] == 35


# AC9 — set_setting upserts project-scoped row
def test_set_setting_upserts_project():
    set_setting("project", "estimation", {"buffer_pct": 50}, project="proj-C")
    result = get_setting("estimation", project="proj-C")
    assert result["buffer_pct"] == 50


def test_set_setting_project_upsert_overwrites():
    set_setting("project", "estimation", {"buffer_pct": 50}, project="proj-D")
    set_setting("project", "estimation", {"buffer_pct": 70}, project="proj-D")
    result = get_setting("estimation", project="proj-D")
    assert result["buffer_pct"] == 70


# AC10 — helpers live in services/sprint_manager/; dashboard DB untouched
def test_helpers_in_sprint_manager_module():
    assert hasattr(settings_repo, "get_setting")
    assert hasattr(settings_repo, "set_setting")


def test_dashboard_db_module_unchanged():
    import importlib
    import apps.dashboard.db as db_mod
    assert not hasattr(db_mod, "get_setting"), (
        "get_setting should NOT be in apps/dashboard/db — it belongs in sprint_manager"
    )


# AC11 — SCHEMA.md documents settings table and estimated_size
def test_schema_md_documents_settings_table():
    schema_path = Path(__file__).parent.parent / "SCHEMA.md"
    content = schema_path.read_text()
    assert "settings" in content
    assert "scope" in content
    assert "estimated_size" in content
