"""Tests for issue #2031 — false-orphan sweep must not flag passing/finalizing sprints.

AC1 (sweep behavior):
    The orphan sweep does not flag a sprint whose plan.json is already in a
    terminal non-running state (e.g. ready_to_merge), even when the DB row
    still shows 'running' and the manager PID is dead.

AC2 (reproduce + fix):
    test_false_orphan_passing_sprint_not_swept and sibling tests reproduce the
    exact false-orphan condition and assert the fix leaves the sprint un-flagged.

AC3 (genuine orphan still detected):
    Sprints with plan.json=running (manager crashed before writing terminal
    state) are still swept — the fix does not disable the sweep.

Git-isolation guarantee
-----------------------
Every test is guarded by the ``git_no_mutation`` autouse fixture (pattern
copied from test_2030__crash_safe_finalization.py).  Any code path that runs
``git commit`` or ``git add`` causes the fixture to fail loudly.

How these tests FAIL against pre-fix code (AC2)
-------------------------------------------------
Before the fix, ``_sweep_orphan_db_running_rows`` does not read plan.json at
all.  For a sprint with DB=running, PID=dead, and branch not merged, it
unconditionally calls ``record_sprint_needs_rework`` with
``end_reason="orphaned (no live process)"`` — even when plan.json already
says ``ready_to_merge``.

  test_false_orphan_passing_sprint_not_swept:
      Pre-fix: label IS in swept (false-orphan written to DB).
      The assertion ``assert label not in swept`` FAILS.

  test_false_orphan_db_synced_to_ready_to_merge:
      Pre-fix: DB state is ``needs_rework`` (wrongly orphaned).
      The assertion ``assert row["state"] == "ready_to_merge"`` FAILS.

  test_false_orphan_hard_crash_end_reason_preserved:
      Pre-fix: DB end_reason becomes ``"orphaned (no live process)"``
      overwriting #2030's ``"hard-crash"`` diagnostic.
      The assertion ``assert label not in swept`` FAILS.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault(
    "DB_PATH", str(REPO_ROOT / "apps" / "dashboard" / "commander.db")
)

import db as _db_module  # noqa: E402
import startup as _startup  # noqa: E402


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    """Return current HEAD SHA for the working repo."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Records ``git rev-parse HEAD`` before each test and asserts it is
    unchanged afterward.  If HEAD moved, the fixture fails loudly with the
    before/after SHAs so the offending test is immediately obvious.

    Pattern copied verbatim from test_2030__crash_safe_finalization.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'.\n"
        "Ensure all git-touching sprint-end steps are stubbed."
    )


# ── fixtures ──────────────────────────────────────────────────────────────────

_OLD_START = "2020-01-01T00:00:00+00:00"


@pytest.fixture
def fresh_db(tmp_path):
    """Isolated SQLite DB that reverts to the original path after each test."""
    original = _db_module.DB_PATH
    _db_module.DB_PATH = tmp_path / "test_2031.db"
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


@pytest.fixture
def sweep(fresh_db, monkeypatch):
    """Return _sweep_orphan_db_running_rows with shared boundary stubs applied.

    Common stubs:
      _all_sprints_running       → [] (sprint drops from the live list)
      list_merged_sprint_branches → empty set (branch not yet merged)
      _live_manager_pid          → None (manager PID is dead / no PID file)
    """
    monkeypatch.setattr(_startup, "_all_sprints_running", lambda: [])
    monkeypatch.setattr(
        _startup.github_client,
        "list_merged_sprint_branches",
        lambda *a, **k: set(),
    )
    monkeypatch.setattr(_startup, "_live_manager_pid", lambda *a, **k: None)
    return _startup._sweep_orphan_db_running_rows


def _write_plan_json(sprints_dir: Path, label: str, state: str, **extra) -> None:
    """Write a minimal plan.json with the given state into sprints_dir."""
    sprints_dir.mkdir(parents=True, exist_ok=True)
    data = {"state": state}
    data.update(extra)
    (sprints_dir / f"{label}-plan.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


# ── AC2: false-orphan condition — must NOT be swept ───────────────────────────

class TestFalseOrphanNotFlagged:
    """AC2: Reproduce the false-orphan condition and verify the fix leaves it un-flagged.

    Scenario modelled:
      1. Sprint completed normally; all tickets passed.
      2. sprint_manager wrote plan.json → ready_to_merge (terminal).
      3. _sprint_db_set_state_sm failed silently → DB still shows running.
      4. Manager process exited (PID file removed).
      5. Sprint branch exists but PR is not yet merged.

      Pre-fix: sweep writes needs_rework / "orphaned (no live process)" — FALSE.
      Post-fix: sweep reads plan.json=ready_to_merge, syncs DB, skips. CORRECT.
    """

    def test_false_orphan_passing_sprint_not_swept(
        self, sweep, fresh_db, tmp_path, monkeypatch
    ):
        """AC2a: Sprint with plan.json=ready_to_merge must NOT appear in swept.

        Pre-fix failure: label IS in swept — the false-orphan is written to DB.
        The assertion ``assert label not in swept`` fails pre-fix.
        """
        label = "sprint-2031-passing"
        project = "zealchaiwut/test-project"
        fresh_db.record_sprint_start(label, project=project, started_at=_OLD_START)

        sprints_dir = tmp_path / ".commander" / "sprints"
        _write_plan_json(
            sprints_dir, label, "ready_to_merge",
            end_reason="natural",
            ended_at="2025-01-01T01:00:00+00:00",
        )

        # Point _project_root_path → tmp_path so the sweep finds the plan.json
        monkeypatch.setattr(_startup, "_project_root_path", lambda repo: tmp_path)

        swept = sweep()

        assert label not in swept, (
            f"False-orphan: sweep flagged a sprint whose plan.json=ready_to_merge "
            f"as orphaned.  swept={swept!r}"
        )

    def test_false_orphan_db_synced_to_ready_to_merge(
        self, sweep, fresh_db, tmp_path, monkeypatch
    ):
        """AC2b: DB is synced from plan.json=ready_to_merge, not set to needs_rework.

        Pre-fix failure: DB state is needs_rework (wrongly orphaned).
        """
        label = "sprint-2031-sync"
        project = "zealchaiwut/test-project"
        fresh_db.record_sprint_start(label, project=project, started_at=_OLD_START)

        sprints_dir = tmp_path / ".commander" / "sprints"
        _write_plan_json(sprints_dir, label, "ready_to_merge", end_reason="natural")

        monkeypatch.setattr(_startup, "_project_root_path", lambda repo: tmp_path)

        sweep()

        row = fresh_db.get_sprint(label)
        assert row["state"] == "ready_to_merge", (
            f"DB must be synced to ready_to_merge from plan.json; "
            f"got state={row['state']!r}"
        )

    def test_false_orphan_hard_crash_end_reason_preserved(
        self, sweep, fresh_db, tmp_path, monkeypatch
    ):
        """AC2c: plan.json=needs_rework with end_reason=hard-crash (from #2030) is
        NOT re-orphaned as 'orphaned (no live process)'.

        Pre-fix failure: DB gets end_reason='orphaned (no live process)', destroying
        the diagnostic written by #2030's crash handler.
        """
        label = "sprint-2031-crash"
        project = "zealchaiwut/test-project"
        fresh_db.record_sprint_start(label, project=project, started_at=_OLD_START)

        sprints_dir = tmp_path / ".commander" / "sprints"
        _write_plan_json(sprints_dir, label, "needs_rework", end_reason="hard-crash")

        monkeypatch.setattr(_startup, "_project_root_path", lambda repo: tmp_path)

        swept = sweep()

        assert label not in swept, (
            "A sprint already finalized with end_reason=hard-crash must not be "
            "re-orphaned.  swept=%r" % (swept,)
        )
        row = fresh_db.get_sprint(label)
        assert row.get("end_reason") != "orphaned (no live process)", (
            f"end_reason must not be overwritten to 'orphaned (no live process)'; "
            f"got {row.get('end_reason')!r}"
        )

    def test_false_orphan_documenter_crash_preserved(
        self, sweep, fresh_db, tmp_path, monkeypatch
    ):
        """AC2d: plan.json=needs_rework with end_reason=documenter-crash is not re-orphaned.

        This case arises when #2030's documenter except-block wrote terminal state
        but the DB write failed.  The sweep must not overwrite the real end_reason.
        """
        label = "sprint-2031-doc-crash"
        project = "zealchaiwut/test-project"
        fresh_db.record_sprint_start(label, project=project, started_at=_OLD_START)

        sprints_dir = tmp_path / ".commander" / "sprints"
        _write_plan_json(
            sprints_dir, label, "needs_rework", end_reason="documenter-crash"
        )

        monkeypatch.setattr(_startup, "_project_root_path", lambda repo: tmp_path)

        swept = sweep()

        assert label not in swept, (
            "A sprint finalized as documenter-crash must not be re-orphaned."
        )
        row = fresh_db.get_sprint(label)
        assert row.get("end_reason") != "orphaned (no live process)", (
            f"end_reason='documenter-crash' must be preserved, not overwritten; "
            f"got {row.get('end_reason')!r}"
        )


# ── AC3: genuine orphan is still detected ────────────────────────────────────

class TestGenuineOrphanStillDetected:
    """AC3: Sprints with plan.json=running (manager crashed before terminal write)
    are still flagged as orphaned.

    These tests prove the fix does not disable the sweep — only sprints that
    were already properly finalized (terminal plan.json) are protected.
    """

    def test_genuine_orphan_running_plan_is_swept(
        self, sweep, fresh_db, tmp_path, monkeypatch
    ):
        """AC3a: plan.json=running + dead PID → sprint IS swept as needs_rework.

        The manager crashed before writing its terminal state.  The DB row is
        legitimately stuck running.  The sweep must still settle it.
        """
        label = "sprint-2031-genuine-orphan"
        project = "zealchaiwut/test-project"
        fresh_db.record_sprint_start(label, project=project, started_at=_OLD_START)

        # plan.json is still running — manager crashed before the terminal write
        sprints_dir = tmp_path / ".commander" / "sprints"
        _write_plan_json(sprints_dir, label, "running")

        monkeypatch.setattr(_startup, "_project_root_path", lambda repo: tmp_path)

        swept = sweep()

        assert label in swept, (
            f"A genuine orphan (plan.json=running, dead PID) must be swept. "
            f"swept={swept!r}"
        )
        row = fresh_db.get_sprint(label)
        assert row["state"] in ("needs_rework", "completed"), (
            f"Swept genuine orphan must be in a terminal state; got {row['state']!r}"
        )

    def test_genuine_orphan_no_plan_json_is_swept(
        self, sweep, fresh_db, tmp_path, monkeypatch
    ):
        """AC3b: No plan.json at all + dead PID → sprint IS swept.

        Covers legacy sprints (from before plan.json existed) where no plan
        file is present on disk.  The existing orphan path must still fire.
        """
        label = "sprint-2031-no-plan"
        project = "zealchaiwut/test-project"
        fresh_db.record_sprint_start(label, project=project, started_at=_OLD_START)

        # No plan.json — point root at tmp_path so the check runs against an
        # existing directory with a sprints/ subdir but no plan.json for this label.
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(_startup, "_project_root_path", lambda repo: tmp_path)

        swept = sweep()

        assert label in swept, (
            f"A legacy sprint with no plan.json must still be swept. swept={swept!r}"
        )

    def test_genuine_orphan_with_incomplete_tickets_is_swept(
        self, sweep, fresh_db, tmp_path, monkeypatch
    ):
        """AC3c: Genuine orphan with partial ticket progress is swept.

        Even if some tickets merged, a plan.json still showing running means
        the sprint did not complete normally and must be settled as needs_rework.
        """
        label = "sprint-2031-partial"
        project = "zealchaiwut/test-project"
        fresh_db.record_sprint_start(label, project=project, started_at=_OLD_START)

        sprints_dir = tmp_path / ".commander" / "sprints"
        # plan.json in running state with partial ticket data
        _write_plan_json(
            sprints_dir, label, "running",
            tickets=[{"num": 1, "status": "done"}, {"num": 2, "status": "in-progress"}],
        )

        monkeypatch.setattr(_startup, "_project_root_path", lambda repo: tmp_path)

        swept = sweep()

        assert label in swept, (
            f"Partial-progress genuine orphan must be swept. swept={swept!r}"
        )
        row = fresh_db.get_sprint(label)
        assert row["state"] == "needs_rework", (
            f"Partial-progress orphan must land in needs_rework; got {row['state']!r}"
        )
        assert row.get("end_reason") == "orphaned (no live process)", (
            f"Genuine orphan end_reason must be 'orphaned (no live process)'; "
            f"got {row.get('end_reason')!r}"
        )
