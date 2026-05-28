"""Tests for issue #331: Fix estimation failure on bulk create operation.

AC coverage:
  1. Estimation completes successfully when bulk creating records (2+ items).
  2. Estimation completes successfully when creating a single record.
  3. Estimation result is accurate and reflects the actual outcome of the create operation.
  4. A clear error message is shown if estimation genuinely cannot be performed,
     rather than a silent or unexpected failure.
  5. No regression introduced to other estimation entry points (e.g., update, delete).
"""
from __future__ import annotations

import asyncio
import sys
import os
import time
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import app, _bulk_jobs, _bulk_job_queues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_created_ticket(index: int, issue_num: int = 100) -> dict:
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
# AC1 & AC2: Estimation completes successfully (bulk and single)
# ---------------------------------------------------------------------------

class TestEstimationCompletesOnBulkCreate:
    """AC: Estimation completes successfully when bulk creating 2+ tickets."""

    @pytest.mark.asyncio
    async def test_bulk_two_tickets_both_reach_sized_state(self):
        """Two concurrently estimated tickets both reach 'sized' state."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues
        from unittest.mock import AsyncMock

        job_id = "test-331-bulk-two"
        tickets = [_make_created_ticket(i, issue_num=600) for i in range(2)]
        _bulk_jobs[job_id] = {"job_id": job_id, "status": "running", "tickets": tickets}
        _bulk_job_queues[job_id] = []

        successful_stdout = b'{"size": "S"}\n'

        with (
            patch("server._broadcast_bulk_event", new_callable=AsyncMock),
            patch("server._post_estimator_warning"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
            patch("server.github_client.invalidate"),
        ):
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(successful_stdout, b""))
            mock_exec.return_value = mock_proc

            await asyncio.gather(
                _run_bulk_estimator_for_ticket(job_id, 0, 600, "test/repo"),
                _run_bulk_estimator_for_ticket(job_id, 1, 601, "test/repo"),
            )

        tickets_after = _bulk_jobs[job_id]["tickets"]
        assert tickets_after[0]["state"] == "sized"
        assert tickets_after[1]["state"] == "sized"
        assert tickets_after[0]["estimate_size"] == "S"
        assert tickets_after[1]["estimate_size"] == "S"

    @pytest.mark.asyncio
    async def test_single_ticket_reaches_sized_state(self):
        """A single ticket estimation reaches 'sized' state — no regression."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues
        from unittest.mock import AsyncMock

        job_id = "test-331-single"
        ticket = _make_created_ticket(0, issue_num=700)
        _bulk_jobs[job_id] = {"job_id": job_id, "status": "running", "tickets": [ticket]}
        _bulk_job_queues[job_id] = []

        stdout = b'{"size": "M", "estimated_hours": "0.25", "confidence": "high"}\n'

        with (
            patch("server._broadcast_bulk_event", new_callable=AsyncMock),
            patch("server._post_estimator_warning"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
            patch("server.github_client.invalidate"),
        ):
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(stdout, b""))
            mock_exec.return_value = mock_proc

            await _run_bulk_estimator_for_ticket(job_id, 0, 700, "test/repo")

        assert _bulk_jobs[job_id]["tickets"][0]["state"] == "sized"
        assert _bulk_jobs[job_id]["tickets"][0]["estimate_size"] == "M"


# ---------------------------------------------------------------------------
# AC3: Estimation result is accurate
# ---------------------------------------------------------------------------

class TestEstimationResultAccuracy:
    """AC: Estimation result is accurate and reflects actual outcome."""

    @pytest.mark.asyncio
    async def test_size_extracted_correctly_from_subprocess_stdout(self):
        """Size extracted from estimator stdout matches the JSON output."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues
        from unittest.mock import AsyncMock

        for size in ("S", "M", "L", "XL"):
            job_id = f"test-331-size-{size}"
            ticket = _make_created_ticket(0, issue_num=800)
            _bulk_jobs[job_id] = {"job_id": job_id, "status": "running", "tickets": [ticket]}
            _bulk_job_queues[job_id] = []

            stdout = f'{{"size": "{size}", "estimated_hours": "0.5"}}\n'.encode()

            with (
                patch("server._broadcast_bulk_event", new_callable=AsyncMock),
                patch("server._post_estimator_warning"),
                patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
                patch("server.asyncio.create_subprocess_exec") as mock_exec,
                patch("server.github_client.invalidate"),
            ):
                mock_script.exists = lambda: True
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(stdout, b""))
                mock_exec.return_value = mock_proc

                await _run_bulk_estimator_for_ticket(job_id, 0, 800, "test/repo")

            assert _bulk_jobs[job_id]["tickets"][0]["estimate_size"] == size, \
                f"Expected size={size}, got {_bulk_jobs[job_id]['tickets'][0]['estimate_size']}"

    @pytest.mark.asyncio
    async def test_size_is_none_when_estimator_returns_unrecognised_size(self):
        """estimate_size is None (not a crash) when the output size is unrecognised."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues
        from unittest.mock import AsyncMock

        job_id = "test-331-bad-size"
        ticket = _make_created_ticket(0, issue_num=900)
        _bulk_jobs[job_id] = {"job_id": job_id, "status": "running", "tickets": [ticket]}
        _bulk_job_queues[job_id] = []

        stdout = b'{"size": "HUGE", "estimated_hours": "99"}\n'

        with (
            patch("server._broadcast_bulk_event", new_callable=AsyncMock),
            patch("server._post_estimator_warning"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
            patch("server.github_client.invalidate"),
        ):
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(stdout, b""))
            mock_exec.return_value = mock_proc

            await _run_bulk_estimator_for_ticket(job_id, 0, 900, "test/repo")

        t = _bulk_jobs[job_id]["tickets"][0]
        assert t["state"] == "sized"
        assert t["estimate_size"] is None


# ---------------------------------------------------------------------------
# AC4: Clear error message when estimation genuinely cannot be performed
# ---------------------------------------------------------------------------

class TestClearErrorWhenEstimationCannotRun:
    """AC: A clear error message is shown if estimation genuinely cannot be performed."""

    @pytest.mark.asyncio
    async def test_estimate_failed_state_has_error_message(self):
        """estimate_failed ticket has a non-empty estimate_error field."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues
        from unittest.mock import AsyncMock

        job_id = "test-331-clear-error"
        ticket = _make_created_ticket(0, issue_num=1000)
        _bulk_jobs[job_id] = {"job_id": job_id, "status": "running", "tickets": [ticket]}
        _bulk_job_queues[job_id] = []

        with (
            patch("server._broadcast_bulk_event", new_callable=AsyncMock),
            patch("server._post_estimator_warning") as mock_warn,
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_script.exists = lambda: True
            mock_proc = AsyncMock()
            mock_proc.returncode = 1
            mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: agent exited 1"))
            mock_exec.return_value = mock_proc

            await _run_bulk_estimator_for_ticket(job_id, 0, 1000, "test/repo")

        t = _bulk_jobs[job_id]["tickets"][0]
        assert t["state"] == "estimate_failed"
        assert t.get("estimate_error"), "estimate_error should be non-empty for a clear message"
        assert "1" in t["estimate_error"]
        mock_warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_repo_marks_ticket_estimate_failed_with_reason(self):
        """When repo cannot be resolved, ticket gets estimate_failed with a clear reason."""
        from server import _bulk_flusher, _bulk_jobs, _bulk_job_queues

        job_id = "test-331-no-repo"
        ticket = {
            "index": 0,
            "prompt": "Create a feature",
            "state": "draft_ready",
            "title": "Feature X",
            "body": "Body text",
            "body_preview": "Body text",
            "issue_num": None,
            "issue_url": None,
            "label_pills": [],
            "error": None,
            "attachment_warning": None,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
            "retry_count": 0,
            "last_error": None,
            "_default_labels": ["backlog"],
            "_repo": None,  # no repo on ticket — forces get_repo_for_operation lookup
        }
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "repo": "test/repo",
            "has_attachments": False,
            "tickets": [ticket],
        }
        _bulk_job_queues[job_id] = []

        broadcast_events: list[dict] = []

        async def capture_broadcast(jid, event):
            broadcast_events.append(event)

        with (
            patch("server._broadcast_bulk_event", side_effect=capture_broadcast),
            patch("server._persist_bulk_job"),
            patch("server.github_client.create_issue", return_value=(42, "https://github.com/test/repo/issues/42")),
            patch("server.github_client.get_repo_for_operation", side_effect=Exception("no repo")),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
        ):
            mock_script.exists = lambda: True
            mock_script.__bool__ = lambda self: True

            await _bulk_flusher(job_id)

        final_ticket = _bulk_jobs[job_id]["tickets"][0]
        assert final_ticket["state"] == "estimate_failed", \
            f"Expected estimate_failed, got {final_ticket['state']}"
        assert final_ticket.get("estimate_error"), "estimate_error should describe why estimation failed"
        assert "repository" in final_ticket["estimate_error"].lower() or "repo" in final_ticket["estimate_error"].lower()

    @pytest.mark.asyncio
    async def test_no_script_marks_ticket_estimate_failed_with_reason(self):
        """When estimate_issue.py is missing, ticket gets estimate_failed with a clear reason."""
        from server import _bulk_flusher, _bulk_jobs, _bulk_job_queues

        job_id = "test-331-no-script"
        ticket = {
            "index": 0,
            "prompt": "Create a feature",
            "state": "draft_ready",
            "title": "Feature Y",
            "body": "Body text",
            "body_preview": "Body text",
            "issue_num": None,
            "issue_url": None,
            "label_pills": [],
            "error": None,
            "attachment_warning": None,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
            "retry_count": 0,
            "last_error": None,
            "_default_labels": ["backlog"],
            "_repo": "test/repo",
        }
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "repo": "test/repo",
            "has_attachments": False,
            "tickets": [ticket],
        }
        _bulk_job_queues[job_id] = []

        broadcast_events: list[dict] = []

        async def capture_broadcast(jid, event):
            broadcast_events.append(event)

        with (
            patch("server._broadcast_bulk_event", side_effect=capture_broadcast),
            patch("server._persist_bulk_job"),
            patch("server.github_client.create_issue", return_value=(43, "https://github.com/test/repo/issues/43")),
            patch("server.github_client.get_repo_for_operation", return_value="test/repo"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
        ):
            mock_script.exists = lambda: False
            mock_script.__bool__ = lambda self: False

            await _bulk_flusher(job_id)

        final_ticket = _bulk_jobs[job_id]["tickets"][0]
        assert final_ticket["state"] == "estimate_failed", \
            f"Expected estimate_failed, got {final_ticket['state']}"
        assert final_ticket.get("estimate_error"), "estimate_error should be set"

    @pytest.mark.asyncio
    async def test_job_done_fires_when_estimation_cannot_start(self):
        """job_done event fires even when estimation cannot start (no repo)."""
        from server import _bulk_flusher, _bulk_jobs, _bulk_job_queues

        job_id = "test-331-job-done-no-repo"
        ticket = {
            "index": 0,
            "prompt": "Create a feature",
            "state": "draft_ready",
            "title": "Feature Z",
            "body": "Body text",
            "body_preview": "Body text",
            "issue_num": None,
            "issue_url": None,
            "label_pills": [],
            "error": None,
            "attachment_warning": None,
            "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
            "retry_count": 0,
            "last_error": None,
            "_default_labels": ["backlog"],
            "_repo": None,  # no repo — forces get_repo_for_operation lookup
        }
        _bulk_jobs[job_id] = {
            "job_id": job_id,
            "status": "running",
            "repo": "test/repo",
            "has_attachments": False,
            "tickets": [ticket],
        }
        _bulk_job_queues[job_id] = []

        broadcast_events: list[dict] = []

        async def capture_broadcast(jid, event):
            broadcast_events.append(event)

        with (
            patch("server._broadcast_bulk_event", side_effect=capture_broadcast),
            patch("server._persist_bulk_job"),
            patch("server.github_client.create_issue", return_value=(44, "https://github.com/test/repo/issues/44")),
            patch("server.github_client.get_repo_for_operation", side_effect=Exception("no repo")),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
        ):
            mock_script.exists = lambda: True
            mock_script.__bool__ = lambda self: True

            await _bulk_flusher(job_id)

        job_done_events = [e for e in broadcast_events if e.get("type") == "job_done"]
        assert job_done_events, "job_done event must be broadcast even when estimation cannot start"


# ---------------------------------------------------------------------------
# AC4: Retry logic in estimate_issue.py
# ---------------------------------------------------------------------------

class TestEstimateIssueRetryLogic:
    """AC: Estimation retries on transient agent failure instead of giving up immediately."""

    def test_run_estimator_retries_on_nonzero_exit_and_succeeds(self):
        """run_estimator retries when claude exits non-zero and succeeds on 2nd attempt."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "estimate_issue",
            Path(__file__).parent.parent.parent.parent / "services" / "sprint_manager" / "estimate_issue.py",
        )
        ei = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ei)

        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                m.returncode = 1
                m.stderr = ""
                m.stdout = ""
            else:
                m.returncode = 0
                m.stdout = '{"size": "M", "estimated_hours": "0.25"}'
                m.stderr = ""
            return m

        with (
            patch.object(ei.subprocess, "run", side_effect=mock_run),
            patch("time.sleep"),
        ):
            result = ei.run_estimator(123, {"title": "Test issue", "body": "Test body"})

        assert result is not None, "Expected successful result after retry"
        assert result.get("size") == "M"
        assert call_count[0] == 2, f"Expected 2 calls (1 fail + 1 success), got {call_count[0]}"

    def test_run_estimator_exhausts_retries_and_returns_none(self):
        """run_estimator returns None after exhausting all retries."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "estimate_issue",
            Path(__file__).parent.parent.parent.parent / "services" / "sprint_manager" / "estimate_issue.py",
        )
        ei = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ei)

        call_count = [0]

        def mock_run(*args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            m.returncode = 1
            m.stderr = "connection error"
            m.stdout = ""
            return m

        with (
            patch.object(ei.subprocess, "run", side_effect=mock_run),
            patch("time.sleep"),
        ):
            result = ei.run_estimator(456, {"title": "Test", "body": "Body"})

        assert result is None, "Expected None after all retries exhausted"
        assert call_count[0] == ei._ESTIMATOR_MAX_RETRIES, \
            f"Expected {ei._ESTIMATOR_MAX_RETRIES} attempts, got {call_count[0]}"

    def test_no_session_persistence_flag_in_cmd(self):
        """--no-session-persistence is present in the claude CLI command to prevent session conflicts."""
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "estimate_issue",
            Path(__file__).parent.parent.parent.parent / "services" / "sprint_manager" / "estimate_issue.py",
        )
        ei = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ei)

        captured_cmds: list[list] = []

        def mock_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stdout = '{"size": "S"}'
            m.stderr = ""
            return m

        with patch.object(ei.subprocess, "run", side_effect=mock_run):
            ei.run_estimator(789, {"title": "Test", "body": "Body"})

        assert captured_cmds, "subprocess.run should have been called"
        assert "--no-session-persistence" in captured_cmds[0], \
            "--no-session-persistence must be in the claude CLI command"


# ---------------------------------------------------------------------------
# AC5: No regression in other estimation entry points
# ---------------------------------------------------------------------------

class TestNoRegressionInOtherEstimationPaths:
    """AC: No regression introduced to other estimation entry points."""

    @pytest.mark.asyncio
    async def test_estimate_failed_does_not_affect_other_tickets_in_batch(self):
        """Failure of one estimation does not propagate to other tickets in the same job."""
        from server import _run_bulk_estimator_for_ticket, _bulk_jobs, _bulk_job_queues
        from unittest.mock import AsyncMock

        job_id = "test-331-regression-isolation"
        tickets = [_make_created_ticket(i, issue_num=1100) for i in range(3)]
        _bulk_jobs[job_id] = {"job_id": job_id, "status": "running", "tickets": tickets}
        _bulk_job_queues[job_id] = []

        call_count = [0]

        async def varying_exec(*args, **kwargs):
            call_count[0] += 1
            mock_proc = AsyncMock()
            if call_count[0] == 2:
                mock_proc.returncode = 1
                mock_proc.communicate = AsyncMock(return_value=(b"", b"transient error"))
            else:
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b'{"size": "S"}', b""))
            return mock_proc

        with (
            patch("server._broadcast_bulk_event", new_callable=AsyncMock),
            patch("server._post_estimator_warning"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
            patch("server.asyncio.create_subprocess_exec", side_effect=varying_exec),
            patch("server.github_client.invalidate"),
        ):
            mock_script.exists = lambda: True

            await asyncio.gather(
                _run_bulk_estimator_for_ticket(job_id, 0, 1100, "test/repo"),
                _run_bulk_estimator_for_ticket(job_id, 1, 1101, "test/repo"),
                _run_bulk_estimator_for_ticket(job_id, 2, 1102, "test/repo"),
            )

        result = _bulk_jobs[job_id]["tickets"]
        assert result[0]["state"] == "sized"
        assert result[1]["state"] == "estimate_failed"
        assert result[2]["state"] == "sized"

    @pytest.mark.asyncio
    async def test_single_ticket_background_estimator_still_works(self):
        """_run_estimator_for_issue (single-ticket path) is unaffected."""
        from server import _run_estimator_for_issue

        with (
            patch("server.asyncio.create_subprocess_exec") as mock_exec,
            patch("server.github_client.invalidate"),
            patch("server._post_estimator_warning") as mock_warn,
        ):
            from unittest.mock import AsyncMock
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
            mock_exec.return_value = mock_proc

            await _run_estimator_for_issue(1200, "test/repo")

        mock_warn.assert_not_called()
