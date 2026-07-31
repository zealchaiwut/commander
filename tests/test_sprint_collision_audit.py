"""Tests for scripts/audit_sprint_collisions.py (issue #1461).

Each test is anchored to a specific acceptance criterion:

AC1  Script exists and is importable; has a run_audit() public function.
AC2  run_audit prints a Markdown table with label/survivor/lost columns.
AC3  run_audit writes sprint-collisions.json with label/survivor/lost fields.
AC6  Synthetic two-project collision: manifest correctly identifies survivor vs lost.
AC7  Script is read-only: no DB writes occur; only .commander/runtime/ is written.
AC5  GET /api/debug/sprint-collisions returns HTTP 200 whose body is the manifest.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SCRIPTS_DIR = REPO_ROOT / "scripts"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402

_PROJ_A = "zealchaiwut/proj-alpha"
_PROJ_B = "zealchaiwut/proj-beta"
_LABEL = "sprint-collision-test"


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB for each test; restores DB_PATH on teardown."""
    db_file = tmp_path / "test_audit_collision.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def audit(tmp_path):
    """Import (or reload) audit_sprint_collisions with a fresh tmp runtime dir."""
    import audit_sprint_collisions as _mod
    importlib.reload(_mod)
    return _mod


def _seed_collision(db_module, *, survivor_project: str, lost_project: str, label: str):
    """Seed the DB with a synthetic two-project collision.

    survivor_project owns the current sprints row; lost_project appears only in
    sprint_history (simulating the project that was overwritten).
    """
    with db_module.get_conn() as conn:
        db_module._create_sprint_lifecycle_tables(conn)
        # Survivor: current sprints row
        conn.execute(
            "INSERT OR REPLACE INTO sprints (label, project, state) VALUES (?, ?, ?)",
            (label, survivor_project, "completed"),
        )
        # Lost: historical record in sprint_history
        db_module._create_sprint_history_table(conn)
        conn.execute(
            """
            INSERT INTO sprint_history
                (label, project, lifecycle_state, issues_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (label, lost_project, "completed", "[]", "2026-01-01T00:00:00"),
        )
        conn.commit()


# ── AC1: importable ───────────────────────────────────────────────────────────

def test_ac1_script_is_importable():
    """audit_sprint_collisions.py exists and is importable."""
    assert (SCRIPTS_DIR / "audit_sprint_collisions.py").is_file(), (
        "scripts/audit_sprint_collisions.py must exist"
    )
    import audit_sprint_collisions as mod
    assert hasattr(mod, "run_audit"), "Module must expose a run_audit() function"


# ── AC2: Markdown table ───────────────────────────────────────────────────────

def test_ac2_prints_markdown_table_with_header(capsys, fresh_db, tmp_path, audit):
    """run_audit prints a Markdown table with a header row."""
    _seed_collision(
        fresh_db,
        survivor_project=_PROJ_A,
        lost_project=_PROJ_B,
        label=_LABEL,
    )
    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    out = capsys.readouterr().out
    assert "| label" in out.lower() or "| Label" in out, (
        "Markdown table must have a 'label' column header"
    )
    assert "survivor" in out.lower(), "Markdown table must have a 'survivor' column"
    assert "lost" in out.lower(), "Markdown table must have a 'lost' column"


def test_ac2_table_contains_collision_label(capsys, fresh_db, tmp_path, audit):
    """run_audit prints the colliding sprint label in the table."""
    _seed_collision(
        fresh_db,
        survivor_project=_PROJ_A,
        lost_project=_PROJ_B,
        label=_LABEL,
    )
    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    out = capsys.readouterr().out
    assert _LABEL in out, f"Table must contain the colliding label '{_LABEL}'"


# ── AC3: JSON manifest ────────────────────────────────────────────────────────

def test_ac3_writes_manifest_json(fresh_db, tmp_path, audit):
    """run_audit writes sprint-collisions.json to runtime_dir."""
    _seed_collision(
        fresh_db,
        survivor_project=_PROJ_A,
        lost_project=_PROJ_B,
        label=_LABEL,
    )
    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    manifest_path = runtime_dir / "sprint-collisions.json"
    assert manifest_path.is_file(), "sprint-collisions.json must be written to runtime_dir"


def test_ac3_manifest_has_required_fields(fresh_db, tmp_path, audit):
    """Each manifest entry has label, survivor, and lost fields."""
    _seed_collision(
        fresh_db,
        survivor_project=_PROJ_A,
        lost_project=_PROJ_B,
        label=_LABEL,
    )
    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    manifest_path = runtime_dir / "sprint-collisions.json"
    entries = json.loads(manifest_path.read_text())
    assert isinstance(entries, list), "Manifest must be a JSON array"

    entry = next((e for e in entries if e["label"] == _LABEL), None)
    assert entry is not None, f"Manifest must contain an entry for '{_LABEL}'"
    assert "label" in entry
    assert "survivor" in entry
    assert "lost" in entry
    assert isinstance(entry["lost"], list)


# ── AC6: synthetic collision correctly assigns survivor / lost ────────────────

def test_ac6_survivor_is_current_sprints_row_owner(fresh_db, tmp_path, audit):
    """Survivor is the project holding the current sprints row."""
    _seed_collision(
        fresh_db,
        survivor_project=_PROJ_A,
        lost_project=_PROJ_B,
        label=_LABEL,
    )
    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    manifest_path = runtime_dir / "sprint-collisions.json"
    entries = json.loads(manifest_path.read_text())
    entry = next(e for e in entries if e["label"] == _LABEL)

    assert entry["survivor"] == _PROJ_A, (
        f"survivor must be {_PROJ_A!r} (current sprints row owner)"
    )


def test_ac6_lost_contains_overwritten_project(fresh_db, tmp_path, audit):
    """Lost array contains the project displaced from the sprints table."""
    _seed_collision(
        fresh_db,
        survivor_project=_PROJ_A,
        lost_project=_PROJ_B,
        label=_LABEL,
    )
    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    manifest_path = runtime_dir / "sprint-collisions.json"
    entries = json.loads(manifest_path.read_text())
    entry = next(e for e in entries if e["label"] == _LABEL)

    assert _PROJ_B in entry["lost"], (
        f"{_PROJ_B!r} must appear in 'lost' (it was overwritten)"
    )
    assert _PROJ_A not in entry["lost"], (
        f"survivor {_PROJ_A!r} must NOT appear in 'lost'"
    )


def test_ac6_collision_via_agent_runs(fresh_db, tmp_path, audit):
    """Collision is detected when the losing project appears only in agent_runs."""
    with fresh_db.get_conn() as conn:
        fresh_db._create_sprint_lifecycle_tables(conn)
        # Survivor in sprints
        conn.execute(
            "INSERT OR REPLACE INTO sprints (label, project, state) VALUES (?, ?, ?)",
            (_LABEL, _PROJ_A, "completed"),
        )
        # Lost project appears only in agent_runs
        fresh_db._create_agent_runs_table(conn)
        conn.execute(
            """
            INSERT INTO agent_runs
                (issue_number, sprint_label, agent, started_at, project)
            VALUES (?, ?, ?, ?, ?)
            """,
            (42, _LABEL, "coder", "2026-01-01T00:00:00", _PROJ_B),
        )
        conn.commit()

    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    manifest_path = runtime_dir / "sprint-collisions.json"
    entries = json.loads(manifest_path.read_text())
    entry = next((e for e in entries if e["label"] == _LABEL), None)

    assert entry is not None, "Collision via agent_runs must be reported"
    assert entry["survivor"] == _PROJ_A
    assert _PROJ_B in entry["lost"]


def test_ac6_no_collision_when_single_project(fresh_db, tmp_path, audit):
    """Labels claimed by only one project must NOT appear in the manifest."""
    with fresh_db.get_conn() as conn:
        fresh_db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT OR REPLACE INTO sprints (label, project, state) VALUES (?, ?, ?)",
            (_LABEL, _PROJ_A, "completed"),
        )
        conn.commit()

    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    manifest_path = runtime_dir / "sprint-collisions.json"
    entries = json.loads(manifest_path.read_text())
    labels = [e["label"] for e in entries]
    assert _LABEL not in labels, (
        "Labels with only one project must not appear in the collision manifest"
    )


# ── AC7: read-only (DB) ───────────────────────────────────────────────────────

def test_ac7_script_makes_no_db_writes(fresh_db, tmp_path, audit):
    """run_audit makes no DB writes — sprints row count is unchanged."""
    _seed_collision(
        fresh_db,
        survivor_project=_PROJ_A,
        lost_project=_PROJ_B,
        label=_LABEL,
    )
    with fresh_db.get_conn() as conn:
        before_sprints = conn.execute("SELECT count(*) FROM sprints").fetchone()[0]
        before_history = conn.execute("SELECT count(*) FROM sprint_history").fetchone()[0]

    runtime_dir = tmp_path / ".commander" / "runtime"
    audit.run_audit(db_path=fresh_db.DB_PATH, runtime_dir=runtime_dir)

    with fresh_db.get_conn() as conn:
        after_sprints = conn.execute("SELECT count(*) FROM sprints").fetchone()[0]
        after_history = conn.execute("SELECT count(*) FROM sprint_history").fetchone()[0]

    assert before_sprints == after_sprints, "run_audit must not modify sprints table"
    assert before_history == after_history, "run_audit must not modify sprint_history table"


# ── AC5: GET /api/debug/sprint-collisions ────────────────────────────────────

def test_ac5_endpoint_returns_200(tmp_path, monkeypatch):
    """GET /api/debug/sprint-collisions returns HTTP 200."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    # Build minimal app with just the sprint_collisions router
    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    import sprint_collisions as _sc_mod
    importlib.reload(_sc_mod)

    manifest = [{"label": "sprint-99", "survivor": _PROJ_A, "lost": [_PROJ_B]}]
    manifest_file = tmp_path / "sprint-collisions.json"
    manifest_file.write_text(json.dumps(manifest))

    monkeypatch.setattr(_sc_mod, "_manifest_path", lambda: manifest_file)

    test_app = FastAPI()
    test_app.include_router(_sc_mod.router)
    client = TestClient(test_app)

    resp = client.get("/api/debug/sprint-collisions")
    assert resp.status_code == 200


def test_ac5_endpoint_body_matches_manifest(tmp_path, monkeypatch):
    """GET /api/debug/sprint-collisions body matches sprint-collisions.json exactly."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    import sprint_collisions as _sc_mod
    importlib.reload(_sc_mod)

    manifest = [
        {"label": "sprint-99", "survivor": _PROJ_A, "lost": [_PROJ_B]},
        {"label": "sprint-77", "survivor": _PROJ_B, "lost": [_PROJ_A]},
    ]
    manifest_file = tmp_path / "sprint-collisions.json"
    manifest_file.write_text(json.dumps(manifest))

    monkeypatch.setattr(_sc_mod, "_manifest_path", lambda: manifest_file)

    test_app = FastAPI()
    test_app.include_router(_sc_mod.router)
    client = TestClient(test_app)

    resp = client.get("/api/debug/sprint-collisions")
    assert resp.status_code == 200
    body = resp.json()
    assert body == manifest


def test_ac5_endpoint_returns_empty_list_when_no_manifest(tmp_path, monkeypatch):
    """GET /api/debug/sprint-collisions returns [] when manifest file is absent."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    import sprint_collisions as _sc_mod
    importlib.reload(_sc_mod)

    monkeypatch.setattr(_sc_mod, "_manifest_path", lambda: tmp_path / "nonexistent.json")

    test_app = FastAPI()
    test_app.include_router(_sc_mod.router)
    client = TestClient(test_app)

    resp = client.get("/api/debug/sprint-collisions")
    assert resp.status_code == 200
    assert resp.json() == []
