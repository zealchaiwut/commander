"""Tests for issue #1302: Redundant disk-reload after _get_bulk_job in bulk job routes.

AC coverage:
  AC1 - bulk_get_job has no inline disk-reload block after _get_bulk_job returns falsy
  AC2 - bulk_job_stream has no inline disk-reload block after _get_bulk_job returns falsy
  AC3 - Both handlers return HTTP 404 when _get_bulk_job returns None (no file exists)
  AC4 - Both handlers return correct data when the job exists (memory or disk)
  AC5 - No other call site in the module performs an inline disk-reload after _get_bulk_job
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_job(job_id: str = "abc123") -> dict:
    return {
        "job_id": job_id,
        "status": "done",
        "concurrency": 2,
        "repo": "owner/repo",
        "default_labels": ["sprint-99"],
        "created_at": "2026-01-01T00:00:00+00:00",
        "tickets": [
            {
                "index": 0,
                "prompt": "Prompt 0",
                "state": "done",
                "title": "Ticket 0",
                "body": "Body 0",
                "body_preview": None,
                "issue_num": 1,
                "issue_url": "https://github.com/owner/repo/issues/1",
                "label_pills": [],
                "error": None,
                "retry_count": 0,
                "_internal_key": "should_be_stripped",
            }
        ],
    }


def _source_of(func_name: str, source: str) -> str:
    """Return the source text of a top-level function from a source string."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            lines = source.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise ValueError(f"Function {func_name!r} not found")


def _bulk_tickets_source() -> str:
    """Return source of the module that owns bulk_get_job and bulk_job_stream.

    In sprint-88 these routes live in server.py (bulk_tickets.py was not yet
    extracted from the monolith when this sprint branched off develop). The
    fix applies wherever the code lives.
    """
    candidate = DASHBOARD_DIR / "routers" / "bulk_tickets.py"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    return (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")


# ── AC1: bulk_get_job has no redundant inline disk-reload block ───────────────


def test_bulk_get_job_no_inline_disk_reload_after_get_bulk_job():
    """AC1: After _get_bulk_job(), bulk_get_job must not re-read from disk inline."""
    src = _source_of("bulk_get_job", _bulk_tickets_source())

    # The redundant pattern constructs the path and calls path.read_text/path.exists
    # after _get_bulk_job. If _get_bulk_job returned None, disk fallback is already done.
    # A fresh inline `_bulk_jobs_dir()` call after `_get_bulk_job` is the tell.
    assert "_bulk_jobs_dir()" not in src, (
        "bulk_get_job still contains a redundant _bulk_jobs_dir() call after "
        "_get_bulk_job(); the disk-reload block should have been removed"
    )


# ── AC2: bulk_job_stream has no redundant inline disk-reload block ────────────


def test_bulk_job_stream_no_inline_disk_reload_after_get_bulk_job():
    """AC2: After _get_bulk_job(), bulk_job_stream must not re-read from disk inline."""
    src = _source_of("bulk_job_stream", _bulk_tickets_source())

    assert "_bulk_jobs_dir()" not in src, (
        "bulk_job_stream still contains a redundant _bulk_jobs_dir() call after "
        "_get_bulk_job(); the disk-reload block should have been removed"
    )


# ── AC3: 404 when job does not exist ─────────────────────────────────────────


class TestBulkGetJobNotFound:
    """AC3: bulk_get_job returns 404 when _get_bulk_job returns None."""

    def test_returns_404_when_job_missing_from_memory_and_disk(self, tmp_path):
        import server

        job_id = "nonexistent-job-ac3"
        server._bulk_jobs.pop(job_id, None)

        empty_dir = tmp_path / "bulk-jobs"
        empty_dir.mkdir(parents=True)

        import httpx
        from fastapi.testclient import TestClient

        with patch("server._bulk_jobs_dir", return_value=empty_dir):
            client = TestClient(server.app, raise_server_exceptions=False)
            response = client.get(f"/api/tickets/bulk/{job_id}")

        assert response.status_code == 404, (
            f"Expected 404 for unknown job, got {response.status_code}"
        )

    def test_response_body_has_job_not_found_detail(self, tmp_path):
        import server

        job_id = "nonexistent-job-detail"
        server._bulk_jobs.pop(job_id, None)

        empty_dir = tmp_path / "bulk-jobs"
        empty_dir.mkdir(parents=True)

        import httpx
        from fastapi.testclient import TestClient

        with patch("server._bulk_jobs_dir", return_value=empty_dir):
            client = TestClient(server.app, raise_server_exceptions=False)
            response = client.get(f"/api/tickets/bulk/{job_id}")

        data = response.json()
        assert "not found" in data.get("detail", "").lower(), (
            f"Expected 'not found' in detail, got: {data}"
        )


class TestBulkJobStreamNotFound:
    """AC3: bulk_job_stream returns 404 when _get_bulk_job returns None."""

    def test_stream_returns_404_when_job_missing(self, tmp_path):
        import server

        job_id = "nonexistent-stream-ac3"
        server._bulk_jobs.pop(job_id, None)

        empty_dir = tmp_path / "bulk-jobs"
        empty_dir.mkdir(parents=True)

        from fastapi.testclient import TestClient

        with patch("server._bulk_jobs_dir", return_value=empty_dir):
            client = TestClient(server.app, raise_server_exceptions=False)
            response = client.get(f"/api/tickets/bulk/{job_id}/stream")

        assert response.status_code == 404, (
            f"Expected 404 for unknown job stream, got {response.status_code}"
        )


# ── AC4: correct data returned when job exists ────────────────────────────────


class TestBulkGetJobFound:
    """AC4: bulk_get_job returns correct job data when found in memory."""

    def test_returns_200_with_correct_fields_from_memory(self, tmp_path):
        import server

        job = _make_job("memory-job-ac4")
        server._bulk_jobs[job["job_id"]] = job

        from fastapi.testclient import TestClient

        client = TestClient(server.app, raise_server_exceptions=False)
        response = client.get(f"/api/tickets/bulk/{job['job_id']}")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["job_id"] == job["job_id"]
        assert data["status"] == job["status"]
        assert data["repo"] == job["repo"]

        # Clean up
        server._bulk_jobs.pop(job["job_id"], None)

    def test_internal_fields_stripped_from_tickets(self, tmp_path):
        import server

        job = _make_job("strip-fields-job-ac4")
        server._bulk_jobs[job["job_id"]] = job

        from fastapi.testclient import TestClient

        client = TestClient(server.app, raise_server_exceptions=False)
        response = client.get(f"/api/tickets/bulk/{job['job_id']}")

        assert response.status_code == 200
        tickets = response.json()["tickets"]
        for ticket in tickets:
            internal_keys = [k for k in ticket if k.startswith("_")]
            assert not internal_keys, f"Internal keys not stripped: {internal_keys}"

        server._bulk_jobs.pop(job["job_id"], None)

    def test_returns_200_with_job_loaded_from_disk(self, tmp_path):
        """AC4: disk fallback via _get_bulk_job (not a redundant inline block) works."""
        import server

        job = _make_job("disk-job-ac4")
        job_id = job["job_id"]
        server._bulk_jobs.pop(job_id, None)

        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job_id}.json").write_text(
            json.dumps(job), encoding="utf-8"
        )

        from fastapi.testclient import TestClient

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            client = TestClient(server.app, raise_server_exceptions=False)
            response = client.get(f"/api/tickets/bulk/{job_id}")

        assert response.status_code == 200, (
            f"Expected 200 when job exists on disk, got {response.status_code}"
        )
        assert response.json()["job_id"] == job_id

        server._bulk_jobs.pop(job_id, None)


# ── AC5: no other callers in the module do inline disk-reload ─────────────────


def test_no_other_callers_do_inline_disk_reload():
    """AC5: No call site after _get_bulk_job() in the module performs an inline disk-reload.

    Checks that _bulk_jobs_dir() is not called inside any function AFTER a
    _get_bulk_job() call within the same function body.
    """
    src = _bulk_tickets_source()
    tree = ast.parse(src)
    lines = src.splitlines()

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        func_lines = lines[node.lineno - 1 : node.end_lineno]
        func_src = "\n".join(func_lines)

        if "_get_bulk_job(" not in func_src:
            continue
        if "_bulk_jobs_dir()" not in func_src:
            continue

        # Find relative positions
        get_bulk_pos = func_src.find("_get_bulk_job(")
        dir_pos = func_src.find("_bulk_jobs_dir()")

        # _bulk_jobs_dir() call inside _get_bulk_job itself is fine
        if node.name == "_get_bulk_job":
            continue

        if dir_pos > get_bulk_pos:
            violations.append(node.name)

    assert not violations, (
        f"Functions still perform inline disk-reload after _get_bulk_job(): {violations}"
    )
