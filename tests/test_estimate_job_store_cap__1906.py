"""Tests for issue #1906: estimate-job store cap must hold even when all jobs are running/queued.

AC1: _evict_if_needed enforces _MAX_JOBS even when all existing jobs are
     queued/running — it falls back to evicting the oldest non-terminal entries
     after exhausting done/failed candidates.

AC2: get_estimate_job calls _evict_if_needed after lazy-loading from disk so
     the in-memory dict cannot grow unbounded via reads after a restart.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routers.estimate_jobs as ej


@pytest.fixture(autouse=True)
def clear_job_store():
    ej._estimate_jobs.clear()
    yield
    ej._estimate_jobs.clear()


# ── AC1: hard ceiling covers queued/running when done/failed are absent ────────

class TestAC1HardCeilingForNonTerminalJobs:
    """AC1: _evict_if_needed must enforce _MAX_JOBS even when all slots are queued/running."""

    def test_evict_if_needed_removes_oldest_running_when_no_terminal_jobs(self):
        """AC1: With _MAX_JOBS running jobs and no done/failed, eviction still fires."""
        base_ts = "2024-01-01T00:00:00+00:00"
        for i in range(ej._MAX_JOBS):
            jid = str(uuid.uuid4())
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "running",
                "created_at": f"2024-01-01T00:{i:02d}:00+00:00",
            }

        assert len(ej._estimate_jobs) == ej._MAX_JOBS

        # Calling _evict_if_needed should reduce the count below _MAX_JOBS
        ej._evict_if_needed()

        assert len(ej._estimate_jobs) < ej._MAX_JOBS, (
            "_evict_if_needed did not evict any running jobs when at cap with no terminal jobs"
        )

    def test_evict_if_needed_removes_oldest_queued_when_no_terminal_jobs(self):
        """AC1: With _MAX_JOBS queued jobs and no done/failed, eviction still fires."""
        for i in range(ej._MAX_JOBS):
            jid = str(uuid.uuid4())
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "queued",
                "created_at": f"2024-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00",
            }

        assert len(ej._estimate_jobs) == ej._MAX_JOBS

        ej._evict_if_needed()

        assert len(ej._estimate_jobs) < ej._MAX_JOBS, (
            "_evict_if_needed did not evict any queued jobs when at cap with no terminal jobs"
        )

    def test_create_estimate_job_respects_cap_with_all_running_jobs(self, tmp_path, monkeypatch):
        """AC1: create_estimate_job never grows the store beyond _MAX_JOBS."""
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        for i in range(ej._MAX_JOBS):
            jid = str(uuid.uuid4())
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "running",
                "created_at": f"2024-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
            }

        # Creating a new job should trigger eviction before inserting
        ej.create_estimate_job(42, "test/repo")

        assert len(ej._estimate_jobs) <= ej._MAX_JOBS, (
            f"store grew to {len(ej._estimate_jobs)} > _MAX_JOBS={ej._MAX_JOBS}"
        )

    def test_done_failed_jobs_evicted_first_before_running(self, tmp_path, monkeypatch):
        """AC1: terminal (done/failed) jobs are evicted before non-terminal ones."""
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        done_ids = set()
        running_ids = set()

        # Fill to _MAX_JOBS with a mix: half done, half running
        for i in range(ej._MAX_JOBS // 2):
            jid = str(uuid.uuid4())
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "done",
                "created_at": f"2024-01-01T00:00:{i:02d}+00:00",
            }
            done_ids.add(jid)

        for i in range(ej._MAX_JOBS - len(done_ids)):
            jid = str(uuid.uuid4())
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "running",
                "created_at": f"2024-01-01T00:01:{i:02d}+00:00",
            }
            running_ids.add(jid)

        ej._evict_if_needed()

        # The remaining running jobs should still all be present
        remaining_running = {jid for jid in running_ids if jid in ej._estimate_jobs}
        assert remaining_running == running_ids, (
            "running jobs were evicted before done jobs — terminal jobs should be preferred"
        )
        # And at least some done jobs were removed
        remaining_done = {jid for jid in done_ids if jid in ej._estimate_jobs}
        assert len(remaining_done) < len(done_ids), (
            "no done jobs were evicted; terminal jobs should have been evicted first"
        )


# ── AC2: lazy-load path enforces the cap ──────────────────────────────────────

class TestAC2LazyLoadTriggersEviction:
    """AC2: get_estimate_job calls _evict_if_needed after adding a lazy-loaded job."""

    def test_lazy_load_does_not_grow_store_beyond_max(self, tmp_path, monkeypatch):
        """AC2: lazy-loading a persisted job triggers eviction when store is full."""
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        # Fill store to _MAX_JOBS with done jobs
        for i in range(ej._MAX_JOBS):
            jid = str(uuid.uuid4())
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "done",
                "created_at": f"2024-01-01T00:00:{i:02d}+00:00",
            }

        # Write a job to disk that is NOT in memory
        disk_job_id = "disk-only-job-ac2"
        disk_job = {
            "job_id": disk_job_id,
            "status": "done",
            "result": {"size": "S", "minutes": 5},
            "created_at": "2024-01-01T01:00:00+00:00",
        }
        (tmp_path / f"{disk_job_id}.json").write_text(json.dumps(disk_job))

        # Lazy-load the disk job — this should trigger eviction
        loaded = ej.get_estimate_job(disk_job_id)

        assert loaded is not None, "lazy-load returned None unexpectedly"
        assert len(ej._estimate_jobs) <= ej._MAX_JOBS, (
            f"store grew to {len(ej._estimate_jobs)} > _MAX_JOBS={ej._MAX_JOBS} after lazy-load"
        )

    def test_lazy_load_evicts_when_store_full_of_running_jobs(self, tmp_path, monkeypatch):
        """AC2: lazy-load eviction fires even when the store is full of running/queued jobs."""
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        # Fill store to _MAX_JOBS with running jobs (no terminal jobs to prefer)
        for i in range(ej._MAX_JOBS):
            jid = str(uuid.uuid4())
            ej._estimate_jobs[jid] = {
                "job_id": jid,
                "status": "running",
                "created_at": f"2024-01-01T00:{i // 60:02d}:{i % 60:02d}+00:00",
            }

        # Write a job to disk not in memory
        disk_job_id = "disk-only-running-ac2"
        disk_job = {
            "job_id": disk_job_id,
            "status": "done",
            "result": {"size": "M", "minutes": 15},
            "created_at": "2024-01-01T02:00:00+00:00",
        }
        (tmp_path / f"{disk_job_id}.json").write_text(json.dumps(disk_job))

        loaded = ej.get_estimate_job(disk_job_id)

        assert loaded is not None, "lazy-load returned None unexpectedly"
        assert len(ej._estimate_jobs) <= ej._MAX_JOBS, (
            f"store grew to {len(ej._estimate_jobs)} > _MAX_JOBS={ej._MAX_JOBS} after lazy-load with running jobs"
        )
