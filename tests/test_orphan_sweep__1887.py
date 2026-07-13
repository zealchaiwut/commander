"""Tests for issue #1887: orphan DB sweep false-fails sprints in post-sprint phase.

Acceptance Criteria:
- [ ] AC1: sweep verifies manager PID is dead before settling running row
- [ ] AC2: sprint in post-sprint phase survives sweep untouched
- [ ] AC3: _manager_pid_file resolves under project root, not _REPO_ROOT
- [ ] AC4: comprehensive test coverage: live PID → no-op, dead PID → settle, project-scoped paths honored

This test file verifies the AC via direct unit testing since these are
internal backend behaviors not exposed via the HTTP API.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "test_orphan_sweep_1887.db"))


@pytest.fixture()
def isolated_db(tmp_path):
    """Patch db.DB_PATH to a temp file so tests never touch the production DB."""
    import db as db_module
    original = db_module.DB_PATH
    db_module.DB_PATH = tmp_path / "test_1887.db"
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


def test_ac1_live_manager_pid_prevents_orphan_settle(isolated_db):
    """AC1: sweep must verify manager PID is dead before settling running row.

    Reproduces perf-coach sprint-104.1 false-fail: plan.json leaves state=running
    during post-sprint phase, sprint drops from _all_sprints_running, but manager
    process is alive dispatching documenter/reviewer. Sweep must NOT settle this.
    """
    import startup
    db_module = isolated_db
    label = "sprint-104.1-ac1"
    project = "zealchaiwut/perf-coach"

    # Seed running sprint with old started_at (triggers sweep eligibility)
    _old = "2020-01-01T00:00:00+00:00"
    db_module.record_sprint_start(label, project=project, started_at=_old)

    # Verify it's in running state
    row_before = db_module.get_sprint(label)
    assert row_before["state"] == "running", f"seed failed: {row_before}"

    # Patch _all_sprints_running to return empty (sprint drops from live list)
    # Patch _live_manager_pid to return a live PID (simulating active manager)
    with patch.object(startup, "_all_sprints_running", return_value=[]):
        with patch.object(startup, "_live_manager_pid", return_value=os.getpid()):
            swept = startup._sweep_orphan_db_running_rows()

    # Sprint must NOT be in swept list when manager is alive
    assert label not in swept, (
        "AC1 failed: sweep settled running row even though manager PID was alive"
    )
    # State must remain 'running'
    row_after = db_module.get_sprint(label)
    assert row_after["state"] == "running", (
        f"AC1 failed: state changed to {row_after['state']} despite live manager"
    )


def test_ac1_dead_manager_pid_allows_settle(isolated_db):
    """AC1 corollary: sweep must settle row when manager PID is dead."""
    import startup
    db_module = isolated_db
    label = "sprint-999-dead"
    project = "zealchaiwut/perf-coach"

    _old = "2020-01-01T00:00:00+00:00"
    db_module.record_sprint_start(label, project=project, started_at=_old)

    # Patch _all_sprints_running to return empty
    # Patch _live_manager_pid to return None (dead PID / no PID file)
    with patch.object(startup, "_all_sprints_running", return_value=[]):
        with patch.object(startup, "_live_manager_pid", return_value=None):
            swept = startup._sweep_orphan_db_running_rows()

    # Sprint MUST be in swept list when manager is dead
    assert label in swept, (
        "AC1 corollary failed: sweep did not settle row with dead manager PID"
    )
    # State must be terminal (needs_rework, completed, etc.)
    row_after = db_module.get_sprint(label)
    assert row_after["state"] in ("needs_rework", "completed"), (
        f"AC1 corollary failed: state is {row_after['state']}, not terminal"
    )


def test_ac2_post_sprint_phase_survives_sweep(isolated_db):
    """AC2: sprint in post-sprint phase (tickets done, documenter running) survives.

    During post-sprint, plan.json leaves state=running but _all_sprints_running
    filters it out. The manager PID is live (documenter/reviewer dispatched).
    Sweep must see the live PID and leave the row untouched.
    """
    import startup
    db_module = isolated_db
    label = "sprint-104-post-sprint-ac2"
    project = "zealchaiwut/perf-coach"

    _old = "2020-01-01T00:00:00+00:00"
    db_module.record_sprint_start(label, project=project, started_at=_old)

    # Simulate post-sprint: plan.json state=running but manager alive
    with patch.object(startup, "_all_sprints_running", return_value=[]):
        with patch.object(startup, "_live_manager_pid", return_value=os.getpid()):
            swept = startup._sweep_orphan_db_running_rows()

    # Sprint must survive
    assert label not in swept
    row = db_module.get_sprint(label)
    assert row["state"] == "running", "post-sprint sprint must remain running"


def test_ac3_manager_pid_file_resolves_under_project_root():
    """AC3: _manager_pid_file must resolve under sprint's PROJECT root.

    For non-commander projects (e.g. perf-coach), the PID file must be at
    <perf-coach-root>/.commander/sprints/<label>-pid, not commander repo root.
    """
    import server as srv
    from routers import sprint_reconcile_service as srs

    project = "zealchaiwut/perf-coach"
    label = "sprint-104.1"
    fake_project_root = Path("/fake/perf-coach")

    # Patch _project_root_path to return our fake root
    with patch.object(srv, "_project_root_path", return_value=fake_project_root):
        pid_path = srs._manager_pid_file(label, project)

    expected = fake_project_root / ".commander" / "sprints" / f"{label}-pid"
    assert pid_path == expected, (
        f"AC3 failed: resolved to {pid_path}, expected {expected}"
    )


def test_ac3_manager_pid_file_fallback_without_project():
    """AC3: _manager_pid_file without project falls back to _REPO_ROOT."""
    from routers import sprint_reconcile_service as srs

    label = "sprint-1"
    pid_path = srs._manager_pid_file(label, project="")

    # Must have a valid path structure
    assert pid_path.name == f"{label}-pid"
    assert ".commander" in str(pid_path)
    assert "sprints" in str(pid_path)


def test_ac3_is_manager_pid_alive_honors_project_root():
    """AC3: _is_manager_pid_alive must read PID from project-scoped file."""
    import server as srv
    from routers import sprint_reconcile_service as srs
    from tempfile import TemporaryDirectory

    project = "zealchaiwut/perf-coach"
    label = "sprint-104.1"

    with TemporaryDirectory() as tmpdir:
        fake_project_root = Path(tmpdir)
        sprints_dir = fake_project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)

        # Write our current PID to the project-scoped file
        live_pid = os.getpid()
        (sprints_dir / f"{label}-pid").write_text(str(live_pid))

        # Patch _project_root_path
        with patch.object(srv, "_project_root_path", return_value=fake_project_root):
            alive = srs._is_manager_pid_alive(label, project)

        assert alive is True, (
            "AC3 failed: alive check did not find live PID in project-scoped path"
        )


def test_ac4_comprehensive_sweep_scenarios(isolated_db):
    """AC4: comprehensive test coverage of all sweep scenarios.

    Tests the complete matrix:
    - Live PID in project root → sweep skips
    - Dead PID in project root → sweep settles
    - PID file absent → sweep settles (checked via _live_manager_pid returning None)
    - PID check exception → sweep proceeds (best-effort)
    """
    import startup
    db_module = isolated_db

    # Scenario 1: Live PID (already tested in AC1, here for matrix coverage)
    label_live = "scenario-live-ac4"
    db_module.record_sprint_start(label_live, project="p1", started_at="2020-01-01T00:00:00+00:00")

    # Scenario 2: Dead PID
    label_dead = "scenario-dead-ac4"
    db_module.record_sprint_start(label_dead, project="p2", started_at="2020-01-01T00:00:00+00:00")

    # Scenario 3: No PID file (None from _live_manager_pid)
    label_no_pid = "scenario-no-pid-ac4"
    db_module.record_sprint_start(label_no_pid, project="p3", started_at="2020-01-01T00:00:00+00:00")

    # Scenario 4: PID check exception (still sweeps)
    label_exception = "scenario-exception-ac4"
    db_module.record_sprint_start(label_exception, project="p4", started_at="2020-01-01T00:00:00+00:00")

    def mock_live_manager_pid(proj_root, lbl):
        """Return different values per scenario."""
        if lbl == label_live:
            return os.getpid()  # live
        elif lbl == label_dead:
            return None  # dead
        elif lbl == label_no_pid:
            return None  # no PID file
        elif lbl == label_exception:
            raise RuntimeError("simulated error")  # exception swallowed
        return None

    with patch.object(startup, "_all_sprints_running", return_value=[]):
        with patch.object(startup, "_live_manager_pid", side_effect=mock_live_manager_pid):
            swept = startup._sweep_orphan_db_running_rows()

    # Verify outcomes
    assert label_live not in swept, "Live PID scenario must not sweep"
    assert label_dead in swept, "Dead PID scenario must sweep"
    assert label_no_pid in swept, "No PID scenario must sweep"
    assert label_exception in swept, "Exception scenario must sweep (best-effort)"

    # Verify states
    assert db_module.get_sprint(label_live)["state"] == "running"
    assert db_module.get_sprint(label_dead)["state"] in ("needs_rework", "completed")
    assert db_module.get_sprint(label_no_pid)["state"] in ("needs_rework", "completed")
    assert db_module.get_sprint(label_exception)["state"] in ("needs_rework", "completed")
