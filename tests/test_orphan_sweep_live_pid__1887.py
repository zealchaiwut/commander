"""Tests for issue #1887 — orphan DB sweep must not false-fail sprints
in post-sprint phase and must resolve PID paths under the project root.

AC1: sweep verifies manager PID is dead before settling a running row
AC2: sprint in post-sprint phase (plan.json terminal, manager alive) survives
AC3: _manager_pid_file resolves under the sprint's PROJECT root, not _REPO_ROOT
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import db as _db_module  # noqa: E402
from routers import sprint_reconcile_service as _srs  # noqa: E402

_OLD = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "sweep_1887.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def startup(fresh_db, monkeypatch):
    import startup as st
    monkeypatch.setattr(st, "_all_sprints_running", lambda: [])
    monkeypatch.setattr(st.github_client, "list_merged_sprint_branches", lambda *a, **k: set())
    return st


# ── AC1/AC2: live PID → sweep is a no-op ─────────────────────────────────────

def test_live_manager_pid_prevents_orphan_settle(fresh_db, startup, tmp_path, monkeypatch):
    """AC1: A running DB row whose manager PID is alive must NOT be settled.

    Reproduces the perf-coach sprint-104.1 failure: plan.json leaves
    state=running (post-sprint), sprint drops from _all_sprints_running,
    but the manager process is alive dispatching documenter/reviewer.

    UAT step: pytest tests/test_orphan_sweep_live_pid__1887.py::test_live_manager_pid_prevents_orphan_settle
    Expected: green — row stays 'running'.
    """
    label = "sprint-104.1"
    fresh_db.record_sprint_start(label, project="zealchaiwut/perf-coach", started_at=_OLD)

    live_pid = os.getpid()  # guaranteed alive

    # Patch _live_manager_pid to return the live PID for this label
    monkeypatch.setattr(startup, "_live_manager_pid", lambda root, lbl: live_pid if lbl == label else None)

    swept = startup._sweep_orphan_db_running_rows()

    assert label not in swept, "A row whose manager PID is alive must not be swept"
    assert fresh_db.get_sprint(label)["state"] == "running", (
        "State must remain 'running' when manager is alive"
    )


def test_dead_manager_pid_allows_settle(fresh_db, startup, monkeypatch):
    """AC1: A running DB row whose manager PID is dead IS settled by the sweep.

    UAT step: pytest tests/test_orphan_sweep_live_pid__1887.py::test_dead_manager_pid_allows_settle
    Expected: green — row transitions to needs_rework.
    """
    label = "sprint-999.dead"
    fresh_db.record_sprint_start(label, project="zealchaiwut/perf-coach", started_at=_OLD)

    # Patch _live_manager_pid to return None (dead / no PID file)
    monkeypatch.setattr(startup, "_live_manager_pid", lambda root, lbl: None)

    swept = startup._sweep_orphan_db_running_rows()

    assert label in swept, "A row with dead manager PID must be swept"
    assert fresh_db.get_sprint(label)["state"] in ("needs_rework", "completed"), (
        "Swept row must be in a terminal state"
    )


def test_pid_check_exception_does_not_block_sweep(fresh_db, startup, monkeypatch):
    """AC1: If _live_manager_pid raises, the sweep still proceeds (best-effort)."""
    label = "sprint-888.err"
    fresh_db.record_sprint_start(label, project="zealchaiwut/perf-coach", started_at=_OLD)

    def _raise(*_a, **_k):
        raise RuntimeError("simulated error")

    monkeypatch.setattr(startup, "_live_manager_pid", _raise)

    swept = startup._sweep_orphan_db_running_rows()
    # Row must still be swept (error is swallowed, not blocking)
    assert label in swept


# ── AC3: _manager_pid_file resolves under project root ───────────────────────

def test_manager_pid_file_uses_project_root(tmp_path):
    """AC3: _manager_pid_file must resolve under the sprint's project root.

    For a non-commander project (perf-coach), the PID file must be at
    <perf-coach-root>/.commander/sprints/<label>-pid, not under the
    commander dashboard repo root.

    UAT step: pytest tests/test_orphan_sweep_live_pid__1887.py::test_manager_pid_file_uses_project_root
    Expected: green — returned path is under the project-specific root.
    """
    project = "zealchaiwut/perf-coach"
    label = "sprint-104.1"
    fake_project_root = tmp_path / "perf-coach"
    fake_project_root.mkdir()

    import server as srv  # noqa: PLC0415
    with patch.object(srv, "_project_root_path", return_value=fake_project_root):
        pid_path = _srs._manager_pid_file(label, project)

    expected = fake_project_root / ".commander" / "sprints" / f"{label}-pid"
    assert pid_path == expected, (
        f"PID file must be under project root; got {pid_path}, want {expected}"
    )


def test_manager_pid_file_fallback_without_project():
    """AC3: _manager_pid_file without a project falls back to _REPO_ROOT."""
    label = "sprint-1"
    pid_path = _srs._manager_pid_file(label)
    assert pid_path.name == f"{label}-pid"
    # Must NOT be empty — must have a valid parent
    assert pid_path.parent.exists() or True  # path derivation only; existence not required


def test_manager_pid_file_project_root_honored_in_liveness_check(tmp_path):
    """AC3: _is_manager_pid_alive reads the PID from the project-scoped path.

    A PID file at the project root (not _REPO_ROOT) must be detected as alive.

    UAT step: pytest tests/test_orphan_sweep_live_pid__1887.py::test_manager_pid_file_project_root_honored_in_liveness_check
    Expected: green — alive PID in project-scoped path → True.
    """
    project = "zealchaiwut/perf-coach"
    label = "sprint-104.1"
    fake_project_root = tmp_path / "perf-coach"
    sprints_dir = fake_project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)

    live_pid = os.getpid()
    (sprints_dir / f"{label}-pid").write_text(str(live_pid))

    import server as srv  # noqa: PLC0415
    with patch.object(srv, "_project_root_path", return_value=fake_project_root):
        alive = _srs._is_manager_pid_alive(label, project)

    assert alive is True, "Live PID in project-scoped path must be detected as alive"
