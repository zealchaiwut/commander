"""Tests for issue #374: Improve Bulk Create UX — Labels, Preview, and Attachments.

Covers all acceptance criteria:
- Label input hidden in step 1, per-ticket label input in step 2
- Review screen with checkboxes before GitHub API call
- Post-to-GitHub button disabled when no tickets selected
- Per-ticket recreate (redraft) updates only that card
- Attachment uploader accepts .md, .html, .pdf
- Unsupported file types show inline error
- Existing bulk-create functionality not regressed
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient


DASHBOARD_DIR = Path(__file__).parent.parent
INDEX_HTML = DASHBOARD_DIR / "static" / "index.html"
PROJECT_HTML = DASHBOARD_DIR / "static" / "project.html"
APP_JS = DASHBOARD_DIR / "static" / "app.js"


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX_HTML.read_text()


@pytest.fixture(scope="module")
def project_html() -> str:
    return PROJECT_HTML.read_text()


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))

    import server
    import projects as projects_module
    import github_client as gc

    fake_project = {"repo": "zealchaiwut/commander", "name": "Commander"}
    monkeypatch.setattr(projects_module, "load_projects", lambda: [fake_project])
    monkeypatch.setattr("server.projects_module.load_projects", lambda: [fake_project])
    monkeypatch.setattr(
        gc, "list_labels",
        lambda repo_name=None: [
            {"name": "enhancement", "color": "84b6eb"},
            {"name": "backlog", "color": "e4e669"},
            {"name": "bug", "color": "d73a4a"},
        ],
    )

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


def _bulk_post(client, *, repo="zealchaiwut/commander", prompts=None, concurrency=3,
               default_labels=None):
    data = {
        "repo": repo,
        "prompts": json.dumps(prompts or ["test prompt"]),
        "concurrency": str(concurrency),
    }
    if default_labels is not None:
        data["default_labels"] = json.dumps(default_labels)
    with patch("asyncio.create_task"):
        return client.post("/api/tickets/bulk", data=data)


# ---------------------------------------------------------------------------
# AC1: Label input hidden in step 1
# ---------------------------------------------------------------------------

class TestLabelFieldInStep1:
    def test_bc_default_labels_element_exists(self, index_html):
        """#bc-default-labels element still exists in the HTML (for JS compatibility)."""
        assert 'id="bc-default-labels"' in index_html

    def test_bc_default_labels_hidden_in_step1(self, index_html):
        """The labels field wrapper is hidden via display:none in step 1."""
        step1_start = index_html.find('id="bc-step1"')
        step1_end = index_html.find('id="bc-step2"')
        step1_region = index_html[step1_start:step1_end]
        # The labels field wrapper should have display:none
        assert "display:none" in step1_region or "display: none" in step1_region, \
            "Labels field must be hidden (display:none) in step 1"
        # And the hidden element must contain bc-default-labels
        labels_pos = step1_region.find('id="bc-default-labels"')
        assert labels_pos != -1, "bc-default-labels must still exist inside step 1"

    def test_bc_default_labels_hidden_in_project_html(self, project_html):
        """The labels field wrapper is hidden in project.html step 1 too."""
        step1_start = project_html.find('id="bc-step1"')
        step1_end = project_html.find('id="bc-step2"')
        step1_region = project_html[step1_start:step1_end]
        assert "display:none" in step1_region or "display: none" in step1_region, \
            "Labels field must be hidden in project.html step 1"


# ---------------------------------------------------------------------------
# AC2: Per-ticket label input in step 2
# ---------------------------------------------------------------------------

class TestPerTicketLabelInput:
    def test_draft_ready_card_rendered_with_label_input(self, app_js):
        """_bcRenderCard renders a label input for draft_ready tickets."""
        # Find draft_ready branch in _bcRenderCard
        card_fn_start = app_js.find("function _bcRenderCard(")
        assert card_fn_start != -1, "_bcRenderCard not found"
        fn_body = app_js[card_fn_start: card_fn_start + 5000]
        assert "draft_ready" in fn_body, "draft_ready state not handled in _bcRenderCard"
        assert "bc-ticket-label" in fn_body or "bc-ticket-labels" in fn_body, \
            "Per-ticket label input must be rendered in draft_ready card"

    def test_per_ticket_label_input_has_index(self, app_js):
        """Per-ticket label input id includes the ticket index."""
        card_fn_start = app_js.find("function _bcRenderCard(")
        fn_body = app_js[card_fn_start: card_fn_start + 5000]
        # Check for dynamic id like bc-ticket-labels-${t.index}
        assert "bc-ticket-labels-" in fn_body or "bc-ticket-label-" in fn_body, \
            "Per-ticket label input must have index-based id"

    def test_default_labels_prepopulated(self, app_js):
        """Per-ticket label input is pre-populated from _bcLastInput or default."""
        card_fn_start = app_js.find("function _bcRenderCard(")
        fn_body = app_js[card_fn_start: card_fn_start + 5000]
        assert "_bcLastInput" in fn_body or "enhancement" in fn_body, \
            "Per-ticket label must be pre-populated with default value"


# ---------------------------------------------------------------------------
# AC3 & AC4: Review screen before GitHub API call
# ---------------------------------------------------------------------------

class TestReviewScreen:
    def test_review_overlay_exists_in_index_html(self, index_html):
        """Review overlay element exists in index.html step 2."""
        assert 'id="bc-review-overlay"' in index_html, \
            "bc-review-overlay must exist in index.html"

    def test_review_overlay_exists_in_project_html(self, project_html):
        """Review overlay element exists in project.html step 2."""
        assert 'id="bc-review-overlay"' in project_html, \
            "bc-review-overlay must exist in project.html"

    def test_review_overlay_hidden_by_default(self, index_html):
        """Review overlay has hidden class by default."""
        overlay_pos = index_html.find('id="bc-review-overlay"')
        snippet = index_html[max(0, overlay_pos - 100): overlay_pos + 200]
        assert "hidden" in snippet, "Review overlay must be hidden by default"

    def test_review_list_container_exists(self, index_html):
        """Review list container exists inside the overlay."""
        assert 'id="bc-review-list"' in index_html, \
            "bc-review-list container must exist"

    def test_post_to_github_btn_exists(self, index_html):
        """Post to GitHub button exists in the review overlay."""
        assert 'id="bc-post-github-btn"' in index_html, \
            "bc-post-github-btn must exist"

    def test_post_to_github_btn_disabled_by_default(self, index_html):
        """Post to GitHub button is disabled by default."""
        btn_pos = index_html.find('id="bc-post-github-btn"')
        snippet = index_html[max(0, btn_pos - 100): btn_pos + 200]
        assert "disabled" in snippet, "Post to GitHub button must be disabled by default"

    def test_review_post_btn_exists(self, index_html):
        """Review & Post button exists in step 2 footer."""
        assert 'id="bc-review-post-btn"' in index_html, \
            "bc-review-post-btn must exist in step 2 footer"

    def test_bcReviewAndPost_function_exists(self, app_js):
        """bcReviewAndPost function is defined in app.js."""
        assert "function bcReviewAndPost()" in app_js, \
            "bcReviewAndPost() function not found in app.js"

    def test_bcPostSelected_function_exists(self, app_js):
        """bcPostSelected function is defined in app.js."""
        assert "function bcPostSelected()" in app_js or "async function bcPostSelected()" in app_js, \
            "bcPostSelected() function not found in app.js"

    def test_bcCancelReview_function_exists(self, app_js):
        """bcCancelReview function is defined in app.js."""
        assert "function bcCancelReview()" in app_js, \
            "bcCancelReview() function not found in app.js"

    def test_post_button_disabled_check(self, app_js):
        """Post button is disabled when no checkboxes are checked."""
        assert "_bcUpdatePostBtn" in app_js, "_bcUpdatePostBtn not found"
        fn_start = app_js.find("function _bcUpdatePostBtn()")
        fn_body = app_js[fn_start: fn_start + 500]
        assert "disabled" in fn_body, \
            "_bcUpdatePostBtn must set disabled based on checkbox state"


# ---------------------------------------------------------------------------
# AC5: Per-ticket recreate
# ---------------------------------------------------------------------------

class TestPerTicketRecreate:
    def test_bcRedraftTicket_function_exists(self, app_js):
        """bcRedraftTicket function is defined in app.js."""
        assert "function bcRedraftTicket(" in app_js or "async function bcRedraftTicket(" in app_js, \
            "bcRedraftTicket() function not found in app.js"

    def test_draft_ready_card_has_recreate_button(self, app_js):
        """draft_ready card includes a per-ticket recreate button."""
        card_fn_start = app_js.find("function _bcRenderCard(")
        fn_body = app_js[card_fn_start: card_fn_start + 8000]
        draft_pos = fn_body.find("draft_ready")
        assert draft_pos != -1, "draft_ready state not found in _bcRenderCard"
        # The draft_ready branch uses a return statement — capture entire template literal
        draft_section = fn_body[draft_pos: draft_pos + 2000]
        assert "bcRedraftTicket" in draft_section, \
            "Per-ticket recreate button must call bcRedraftTicket in draft_ready card"

    def test_redraft_endpoint_exists(self, client):
        """POST /api/tickets/bulk/{job_id}/redraft endpoint returns 404 for unknown job."""
        res = client.post(
            "/api/tickets/bulk/nonexistent-job/redraft",
            json={"index": 0},
        )
        assert res.status_code == 404

    def test_redraft_returns_ok_for_valid_draft_ready(self, client, monkeypatch):
        """Redraft endpoint accepts draft_ready ticket and starts re-draft."""
        import server

        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id,
            "status": "drafts_ready",
            "repo": "zealchaiwut/commander",
            "default_labels": ["enhancement"],
            "concurrency": 1,
            "has_attachments": False,
            "image_url_map": {},
            "created_at": "2024-01-01T00:00:00+00:00",
            "tickets": [
                {
                    "index": 0, "prompt": "test prompt", "state": "draft_ready",
                    "title": "Test ticket", "body": "body", "body_preview": "body",
                    "issue_num": None, "issue_url": None, "label_pills": None,
                    "error": None, "started_at": None, "finished_at": None,
                }
            ],
        }
        server._bulk_jobs[job_id] = job

        with patch("asyncio.create_task"):
            res = client.post(
                f"/api/tickets/bulk/{job_id}/redraft",
                json={"index": 0},
            )
        assert res.status_code == 200
        assert res.json().get("ok") is True
        # Cleanup
        server._bulk_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC6 & AC7: Attachment file types and inline errors
# ---------------------------------------------------------------------------

class TestAttachmentTypes:
    def test_md_file_accepted(self, client):
        """AC6: .md files are accepted by bulk create."""
        md_bytes = b"# Feature spec\n\nDetails here.\n"
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
                files=[("files", ("spec.md", md_bytes, "text/markdown"))],
            )
        assert res.status_code == 202

    def test_html_file_accepted(self, client):
        """AC6: .html files are accepted by bulk create."""
        html_bytes = b"<html><body>Test</body></html>"
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
                files=[("files", ("page.html", html_bytes, "text/html"))],
            )
        assert res.status_code == 202

    def test_pdf_file_accepted(self, client):
        """AC6: .pdf files are accepted by bulk create."""
        pdf_bytes = b"%PDF-1.4 " + b"\x00" * 50
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
                files=[("files", ("spec.pdf", pdf_bytes, "application/pdf"))],
            )
        assert res.status_code == 202

    def test_png_file_still_accepted(self, client):
        """AC8: Previously supported .png files still work."""
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
                files=[("files", ("screenshot.png", png_bytes, "image/png"))],
            )
        assert res.status_code == 202

    def test_exe_file_rejected(self, client):
        """AC7: Unsupported file types return 422 with inline error message."""
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
                files=[("files", ("malware.exe", b"MZ", "application/octet-stream"))],
            )
        assert res.status_code == 422
        body = res.text.lower()
        # Error message should mention accepted formats
        assert "accepted" in body or "unsupported" in body or ".exe" in body, \
            "Error message must describe accepted file types"

    def test_error_message_lists_accepted_formats(self, client):
        """AC7: Unsupported file type error message lists accepted formats."""
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
                files=[("files", ("bad.zip2", b"PK", "application/octet-stream"))],
            )
        assert res.status_code == 422
        # The error should mention at least one accepted type
        body = res.text.lower()
        assert "pdf" in body or "png" in body or "md" in body or "accepted" in body, \
            "Error message must list accepted file formats"

    def test_htm_file_accepted(self, client):
        """AC6: .htm extension is also accepted."""
        htm_bytes = b"<html><body>Test</body></html>"
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
                files=[("files", ("page.htm", htm_bytes, "text/html"))],
            )
        assert res.status_code == 202

    def test_accept_attribute_includes_md_html_pdf(self, project_html):
        """project.html file input accept attribute includes .md, .html, .pdf."""
        file_input_pos = project_html.find('id="bc-file-input"')
        snippet = project_html[max(0, file_input_pos - 50): file_input_pos + 400]
        assert ".md" in snippet, "File input should accept .md"
        assert ".html" in snippet, "File input should accept .html"
        assert ".pdf" in snippet, "File input should accept .pdf"

    def test_inline_error_shown_for_unsupported_type(self, app_js):
        """app.js _bcAddFiles shows inline error for unsupported file types."""
        add_files_start = app_js.find("function _bcAddFiles(")
        fn_body = app_js[add_files_start: add_files_start + 800]
        assert "bc-file-error" in fn_body, "Error element must be referenced in _bcAddFiles"
        assert "unsupported" in fn_body.lower() or "disallowed" in fn_body.lower() or \
               "accepted" in fn_body.lower(), \
            "Error message must describe unsupported type"


# ---------------------------------------------------------------------------
# AC3: /post-selected API endpoint
# ---------------------------------------------------------------------------

class TestPostSelectedEndpoint:
    def test_post_selected_404_for_unknown_job(self, client):
        """POST /api/tickets/bulk/{job_id}/post-selected returns 404 for unknown job."""
        res = client.post(
            "/api/tickets/bulk/nonexistent/post-selected",
            json={"tickets": [{"index": 0, "labels": ["bug"]}]},
        )
        assert res.status_code == 404

    def test_post_selected_422_for_no_tickets(self, client, monkeypatch):
        """POST /post-selected with empty tickets list returns 422."""
        import server
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id, "status": "drafts_ready",
            "repo": "zealchaiwut/commander", "default_labels": [],
            "concurrency": 1, "has_attachments": False, "image_url_map": {},
            "created_at": "2024-01-01T00:00:00+00:00",
            "tickets": [],
        }
        server._bulk_jobs[job_id] = job
        res = client.post(
            f"/api/tickets/bulk/{job_id}/post-selected",
            json={"tickets": []},
        )
        assert res.status_code == 422
        server._bulk_jobs.pop(job_id, None)

    def test_post_selected_422_for_non_draft_ready_ticket(self, client, monkeypatch):
        """POST /post-selected returns 422 if a ticket is not in draft_ready state."""
        import server
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id, "status": "drafts_ready",
            "repo": "zealchaiwut/commander", "default_labels": [],
            "concurrency": 1, "has_attachments": False, "image_url_map": {},
            "created_at": "2024-01-01T00:00:00+00:00",
            "tickets": [
                {
                    "index": 0, "prompt": "test", "state": "failed",
                    "title": None, "body": None, "body_preview": None,
                    "issue_num": None, "issue_url": None, "label_pills": None,
                    "error": "some error", "started_at": None, "finished_at": None,
                }
            ],
        }
        server._bulk_jobs[job_id] = job
        res = client.post(
            f"/api/tickets/bulk/{job_id}/post-selected",
            json={"tickets": [{"index": 0, "labels": ["bug"]}]},
        )
        assert res.status_code == 422
        assert "draft_ready" in res.text.lower()
        server._bulk_jobs.pop(job_id, None)

    def test_post_selected_starts_job_for_draft_ready_tickets(self, client, monkeypatch):
        """POST /post-selected returns 200 and sets job status to running for valid tickets."""
        import server
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id, "status": "drafts_ready",
            "repo": "zealchaiwut/commander", "default_labels": [],
            "concurrency": 1, "has_attachments": False, "image_url_map": {},
            "created_at": "2024-01-01T00:00:00+00:00",
            "tickets": [
                {
                    "index": 0, "prompt": "test", "state": "draft_ready",
                    "title": "Test", "body": "body", "body_preview": "body",
                    "issue_num": None, "issue_url": None, "label_pills": None,
                    "error": None, "started_at": None, "finished_at": None,
                }
            ],
        }
        server._bulk_jobs[job_id] = job
        with patch("asyncio.create_task"):
            res = client.post(
                f"/api/tickets/bulk/{job_id}/post-selected",
                json={"tickets": [{"index": 0, "labels": ["enhancement"]}]},
            )
        assert res.status_code == 200
        assert res.json().get("ok") is True
        server._bulk_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC3: SSE handles job_drafts_ready event
# ---------------------------------------------------------------------------

class TestDraftsReadyEvent:
    def test_job_drafts_ready_event_handled_in_js(self, app_js):
        """app.js handles 'job_drafts_ready' SSE event."""
        assert "job_drafts_ready" in app_js, \
            "app.js must handle the job_drafts_ready SSE event"

    def test_drafts_ready_status_renders_review_post_btn(self, app_js):
        """_bcRenderStep2 shows Review & Post button when status is drafts_ready."""
        render_fn = app_js.find("function _bcRenderStep2()")
        fn_body = app_js[render_fn: render_fn + 5000]
        assert "drafts_ready" in fn_body, \
            "_bcRenderStep2 must handle drafts_ready status"
        assert "bc-review-post-btn" in fn_body, \
            "_bcRenderStep2 must reference bc-review-post-btn when drafts_ready"

    def test_flusher_does_not_create_github_issues(self):
        """_bulk_flusher no longer calls github_client.create_issue directly."""
        server_py = (DASHBOARD_DIR / "server.py").read_text()
        # Find the _bulk_flusher function
        flusher_start = server_py.find("async def _bulk_flusher(")
        flusher_end = server_py.find("\nasync def _bulk_worker(", flusher_start)
        flusher_body = server_py[flusher_start:flusher_end]
        # Flusher should NOT call create_issue anymore
        assert "create_issue" not in flusher_body, \
            "_bulk_flusher must not call github_client.create_issue (issue #374)"

    def test_post_selected_endpoint_calls_create_issue(self):
        """bulk_post_selected endpoint does call github_client.create_issue."""
        server_py = (DASHBOARD_DIR / "server.py").read_text()
        # Find the bulk_post_selected function
        fn_start = server_py.find("async def bulk_post_selected(")
        fn_end = server_py.find("\nasync def ", fn_start + 1)
        fn_body = server_py[fn_start:fn_end]
        assert "create_issue" in fn_body, \
            "bulk_post_selected must call github_client.create_issue"


# ---------------------------------------------------------------------------
# AC8: Existing functionality not regressed
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_basic_job_creation_still_works(self, client):
        """Existing POST /api/tickets/bulk still creates a job."""
        res = _bulk_post(client, prompts=["build a feature"])
        assert res.status_code == 202
        assert "job_id" in res.json()

    def test_job_status_returns_job(self, client):
        """GET /api/tickets/bulk/{job_id} still returns job state."""
        res = _bulk_post(client, prompts=["build a feature"])
        job_id = res.json()["job_id"]
        status_res = client.get(f"/api/tickets/bulk/{job_id}")
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["job_id"] == job_id
        assert "tickets" in data

    def test_stop_endpoint_still_works(self, client):
        """POST /api/tickets/bulk/{job_id}/stop still returns 200."""
        res = _bulk_post(client, prompts=["test"])
        job_id = res.json()["job_id"]
        stop_res = client.post(f"/api/tickets/bulk/{job_id}/stop")
        assert stop_res.status_code == 200

    def test_skip_endpoint_still_works(self, client):
        """POST /api/tickets/bulk/{job_id}/skip still returns 200."""
        res = _bulk_post(client, prompts=["test"])
        job_id = res.json()["job_id"]
        skip_res = client.post(f"/api/tickets/bulk/{job_id}/skip", json={"index": 0})
        assert skip_res.status_code == 200

    def test_retry_endpoint_still_works(self, client):
        """POST /api/tickets/bulk/{job_id}/retry returns 200 (or 404 for unknown state)."""
        res = _bulk_post(client, prompts=["test"])
        job_id = res.json()["job_id"]
        retry_res = client.post(f"/api/tickets/bulk/{job_id}/retry", json={"index": 0})
        # Ticket is pending (not failed), so returns ok with current state
        assert retry_res.status_code == 200
