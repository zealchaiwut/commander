"""Tests for issue #920 AC3 — History stats per-sprint coder backend split.

AC3: History stats: per-sprint coder backend split (cline count, claude-code
count) and per-backend duration shown.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB; patches db module."""
    db_file = tmp_path / "test_920_ac3.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


def _insert_coder_run(fresh_db, issue: int, label: str, backend: str, dur: int):
    """Insert a finished coder run with a known backend and duration."""
    import datetime
    t0 = "2026-06-14T10:00:00"
    t1_dt = datetime.datetime.fromisoformat(t0) + datetime.timedelta(seconds=dur)
    t1 = t1_dt.strftime("%Y-%m-%dT%H:%M:%S")
    fresh_db.record_agent_start(issue, label, "coder", started_at=t0, backend=backend)
    fresh_db.record_agent_finish(issue, label, "coder", finished_at=t1, outcome="success")


def test_sprint_run_stats_returns_coder_backend_split(fresh_db, monkeypatch):
    """AC3: sprint_run_stats includes cline_count and claude_code_count."""
    _insert_coder_run(fresh_db, 101, "sprint-71", "cline", 60)
    _insert_coder_run(fresh_db, 102, "sprint-71", "claude-code", 90)

    monkeypatch.setattr("db.DB_PATH", fresh_db.DB_PATH)

    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    from routers import run_stats_service
    monkeypatch.setattr(run_stats_service, "_db", lambda: fresh_db)

    result = run_stats_service.sprint_run_stats("sprint-71")
    cbs = result.get("coder_backend_split", {})
    assert cbs.get("cline_count", 0) == 1, f"expected cline_count=1, got {cbs}"
    assert cbs.get("claude_code_count", 0) == 1, f"expected claude_code_count=1, got {cbs}"


def test_sprint_run_stats_returns_coder_backend_durations(fresh_db, monkeypatch):
    """AC3: sprint_run_stats includes per-backend coder duration."""
    _insert_coder_run(fresh_db, 101, "sprint-71", "cline", 60)
    _insert_coder_run(fresh_db, 102, "sprint-71", "claude-code", 90)

    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    from routers import run_stats_service
    monkeypatch.setattr(run_stats_service, "_db", lambda: fresh_db)

    result = run_stats_service.sprint_run_stats("sprint-71")
    cbs = result.get("coder_backend_split", {})
    assert cbs.get("cline_seconds", 0) == 60, f"expected cline_seconds=60, got {cbs}"
    assert cbs.get("claude_code_seconds", 0) == 90, f"expected claude_code_seconds=90, got {cbs}"


def test_sprint_run_stats_backend_split_absent_when_no_backend(fresh_db, monkeypatch):
    """AC3: when no runs have a backend field, coder_backend_split totals are zero."""
    _db_module.record_agent_start(103, "sprint-72", "coder", started_at="2026-06-14T10:00:00")
    _db_module.record_agent_finish(103, "sprint-72", "coder",
                                   finished_at="2026-06-14T10:01:00", outcome="success")

    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    from routers import run_stats_service
    monkeypatch.setattr(run_stats_service, "_db", lambda: fresh_db)

    result = run_stats_service.sprint_run_stats("sprint-72")
    # Either no key (empty sprint) or zero counts for both backends
    cbs = result.get("coder_backend_split", {})
    assert cbs.get("cline_count", 0) == 0
    assert cbs.get("claude_code_count", 0) == 0


def test_history_stats_ui_shows_backend_split():
    """AC3: project.html must reference coder_backend_split for display."""
    src = (DASHBOARD_DIR / "static" / "project.html").read_text()
    assert "coder_backend_split" in src or "cline_count" in src, (
        "project.html must render coder backend split from run-stats data"
    )
