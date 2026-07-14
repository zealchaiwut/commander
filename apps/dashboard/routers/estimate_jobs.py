"""Async estimate job router (issue #1863).

Routes owned by this module:
  GET  /api/estimate-jobs/{job_id}
  POST /api/sprints/{sprint_label}/estimate?project=

The async single-issue path is wired into routers/system_misc.py —
POST /api/issues/{issue_id}/estimate?async=1 — and uses the same job store
exposed here (create_estimate_job / run_issue_estimate).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import github_client  # noqa: E402

router = APIRouter(tags=["estimate_jobs"])

# ── Job store (in-memory + disk) ──────────────────────────────────────────────

_estimate_jobs: dict[str, dict] = {}

# Cap on the in-memory job store — oldest done/failed jobs are evicted when
# this limit is hit (issue #1897). Running/queued jobs are never evicted.
_MAX_JOBS = 200

# Job files older than this many hours are pruned from disk on each new job
# creation (issue #1897).
_JOB_FILE_MAX_AGE_HOURS = 24

# Max concurrent estimator subprocesses — protects the shared claude CLI.
_ESTIMATE_CONCURRENCY = 1
_estimate_semaphore = threading.Semaphore(_ESTIMATE_CONCURRENCY)

_PROJECTS_BASE = Path.home() / "dev"
_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")


def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _jobs_dir() -> Path:
    jobs_dir = _REPO_ROOT / ".commander" / "estimate-jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return jobs_dir


def _persist_job(job: dict) -> None:
    try:
        path = _jobs_dir() / f"{job['job_id']}.json"
        path.write_text(json.dumps(job, default=str), encoding="utf-8")
    except Exception:
        pass


def _prune_old_job_files() -> None:
    """Remove persisted job files older than _JOB_FILE_MAX_AGE_HOURS (issue #1897)."""
    try:
        cutoff = time.time() - _JOB_FILE_MAX_AGE_HOURS * 3600
        for f in _jobs_dir().iterdir():
            if f.suffix == ".json":
                try:
                    if os.path.getmtime(f) < cutoff:
                        f.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass


def _evict_if_needed() -> None:
    """Evict oldest done/failed jobs when _estimate_jobs reaches _MAX_JOBS (issue #1897).

    Running and queued jobs are never evicted — only completed (done/failed) ones
    are candidates. After evicting from memory, prune old disk files by age.
    """
    if len(_estimate_jobs) < _MAX_JOBS:
        return
    evictable = sorted(
        (j.get("created_at", ""), jid)
        for jid, j in _estimate_jobs.items()
        if j.get("status") in ("done", "failed")
    )
    n_to_evict = max(1, len(_estimate_jobs) - _MAX_JOBS + 1)
    for _, jid in evictable[:n_to_evict]:
        _estimate_jobs.pop(jid, None)
    _prune_old_job_files()


def get_estimate_job(job_id: str) -> dict | None:
    """Return a job from memory or lazy-load from disk after a server restart."""
    job = _estimate_jobs.get(job_id)
    if job is not None:
        return job
    try:
        path = _jobs_dir() / f"{job_id}.json"
        if path.exists():
            job = json.loads(path.read_text(encoding="utf-8"))
            _estimate_jobs[job_id] = job
            return job
    except Exception:
        pass
    return None


def create_estimate_job(issue_id: int, repo: str) -> str:
    """Create and persist a new single-issue estimate job; return its job_id."""
    _evict_if_needed()
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "type": "issue_estimate",
        "issue_id": issue_id,
        "repo": repo,
        "status": "queued",
        "result": None,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _estimate_jobs[job_id] = job
    _persist_job(job)
    return job_id


# ── Background task helpers ───────────────────────────────────────────────────

def _server():
    import server  # noqa: PLC0415
    return server


def run_issue_estimate(job_id: str, issue_id: int, repo: str) -> None:
    """Background task: run the estimator for one issue, update job state."""
    import subprocess  # noqa: PLC0415

    job = _estimate_jobs.get(job_id)
    if job is None:
        return

    srv = _server()

    with _estimate_semaphore:
        job["status"] = "running"
        _persist_job(job)

        try:
            issue_data = srv._ei_fetch_issue(issue_id, repo)
        except subprocess.CalledProcessError as e:
            job["status"] = "failed"
            job["error"] = f"Could not fetch issue #{issue_id}: {e}"
            _persist_job(job)
            return

        estimate, error_type = srv._ei_run_estimator(issue_id, issue_data)
        if estimate is None:
            job["status"] = "failed"
            job["error"] = f"Estimation failed: {error_type}"
            _persist_job(job)
            return

        size = estimate.get("size")
        if not size:
            job["status"] = "failed"
            job["error"] = "Estimator returned no size"
            _persist_job(job)
            return

        minutes: int = estimate.get("minutes") or srv._minutes_from_letter(size)
        if not estimate.get("minutes"):
            estimate["minutes"] = minutes

        try:
            srv._ei_apply_label(issue_id, repo, size)
        except Exception:
            pass

        try:
            srv._ei_apply_estimated_status(issue_id, repo)
        except Exception:
            pass

        project_root = _project_root_path(repo)
        estimates_dir = _commander_dir(project_root) / "estimates"
        estimates_dir.mkdir(parents=True, exist_ok=True)
        (estimates_dir / f"issue-{issue_id}.json").write_text(
            json.dumps(estimate, indent=2), encoding="utf-8"
        )

        job["status"] = "done"
        job["result"] = {"size": size, "minutes": minutes}
        _persist_job(job)


def run_sprint_estimate(job_id: str, sprint_label: str, repo: str) -> None:
    """Background task: run batch estimation for all uncached sprint issues."""
    import subprocess  # noqa: PLC0415
    from sizing import minutes_from_letter as _minutes_from_letter  # noqa: PLC0415

    job = _estimate_jobs.get(job_id)
    if job is None:
        return

    job["status"] = "running"
    _persist_job(job)

    srv = _server()

    try:
        all_issues = github_client.list_open_issues_with_body(repo_name=repo, limit=200)
        sprint_re = _SPRINT_LABEL_RE

        def _primary_label(iss: dict) -> str | None:
            for lbl in iss.get("labels", []):
                if sprint_re.match(lbl["name"]):
                    return lbl["name"]
            return None

        issues = [iss for iss in all_issues if _primary_label(iss) == sprint_label]
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"Could not fetch sprint issues: {e}"
        _persist_job(job)
        return

    project_root = _project_root_path(repo)
    estimates_dir = _commander_dir(project_root) / "estimates"
    estimates_dir.mkdir(parents=True, exist_ok=True)

    progress = [{"issue": iss["number"], "status": "queued", "size": None} for iss in issues]
    job["progress"] = progress
    _persist_job(job)

    for idx, iss in enumerate(issues):
        issue_num = iss["number"]
        job["progress"][idx]["status"] = "running"
        _persist_job(job)

        # Check cache — cached issues skip the estimator entirely.
        cache_path = estimates_dir / f"issue-{issue_num}.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached_size = cached.get("size")
                if cached_size:
                    job["progress"][idx]["status"] = "done"
                    job["progress"][idx]["size"] = cached_size
                    _persist_job(job)
                    continue
            except Exception:
                pass

        # Uncached — run the estimator under the concurrency bound.
        with _estimate_semaphore:
            try:
                issue_data = srv._ei_fetch_issue(issue_num, repo)
            except (subprocess.CalledProcessError, Exception):
                job["progress"][idx]["status"] = "failed"
                _persist_job(job)
                continue

            estimate, error_type = srv._ei_run_estimator(issue_num, issue_data)
            if estimate is None:
                job["progress"][idx]["status"] = "failed"
                _persist_job(job)
                continue

            size = estimate.get("size")
            if not size:
                job["progress"][idx]["status"] = "failed"
                _persist_job(job)
                continue

            minutes_val: int = estimate.get("minutes") or _minutes_from_letter(size)
            if not estimate.get("minutes"):
                estimate["minutes"] = minutes_val

            try:
                srv._ei_apply_label(issue_num, repo, size)
            except Exception:
                pass

            try:
                srv._ei_apply_estimated_status(issue_num, repo)
            except Exception:
                pass

            (estimates_dir / f"issue-{issue_num}.json").write_text(
                json.dumps(estimate, indent=2), encoding="utf-8"
            )

            job["progress"][idx]["status"] = "done"
            job["progress"][idx]["size"] = size
            _persist_job(job)

    done_count = sum(1 for p in job["progress"] if p["status"] == "done")
    failed_count = sum(1 for p in job["progress"] if p["status"] == "failed")
    job["status"] = "done" if (done_count > 0 or failed_count == 0) else "failed"
    _persist_job(job)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/estimate-jobs/{job_id}")
def get_estimate_job_endpoint(job_id: str):
    """Return the current status of an async estimate job."""
    job = get_estimate_job(job_id)
    if job is None:
        raise HTTPException(404, detail="Job not found")
    return job


@router.post("/api/sprints/{sprint_label}/estimate", status_code=202)
def post_sprint_estimate(
    sprint_label: str,
    project: str,
    background_tasks: BackgroundTasks,
):
    """Start an async batch estimate job for all issues in a sprint."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    _evict_if_needed()
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "type": "sprint_estimate",
        "sprint_label": sprint_label,
        "repo": project,
        "status": "queued",
        "progress": [],
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _estimate_jobs[job_id] = job
    _persist_job(job)
    background_tasks.add_task(run_sprint_estimate, job_id, sprint_label, project)
    return {"job_id": job_id}
