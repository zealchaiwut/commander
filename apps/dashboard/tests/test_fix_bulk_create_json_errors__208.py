"""Tests for issue #208: Fix Bulk Create JSON Errors and Add Recreate Button.

AC coverage:
  AC1 - All tickets generated are valid, well-formed JSON (no malformed fields)
  AC2 - A Recreate button is present on the bulk create confirmation (Step 2) screen
  AC3 - Clicking outside the confirmation modal does NOT submit or close it
  AC4 - Cancel button closes the modal without submitting
  AC5 - Close icon (x) dismisses without triggering submission
  AC6 - Recreate button re-runs bulk creation using the same input data
  AC7 - JSON errors detected server-side surface a clear error message per ticket
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient


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
    """TestClient with projects and github stubs."""
    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))

    import server
    import projects as projects_module

    fake_project = {"repo": "zealchaiwut/commander", "name": "Commander"}
    monkeypatch.setattr(projects_module, "load_projects", lambda: [fake_project])
    monkeypatch.setattr("server.projects_module.load_projects", lambda: [fake_project])

    import github_client as gc
    monkeypatch.setattr(
        gc, "list_labels",
        lambda repo_name=None: [
            {"name": "enhancement", "color": "84b6eb"},
            {"name": "backlog", "color": "e4e669"},
        ],
    )

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


def _bulk_post(client, *, repo: str, prompts: list, concurrency: int = 3,
               default_labels: list | None = None):
    data = {
        "repo": repo,
        "prompts": json.dumps(prompts),
        "concurrency": str(concurrency),
    }
    if default_labels is not None:
        data["default_labels"] = json.dumps(default_labels)
    return client.post("/api/tickets/bulk", data=data)


# ── AC1: Well-formed JSON tickets ─────────────────────────────────────────────

class TestWellFormedJson:
    def test_parse_ba_draft_returns_json_ok_true_for_valid_json(self):
        """_parse_ba_draft returns json_ok=True when output is valid JSON."""
        import server
        raw = json.dumps({"title": "Valid Title", "body": "## What\n\nBody text"})
        title, body, json_ok = server._parse_ba_draft(raw)
        assert json_ok is True
        assert title == "Valid Title"
        assert "Body text" in body

    def test_parse_ba_draft_returns_json_ok_false_for_invalid_json(self):
        """_parse_ba_draft returns json_ok=False when output is not parseable JSON."""
        import server
        raw = "This is not JSON at all. Just some random text from a failed BA call."
        title, body, json_ok = server._parse_ba_draft(raw)
        assert json_ok is False

    def test_parse_ba_draft_json_ok_true_for_fenced_json(self):
        """_parse_ba_draft handles ```json fences and returns json_ok=True."""
        import server
        raw = "```json\n" + json.dumps({"title": "T", "body": "B"}) + "\n```"
        title, body, json_ok = server._parse_ba_draft(raw)
        assert json_ok is True
        assert title == "T"

    def test_parse_ba_draft_json_ok_true_for_embedded_json(self):
        """_parse_ba_draft extracts embedded JSON block and returns json_ok=True."""
        import server
        raw = 'Some preamble text\n{"title": "Embedded", "body": "Body here"}\nsome suffix'
        title, body, json_ok = server._parse_ba_draft(raw)
        assert json_ok is True
        assert title == "Embedded"

    def test_bulk_ticket_malformed_json_marks_ticket_failed(self, client):
        """When BA returns non-JSON output, the code path marks ticket failed (AC1, AC7).

        This tests the integration between _parse_ba_draft (json_ok=False) and
        _run_single_ba_ticket's error handling by running the function directly
        with a mocked subprocess.
        """
        import server
        import asyncio as _asyncio

        # Create a job in _bulk_jobs manually (bypass HTTP to avoid mock complexity)
        from datetime import datetime, timezone as _tz
        job_id_direct = "test-json-fail-" + str(id(self))
        now = datetime.now(_tz.utc).isoformat()
        ticket = {
            "index": 0,
            "prompt": "Create a ticket about payments",
            "state": "pending",
            "title": None, "body": None, "body_preview": None,
            "issue_num": None, "issue_url": None, "label_pills": None,
            "error": None, "attachment_warning": None,
            "started_at": None, "finished_at": None,
        }
        server._bulk_jobs[job_id_direct] = {
            "job_id": job_id_direct,
            "status": "running",
            "repo": "zealchaiwut/commander",
            "default_labels": [],
            "concurrency": 3,
            "created_at": now,
            "stop_requested": False,
            "has_attachments": False,
            "attachment_filenames": [],
            "tickets": [ticket],
        }
        server._bulk_job_queues[job_id_direct] = []

        async def run_with_mock():
            with patch("server.asyncio.create_subprocess_exec") as mock_exec:
                proc = AsyncMock()
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"not json output at all", b""))
                mock_exec.return_value = proc
                await server._run_single_ba_ticket(
                    job_id_direct, 0,
                    "Create a ticket about payments",
                    "zealchaiwut/commander",
                    [],
                )

        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(run_with_mock())
        finally:
            loop.close()

        job = server._bulk_jobs.get(job_id_direct)
        assert job is not None, "Job should exist in memory"
        ticket_result = job["tickets"][0]
        assert ticket_result["state"] == "failed", (
            f"Ticket with malformed JSON should be 'failed', got {ticket_result['state']!r}"
        )
        assert ticket_result["error"] is not None, "Error message should be set"
        assert "malformed JSON" in ticket_result["error"] or "json" in ticket_result["error"].lower(), (
            f"Error should mention JSON issue, got: {ticket_result['error']!r}"
        )


# ── AC2: Recreate button present in Step 2 ────────────────────────────────────

class TestRecreateButtonPresent:
    def test_recreate_button_exists_in_html(self, html):
        """A Recreate button with id=bc-recreate-btn is present in the Step 2 footer."""
        assert 'id="bc-recreate-btn"' in html, \
            "bc-recreate-btn element not found in index.html"

    def test_recreate_button_calls_bcRecreate(self, html):
        """The Recreate button calls bcRecreate()."""
        assert "bcRecreate()" in html, \
            "bcRecreate() onclick handler not found in index.html"

    def test_recreate_button_labeled_recreate(self, html):
        """The Recreate button has the text 'Recreate'."""
        assert ">Recreate<" in html, \
            "Button text 'Recreate' not found in index.html"

    def test_recreate_button_in_step2_footer(self, html):
        """The Recreate button is inside the Step 2 section."""
        step2_start = html.find('id="bc-step2"')
        assert step2_start != -1, "bc-step2 section not found"
        # The closing </div> of bc-step2 block — find after it
        recreate_pos = html.find('id="bc-recreate-btn"', step2_start)
        assert recreate_pos != -1, "bc-recreate-btn not found within bc-step2"
        # Ensure it's within the step2 block (before the next major section)
        next_modal = html.find('</div>\n</div>', step2_start)
        assert recreate_pos < next_modal + 100, \
            "bc-recreate-btn should be inside bc-step2 footer"


# ── AC3: Backdrop click does not close/submit modal ───────────────────────────

class TestBackdropClickNoOp:
    def test_backdrop_has_click_handler(self, html):
        """The backdrop element has a click handler defined."""
        assert "_bcBackdropClick()" in html, \
            "_bcBackdropClick() handler not referenced on backdrop in index.html"

    def test_backdrop_click_fn_does_not_call_close(self, js):
        """_bcBackdropClick function body does NOT call closeBulkCreateModal."""
        fn_start = js.find("function _bcBackdropClick()")
        assert fn_start != -1, "_bcBackdropClick function not found in app.js"
        # Extract the function body (up to the closing brace)
        brace_open = js.find("{", fn_start)
        brace_close = js.find("}", brace_open)
        fn_body = js[brace_open:brace_close + 1]
        assert "closeBulkCreateModal" not in fn_body, (
            "_bcBackdropClick must NOT call closeBulkCreateModal (AC3: "
            "backdrop click should not close the modal)"
        )

    def test_backdrop_does_not_submit(self, js):
        """_bcBackdropClick does not call bcRunAll or any submit action."""
        fn_start = js.find("function _bcBackdropClick()")
        assert fn_start != -1
        brace_open = js.find("{", fn_start)
        brace_close = js.find("}", brace_open)
        fn_body = js[brace_open:brace_close + 1]
        assert "bcRunAll" not in fn_body, \
            "_bcBackdropClick must not trigger bcRunAll"
        assert "fetch(" not in fn_body, \
            "_bcBackdropClick must not make any API calls"


# ── AC4: Cancel button closes modal without submitting ────────────────────────

class TestCancelButton:
    def test_cancel_button_exists_in_step1(self, html):
        """Step 1 footer has a Cancel button."""
        assert "Cancel" in html, "Cancel button text not found in index.html"

    def test_cancel_button_calls_close(self, html):
        """Cancel button calls closeBulkCreateModal()."""
        # Find the step1 region
        step1_start = html.find('id="bc-step1"')
        assert step1_start != -1, "bc-step1 section not found"
        step1_region = html[step1_start: step1_start + 3000]
        assert "closeBulkCreateModal()" in step1_region, \
            "Cancel button must call closeBulkCreateModal() inside bc-step1"

    def test_cancel_button_does_not_submit(self, html):
        """Cancel button does NOT call bcRunAll."""
        step1_start = html.find('id="bc-step1"')
        step1_end = html.find('id="bc-step2"')
        step1_region = html[step1_start:step1_end]
        # The cancel button text is near closeBulkCreateModal and NOT near bcRunAll inline
        cancel_pos = step1_region.find(">Cancel<")
        assert cancel_pos != -1, "Cancel button text not found in step1"
        # The onclick on the cancel button element
        btn_snippet = step1_region[max(0, cancel_pos - 200):cancel_pos + 50]
        assert "bcRunAll" not in btn_snippet, \
            "Cancel button must not trigger bcRunAll"


# ── AC5: Close icon dismisses without submission ──────────────────────────────

class TestCloseIcon:
    def test_close_icon_exists_in_modal_header(self, html):
        """The modal header has a close icon button."""
        # The bc-modal header region
        modal_start = html.find('id="bc-modal"')
        assert modal_start != -1
        header_end = html.find('id="bc-step1"', modal_start)
        header_region = html[modal_start:header_end]
        assert "modal-close" in header_region, \
            "modal-close class not found in bc-modal header"

    def test_close_icon_calls_close_modal(self, html):
        """The close icon calls closeBulkCreateModal()."""
        modal_start = html.find('id="bc-modal"')
        header_end = html.find('id="bc-step1"', modal_start)
        header_region = html[modal_start:header_end]
        assert "closeBulkCreateModal()" in header_region, \
            "Close icon must call closeBulkCreateModal() in modal header"

    def test_close_icon_does_not_call_submit(self, html):
        """The close icon does not trigger bcRunAll."""
        modal_start = html.find('id="bc-modal"')
        header_end = html.find('id="bc-step1"', modal_start)
        header_region = html[modal_start:header_end]
        # Check no submit action on close button
        close_pos = header_region.find("modal-close")
        btn_snippet = header_region[max(0, close_pos - 10):close_pos + 200]
        assert "bcRunAll" not in btn_snippet, \
            "Close icon must not trigger bcRunAll"


# ── AC6: Recreate re-runs with same input data ────────────────────────────────

class TestRecreateFunction:
    def test_bcRecreate_function_defined(self, js):
        """bcRecreate function is defined in app.js."""
        assert "function bcRecreate()" in js, \
            "bcRecreate() function not found in app.js"

    def test_bcRecreate_shows_step1(self, js):
        """bcRecreate calls _bcShowStep1() to go back to the input step."""
        fn_start = js.find("function bcRecreate()")
        assert fn_start != -1
        fn_end = js.find("\n}\n", fn_start)
        fn_body = js[fn_start:fn_end + 3]
        assert "_bcShowStep1()" in fn_body, \
            "bcRecreate must call _bcShowStep1() to show the input form"

    def test_bcRecreate_restores_last_input(self, js):
        """bcRecreate uses _bcLastInput to restore previous Step 1 values."""
        fn_start = js.find("function bcRecreate()")
        assert fn_start != -1
        fn_end = js.find("\n}\n", fn_start)
        fn_body = js[fn_start:fn_end + 3]
        assert "_bcLastInput" in fn_body, \
            "bcRecreate must reference _bcLastInput to restore previous inputs"

    def test_bcLastInput_set_in_bcRunAll(self, js):
        """bcRunAll stores input values into _bcLastInput before submitting."""
        fn_start = js.find("async function bcRunAll()")
        assert fn_start != -1
        fn_end = js.find("\n}\n", fn_start)
        fn_body = js[fn_start:fn_end + 3]
        assert "_bcLastInput" in fn_body, \
            "bcRunAll must save input to _bcLastInput for later Recreate use"

    def test_bcRecreate_clears_job_state(self, js):
        """bcRecreate clears SSE connection and job state."""
        fn_start = js.find("function bcRecreate()")
        assert fn_start != -1
        fn_end = js.find("\n}\n", fn_start)
        fn_body = js[fn_start:fn_end + 3]
        assert "_bcJobId = null" in fn_body or "_bcJobId=null" in fn_body, \
            "bcRecreate must reset _bcJobId to null"
        assert "_bcJobState = null" in fn_body or "_bcJobState=null" in fn_body, \
            "bcRecreate must reset _bcJobState to null"

    def test_recreate_button_initially_hidden(self, html):
        """Recreate button starts hidden (only shown after job completes)."""
        btn_pos = html.find('id="bc-recreate-btn"')
        assert btn_pos != -1
        # Check the button element for hidden class
        btn_start = html.rfind("<button", 0, btn_pos)
        btn_end = html.find(">", btn_pos)
        btn_tag = html[btn_start:btn_end + 1]
        assert "hidden" in btn_tag, \
            "bc-recreate-btn should have 'hidden' class initially"


# ── AC7: JSON errors surface clear per-ticket error message ───────────────────

class TestJsonErrorSurfacing:
    def test_failed_ticket_has_error_field(self):
        """When a ticket fails due to JSON parse error, the error field has a descriptive message.

        Tests the error path via _parse_ba_draft directly — json_ok=False should propagate
        to the ticket error field in _run_single_ba_ticket.
        """
        import server
        import asyncio as _asyncio
        from datetime import datetime, timezone as _tz

        job_id_direct = "test-json-err-" + str(id(self))
        now = datetime.now(_tz.utc).isoformat()
        ticket = {
            "index": 0, "prompt": "Build a payment system",
            "state": "pending", "title": None, "body": None,
            "body_preview": None, "issue_num": None, "issue_url": None,
            "label_pills": None, "error": None, "attachment_warning": None,
            "started_at": None, "finished_at": None,
        }
        server._bulk_jobs[job_id_direct] = {
            "job_id": job_id_direct, "status": "running",
            "repo": "zealchaiwut/commander", "default_labels": [],
            "concurrency": 3, "created_at": now, "stop_requested": False,
            "has_attachments": False, "attachment_filenames": [],
            "tickets": [ticket],
        }
        server._bulk_job_queues[job_id_direct] = []

        async def run_with_mock():
            with patch("server.asyncio.create_subprocess_exec") as mock_exec:
                proc = AsyncMock()
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"NOT VALID JSON!!!{broken", b""))
                mock_exec.return_value = proc
                await server._run_single_ba_ticket(
                    job_id_direct, 0,
                    "Build a payment system",
                    "zealchaiwut/commander",
                    [],
                )

        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(run_with_mock())
        finally:
            loop.close()

        job = server._bulk_jobs.get(job_id_direct)
        ticket_result = job["tickets"][0]
        assert ticket_result["state"] == "failed"
        assert ticket_result["error"], "error field must be non-empty"
        error_lower = ticket_result["error"].lower()
        assert "json" in error_lower or "malformed" in error_lower or "parse" in error_lower, (
            f"Error message should mention JSON/malformed/parse, got: {ticket_result['error']!r}"
        )

    def test_failed_card_has_error_style_in_js(self, js):
        """The JS card renderer uses bc-card-preview--error class for failed tickets."""
        assert "bc-card-preview--error" in js, \
            "bc-card-preview--error class not used in card renderer"

    def test_failed_ticket_error_shown_in_render(self, js):
        """_bcRenderCard uses t.error as preview text for failed state."""
        fn_start = js.find("function _bcRenderCard(")
        assert fn_start != -1
        fn_end = js.find("\n}\n", fn_start)
        fn_body = js[fn_start:fn_end + 3]
        assert "t.error" in fn_body, \
            "_bcRenderCard should display t.error for failed tickets"
