"""Tests for issue #1897: 3 confirmed backend/reliability bugs.

AC1: Running-pane metrics no longer leak across projects.
  - _fetch_sprint_agent_run_rows(sprint_label, project) scopes by project
  - Falls back to blank/NULL-project rows when no scoped rows exist (legacy compat)
  - running_metrics passes project through and returns only that project's metrics

AC2: board_invalidated SSE broadcast fires from sync (threadpool) route handlers.
  - set_main_loop captures the event loop
  - invalidate_board uses run_coroutine_threadsafe when called outside async context
  - When no loop is set and no running loop, the old silent-skip is preserved

AC3: Estimate-job store is bounded with eviction + file pruning.
  - _estimate_jobs dict is capped at _MAX_JOBS; oldest done/failed are evicted
  - Running/queued jobs are NOT evicted even when at cap
  - Job files older than _JOB_FILE_MAX_AGE_HOURS are pruned
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS_DIR = _DASHBOARD_DIR / "routers"

for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

_PROJECT_A = "owner/perf-coach"
_PROJECT_B = "owner/commander"
_LABEL = "sprint-104"


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """Each test gets its own SQLite DB to avoid cross-test contamination."""
    global db
    db = importlib.import_module("db")
    db_file = tmp_path / "test_1897.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield tmp_path
    db.DB_PATH = original


def _seed_run(
    project: str | None,
    sprint_label: str = _LABEL,
    issue: int = 1,
    agent: str = "coder",
    total_tokens: int = 1000,
    duration_seconds: int = 60,
    attempt_kind: str = "initial",
) -> int:
    """Insert a completed agent_runs row."""
    with db.get_conn() as conn:
        db._create_agent_runs_table(conn)
        cur = conn.execute(
            "INSERT INTO agent_runs "
            "(issue_number, sprint_label, agent, started_at, finished_at, "
            " duration_seconds, outcome, total_tokens, attempt_kind, project) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                issue, sprint_label, agent,
                "2026-07-13T10:00:00", "2026-07-13T10:01:00",
                duration_seconds, "passed", total_tokens, attempt_kind, project,
            ),
        )
        conn.commit()
        return cur.lastrowid


# ── AC1: Cross-project metrics leak ──────────────────────────────────────────

class TestAC1MetricsLeak:
    """_fetch_sprint_agent_run_rows must scope by project to prevent cross-project bleed."""

    def test_scoped_query_excludes_other_project_rows(self):
        """AC1: rows for project_B are not returned when querying for project_A."""
        import live_metrics as lm

        _seed_run(_PROJECT_B, total_tokens=9999)
        rows = lm._fetch_sprint_agent_run_rows(_LABEL, project=_PROJECT_A)
        assert rows == [], (
            f"project-scoped read for {_PROJECT_A} must not return "
            f"{_PROJECT_B}'s rows; got {rows}"
        )

    def test_scoped_query_returns_own_rows(self):
        """AC1: rows belonging to project_A are returned when querying for project_A."""
        import live_metrics as lm

        _seed_run(_PROJECT_A, issue=10, total_tokens=500)
        _seed_run(_PROJECT_B, issue=20, total_tokens=9999)
        rows = lm._fetch_sprint_agent_run_rows(_LABEL, project=_PROJECT_A)
        assert len(rows) == 1
        assert rows[0]["issue_number"] == 10
        assert rows[0]["total_tokens"] == 500

    def test_fallback_to_legacy_blank_project_rows(self):
        """AC1: when project_A has no rows, falls back to blank/NULL-project rows."""
        import live_metrics as lm

        # Seed a legacy row with no project (NULL)
        with db.get_conn() as conn:
            db._create_agent_runs_table(conn)
            conn.execute(
                "INSERT INTO agent_runs "
                "(issue_number, sprint_label, agent, started_at, finished_at, "
                " duration_seconds, outcome, total_tokens, attempt_kind, project) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (99, _LABEL, "coder",
                 "2026-07-13T10:00:00", "2026-07-13T10:01:00",
                 60, "passed", 777, "initial", None),
            )
            conn.commit()

        rows = lm._fetch_sprint_agent_run_rows(_LABEL, project=_PROJECT_A)
        assert len(rows) == 1
        assert rows[0]["issue_number"] == 99, "legacy blank-project rows must still be returned"

    def test_unscoped_call_returns_all_rows(self):
        """AC1: without project, the existing unscoped behavior is unchanged."""
        import live_metrics as lm

        _seed_run(_PROJECT_A, issue=1, total_tokens=100)
        _seed_run(_PROJECT_B, issue=2, total_tokens=200)
        rows = lm._fetch_sprint_agent_run_rows(_LABEL)
        assert len(rows) == 2

    def test_running_metrics_scoped_to_project(self):
        """AC1: running_metrics only counts project_A's tokens when project=project_A."""
        import live_metrics as lm

        _seed_run(_PROJECT_A, total_tokens=300, duration_seconds=30)
        _seed_run(_PROJECT_B, total_tokens=50000, duration_seconds=9999)

        metrics = lm.running_metrics(_LABEL, project=_PROJECT_A)
        assert metrics, "running_metrics must return data when project_A has rows"
        assert metrics["token_total"] == 300, (
            f"token_total should be project_A's 300 tokens, not {metrics['token_total']}"
        )
        assert metrics["agent_time_split"]["coder"] == 30, (
            "agent_time_split must only count project_A's coder time"
        )

    def test_running_metrics_empty_when_no_own_rows(self):
        """AC1: running_metrics returns {} for project_A when only project_B has rows."""
        import live_metrics as lm

        _seed_run(_PROJECT_B, total_tokens=9999)
        metrics = lm.running_metrics(_LABEL, project=_PROJECT_A)
        assert metrics == {}, (
            "running_metrics must return {} when project_A has no rows — "
            "returning project_B's data would be a metrics leak"
        )


# ── AC2: SSE broadcast fires from threadpool ──────────────────────────────────

class TestAC2SseBroadcastFromThreadpool:
    """invalidate_board must schedule the SSE broadcast via run_coroutine_threadsafe
    when called from a threadpool (sync route handler) context where there is no
    running event loop."""

    @pytest.fixture(autouse=True)
    def _reset_main_loop(self):
        """Ensure _main_loop is clean before and after each test.

        Loads board_cache via importlib to avoid adding _ROUTERS_DIR to sys.path
        (which would shadow services/sprint_manager/calibration.py with the
        routers/calibration.py when other tests later do `import routers.*`).
        """
        import importlib.util
        _bc_path = _ROUTERS_DIR / "board_cache.py"
        # Check if already cached under the routers package name
        if "routers.board_cache" in sys.modules:
            bc = sys.modules["routers.board_cache"]
        else:
            spec = importlib.util.spec_from_file_location("board_cache_1897", _bc_path)
            bc = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bc)
        original = bc._main_loop
        bc._main_loop = None
        yield bc
        bc._main_loop = original

    def test_set_main_loop_stores_loop(self, _reset_main_loop):
        """AC2: set_main_loop persists the loop in the module-level _main_loop."""
        bc = _reset_main_loop
        loop = asyncio.new_event_loop()
        try:
            bc.set_main_loop(loop)
            assert bc._main_loop is loop
        finally:
            loop.close()

    def test_invalidate_uses_run_coroutine_threadsafe_when_called_from_thread(
        self, _reset_main_loop, monkeypatch
    ):
        """AC2: when called from a non-async thread with _main_loop set,
        run_coroutine_threadsafe is called — the broadcast is NOT silently skipped."""
        bc = _reset_main_loop

        loop = asyncio.new_event_loop()
        bc.set_main_loop(loop)

        scheduled = []

        def _fake_rcts(coro, lp):
            scheduled.append(lp)
            coro.close()

        monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _fake_rcts)

        # Inject a fake logs_service so the import inside invalidate_board resolves
        async def _fake_broadcast(data):
            pass

        fake_ls = types.ModuleType("logs_service")
        fake_ls.broadcast = _fake_broadcast
        sys.modules.setdefault("logs_service", fake_ls)
        orig_ls = sys.modules.get("logs_service")
        sys.modules["logs_service"] = fake_ls

        try:
            errors: list[Exception] = []

            def _run_from_thread():
                try:
                    bc.invalidate_board("owner/test-project")
                except Exception as e:
                    errors.append(e)

            t = threading.Thread(target=_run_from_thread)
            t.start()
            t.join(timeout=5)
        finally:
            loop.close()
            if orig_ls is None:
                sys.modules.pop("logs_service", None)
            else:
                sys.modules["logs_service"] = orig_ls

        assert not errors, f"invalidate_board raised: {errors}"
        assert len(scheduled) == 1, (
            "run_coroutine_threadsafe was not called — broadcast was silently skipped "
            "from a threadpool handler, which is the bug this AC fixes"
        )
        assert scheduled[0] is loop

    def test_invalidate_skips_gracefully_when_no_main_loop_and_no_running_loop(
        self, _reset_main_loop
    ):
        """AC2: when _main_loop is not set, the old behavior (skip silently) is preserved."""
        bc = _reset_main_loop
        assert bc._main_loop is None

        errors: list[Exception] = []

        def _run_from_thread():
            try:
                bc.invalidate_board("owner/no-loop-project")
            except Exception as e:
                errors.append(e)

        t = threading.Thread(target=_run_from_thread)
        t.start()
        t.join(timeout=5)

        assert not errors, f"invalidate_board raised when _main_loop unset: {errors}"

    def test_set_main_loop_function_exists(self, _reset_main_loop):
        """AC2: board_cache exposes a set_main_loop() function."""
        bc = _reset_main_loop
        assert callable(getattr(bc, "set_main_loop", None)), (
            "board_cache must expose set_main_loop() for server lifespan wiring"
        )

    def test_main_loop_attribute_exists(self, _reset_main_loop):
        """AC2: board_cache has a _main_loop attribute."""
        bc = _reset_main_loop
        assert hasattr(bc, "_main_loop")


# ── AC3: Estimate-job store bounded ──────────────────────────────────────────

class TestAC3EstimateJobStoreBounded:
    """_estimate_jobs dict must be bounded; old files must be pruned."""

    @pytest.fixture(autouse=True)
    def _reset_jobs(self):
        """Load estimate_jobs directly from file to bypass routers/__init__.py,
        which imports sprint_preflight → estimate_issue → calibration and hits a
        module-name collision with routers/calibration.py vs services/.../calibration.py.
        """
        import importlib.util
        _ej_path = _ROUTERS_DIR / "estimate_jobs.py"
        if "routers.estimate_jobs" in sys.modules:
            ej = sys.modules["routers.estimate_jobs"]
        else:
            spec = importlib.util.spec_from_file_location("estimate_jobs_1897", _ej_path)
            ej = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ej)
        ej._estimate_jobs.clear()
        yield ej
        ej._estimate_jobs.clear()

    def test_max_jobs_constant_exists(self, _reset_jobs):
        """AC3: _MAX_JOBS is defined and in a reasonable range."""
        ej = _reset_jobs
        assert hasattr(ej, "_MAX_JOBS")
        assert isinstance(ej._MAX_JOBS, int)
        assert 50 <= ej._MAX_JOBS <= 10_000, (
            f"_MAX_JOBS={ej._MAX_JOBS} — should be a reasonable cap (50–10000)"
        )

    def test_job_file_max_age_constant_exists(self, _reset_jobs):
        """AC3: _JOB_FILE_MAX_AGE_HOURS is defined."""
        ej = _reset_jobs
        assert hasattr(ej, "_JOB_FILE_MAX_AGE_HOURS")
        assert isinstance(ej._JOB_FILE_MAX_AGE_HOURS, (int, float))
        assert ej._JOB_FILE_MAX_AGE_HOURS > 0

    def test_done_jobs_evicted_when_at_cap(self, _reset_jobs, tmp_path, monkeypatch):
        """AC3: when dict is at _MAX_JOBS, creating a new job evicts an old done/failed job."""
        ej = _reset_jobs
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        # Fill the dict to exactly _MAX_JOBS with done jobs
        for i in range(ej._MAX_JOBS):
            jid = f"done-{i:05d}"
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "done",
                "created_at": f"2026-01-01T00:{i:02d}:00+00:00",
            }
        assert len(ej._estimate_jobs) == ej._MAX_JOBS

        new_jid = ej.create_estimate_job(999, "test/repo")

        assert len(ej._estimate_jobs) <= ej._MAX_JOBS, (
            f"dict grew to {len(ej._estimate_jobs)} beyond cap {ej._MAX_JOBS}"
        )
        assert new_jid in ej._estimate_jobs, "newly created job must be in the store"

    def test_oldest_done_job_evicted_first(self, _reset_jobs, tmp_path, monkeypatch):
        """AC3: the oldest completed job is evicted, not a random or recent one."""
        ej = _reset_jobs
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        oldest_jid = "done-oldest"
        newest_jid = "done-newest"
        ej._estimate_jobs[oldest_jid] = {
            "job_id": oldest_jid,
            "status": "done",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        # Fill remaining slots with done jobs
        for i in range(ej._MAX_JOBS - 1):
            jid = f"done-mid-{i:05d}"
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "done",
                "created_at": f"2026-06-01T{i:02d}:00:00+00:00",
            }
        # Mark the last slot as 'newest' (also done)
        ej._estimate_jobs[newest_jid] = {
            "job_id": newest_jid,
            "status": "done",
            "created_at": "2026-12-31T23:59:59+00:00",
        }
        assert len(ej._estimate_jobs) > ej._MAX_JOBS

        ej._evict_if_needed()

        assert oldest_jid not in ej._estimate_jobs, (
            "oldest done job must be evicted first"
        )
        assert newest_jid in ej._estimate_jobs, (
            "newest done job must be kept"
        )

    def test_running_jobs_not_evicted_at_cap(self, _reset_jobs, tmp_path, monkeypatch):
        """AC3: running/queued jobs are NOT evicted even when the dict is at cap."""
        ej = _reset_jobs
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        running_jid = "running-important"
        ej._estimate_jobs[running_jid] = {
            "job_id": running_jid,
            "status": "running",
            "created_at": "2025-01-01T00:00:00+00:00",  # oldest timestamp
        }
        # Fill rest with done jobs to hit cap
        for i in range(ej._MAX_JOBS):
            jid = f"done-{i:05d}"
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "done",
                "created_at": f"2026-06-01T{i:02d}:00:00+00:00",
            }

        ej._evict_if_needed()

        assert running_jid in ej._estimate_jobs, (
            "a running job must never be evicted by the LRU cap"
        )

    def test_queued_jobs_not_evicted_at_cap(self, _reset_jobs, tmp_path, monkeypatch):
        """AC3: queued jobs are NOT evicted at cap."""
        ej = _reset_jobs
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        queued_jid = "queued-waiting"
        ej._estimate_jobs[queued_jid] = {
            "job_id": queued_jid,
            "status": "queued",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        for i in range(ej._MAX_JOBS):
            jid = f"done-{i:05d}"
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "done",
                "created_at": f"2026-06-01T{i:02d}:00:00+00:00",
            }

        ej._evict_if_needed()

        assert queued_jid in ej._estimate_jobs, "queued jobs must not be evicted"

    def test_evict_if_needed_function_exists(self, _reset_jobs):
        """AC3: _evict_if_needed() is a callable on the module."""
        ej = _reset_jobs
        assert callable(getattr(ej, "_evict_if_needed", None)), (
            "_evict_if_needed must be exposed on estimate_jobs for testability"
        )

    def test_prune_old_job_files_function_exists(self, _reset_jobs):
        """AC3: _prune_old_job_files() is a callable on the module."""
        ej = _reset_jobs
        assert callable(getattr(ej, "_prune_old_job_files", None)), (
            "_prune_old_job_files must be exposed on estimate_jobs"
        )

    def test_old_job_files_pruned(self, _reset_jobs, tmp_path, monkeypatch):
        """AC3: job files older than _JOB_FILE_MAX_AGE_HOURS are removed."""
        ej = _reset_jobs
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        old_file = tmp_path / "old-job-12345.json"
        old_file.write_text('{"status": "done", "job_id": "old"}', encoding="utf-8")
        old_mtime = time.time() - (ej._JOB_FILE_MAX_AGE_HOURS + 1) * 3600
        os.utime(old_file, (old_mtime, old_mtime))

        recent_file = tmp_path / "recent-job-67890.json"
        recent_file.write_text('{"status": "done", "job_id": "recent"}', encoding="utf-8")

        ej._prune_old_job_files()

        assert not old_file.exists(), (
            f"{old_file.name} should have been pruned (older than {ej._JOB_FILE_MAX_AGE_HOURS}h)"
        )
        assert recent_file.exists(), "recent job file must NOT be pruned"

    def test_new_job_creation_triggers_eviction(self, _reset_jobs, tmp_path, monkeypatch):
        """AC3: create_estimate_job calls _evict_if_needed before storing."""
        ej = _reset_jobs
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        evict_calls = []
        original_evict = ej._evict_if_needed

        def _spy_evict():
            evict_calls.append(True)
            original_evict()

        monkeypatch.setattr(ej, "_evict_if_needed", _spy_evict)

        ej.create_estimate_job(42, "test/repo")

        assert len(evict_calls) >= 1, (
            "create_estimate_job must call _evict_if_needed to prevent unbounded growth"
        )
