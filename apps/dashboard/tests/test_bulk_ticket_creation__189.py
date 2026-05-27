"""Tests for issue #189: Add Bulk Ticket Creation from Sprint Backlog Input.

Covers the server-side API (all endpoints) and frontend entry-point (button + modal).
BA calls are mocked — we never invoke the real claude CLI.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _bulk_post(client, *, repo: str, prompts: list, concurrency: int = 3,
               default_labels: list | None = None):
    """POST to /api/tickets/bulk using multipart form-data (issue #205 changed the contract)."""
    data = {
        "repo": repo,
        "prompts": json.dumps(prompts),
        "concurrency": str(concurrency),
    }
    if default_labels is not None:
        data["default_labels"] = json.dumps(default_labels)
    return client.post("/api/tickets/bulk", data=data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DASHBOARD_DIR = Path(__file__).parent.parent
INDEX_HTML = DASHBOARD_DIR / "static" / "index.html"
APP_JS = DASHBOARD_DIR / "static" / "app.js"


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX_HTML.read_text()


@pytest.fixture(scope="module")
def js() -> str:
    return APP_JS.read_text()


@pytest.fixture
def client(tmp_path, monkeypatch) -> Generator:
    """TestClient with projects stubbed so validation passes."""
    # Point bulk-jobs dir to a temp directory
    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))

    import server
    import projects as projects_module

    fake_project = {"repo": "zealchaiwut/commander", "name": "Commander"}

    monkeypatch.setattr(projects_module, "load_projects", lambda: [fake_project])
    monkeypatch.setattr(
        "server.projects_module.load_projects", lambda: [fake_project]
    )

    # Stub github_client.list_labels to return some labels
    import github_client as gc
    monkeypatch.setattr(
        gc, "list_labels",
        lambda repo_name=None: [
            {"name": "enhancement", "color": "84b6eb"},
            {"name": "backlog", "color": "e4e669"},
            {"name": "frontend", "color": "0075ca"},
        ],
    )

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# AC: Entry point — "Bulk create" button in toolbar
# ---------------------------------------------------------------------------

class TestBulkCreateButton:
    def test_bulk_create_button_exists(self, html):
        """Bulk create button exists in the smgmt-header-actions."""
        assert "Bulk create" in html, \
            "Bulk create button text not found in index.html"

    def test_bulk_create_button_calls_open_modal(self, html):
        """Bulk create button calls openBulkCreateModal()."""
        assert "openBulkCreateModal()" in html, \
            "openBulkCreateModal() must be referenced in index.html"

    def test_bulk_create_button_adjacent_to_new_ticket(self, html):
        """Bulk create button is near the + New Ticket button."""
        ticket_idx = html.find("+ New Ticket")
        bulk_idx = html.find("Bulk create")
        assert ticket_idx != -1, "+ New Ticket button not found"
        assert bulk_idx != -1, "Bulk create button not found"
        # Both should be within 500 chars of each other (same actions bar)
        assert abs(ticket_idx - bulk_idx) < 500, \
            "Bulk create should be adjacent to + New Ticket in the toolbar"


# ---------------------------------------------------------------------------
# AC: Step 1 — Input modal structure
# ---------------------------------------------------------------------------

class TestBulkModalHtml:
    def test_modal_exists(self, html):
        """bc-modal element exists."""
        assert 'id="bc-modal"' in html

    def test_modal_backdrop_exists(self, html):
        """bc-backdrop element exists."""
        assert 'id="bc-backdrop"' in html

    def test_step1_exists(self, html):
        """bc-step1 div exists."""
        assert 'id="bc-step1"' in html

    def test_step2_exists(self, html):
        """bc-step2 div exists."""
        assert 'id="bc-step2"' in html

    def test_modal_header_step_badge(self, html):
        """Step badge element is present."""
        assert 'id="bc-step-badge"' in html

    def test_repo_dropdown(self, html):
        """Repo dropdown is present."""
        assert 'id="bc-repo"' in html

    def test_default_labels_input(self, html):
        """Default labels text input is present."""
        assert 'id="bc-default-labels"' in html

    def test_textarea_exists(self, html):
        """Monospace textarea is present."""
        assert 'id="bc-textarea"' in html

    def test_counter_row(self, html):
        """Live counter element is present."""
        assert 'id="bc-counter"' in html

    def test_info_note_separator(self, html):
        """Info note mentioning --- separator is in the modal."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 5000]
        assert "---" in modal_block, "Info note about --- separator must be in modal"

    def test_concurrency_selector(self, html):
        """Concurrency selector is present."""
        assert 'id="bc-concurrency"' in html

    def test_concurrency_options(self, html):
        """Concurrency options include 1, 3, and 5."""
        assert 'value="1"' in html
        assert 'value="3"' in html
        assert 'value="5"' in html

    def test_run_btn_exists(self, html):
        """Run BA button is present."""
        assert 'id="bc-run-btn"' in html

    def test_parallel_banner(self, html):
        """Parallel banner is present in step 2."""
        assert 'id="bc-parallel-banner"' in html

    def test_summary_strip(self, html):
        """Summary strip is present in step 2."""
        assert 'id="bc-summary-strip"' in html

    def test_ticket_list(self, html):
        """Ticket list container is present in step 2."""
        assert 'id="bc-ticket-list"' in html

    def test_stop_btn(self, html):
        """Stop after current button is present in step 2."""
        assert 'id="bc-stop-btn"' in html

    def test_done_btn(self, html):
        """Done/Close button is present in step 2."""
        assert 'id="bc-done-btn"' in html


# ---------------------------------------------------------------------------
# AC: JavaScript functions
# ---------------------------------------------------------------------------

class TestBulkCreateJS:
    def test_open_function_exists(self, js):
        """openBulkCreateModal function is defined."""
        assert "function openBulkCreateModal" in js

    def test_close_function_exists(self, js):
        """closeBulkCreateModal function is defined."""
        assert "function closeBulkCreateModal" in js

    def test_run_all_function_exists(self, js):
        """bcRunAll function is defined."""
        assert "function bcRunAll" in js

    def test_counter_update_function_exists(self, js):
        """_bcUpdateCounter function is defined."""
        assert "function _bcUpdateCounter" in js

    def test_separator_detection(self, js):
        """Separator logic uses --- on its own line."""
        assert "---" in js, "Separator --- must be referenced in JS"

    def test_debounce_200ms(self, js):
        """Counter debounce of 200ms is used."""
        assert "200" in js, "200ms debounce must be present in JS"

    def test_max_prompts_constant(self, js):
        """BC_MAX_PROMPTS constant of 25 is defined."""
        assert "BC_MAX_PROMPTS = 25" in js or "BC_MAX_PROMPTS=25" in js

    def test_sse_connect_function(self, js):
        """_bcConnectSSE function is defined."""
        assert "function _bcConnectSSE" in js

    def test_render_step2(self, js):
        """_bcRenderStep2 function is defined."""
        assert "function _bcRenderStep2" in js

    def test_skip_function(self, js):
        """bcSkipTicket function is defined."""
        assert "function bcSkipTicket" in js

    def test_retry_function(self, js):
        """bcRetryTicket function is defined."""
        assert "function bcRetryTicket" in js

    def test_stop_function(self, js):
        """bcStop function is defined."""
        assert "function bcStop" in js

    def test_toast_function(self, js):
        """_bcShowToast function is defined."""
        assert "function _bcShowToast" in js

    def test_undo_skip_function(self, js):
        """bcUndoSkip function is defined."""
        assert "function bcUndoSkip" in js

    def test_localstorage_job_id(self, js):
        """localStorage.setItem used for job_id persistence."""
        assert "localStorage.setItem" in js
        assert "bc_job_id" in js

    def test_cmd_enter_triggers_run(self, js):
        """Cmd/Ctrl+Enter keyboard shortcut triggers Run."""
        assert "metaKey" in js or "ctrlKey" in js

    def test_esc_closes_modal(self, js):
        """Escape key closes modal."""
        assert "Escape" in js


# ---------------------------------------------------------------------------
# AC: POST /api/tickets/bulk validation
# ---------------------------------------------------------------------------

class TestBulkCreateAPIValidation:
    def test_empty_prompts_422(self, client):
        """Reject empty batch with 422."""
        res = _bulk_post(client, repo='zealchaiwut/commander', prompts=[])
        assert res.status_code == 422

    def test_blank_only_prompts_422(self, client):
        """Reject batch of only blank prompts with 422."""
        res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['   ', '\t', ''])
        assert res.status_code == 422

    def test_over_25_prompts_422(self, client):
        """Reject batches over 25 prompts with 422."""
        prompts = [f"prompt {i}" for i in range(26)]
        res = _bulk_post(client, repo='zealchaiwut/commander', prompts=prompts)
        assert res.status_code == 422
        assert "25" in res.text

    def test_invalid_concurrency_422(self, client):
        """Reject concurrency values outside {1, 3, 5} with 422."""
        res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['some prompt'], concurrency=4)
        assert res.status_code == 422

    def test_concurrency_2_422(self, client):
        """Concurrency 2 is not allowed."""
        res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['some prompt'], concurrency=2)
        assert res.status_code == 422

    def test_unknown_repo_422(self, client):
        """Reject unknown repos with 422."""
        res = _bulk_post(client, repo='unknown/repo', prompts=['some prompt'])
        assert res.status_code == 422

    def test_unknown_default_label_422(self, client):
        """Reject default labels not in the repo with 422."""
        res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['some prompt'], default_labels=['non-existent-label-xyz'])
        assert res.status_code == 422

    def test_valid_request_returns_job_id(self, client):
        """Valid request returns a job_id."""
        import server

        # Mock _run_bulk_job to avoid actually running BA
        async def _noop_job(job_id):
            job = server._bulk_jobs.get(job_id)
            if job:
                job["status"] = "done"
                for t in job["tickets"]:
                    t["state"] = "skipped"

        with patch.object(server, "_run_bulk_job", side_effect=_noop_job):
            # asyncio.create_task is called inside the endpoint, patch it
            with patch("asyncio.create_task"):
                res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['Add user avatar', 'API endpoint for avatar'])
        assert res.status_code == 202
        data = res.json()
        assert "job_id" in data
        assert len(data["job_id"]) > 0

    def test_exactly_25_prompts_accepted(self, client):
        """Exactly 25 prompts is within limit."""
        import server

        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander',
                             prompts=[f"prompt {i}" for i in range(25)], concurrency=1)
        assert res.status_code == 202

    def test_concurrency_1_accepted(self, client):
        """Concurrency 1 is valid."""
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['test prompt'], concurrency=1)
        assert res.status_code == 202

    def test_concurrency_5_accepted(self, client):
        """Concurrency 5 is valid."""
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['test prompt'], concurrency=5)
        assert res.status_code == 202

    def test_valid_known_labels_accepted(self, client):
        """Valid known default labels are accepted."""
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['test prompt'], default_labels=['enhancement'])
        assert res.status_code == 202


# ---------------------------------------------------------------------------
# AC: GET /api/tickets/bulk/{job_id}
# ---------------------------------------------------------------------------

class TestBulkGetJob:
    def _create_job(self, client) -> str:
        """Helper to create a job and return job_id."""
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['prompt one', 'prompt two'])
        assert res.status_code == 202
        return res.json()["job_id"]

    def test_get_job_returns_state(self, client):
        """GET /api/tickets/bulk/{job_id} returns job state."""
        job_id = self._create_job(client)
        res = client.get(f"/api/tickets/bulk/{job_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["job_id"] == job_id
        assert "status" in data
        assert "tickets" in data
        assert len(data["tickets"]) == 2

    def test_get_job_tickets_have_required_fields(self, client):
        """Each ticket in the job state has required fields."""
        job_id = self._create_job(client)
        res = client.get(f"/api/tickets/bulk/{job_id}")
        assert res.status_code == 200
        for ticket in res.json()["tickets"]:
            assert "index" in ticket
            assert "prompt" in ticket
            assert "state" in ticket

    def test_get_job_404_for_unknown(self, client):
        """GET returns 404 for unknown job_id."""
        res = client.get(f"/api/tickets/bulk/{uuid.uuid4()}")
        assert res.status_code == 404

    def test_get_job_no_internal_fields(self, client):
        """GET response does not expose internal _ prefixed fields."""
        job_id = self._create_job(client)
        res = client.get(f"/api/tickets/bulk/{job_id}")
        for ticket in res.json()["tickets"]:
            for key in ticket:
                assert not key.startswith("_"), f"Internal field {key!r} exposed in API"


# ---------------------------------------------------------------------------
# AC: POST /api/tickets/bulk/{job_id}/stop
# ---------------------------------------------------------------------------

class TestBulkStop:
    def test_stop_sets_flag(self, client):
        """POST /stop sets stop_requested on the job."""
        import server

        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['p1', 'p2'])
        job_id = res.json()["job_id"]

        stop_res = client.post(f"/api/tickets/bulk/{job_id}/stop")
        assert stop_res.status_code == 200
        assert server._bulk_jobs[job_id]["stop_requested"] is True

    def test_stop_404_unknown(self, client):
        """POST /stop returns 404 for unknown job."""
        res = client.post(f"/api/tickets/bulk/{uuid.uuid4()}/stop")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# AC: POST /api/tickets/bulk/{job_id}/skip
# ---------------------------------------------------------------------------

class TestBulkSkip:
    def _make_job(self, client) -> str:
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['p1', 'p2', 'p3'])
        return res.json()["job_id"]

    def test_skip_pending_ticket(self, client):
        """Skipping a pending ticket sets state to skipped."""
        import server
        job_id = self._make_job(client)

        res = client.post(f"/api/tickets/bulk/{job_id}/skip", json={"index": 0})
        assert res.status_code == 200
        assert server._bulk_jobs[job_id]["tickets"][0]["state"] == "skipped"

    def test_skip_is_noop_for_created(self, client):
        """Skipping an already-created ticket is a no-op (state unchanged)."""
        import server
        job_id = self._make_job(client)
        # Manually set ticket 0 as created
        server._bulk_jobs[job_id]["tickets"][0]["state"] = "created"

        res = client.post(f"/api/tickets/bulk/{job_id}/skip", json={"index": 0})
        assert res.status_code == 200
        assert server._bulk_jobs[job_id]["tickets"][0]["state"] == "created"

    def test_skip_invalid_index_422(self, client):
        """Skipping an invalid index returns 422."""
        job_id = self._make_job(client)
        res = client.post(f"/api/tickets/bulk/{job_id}/skip", json={"index": 99})
        assert res.status_code == 422

    def test_skip_404_unknown_job(self, client):
        """Skipping on unknown job returns 404."""
        res = client.post(f"/api/tickets/bulk/{uuid.uuid4()}/skip", json={"index": 0})
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# AC: POST /api/tickets/bulk/{job_id}/retry
# ---------------------------------------------------------------------------

class TestBulkRetry:
    def _make_job_with_failed(self, client) -> str:
        """Create a job and set first ticket to failed."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['p1', 'p2'])
        job_id = res.json()["job_id"]
        server._bulk_jobs[job_id]["tickets"][0]["state"] = "failed"
        server._bulk_jobs[job_id]["tickets"][0]["error"] = "BA timed out"
        return job_id

    def test_retry_failed_ticket(self, client):
        """Retrying a failed ticket re-queues it (sets pending or triggers BA)."""
        import server
        job_id = self._make_job_with_failed(client)

        with patch("asyncio.create_task"):
            res = client.post(f"/api/tickets/bulk/{job_id}/retry", json={"index": 0})
        assert res.status_code == 200

    def test_retry_404_unknown_job(self, client):
        """Retry on unknown job returns 404."""
        res = client.post(f"/api/tickets/bulk/{uuid.uuid4()}/retry", json={"index": 0})
        assert res.status_code == 404

    def test_retry_422_invalid_index(self, client):
        """Retry with invalid index returns 422."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['p1'])
        job_id = res.json()["job_id"]
        res = client.post(f"/api/tickets/bulk/{job_id}/retry", json={"index": 50})
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# AC: GET /api/tickets/bulk/{job_id}/stream (SSE)
# ---------------------------------------------------------------------------

class TestBulkStream:
    def test_stream_404_unknown_job(self, client):
        """SSE stream returns 404 for unknown job."""
        res = client.get(f"/api/tickets/bulk/{uuid.uuid4()}/stream")
        assert res.status_code == 404

    def test_stream_url_pattern_in_js(self, js):
        """Frontend connects to the /stream endpoint via EventSource."""
        assert "/stream" in js or "stream" in js
        assert "EventSource" in js


# ---------------------------------------------------------------------------
# AC: In-memory job state management
# ---------------------------------------------------------------------------

class TestBulkJobState:
    def test_job_stored_in_memory(self, client):
        """After POST, the job is stored in _bulk_jobs."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['test'])
        job_id = res.json()["job_id"]
        assert job_id in server._bulk_jobs

    def test_job_initial_status_running(self, client):
        """Newly created job has status 'running'."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['test'])
        job_id = res.json()["job_id"]
        assert server._bulk_jobs[job_id]["status"] == "running"

    def test_job_tickets_initial_state(self, client):
        """All tickets start in 'pending' state."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['p1', 'p2', 'p3'])
        job_id = res.json()["job_id"]
        for t in server._bulk_jobs[job_id]["tickets"]:
            assert t["state"] == "pending"

    def test_job_blank_prompts_filtered(self, client):
        """Blank prompts between separators are filtered out."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['real prompt', '   ', 'another prompt'])
        job_id = res.json()["job_id"]
        # Only non-blank prompts are stored
        assert len(server._bulk_jobs[job_id]["tickets"]) == 2

    def test_job_concurrency_stored(self, client):
        """Concurrency value is stored on the job."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['test'], concurrency=5)
        job_id = res.json()["job_id"]
        assert server._bulk_jobs[job_id]["concurrency"] == 5

    def test_job_persisted_to_disk(self, client, tmp_path):
        """Job state is persisted to .commander/bulk-jobs/<job_id>.json."""
        import server
        with patch("asyncio.create_task"):
            res = _bulk_post(client, repo='zealchaiwut/commander', prompts=['test'])
        job_id = res.json()["job_id"]
        # Should be persisted somewhere readable
        jobs_dir = server._bulk_jobs_dir()
        snapshot_path = jobs_dir / f"{job_id}.json"
        assert snapshot_path.exists(), f"Job snapshot not found at {snapshot_path}"
        saved = json.loads(snapshot_path.read_text())
        assert saved["job_id"] == job_id


# ---------------------------------------------------------------------------
# AC: _parse_ba_draft reuse
# ---------------------------------------------------------------------------

class TestParseBaDraft:
    def test_parses_json_output(self):
        """_parse_ba_draft extracts title and body from JSON output."""
        import server
        raw = json.dumps({"title": "My Title", "body": "## What\n\nStuff"})
        title, body = server._parse_ba_draft(raw)
        assert title == "My Title"
        assert "Stuff" in body

    def test_parses_fenced_json(self):
        """_parse_ba_draft handles ```json ... ``` fences."""
        import server
        raw = "```json\n" + json.dumps({"title": "T", "body": "B"}) + "\n```"
        title, body = server._parse_ba_draft(raw)
        assert title == "T"
        assert body == "B"
