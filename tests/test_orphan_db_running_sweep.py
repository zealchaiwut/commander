"""Tests for _sweep_orphan_db_running_rows (orphan 'running' DB-row expiry).

_all_sprints_running only sees sprints with a local plan.json. A sprints row
left at state='running' with no plan.json (cross-machine copy, deleted plan,
crash before terminal write) is otherwise never cleared. The sweep reconciles
such stale rows to needs_rework, while never touching a row that is genuinely
running locally or one too fresh to judge.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "sweep.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def startup(fresh_db, monkeypatch):
    sys.path.append(str(DASHBOARD_DIR))
    import startup as st
    # Isolate from real projects/plan.json scanning: no sprint is "live".
    monkeypatch.setattr(st, "_all_sprints_running", lambda: [])
    return st


_OLD = "2020-01-01T00:00:00+00:00"


def _running(db, label, project, started_at):
    db.record_sprint_start(label, project=project, started_at=started_at)


def test_sweep_expires_stale_running_row(fresh_db, startup):
    _running(fresh_db, "sprint-66", "zealchaiwut/perf-coach", _OLD)
    swept = startup._sweep_orphan_db_running_rows()
    assert "sprint-66" in swept
    assert fresh_db.get_sprint("sprint-66")["state"] == "needs_rework"


def test_sweep_skips_fresh_running_row(fresh_db, startup):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    _running(fresh_db, "sprint-70", "zealchaiwut/perf-coach", now)
    swept = startup._sweep_orphan_db_running_rows()
    assert "sprint-70" not in swept
    assert fresh_db.get_sprint("sprint-70")["state"] == "running"


def test_sweep_skips_locally_live_sprint(fresh_db, startup, monkeypatch):
    _running(fresh_db, "sprint-71", "zealchaiwut/perf-coach", _OLD)
    monkeypatch.setattr(
        startup, "_all_sprints_running",
        lambda: [{"project": "zealchaiwut/perf-coach", "sprint_label": "sprint-71", "pid": 123}],
    )
    swept = startup._sweep_orphan_db_running_rows()
    assert "sprint-71" not in swept
    assert fresh_db.get_sprint("sprint-71")["state"] == "running"


def test_sweep_leaves_terminal_rows_untouched(fresh_db, startup):
    _running(fresh_db, "sprint-72", "zealchaiwut/perf-coach", _OLD)
    fresh_db.record_sprint_finish("sprint-72", project="zealchaiwut/perf-coach")
    swept = startup._sweep_orphan_db_running_rows()
    assert "sprint-72" not in swept
    assert fresh_db.get_sprint("sprint-72")["state"] == "completed"
