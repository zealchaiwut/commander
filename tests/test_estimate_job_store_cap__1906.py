"""Tests for issue #1906: estimate-job store cap enforcement.

The estimate-job store (_estimate_jobs dict) has a cap (_MAX_JOBS) to prevent
unbounded growth. Two issues are fixed:

1. _evict_if_needed now evicts oldest queued/running jobs after exhausting
   done/failed candidates — preventing unbounded growth when hung subprocesses
   park all jobs in non-terminal states.

2. get_estimate_job calls _evict_if_needed after lazy-loading from disk, so
   reads after server restart cannot bypass the cap.
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
    """Clear the job store before and after each test."""
    ej._estimate_jobs.clear()
    yield
    ej._estimate_jobs.clear()


# ── AC1: eviction fires even when all jobs are queued/running ────────────────

def test_evict_if_needed_removes_oldest_running_when_no_terminal_jobs():
    """With _MAX_JOBS running jobs and no done/failed, eviction sheds oldest running."""
    for i in range(ej._MAX_JOBS):
        jid = str(uuid.uuid4())
        ej._estimate_jobs[jid] = {
            "job_id": jid,
            "status": "running",
            "created_at": f"2024-01-01T00:{i:02d}:00+00:00",
        }

    assert len(ej._estimate_jobs) == ej._MAX_JOBS
    ej._evict_if_needed()
    assert len(ej._estimate_jobs) < ej._MAX_JOBS, "eviction must remove running jobs when no terminal jobs exist"


def test_evict_if_needed_removes_oldest_queued_when_no_terminal_jobs():
    """With _MAX_JOBS queued jobs and no done/failed, eviction sheds oldest queued."""
    for i in range(ej._MAX_JOBS):
        jid = str(uuid.uuid4())
        ej._estimate_jobs[jid] = {
            "job_id": jid,
            "status": "queued",
            "created_at": f"2024-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00",
        }

    assert len(ej._estimate_jobs) == ej._MAX_JOBS
    ej._evict_if_needed()
    assert len(ej._estimate_jobs) < ej._MAX_JOBS, "eviction must remove queued jobs when no terminal jobs exist"


def test_evict_if_needed_prefers_terminal_over_running(tmp_path, monkeypatch):
    """Terminal (done/failed) jobs are evicted before running/queued jobs."""
    monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

    done_ids = set()
    running_ids = set()

    # Fill to _MAX_JOBS: half done, half running
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

    # All running jobs should survive; some done jobs removed
    remaining_running = {jid for jid in running_ids if jid in ej._estimate_jobs}
    assert remaining_running == running_ids, "running jobs must not be evicted before terminal jobs are exhausted"


def test_create_estimate_job_respects_cap_with_all_running_jobs(tmp_path, monkeypatch):
    """create_estimate_job enforces _MAX_JOBS cap even when filled with running jobs."""
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

    assert len(ej._estimate_jobs) <= ej._MAX_JOBS, f"store grew to {len(ej._estimate_jobs)} > _MAX_JOBS"


# ── AC2: lazy-load path enforces the cap ──────────────────────────────────────

def test_lazy_load_does_not_grow_store_beyond_max(tmp_path, monkeypatch):
    """get_estimate_job lazy-loading triggers eviction when store is full."""
    monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

    # Fill store to _MAX_JOBS with done jobs
    for i in range(ej._MAX_JOBS):
        jid = str(uuid.uuid4())
        ej._estimate_jobs[jid] = {
            "job_id": jid,
            "status": "done",
            "created_at": f"2024-01-01T00:00:{i:02d}+00:00",
        }

    # Write a job to disk not in memory
    disk_job_id = "disk-only-job-ac2"
    disk_job = {
        "job_id": disk_job_id,
        "status": "done",
        "result": {"size": "S", "minutes": 5},
        "created_at": "2024-01-01T01:00:00+00:00",
    }
    (tmp_path / f"{disk_job_id}.json").write_text(json.dumps(disk_job))

    # Lazy-load should trigger eviction
    loaded = ej.get_estimate_job(disk_job_id)

    assert loaded is not None, "lazy-load returned None unexpectedly"
    assert len(ej._estimate_jobs) <= ej._MAX_JOBS, f"store grew to {len(ej._estimate_jobs)} > _MAX_JOBS after lazy-load"


def test_lazy_load_evicts_when_full_of_running_jobs(tmp_path, monkeypatch):
    """get_estimate_job lazy-load evicts even when store is full of running jobs."""
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
    assert len(ej._estimate_jobs) <= ej._MAX_JOBS, f"store grew to {len(ej._estimate_jobs)} > _MAX_JOBS after lazy-load with running jobs"
