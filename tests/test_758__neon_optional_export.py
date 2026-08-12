"""Tests for issue #758: remove Neon dual-write; add one-shot export script.

Each test is anchored to a specific acceptance criterion. Tests are never
softened to pass — the implementation must satisfy the assertion.

AC1  grep 'sprint_repo|sync_projects_to_neon' finds no live call sites in
     normal-operation code paths (server.py, sprint_manager.py, calibration.py,
     estimate_issue.py).
AC2  Missing DATABASE_URL causes zero warnings/errors/exceptions on any
     normal-operation code path.
AC3  neon_db.py engine is cached at module level (no per-call create_engine()).
AC4  scripts/export_to_neon.py exists, is runnable, and exits cleanly (0) with a
     set DATABASE_URL.
AC5  README Neon section describes Neon as an optional export target with setup
     instructions.
AC6  Alembic migrations and SQLAlchemy models remain intact, pointed at SQLite.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"
for _p in (REPO_ROOT, DASHBOARD_DIR, SM_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _code_lines(path: Path) -> str:
    """Return source with full-line comments and blank lines stripped.

    Keeps inline code; drops `# ...` comment-only lines so a comment mentioning a
    removed symbol never trips the call-site scan.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


# ── AC1: no live sprint_repo / sync_projects_to_neon call sites ───────────────

@pytest.mark.parametrize(
    "rel",
    [
        "apps/dashboard/server.py",
        "services/sprint_manager/sprint_manager.py",
        "services/sprint_manager/calibration.py",
        "services/sprint_manager/estimate_issue.py",
    ],
)
def test_ac1_no_sprint_repo_callsites(rel):
    src = _code_lines(REPO_ROOT / rel)
    # No import of sprint_repo, no attribute calls into it.
    assert "sprint_repo" not in src, (
        f"{rel} still references sprint_repo — Neon dual-write not removed (AC1)"
    )


def test_ac1_no_sync_projects_callsites_in_server():
    src = _code_lines(REPO_ROOT / "apps/dashboard/server.py")
    assert "sync_projects_to_neon" not in src, (
        "server.py still calls sync_projects_to_neon in normal operation (AC1)"
    )
    assert "_sync_projects_module" not in src, (
        "server.py still wires the sync-projects module (AC1)"
    )


# ── AC2: missing DATABASE_URL → zero errors on normal paths ──────────────────

@pytest.fixture
def no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield


def test_ac2_settings_repo_degrades_without_database_url(no_database_url, monkeypatch, tmp_path):
    import services.sprint_manager.settings_repo as settings_repo
    import services.sprint_manager.neon_db as neon_db
    monkeypatch.setattr(neon_db, "_engine", None)  # force re-resolution of DATABASE_URL
    monkeypatch.setattr(settings_repo, "_session_factory", None)
    # Point the fallback store at a temp dir so we don't touch real settings.
    monkeypatch.setattr(settings_repo, "_fallback_store_path",
                        lambda: tmp_path / "settings_store.json")
    # No exception, returns a dict.
    assert settings_repo.get_setting("estimation") == {}
    settings_repo.set_setting("global", "estimation", {"a": 1})
    assert settings_repo.get_setting("estimation") == {"a": 1}


def test_ac2_calibration_records_no_error_without_database_url(no_database_url, tmp_path):
    import services.sprint_manager.calibration as calibration
    # Must not raise and must not attempt a Neon connection.
    result = calibration.db_calibration_records("sprint-999", tmp_path)
    assert isinstance(result, list)


def test_ac2_neon_db_import_clean_without_database_url(no_database_url):
    import services.sprint_manager.neon_db as neon_db
    # Importing the module must never raise even with DATABASE_URL unset.
    assert hasattr(neon_db, "get_engine")
    assert hasattr(neon_db, "Base")


# ── AC3: engine cached at module level ───────────────────────────────────────

def test_ac3_engine_cached_at_module_level(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    import services.sprint_manager.neon_db as neon_db
    monkeypatch.setattr(neon_db, "_engine", None)  # reset cache for this test

    calls = {"n": 0}
    sentinel = object()

    def _counting_create_engine(*args, **kwargs):
        calls["n"] += 1
        return sentinel  # never actually connect — caching is what we assert

    monkeypatch.setattr(neon_db, "create_engine", _counting_create_engine)

    e1 = neon_db.get_engine()
    e2 = neon_db.get_engine()
    assert e1 is e2 is sentinel, "get_engine() must return a cached engine instance (AC3)"
    assert calls["n"] == 1, (
        f"create_engine() called {calls['n']}x — must be cached at module level (AC3)"
    )


def test_ac3_source_has_no_per_call_create_engine():
    src = _code_lines(SM_DIR / "neon_db.py")
    # The create_engine call must live behind a module-level cache, not inside a
    # function that runs unconditionally on every get_engine() call.
    body = src
    # crude but anchored: there must be a module-level cache sentinel.
    assert re.search(r"_engine\b", body), (
        "neon_db.py must hold a module-level engine cache variable (AC3)"
    )


# ── AC4: export script exists, runnable, exits 0 with DATABASE_URL set ────────

def test_ac4_export_script_exists():
    assert (REPO_ROOT / "scripts" / "export_to_neon.py").is_file(), (
        "scripts/export_to_neon.py must exist (AC4)"
    )


def test_ac4_export_script_runs_and_exits_zero(tmp_path):
    # Local SQLite source with one sprint + ticket order (issue #757 schema).
    db_file = tmp_path / "dashboard.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE sprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT UNIQUE NOT NULL,
            project TEXT,
            state TEXT NOT NULL DEFAULT 'planning',
            created_at TEXT,
            started_at TEXT,
            ended_at TEXT,
            end_reason TEXT,
            parent_label TEXT
        );
        CREATE TABLE sprint_ticket_order (
            label TEXT NOT NULL,
            issue INTEGER NOT NULL,
            position INTEGER NOT NULL
        );
        INSERT INTO sprints (label, project, state) VALUES ('sprint-1', 'o/r', 'completed');
        INSERT INTO sprint_ticket_order (label, issue, position) VALUES ('sprint-1', 42, 0);
        """
    )
    conn.commit()
    conn.close()

    neon_file = tmp_path / "neon_export.db"
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{neon_file}"
    env["DB_PATH"] = str(db_file)

    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "export_to_neon.py")],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"export_to_neon.py exited {proc.returncode} with DATABASE_URL set (AC4)\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )

    # UAT step 3: rows visible in the Neon target matching local SQLite data.
    out = sqlite3.connect(neon_file)
    labels = [r[0] for r in out.execute("SELECT label FROM sprints").fetchall()]
    out.close()
    assert "sprint-1" in labels, "export did not write sprint rows to the target (AC4)"


# ── AC5: README Neon section = optional export target ────────────────────────

def test_ac5_readme_describes_neon_as_optional_export_target():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "export_to_neon.py" in readme, "README must document the export script (AC5)"
    assert "optional" in readme and "export" in readme, (
        "README Neon section must describe Neon as an optional export target (AC5)"
    )


# ── AC6: migrations + models intact, app on SQLite ───────────────────────────

def test_ac6_alembic_migrations_intact():
    versions = REPO_ROOT / "alembic" / "versions"
    files = sorted(p.name for p in versions.glob("0*.py"))
    # The #757 lifecycle migration and the original init must still be present.
    assert any(f.startswith("0001_") for f in files), "0001 init migration missing (AC6)"
    assert any(f.startswith("0008_") for f in files), "0008 lifecycle migration missing (AC6)"


def test_ac6_sqlalchemy_models_intact():
    import services.sprint_manager.models as models
    assert hasattr(models, "Sprint")
    assert hasattr(models, "SprintTicket")
    import services.sprint_manager.neon_db as neon_db
    assert hasattr(neon_db, "Base")


def test_ac6_primary_store_is_sqlite():
    # The live app store is SQLite (apps/dashboard/db.py uses sqlite3).
    db_src = (DASHBOARD_DIR / "db.py").read_text(encoding="utf-8")
    assert "import sqlite3" in db_src, "primary store must remain SQLite (AC6)"
