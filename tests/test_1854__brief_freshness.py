"""Tests for issue #1854 — home brief freshness invalidation.

AC1  Calling invalidate_home_brief_today() deletes today's home brief artifact
     from the store so the next GET regenerates it.
AC2  The invalidation call is fire-and-forget: any DB error is swallowed and
     the lifecycle action (sprint start / finish / label change) is not
     interrupted.
AC3  Home top-nav gets a Refresh button (tested in tests/frontend/).
AC4  Behavioral cycle: after invalidation, get_or_create_home_artifact()
     regenerates and returns a fresh available artifact.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

TODAY = "2026-07-12"


def _load_module(name: str, path: Path):
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


import db as db_module  # noqa: E402

bi = _load_module("routers.brief_invalidation", ROUTERS_DIR / "brief_invalidation.py")

# Load brief_artifact and its deps for AC4
svc = _load_module("routers.brief_service", ROUTERS_DIR / "brief_service.py")
summ = _load_module("routers.brief_summary", ROUTERS_DIR / "brief_summary.py")
artifact = _load_module("routers.brief_artifact", ROUTERS_DIR / "brief_artifact.py")


@pytest.fixture()
def fresh_db(tmp_path):
    db_file = tmp_path / "test_1854.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


def _store_home_artifact(db):
    """Seed a home artifact for TODAY."""
    db.set_brief_artifact("home", "", TODAY, {"scope": "home", "project": "", "date": TODAY})


# ── AC1: invalidate deletes the artifact ─────────────────────────────────────

def test_ac1_invalidate_deletes_home_artifact(fresh_db, monkeypatch):
    monkeypatch.setattr(bi, "_today", lambda: TODAY)
    _store_home_artifact(fresh_db)
    assert fresh_db.get_brief_artifact("home", "", TODAY) is not None

    bi.invalidate_home_brief_today()

    assert fresh_db.get_brief_artifact("home", "", TODAY) is None


# ── AC2: fire-and-forget swallows exceptions ──────────────────────────────────

def test_ac2_invalidation_swallows_db_error(fresh_db, monkeypatch):
    _store_home_artifact(fresh_db)

    def _raise(*a, **k):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(db_module, "delete_brief_artifact", _raise)
    # Must not raise
    bi.invalidate_home_brief_today()


# ── AC1: state_machine._write_ticket_status invalidates artifact ──────────────

def test_ac1_state_machine_transition_invalidates_artifact(fresh_db, monkeypatch):
    _store_home_artifact(fresh_db)

    # Load state_machine via its file path so it picks up DASHBOARD_DIR in path
    spec = importlib.util.spec_from_file_location(
        "services.sprint_manager.state_machine",
        SERVICES_DIR / "state_machine.py",
    )
    sm = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(sm)  # type: ignore[union-attr]

    monkeypatch.setattr(sm, "_brief_today", lambda: TODAY, raising=False)
    sm._write_ticket_status(issue=42, status="SIT", actor="test", note=None)

    assert fresh_db.get_brief_artifact("home", "", TODAY) is None


# ── AC4: behavioral cycle — deleted artifact is rebuilt on next GET ───────────

def test_ac4_behavioral_deleted_then_rebuilt(fresh_db, monkeypatch):
    # Pin "today" so brief_artifact treats TODAY as current
    monkeypatch.setattr(artifact, "current_brief_date", lambda: TODAY, raising=True)
    monkeypatch.setattr(bi, "_today", lambda: TODAY)
    # Stub generation to avoid LLM subprocess
    monkeypatch.setattr(
        artifact, "_generate_home_artifact",
        lambda date: {
            "scope": "home", "project": "", "date": date,
            "covering_since": "Jul 11, 6:00 AM",
            "window_start": "2026-07-11T06:00:00",
            "window_end": "2026-07-12T06:00:00",
            "summary": "All quiet.", "summary_source": "stub",
            "brief": {},
        },
        raising=True,
    )
    monkeypatch.setattr(artifact, "_now_iso", lambda: "2026-07-12T10:00:00")

    # 1. Seed an artifact
    _store_home_artifact(fresh_db)
    assert fresh_db.get_brief_artifact("home", "", TODAY) is not None

    # 2. Invalidate
    bi.invalidate_home_brief_today()
    assert fresh_db.get_brief_artifact("home", "", TODAY) is None

    # 3. GET regenerates it
    result = artifact.get_or_create_home_artifact(date=TODAY)
    assert result.get("available") is True
    assert result.get("summary") == "All quiet."
