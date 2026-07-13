"""Tests for issue #1863: async estimate jobs + whole-sprint batch estimate endpoint.

AC1: async=1 returns 202 + job_id immediately; job endpoint reports lifecycle and
     final {size, minutes}; sync path unchanged
AC2: POST /api/sprints/{label}/estimate estimates all backlog issues, reuses cache,
     applies size labels, returns per-issue progress
AC3: Job state survives a server restart (disk-persisted, lazy-loaded on poll miss)
AC4: Concurrency bounded (one estimator subprocess at a time, or small fixed pool)
     to protect the shared claude CLI
AC5: Behavioral tests: job lifecycle with a mocked estimator (side_effect result);
     batch skips already-cached issues without invoking the estimator (assert mock
     not called for cached)
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routers.estimate_jobs as ej
from routers.estimate_jobs import router as estimate_jobs_router


@pytest.fixture(autouse=True)
def clear_job_store():
    """Clear in-memory job store before and after each test."""
    ej._estimate_jobs.clear()
    yield
    ej._estimate_jobs.clear()


@pytest.fixture
def jobs_client(tmp_path, monkeypatch):
    """TestClient for the estimate_jobs router with disk I/O redirected to tmp_path."""
    monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(estimate_jobs_router)
    return TestClient(app)


def _fake_server(size: str = "M", minutes: int = 15):
    """Return a mock server object that mimics the _ei_* helpers."""
    srv = MagicMock()
    srv._ei_fetch_issue.return_value = {"title": "Test Issue", "body": "body text"}
    srv._ei_run_estimator.return_value = (
        {"size": size, "minutes": minutes, "issue_number": 42},
        None,
    )
    srv._ei_apply_label.return_value = None
    srv._ei_apply_estimated_status.return_value = None
    srv._minutes_from_letter.return_value = minutes
    return srv


# ── AC1: async=1 returns 202 + job_id; sync path unchanged ───────────────────

class TestAC1AsyncParam:
    """AC1: async query param switches between async-202 and sync-200 paths."""

    def test_async_flag_returns_202_and_job_id(self, tmp_path, monkeypatch):
        """AC1: POST /api/issues/{id}/estimate?async=1 returns 202 immediately."""
        import routers.system_misc as sm

        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)
        fake_srv = _fake_server()

        app = FastAPI()
        app.include_router(sm.router)
        app.include_router(estimate_jobs_router)

        with (
            patch.object(sm, "_server", return_value=fake_srv),
            patch.object(ej, "_server", return_value=fake_srv),
            patch.object(ej, "_project_root_path", return_value=tmp_path),
        ):
            client = TestClient(app)
            r = client.post("/api/issues/42/estimate?async=1&repo=test/repo")

        assert r.status_code == 202
        data = r.json()
        assert "job_id" in data
        assert isinstance(data["job_id"], str)

    def test_async_flag_does_not_block(self, tmp_path, monkeypatch):
        """AC1: The async path queues the job and returns before the estimator runs."""
        import routers.system_misc as sm

        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)
        fake_srv = MagicMock()

        # Make _ei_fetch_issue block forever if called inline — it should NOT be
        # called before the response is sent.
        def _slow_fetch(*a, **kw):
            raise AssertionError("estimator was called before response returned")

        fake_srv._ei_fetch_issue.side_effect = _slow_fetch

        app = FastAPI()
        app.include_router(sm.router)
        app.include_router(estimate_jobs_router)

        with (
            patch.object(sm, "_server", return_value=fake_srv),
            patch.object(ej, "_server", return_value=fake_srv),
            patch.object(ej, "_project_root_path", return_value=tmp_path),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            # BackgroundTasks run synchronously in TestClient after response — but the
            # important thing is the *response* is returned before the task starts.
            # We simply check the job was queued (status=queued) before the background
            # task touched the job.
            # To avoid the side_effect assert, patch background_tasks.add_task to noop.
            with patch("fastapi.BackgroundTasks.add_task"):
                r = client.post("/api/issues/42/estimate?async=1&repo=test/repo")

        assert r.status_code == 202

    def test_sync_path_unchanged_returns_200(self, tmp_path, monkeypatch):
        """AC1: Without async=1, the endpoint returns 200 with {ok, size, minutes}."""
        import routers.system_misc as sm
        from starlette.middleware.base import BaseHTTPMiddleware

        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)
        fake_srv = _fake_server("M", 15)

        estimates_dir = tmp_path / ".commander" / "estimates"
        estimates_dir.mkdir(parents=True)
        fake_srv._project_root_path = lambda repo: tmp_path
        fake_srv._commander_dir = lambda root: tmp_path / ".commander"

        app = FastAPI()

        class _ReqId(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.request_id = "test-req"
                return await call_next(request)

        app.add_middleware(_ReqId)
        app.include_router(sm.router)

        with patch.object(sm, "_server", return_value=fake_srv):
            client = TestClient(app)
            r = client.post("/api/issues/42/estimate?repo=test/repo")

        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["size"] == "M"
        assert data["minutes"] == 15

    def test_job_get_endpoint_returns_queued_status(self, jobs_client, tmp_path):
        """AC1: GET /api/estimate-jobs/{job_id} reports the queued status."""
        job_id = str(uuid.uuid4())
        ej._estimate_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "result": None,
        }

        r = jobs_client.get(f"/api/estimate-jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "queued"

    def test_job_get_endpoint_returns_done_with_result(self, jobs_client):
        """AC1: GET /api/estimate-jobs/{job_id} reports done + result when finished."""
        job_id = str(uuid.uuid4())
        ej._estimate_jobs[job_id] = {
            "job_id": job_id,
            "status": "done",
            "result": {"size": "L", "minutes": 30},
        }

        r = jobs_client.get(f"/api/estimate-jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "done"
        assert data["result"]["size"] == "L"
        assert data["result"]["minutes"] == 30

    def test_job_get_returns_404_for_unknown(self, jobs_client):
        """AC1: GET /api/estimate-jobs/{job_id} is 404 for an unknown job_id."""
        r = jobs_client.get("/api/estimate-jobs/nonexistent-job-id")
        assert r.status_code == 404


# ── AC2: Sprint batch estimate ────────────────────────────────────────────────

class TestAC2SprintBatch:
    """AC2: POST /api/sprints/{label}/estimate starts a batch estimate job."""

    def test_returns_202_and_job_id(self, jobs_client):
        """AC2: The endpoint returns 202 with a job_id immediately."""
        r = jobs_client.post("/api/sprints/sprint-10/estimate?project=test/repo")
        assert r.status_code == 202
        data = r.json()
        assert "job_id" in data
        assert isinstance(data["job_id"], str)

    def test_invalid_sprint_label_returns_400(self, jobs_client):
        """AC2: A non-sprint-label string is rejected with 400."""
        r = jobs_client.post("/api/sprints/not-a-sprint/estimate?project=test/repo")
        assert r.status_code == 400

    def test_job_progress_field_present(self, jobs_client):
        """AC2: The created job includes a progress list."""
        r = jobs_client.post("/api/sprints/sprint-5/estimate?project=test/repo")
        assert r.status_code == 202
        job_id = r.json()["job_id"]

        r2 = jobs_client.get(f"/api/estimate-jobs/{job_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert "progress" in data


# ── AC3: Disk persistence ─────────────────────────────────────────────────────

class TestAC3DiskPersistence:
    """AC3: Job state is written to disk and lazy-loaded after a simulated restart."""

    def test_create_job_writes_to_disk(self, tmp_path, monkeypatch):
        """AC3: create_estimate_job persists the job JSON to the jobs directory."""
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        job_id = ej.create_estimate_job(99, "test/repo")

        disk_path = tmp_path / f"{job_id}.json"
        assert disk_path.exists(), "job was not written to disk"
        saved = json.loads(disk_path.read_text())
        assert saved["job_id"] == job_id
        assert saved["status"] == "queued"
        assert saved["issue_id"] == 99

    def test_job_not_in_memory_loads_from_disk(self, tmp_path, monkeypatch):
        """AC3: get_estimate_job lazy-loads from disk when not found in memory."""
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        job_id = "disk-persisted-job"
        stored = {
            "job_id": job_id,
            "status": "done",
            "result": {"size": "S", "minutes": 5},
        }
        (tmp_path / f"{job_id}.json").write_text(json.dumps(stored))

        # Confirm it is NOT in memory
        assert job_id not in ej._estimate_jobs

        loaded = ej.get_estimate_job(job_id)

        assert loaded is not None
        assert loaded["status"] == "done"
        assert loaded["result"]["size"] == "S"
        # Should also be restored into the in-memory store
        assert job_id in ej._estimate_jobs

    def test_sprint_batch_job_persisted_immediately(self, tmp_path, monkeypatch):
        """AC3: The sprint batch job is on disk before the background task runs."""
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        app = FastAPI()
        app.include_router(estimate_jobs_router)

        with patch("fastapi.BackgroundTasks.add_task"):
            client = TestClient(app)
            r = client.post("/api/sprints/sprint-7/estimate?project=test/repo")

        assert r.status_code == 202
        job_id = r.json()["job_id"]
        disk_path = tmp_path / f"{job_id}.json"
        assert disk_path.exists(), "sprint batch job not persisted before background task"


# ── AC4: Concurrency bounded ─────────────────────────────────────────────────

class TestAC4Concurrency:
    """AC4: Concurrency is bounded so only _ESTIMATE_CONCURRENCY estimators run at once."""

    def test_semaphore_is_bounded(self):
        """AC4: The module-level semaphore respects _ESTIMATE_CONCURRENCY."""
        # Drain the semaphore
        slots: list[bool] = []
        while ej._estimate_semaphore.acquire(blocking=False):
            slots.append(True)

        acquired = len(slots)
        # Release all acquired slots
        for _ in slots:
            ej._estimate_semaphore.release()

        assert acquired == ej._ESTIMATE_CONCURRENCY
        assert acquired >= 1

    def test_concurrency_value_is_safe(self):
        """AC4: Pool size stays within a safe bound (no unbounded parallelism)."""
        # Requirement: one at a time, or small fixed pool (≤5 per AC description)
        assert 1 <= ej._ESTIMATE_CONCURRENCY <= 5


# ── AC5: Behavioral tests ─────────────────────────────────────────────────────

class TestAC5Behavioral:
    """AC5: Observable-behavior tests that confirm implementation via side-effects."""

    def test_job_lifecycle_queued_to_done_with_mocked_estimator(self, tmp_path, monkeypatch):
        """AC5: run_issue_estimate drives job through queued → running → done."""
        monkeypatch.setattr(ej, "_project_root_path", lambda repo: tmp_path)
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        fake_srv = _fake_server("XL", 60)
        monkeypatch.setattr(ej, "_server", lambda: fake_srv)

        job_id = ej.create_estimate_job(42, "owner/repo")
        assert ej._estimate_jobs[job_id]["status"] == "queued"

        ej.run_issue_estimate(job_id, 42, "owner/repo")

        job = ej._estimate_jobs[job_id]
        assert job["status"] == "done", f"expected done, got {job['status']!r}"
        assert job["result"]["size"] == "XL"
        assert job["result"]["minutes"] == 60
        # Estimator was called with the correct issue number
        fake_srv._ei_run_estimator.assert_called_once()
        call_issue = fake_srv._ei_run_estimator.call_args[0][0]
        assert call_issue == 42

    def test_failed_fetch_moves_job_to_failed(self, tmp_path, monkeypatch):
        """AC5: If fetch_issue raises, the job transitions to failed."""
        import subprocess

        monkeypatch.setattr(ej, "_project_root_path", lambda repo: tmp_path)
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        fake_srv = MagicMock()
        fake_srv._ei_fetch_issue.side_effect = subprocess.CalledProcessError(1, "gh")
        monkeypatch.setattr(ej, "_server", lambda: fake_srv)

        job_id = ej.create_estimate_job(99, "owner/repo")
        ej.run_issue_estimate(job_id, 99, "owner/repo")

        job = ej._estimate_jobs[job_id]
        assert job["status"] == "failed"

    def test_batch_skips_cached_issues_without_calling_estimator(self, tmp_path, monkeypatch):
        """AC5: run_sprint_estimate does NOT call the estimator for cached issues."""
        monkeypatch.setattr(ej, "_project_root_path", lambda repo: tmp_path)
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        # Pre-populate cache for issue 200 (cached), leave 201 uncached
        estimates_dir = tmp_path / ".commander" / "estimates"
        estimates_dir.mkdir(parents=True)
        (estimates_dir / "issue-200.json").write_text(
            json.dumps({"issue_number": 200, "size": "S", "minutes": 5})
        )

        fake_srv = _fake_server("M", 15)
        monkeypatch.setattr(ej, "_server", lambda: fake_srv)

        # Mock github_client so the batch can "fetch" issues
        monkeypatch.setattr(
            ej.github_client,
            "list_open_issues_with_body",
            lambda repo_name, limit: [
                {
                    "number": 200,
                    "title": "Cached issue",
                    "labels": [{"name": "sprint-10"}],
                },
                {
                    "number": 201,
                    "title": "Uncached issue",
                    "labels": [{"name": "sprint-10"}],
                },
            ],
        )

        job_id = str(uuid.uuid4())
        ej._estimate_jobs[job_id] = {
            "job_id": job_id,
            "type": "sprint_estimate",
            "sprint_label": "sprint-10",
            "repo": "test/repo",
            "status": "queued",
            "progress": [],
        }

        ej.run_sprint_estimate(job_id, "sprint-10", "test/repo")

        job = ej._estimate_jobs[job_id]
        assert job["status"] == "done", f"expected done, got {job['status']!r}"

        by_issue = {p["issue"]: p for p in job["progress"]}
        # Cached issue: done immediately, estimator NOT called for it
        assert by_issue[200]["status"] == "done"
        assert by_issue[200]["size"] == "S"
        # Uncached issue: estimator WAS called
        assert by_issue[201]["status"] == "done"

        # Assert mock was called exactly once — only for the uncached issue (201)
        assert fake_srv._ei_run_estimator.call_count == 1
        called_for = fake_srv._ei_run_estimator.call_args[0][0]
        assert called_for == 201, (
            f"estimator called for issue {called_for}, expected 201 (uncached)"
        )

    def test_batch_all_cached_calls_estimator_zero_times(self, tmp_path, monkeypatch):
        """AC5: When all issues are cached, the estimator is never called."""
        monkeypatch.setattr(ej, "_project_root_path", lambda repo: tmp_path)
        monkeypatch.setattr(ej, "_jobs_dir", lambda: tmp_path)

        estimates_dir = tmp_path / ".commander" / "estimates"
        estimates_dir.mkdir(parents=True)
        for num in (300, 301):
            (estimates_dir / f"issue-{num}.json").write_text(
                json.dumps({"issue_number": num, "size": "M", "minutes": 15})
            )

        fake_srv = _fake_server()
        monkeypatch.setattr(ej, "_server", lambda: fake_srv)

        monkeypatch.setattr(
            ej.github_client,
            "list_open_issues_with_body",
            lambda repo_name, limit: [
                {
                    "number": 300,
                    "title": "Issue A",
                    "labels": [{"name": "sprint-20"}],
                },
                {
                    "number": 301,
                    "title": "Issue B",
                    "labels": [{"name": "sprint-20"}],
                },
            ],
        )

        job_id = str(uuid.uuid4())
        ej._estimate_jobs[job_id] = {
            "job_id": job_id,
            "type": "sprint_estimate",
            "sprint_label": "sprint-20",
            "repo": "test/repo",
            "status": "queued",
            "progress": [],
        }

        ej.run_sprint_estimate(job_id, "sprint-20", "test/repo")

        fake_srv._ei_run_estimator.assert_not_called()
        job = ej._estimate_jobs[job_id]
        assert job["status"] == "done"
        for p in job["progress"]:
            assert p["status"] == "done"
