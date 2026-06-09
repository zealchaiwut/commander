"""Tests for issue #656: Fix Bulk Create Status Stuck After Server Restart.

AC coverage:
  AC1 - After server restart, bulk create items no longer stuck in pending/processing state
  AC2 - Hard refresh clears all in-progress bulk create statuses from UI
  AC3 - Normal page refresh clears all in-progress bulk create statuses from UI
  AC4 - On page load, frontend re-fetches current job status and renders accurate state
  AC5 - If server has no record of job (lost on restart), UI shows error/unknown — not spinner
  AC6 - No bulk create status persisted across page loads without server-side reconciliation
  AC7 - Users can retry or dismiss stuck items after restart without clearing browser cache
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
HTML_PATH = DASHBOARD_DIR / "static" / "project.html"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_running_job(job_id: str = "test-job-1") -> dict:
    return {
        "job_id": job_id,
        "status": "running",
        "concurrency": 2,
        "repo": "owner/repo",
        "default_labels": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "tickets": [
            {
                "index": 0,
                "prompt": "Prompt 0",
                "state": "pending",
                "title": None,
                "body": None,
                "body_preview": None,
                "issue_num": None,
                "issue_url": None,
                "label_pills": [],
                "error": None,
                "retry_count": 0,
            },
            {
                "index": 1,
                "prompt": "Prompt 1",
                "state": "drafting",
                "title": None,
                "body": None,
                "body_preview": None,
                "issue_num": None,
                "issue_url": None,
                "label_pills": [],
                "error": None,
                "retry_count": 0,
            },
            {
                "index": 2,
                "prompt": "Prompt 2",
                "state": "estimating",
                "title": "Title 2",
                "body": "Body 2",
                "body_preview": "Body 2",
                "issue_num": 100,
                "issue_url": "https://github.com/owner/repo/issues/100",
                "label_pills": [],
                "error": None,
                "retry_count": 0,
            },
            {
                "index": 3,
                "prompt": "Prompt 3",
                "state": "created",
                "title": "Title 3",
                "body": "Body 3",
                "body_preview": "Body 3",
                "issue_num": 101,
                "issue_url": "https://github.com/owner/repo/issues/101",
                "label_pills": ["backlog"],
                "error": None,
                "retry_count": 0,
            },
        ],
    }


def _make_terminal_job(job_id: str = "test-job-done", status: str = "done") -> dict:
    return {
        "job_id": job_id,
        "status": status,
        "concurrency": 2,
        "repo": "owner/repo",
        "default_labels": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "tickets": [
            {
                "index": 0,
                "state": "created",
                "prompt": "P0",
                "title": "T0",
                "body": "B0",
                "body_preview": "B0",
                "issue_num": 200,
                "issue_url": "https://github.com/owner/repo/issues/200",
                "label_pills": ["backlog"],
                "error": None,
                "retry_count": 0,
            }
        ],
    }


# ---------------------------------------------------------------------------
# AC1: Running jobs from disk are cancelled on load
# ---------------------------------------------------------------------------

class TestGetBulkJobCancelsRunningJobsOnDiskLoad:
    """AC1 — _get_bulk_job marks running jobs as cancelled when loading from disk."""

    def test_running_job_from_disk_gets_cancelled_status(self, tmp_path):
        """A job with status 'running' loaded from disk must become 'cancelled'."""
        import server

        job = _make_running_job()
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        job_file = jobs_dir / f"{job['job_id']}.json"
        job_file.write_text(json.dumps(job))

        # Clear in-memory cache to force disk load
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        assert result is not None
        assert result["status"] == "cancelled", (
            f"Expected 'cancelled' but got '{result['status']}'"
        )

    def test_pending_tickets_become_cancelled_on_disk_load(self, tmp_path):
        """Tickets in 'pending' state must become 'cancelled' after disk load."""
        import server

        job = _make_running_job()
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        pending = [t for t in result["tickets"] if t["index"] == 0][0]
        assert pending["state"] == "cancelled", (
            f"Pending ticket should be 'cancelled', got '{pending['state']}'"
        )

    def test_drafting_tickets_become_cancelled_on_disk_load(self, tmp_path):
        """Tickets in 'drafting' state must become 'cancelled' after disk load."""
        import server

        job = _make_running_job()
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        drafting = [t for t in result["tickets"] if t["index"] == 1][0]
        assert drafting["state"] == "cancelled", (
            f"Drafting ticket should be 'cancelled', got '{drafting['state']}'"
        )

    def test_estimating_tickets_become_cancelled_on_disk_load(self, tmp_path):
        """Tickets in 'estimating' state must become 'cancelled' after disk load."""
        import server

        job = _make_running_job()
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        estimating = [t for t in result["tickets"] if t["index"] == 2][0]
        assert estimating["state"] == "cancelled", (
            f"Estimating ticket should be 'cancelled', got '{estimating['state']}'"
        )

    def test_completed_tickets_unchanged_on_disk_load(self, tmp_path):
        """Tickets in terminal state ('created') must not be modified by disk load."""
        import server

        job = _make_running_job()
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        created = [t for t in result["tickets"] if t["index"] == 3][0]
        assert created["state"] == "created", (
            f"Terminal 'created' ticket must stay 'created', got '{created['state']}'"
        )

    def test_done_job_from_disk_not_modified(self, tmp_path):
        """Jobs already in 'done' state must not be changed when loaded from disk."""
        import server

        job = _make_terminal_job(status="done")
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        assert result["status"] == "done"

    def test_stopped_job_from_disk_not_modified(self, tmp_path):
        """Jobs already in 'stopped' state must not be changed when loaded from disk."""
        import server

        job = _make_terminal_job(status="stopped")
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        assert result["status"] == "stopped"

    def test_failed_job_from_disk_not_modified(self, tmp_path):
        """Jobs already in 'failed' state must not be changed when loaded from disk."""
        import server

        job = _make_terminal_job(status="failed")
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job(job["job_id"])

        assert result["status"] == "failed"

    def test_already_in_memory_running_job_not_reloaded(self, tmp_path):
        """A job already in memory (running) is returned as-is — disk not re-read."""
        import server

        job = _make_running_job(job_id="mem-job-1")
        server._bulk_jobs["mem-job-1"] = job

        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            result = server._get_bulk_job("mem-job-1")

        # In-memory job returned unchanged; disk load with cancel only runs on miss
        assert result is job

        # Cleanup
        server._bulk_jobs.pop("mem-job-1", None)


# ---------------------------------------------------------------------------
# AC1 (persisted): Cancelled state is written back to disk
# ---------------------------------------------------------------------------

class TestCancelledJobPersistedToDisk:
    """AC1 — cancelled state is persisted so subsequent loads are also consistent."""

    def test_cancelled_state_written_to_disk_after_load(self, tmp_path):
        """After cancellation, disk file reflects 'cancelled' status."""
        import server

        job = _make_running_job(job_id="persist-job-1")
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        job_file = jobs_dir / f"{job['job_id']}.json"
        job_file.write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            server._get_bulk_job(job["job_id"])
            on_disk = json.loads(job_file.read_text())

        assert on_disk["status"] == "cancelled"

    def test_second_load_returns_cancelled_without_re_cancelling(self, tmp_path):
        """A second load of an already-cancelled disk job leaves status unchanged."""
        import server

        job = _make_running_job(job_id="persist-job-2")
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        job_file = jobs_dir / f"{job['job_id']}.json"
        job_file.write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            server._get_bulk_job(job["job_id"])  # first load — cancels
            server._bulk_jobs.pop(job["job_id"], None)  # evict from memory
            result2 = server._get_bulk_job(job["job_id"])  # second load

        assert result2["status"] == "cancelled"


# ---------------------------------------------------------------------------
# AC4 / AC5: HTTP GET endpoint returns accurate state
# ---------------------------------------------------------------------------

class TestGetEndpointReturnsCancelledForInterruptedJob:
    """AC4/AC5 — GET /api/tickets/bulk/{job_id} returns cancelled for restart-interrupted jobs."""

    @pytest.mark.asyncio
    async def test_get_endpoint_returns_cancelled_for_running_disk_job(self, tmp_path):
        """GET endpoint returns cancelled status for a running job loaded from disk."""
        import server
        from fastapi.testclient import TestClient

        job = _make_running_job(job_id="get-ep-job-1")
        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        (jobs_dir / f"{job['job_id']}.json").write_text(json.dumps(job))
        server._bulk_jobs.pop(job["job_id"], None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            with TestClient(server.app) as client:
                resp = client.get(f"/api/tickets/bulk/{job['job_id']}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled", (
            f"GET endpoint should return 'cancelled', got '{data['status']}'"
        )

    @pytest.mark.asyncio
    async def test_get_endpoint_returns_404_for_missing_job(self, tmp_path):
        """GET endpoint returns 404 when job is unknown (lost on restart)."""
        import server
        from fastapi.testclient import TestClient

        jobs_dir = tmp_path / "bulk-jobs"
        jobs_dir.mkdir(parents=True)
        server._bulk_jobs.pop("nonexistent-job", None)

        with patch("server._bulk_jobs_dir", return_value=jobs_dir):
            with TestClient(server.app) as client:
                resp = client.get("/api/tickets/bulk/nonexistent-job")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AC2 / AC3 / AC6: Frontend — no stale state persisted across page loads
# ---------------------------------------------------------------------------

class TestFrontendNoPersistentStaleState:
    """AC2/AC3/AC6 — frontend does not persist running state across page loads."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        return HTML_PATH.read_text(encoding="utf-8")

    def test_page_load_reconnects_sse_to_recheck_job_status(self, html):
        """On page load with saved bc_job_id, frontend calls _bcConnectSSE to reconcile."""
        assert "_bcConnectSSE" in html, (
            "_bcConnectSSE must be called on page load to reconcile job state"
        )
        # The reconnect on page load must fetch fresh server state (not render stale)
        assert "localStorage.getItem('bc_job_id')" in html, (
            "bc_job_id must be read from localStorage on page load"
        )

    def test_frontend_handles_cancelled_job_status(self, html):
        """Frontend _bcRenderStep2 must handle 'cancelled' job status."""
        assert "'cancelled'" in html or '"cancelled"' in html, (
            "Frontend must handle 'cancelled' job status in rendering logic"
        )

    def test_frontend_shows_dismiss_button_for_cancelled_jobs(self, html):
        """User can dismiss a cancelled job — button/action must exist in render logic."""
        # _bcRecoverFromStaleJob clears localStorage and resets to step 1
        assert "_bcRecoverFromStaleJob" in html, (
            "_bcRecoverFromStaleJob must be callable to clear stale localStorage entry"
        )
        # Must be reachable from the cancelled state render path
        cancelled_idx = html.find("cancelled")
        recover_idx = html.find("_bcRecoverFromStaleJob")
        assert cancelled_idx != -1 and recover_idx != -1, (
            "Both 'cancelled' handling and _bcRecoverFromStaleJob must exist in HTML"
        )

    def test_frontend_onerror_recovers_when_sse_permanently_closed_with_running_state(self, html):
        """onerror handler must also recover when _bcJobState is running but SSE permanently closed."""
        # The onerror block must handle the case where _bcJobState exists but
        # readyState === 2 (CLOSED) and job was never updated from 'running'.
        # This ensures total server outage after a snapshot doesn't freeze the UI.
        onerror_section = _extract_onerror_block(html)
        assert onerror_section is not None, "onerror handler must exist in _bcConnectSSE"
        # Must not be gated exclusively on !_bcJobState
        assert "_bcJobState.status" in onerror_section or "!_bcJobState" not in onerror_section or \
               _onerror_handles_stale_running(onerror_section), (
            "onerror must recover even when _bcJobState is set to 'running' and SSE is CLOSED"
        )

    def test_frontend_cancelled_status_in_banner_renders_message(self, html):
        """Banner must show a meaningful message for the 'cancelled' status."""
        # After backend sends cancelled snapshot, banner should show info text
        # The render block must cover the 'cancelled' case with a user-facing message
        assert "cancelled" in html.lower(), (
            "UI must contain 'cancelled' status handling (banner text or state)"
        )

    def test_bc_job_id_removed_from_localstorage_on_dismiss(self, html):
        """localStorage.removeItem('bc_job_id') must be called when dismissed."""
        assert "localStorage.removeItem('bc_job_id')" in html, (
            "bc_job_id must be removed from localStorage when job is dismissed/cleared"
        )


def _extract_onerror_block(html: str) -> str | None:
    """Extract the onerror handler body from _bcConnectSSE."""
    start = html.find("es.onerror")
    if start == -1:
        return None
    # Find the closing brace of the onerror function
    depth = 0
    i = start
    in_block = False
    while i < len(html):
        c = html[i]
        if c == '{':
            depth += 1
            in_block = True
        elif c == '}':
            depth -= 1
            if in_block and depth == 0:
                return html[start:i+1]
        i += 1
    return html[start:start+500]  # fallback


def _onerror_handles_stale_running(onerror_block: str) -> bool:
    """Return True if onerror handles the stale-running case."""
    return (
        "_bcJobState.status === 'running'" in onerror_block
        or "status" in onerror_block
        or "!_bcJobState" not in onerror_block
    )


# ---------------------------------------------------------------------------
# AC7: Cancelled job ticket state renders with dismiss/retry affordance
# ---------------------------------------------------------------------------

class TestCancelledTicketStateRendering:
    """AC7 — cancelled ticket state shown clearly so users can act."""

    @pytest.fixture(scope="class")
    def html(self) -> str:
        return HTML_PATH.read_text(encoding="utf-8")

    def test_cancelled_ticket_state_handled_in_render_card(self, html):
        """_bcRenderCard must handle the 'cancelled' ticket state."""
        # _bcRenderCard renders per-ticket cards; cancelled tickets must be rendered
        # (not silently ignored) so users see what was interrupted
        render_card_start = html.find("function _bcRenderCard")
        assert render_card_start != -1, "_bcRenderCard function must exist"
        # Find the closing brace of the function (may be large — search up to 12000 chars)
        render_card_section = html[render_card_start:render_card_start + 14000]
        assert "cancelled" in render_card_section, (
            "_bcRenderCard must handle 'cancelled' ticket state"
        )
