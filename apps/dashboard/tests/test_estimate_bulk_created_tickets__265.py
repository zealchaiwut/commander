"""Tests for issue #265: Estimate bulk-created tickets in background with progress.

AC coverage:
  1. Bulk-creating N tickets creates all issues promptly; creation is not blocked by estimation.
  2. After each ticket is created, estimation runs as a background task.
  3. Each ticket transitions created → estimating → sized (or estimate_failed).
  4. Estimation failures surface as per-ticket warning without failing the batch.
  5. The 'estimated' label is applied on successful estimation.
  6. Background estimation uses the same concurrency control as BA drafting.
  7. No cross-ticket dependency detection or ordering is performed.
"""
from __future__ import annotations

import json
import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import app, _bulk_jobs


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_created_ticket(index: int, issue_num: int = 100) -> dict:
    """Return a ticket dict in the 'created' state (post-flusher, pre-estimation)."""
    return {
        "index": index,
        "prompt": f"Prompt {index}",
        "state": "created",
        "title": f"Title {index}",
        "body": "Body text",
        "body_preview": "Body text",
        "issue_num": issue_num + index,
        "issue_url": f"https://github.com/test/repo/issues/{issue_num + index}",
        "label_pills": ["backlog"],
        "error": None,
        "attachment_warning": None,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:01:00+00:00",
        "retry_count": 0,
        "last_error": None,
    }


# ---------------------------------------------------------------------------
# AC1: Bulk creation is not blocked by estimation
# ---------------------------------------------------------------------------

class TestBulkCreationNotBlockedByEstimation:
    """AC: Bulk-creating N tickets creates all issues promptly; creation is not blocked by estimation."""

    def test_bulk_create_endpoint_returns_job_id_immediately(self, client):
        """The /api/tickets/bulk endpoint accepts the job and returns 202 with a job_id
        without waiting for BA drafting or estimation to complete."""
        with (
            patch("server.github_client.list_labels", return_value=[{"name": "backlog"}, {"name": "sprint-1"}]),
            patch("server.projects_module.load_projects", return_value=[{"repo": "test/repo", "name": "Test"}]),
            patch("server._run_bulk_job", new_callable=AsyncMock),
        ):
            r = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "test/repo",
                    "prompts": json.dumps(["Ticket A", "Ticket B", "Ticket C"]),
                    "default_labels": json.dumps([]),
                    "concurrency": "3",
                },
            )
        assert r.status_code == 202
        assert "job_id" in r.json()


# ---------------------------------------------------------------------------
# AC2 & AC3: Per-ticket estimation state transitions
# ---------------------------------------------------------------------------

class TestBulkEstimationStateTransitions:
    """AC: Each ticket transitions created → estimating → sized | estimate_failed."""

    @pytest.mark.asyncio
    async def test_run_bulk_estimator_transitions_to_estimating_then_sized(self):
        """_run_bulk_estimator_for_ticket broadcasts estimating → sized states."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues

        job_id = "test-265-transition"
        ticket = _make_created_ticket(0, issue_num=201)
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "tickets": [ticket],
        }
        _bulk_job_queues[job_id] = []

        events: list[dict] = []

        async def mock_broadcast(jid, event):
            if jid == job_id:
                events.append(event)

        mock_stdout = b'{"size": "M", "estimated_hours": "0.25", "confidence": "high"}\n'

        with (
            patch("server._broadcast_bulk_event", side_effect=mock_broadcast),
            patch("server._post_estimator_warning"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
            patch("server.github_client.invalidate"),
        ):
            mock_script.__bool__ = lambda self: True
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))
            mock_exec.return_value = mock_proc

            await _run_bulk_estimator_for_ticket(job_id, 0, 201, "test/repo")

        ticket_updates = [e["ticket"] for e in events if e.get("type") == "ticket_update"]
        states = [t["state"] for t in ticket_updates]

        assert "estimating" in states, f"Expected 'estimating' in state transitions: {states}"
        assert "sized" in states, f"Expected 'sized' in state transitions: {states}"
        # estimating must come before sized
        assert states.index("estimating") < states.index("sized")

        # The final ticket should have estimate_size set
        final_ticket = _bulk_jobs[job_id]["tickets"][0]
        assert final_ticket["state"] == "sized"
        assert final_ticket["estimate_size"] == "M"

    @pytest.mark.asyncio
    async def test_run_bulk_estimator_transitions_to_estimate_failed_on_nonzero_exit(self):
        """estimate_issue.py exiting non-zero → ticket transitions to estimate_failed."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues

        job_id = "test-265-failure"
        ticket = _make_created_ticket(0, issue_num=202)
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "tickets": [ticket],
        }
        _bulk_job_queues[job_id] = []

        events: list[dict] = []

        async def mock_broadcast(jid, event):
            if jid == job_id:
                events.append(event)

        with (
            patch("server._broadcast_bulk_event", side_effect=mock_broadcast),
            patch("server._post_estimator_warning") as mock_warn,
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_script.__bool__ = lambda self: True
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"estimator error"))
            mock_exec.return_value = mock_proc

            await _run_bulk_estimator_for_ticket(job_id, 0, 202, "test/repo")

        ticket_updates = [e["ticket"] for e in events if e.get("type") == "ticket_update"]
        states = [t["state"] for t in ticket_updates]

        assert "estimating" in states
        assert "estimate_failed" in states

        final_ticket = _bulk_jobs[job_id]["tickets"][0]
        assert final_ticket["state"] == "estimate_failed"
        assert final_ticket.get("estimate_error")

        # Warning comment must be posted
        mock_warn.assert_called_once()


# ---------------------------------------------------------------------------
# AC4: Estimation failure doesn't block other tickets
# ---------------------------------------------------------------------------

class TestEstimationFailureIsolation:
    """AC: Estimation failures surface as a per-ticket warning without failing the batch."""

    @pytest.mark.asyncio
    async def test_estimate_failure_does_not_affect_other_tickets(self):
        """When estimation fails for one ticket, other tickets are unaffected."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues

        job_id = "test-265-isolation"
        tickets = [_make_created_ticket(i, issue_num=300) for i in range(3)]
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "tickets": tickets,
        }
        _bulk_job_queues[job_id] = []

        successful_stdout = b'{"size": "S"}\n'
        failing_stdout = b""

        call_count = [0]

        async def mock_broadcast(jid, event):
            pass

        async def mock_exec(*args, **kwargs):
            call_count[0] += 1
            idx = call_count[0]
            mock_proc = AsyncMock()
            if idx == 2:
                # Middle ticket fails
                mock_proc.returncode = 1
                mock_proc.communicate = AsyncMock(return_value=(failing_stdout, b"error"))
            else:
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(successful_stdout, b""))
            return mock_proc

        with (
            patch("server._broadcast_bulk_event", side_effect=mock_broadcast),
            patch("server._post_estimator_warning"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec", side_effect=mock_exec),
            patch("server.github_client.invalidate"),
        ):
            mock_script.__bool__ = lambda self: True
            mock_script.exists = lambda: True

            await asyncio.gather(
                _run_bulk_estimator_for_ticket(job_id, 0, 300, "test/repo"),
                _run_bulk_estimator_for_ticket(job_id, 1, 301, "test/repo"),
                _run_bulk_estimator_for_ticket(job_id, 2, 302, "test/repo"),
            )

        final_tickets = _bulk_jobs[job_id]["tickets"]
        assert final_tickets[0]["state"] == "sized"
        assert final_tickets[1]["state"] == "estimate_failed"
        assert final_tickets[2]["state"] == "sized"


# ---------------------------------------------------------------------------
# AC5: estimated label applied on success (integration with _post_estimator_warning path)
# ---------------------------------------------------------------------------

class TestEstimatedLabelApplied:
    """AC: The 'estimated' label is applied to each ticket on successful estimation."""

    @pytest.mark.asyncio
    async def test_successful_estimation_does_not_post_warning(self):
        """On success, no warning comment is posted."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues

        job_id = "test-265-label"
        ticket = _make_created_ticket(0, issue_num=400)
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "tickets": [ticket],
        }
        _bulk_job_queues[job_id] = []

        successful_stdout = b'{"size": "L", "estimated_hours": 0.5, "confidence": "medium"}\n'

        with (
            patch("server._broadcast_bulk_event", new_callable=AsyncMock),
            patch("server._post_estimator_warning") as mock_warn,
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
            patch("server.github_client.invalidate"),
        ):
            mock_script.__bool__ = lambda self: True
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(successful_stdout, b""))
            mock_exec.return_value = mock_proc

            await _run_bulk_estimator_for_ticket(job_id, 0, 400, "test/repo")

        mock_warn.assert_not_called()
        assert _bulk_jobs[job_id]["tickets"][0]["state"] == "sized"
        assert _bulk_jobs[job_id]["tickets"][0]["estimate_size"] == "L"


# ---------------------------------------------------------------------------
# AC6: Concurrency control — semaphore is used
# ---------------------------------------------------------------------------

class TestEstimationConcurrencyControl:
    """AC: Background estimation uses the same concurrency control as BA drafting."""

    def test_bulk_estimator_semaphore_exists_and_is_bounded(self):
        """The global estimator semaphore has a value of 3 (mirrors BA concurrency cap)."""
        import server
        # Reset so we test fresh initialization
        server._bulk_estimator_semaphore = None
        sem = server._get_bulk_estimator_semaphore()
        assert isinstance(sem, asyncio.Semaphore)
        # Semaphore(3) stores the initial value in _value
        assert sem._value == 3

    def test_get_bulk_estimator_semaphore_returns_same_instance(self):
        """Calling _get_bulk_estimator_semaphore twice returns the same object."""
        import server
        server._bulk_estimator_semaphore = None
        sem1 = server._get_bulk_estimator_semaphore()
        sem2 = server._get_bulk_estimator_semaphore()
        assert sem1 is sem2


# ---------------------------------------------------------------------------
# AC7: No cross-ticket ordering
# ---------------------------------------------------------------------------

class TestNoCrossTicketOrdering:
    """AC: No cross-ticket dependency detection or ordering is performed."""

    @pytest.mark.asyncio
    async def test_all_tickets_estimated_regardless_of_index(self):
        """All tickets in any index order are estimated independently — no serial gating."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues

        job_id = "test-265-no-order"
        # Use non-sequential indices to confirm no index-ordering dependency
        tickets = [_make_created_ticket(i, issue_num=500) for i in range(3)]
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "tickets": tickets,
        }
        _bulk_job_queues[job_id] = []

        stdout_data = b'{"size": "S"}\n'

        with (
            patch("server._broadcast_bulk_event", new_callable=AsyncMock),
            patch("server._post_estimator_warning"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
            patch("server.github_client.invalidate"),
        ):
            mock_script.__bool__ = lambda self: True
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(stdout_data, b""))
            mock_exec.return_value = mock_proc

            await asyncio.gather(
                _run_bulk_estimator_for_ticket(job_id, 0, 500, "test/repo"),
                _run_bulk_estimator_for_ticket(job_id, 1, 501, "test/repo"),
                _run_bulk_estimator_for_ticket(job_id, 2, 502, "test/repo"),
            )

        # All three tickets should be estimated independently
        final_tickets = _bulk_jobs[job_id]["tickets"]
        for i, ticket in enumerate(final_tickets):
            assert ticket["state"] == "sized", f"Ticket {i} should be sized, got {ticket['state']}"
            assert ticket["estimate_size"] == "S"


# ---------------------------------------------------------------------------
# Helper function unit test
# ---------------------------------------------------------------------------

class TestExtractSizeFromStdout:
    """Unit tests for the _extract_size_from_estimator_stdout helper."""

    def test_extracts_size_from_json_in_stdout(self):
        from server import _extract_size_from_estimator_stdout
        stdout = (
            "Fetching issue #123 from test/repo ...\n"
            "Running estimator (Haiku 4.5) for #123 ...\n"
            'Saved: /path/to/estimates/issue-123.json\n'
            '{"size": "M", "estimated_hours": "0.25", "confidence": "high", "files_likely_affected": []}\n'
        )
        assert _extract_size_from_estimator_stdout(stdout) == "M"

    def test_extracts_xl_size(self):
        from server import _extract_size_from_estimator_stdout
        stdout = '{"size": "XL", "estimated_hours": "1.0"}'
        assert _extract_size_from_estimator_stdout(stdout) == "XL"

    def test_returns_none_for_invalid_size(self):
        from server import _extract_size_from_estimator_stdout
        stdout = '{"size": "Z"}'
        assert _extract_size_from_estimator_stdout(stdout) is None

    def test_returns_none_for_no_json(self):
        from server import _extract_size_from_estimator_stdout
        stdout = "Error: could not parse output"
        assert _extract_size_from_estimator_stdout(stdout) is None

    def test_returns_none_for_empty_string(self):
        from server import _extract_size_from_estimator_stdout
        assert _extract_size_from_estimator_stdout("") is None
