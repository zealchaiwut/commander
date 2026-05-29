"""Tests for issue #205: Support Attachments in Bulk Issue Create.

AC items:
  AC1 - Bulk create form exposes a file attachment input (PNG, PDF, HTML)
  AC2 - One or more files can be uploaded before submitting
  AC3 - Every issue in the batch receives all uploaded attachments
  AC4 - Attachment validation (type, size) behaves identically to single-issue
  AC5 - Attachment failure surfaces per-issue without blocking the batch
  AC6 - UI confirms queued files before submission
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


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
    """TestClient with projects and github stubbed."""
    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))

    import server
    import projects as projects_module
    import github_client as gc

    fake_project = {"repo": "zealchaiwut/commander", "name": "Commander"}
    monkeypatch.setattr(projects_module, "load_projects", lambda: [fake_project])
    monkeypatch.setattr("server.projects_module.load_projects", lambda: [fake_project])
    monkeypatch.setattr(
        gc, "list_labels",
        lambda repo_name=None: [{"name": "enhancement"}, {"name": "backlog"}],
    )

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


def _post_bulk(client, prompts, *, files=None, concurrency=3):
    """Post to /api/tickets/bulk with multipart form-data."""
    data = {
        "repo": "zealchaiwut/commander",
        "prompts": json.dumps(prompts),
        "concurrency": str(concurrency),
    }
    return client.post("/api/tickets/bulk", data=data, files=files or [])


# ---------------------------------------------------------------------------
# AC1: File attachment input in the bulk create form
# ---------------------------------------------------------------------------

class TestBulkAttachInputHtml:
    def test_file_input_present_in_bc_modal(self, html):
        """AC1: File input element is inside the bulk create modal."""
        modal_start = html.find('id="bc-modal"')
        assert modal_start != -1
        modal_block = html[modal_start:modal_start + 10000]
        assert 'id="bc-file-input"' in modal_block, \
            "bc-file-input not found inside bc-modal"

    def test_file_input_accepts_multiple(self, html):
        """AC1: File input has the multiple attribute."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 10000]
        assert 'multiple' in modal_block

    def test_file_input_accept_attribute_includes_png(self, html):
        """AC1: accept attribute includes .png."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 10000]
        assert '.png' in modal_block

    def test_file_input_accept_attribute_includes_pdf(self, html):
        """AC1: accept attribute includes .pdf."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 10000]
        assert '.pdf' in modal_block

    def test_file_input_accept_attribute_includes_html(self, html):
        """AC1: accept attribute includes .html."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 10000]
        assert '.html' in modal_block

    def test_dropzone_present(self, html):
        """AC1: Dropzone element is present in the bulk modal."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 10000]
        assert 'id="bc-dropzone"' in modal_block


# ---------------------------------------------------------------------------
# AC6: UI confirms queued files before submission
# ---------------------------------------------------------------------------

class TestBulkAttachQueueHtml:
    def test_file_queue_container_present(self, html):
        """AC6: Container for queued file list is present in bc-step1."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 10000]
        assert 'id="bc-file-queue"' in modal_block

    def test_file_error_element_present(self, html):
        """AC6: Error display element is present in bulk modal."""
        modal_start = html.find('id="bc-modal"')
        modal_block = html[modal_start:modal_start + 10000]
        assert 'id="bc-file-error"' in modal_block


# ---------------------------------------------------------------------------
# AC2 & JS: File pick/add/remove functions
# ---------------------------------------------------------------------------

class TestBulkAttachJS:
    def test_bc_files_variable_declared(self, js):
        """AC2: _bcFiles array is declared."""
        assert '_bcFiles' in js

    def test_bc_pick_files_function(self, js):
        """AC2: _bcPickFiles function is defined."""
        assert 'function _bcPickFiles' in js

    def test_bc_on_file_input_function(self, js):
        """AC2: _bcOnFileInput function is defined."""
        assert 'function _bcOnFileInput' in js

    def test_bc_on_drop_function(self, js):
        """AC2: _bcOnDrop function is defined."""
        assert 'function _bcOnDrop' in js

    def test_bc_add_files_function(self, js):
        """AC2: _bcAddFiles function is defined."""
        assert 'function _bcAddFiles' in js

    def test_bc_remove_file_function(self, js):
        """AC2: _bcRemoveFile function is defined."""
        assert 'function _bcRemoveFile' in js

    def test_bc_render_file_queue_function(self, js):
        """AC6: _bcRenderFileQueue function is defined."""
        assert 'function _bcRenderFileQueue' in js

    def test_bc_run_all_uses_form_data(self, js):
        """AC2: bcRunAll uses FormData to submit (required for file uploads)."""
        assert 'FormData' in js
        assert "formData.append('files'" in js or 'formData.append("files"' in js

    def test_bc_allowed_exts_constant(self, js):
        """AC4: BC_ALLOWED_EXTS constant is defined with extension validation."""
        assert 'BC_ALLOWED_EXTS' in js

    def test_bc_25mb_size_check(self, js):
        """AC4: 25 MB per-file size check is present in bulk create JS."""
        # Use the same size constant as single-issue create
        assert '25 * 1024 * 1024' in js

    def test_bc_files_reset_on_modal_open(self, js):
        """AC2: _bcFiles is reset when the modal opens."""
        # openBulkCreateModal should reset _bcFiles
        open_fn_start = js.find('function openBulkCreateModal')
        assert open_fn_start != -1
        # Find the end of this function (next top-level function)
        next_fn = js.find('\nfunction ', open_fn_start + 1)
        fn_body = js[open_fn_start:next_fn]
        assert '_bcFiles' in fn_body and '= []' in fn_body

    def test_attachment_warning_rendered_in_card(self, js):
        """AC5: attachment_warning field is rendered in bc card UI."""
        assert 'attachment_warning' in js


# ---------------------------------------------------------------------------
# AC4: Server-side validation — disallowed extension
# ---------------------------------------------------------------------------

class TestBulkAttachValidation:
    def test_disallowed_extension_returns_422(self, client):
        """AC4: Uploading a .exe file returns 422."""
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[("files", ("malware.exe", b"MZ\x90\x00", "application/octet-stream"))],
            )
        assert res.status_code == 422
        assert "disallowed extension" in res.text.lower() or "exe" in res.text.lower()

    def test_allowed_png_accepted(self, client):
        """AC4: PNG file is accepted."""
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[("files", ("screenshot.png", png_bytes, "image/png"))],
            )
        assert res.status_code == 202

    def test_pdf_accepted(self, client):
        """AC (issue #374): PDF files are now accepted by bulk create."""
        pdf_bytes = b"%PDF-1.4 " + b"\x00" * 50
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[("files", ("spec.pdf", pdf_bytes, "application/pdf"))],
            )
        assert res.status_code == 202

    def test_html_accepted(self, client):
        """AC (issue #374): HTML files are now accepted by bulk create."""
        html_bytes = b"<html><body>Test</body></html>"
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[("files", ("page.html", html_bytes, "text/html"))],
            )
        assert res.status_code == 202

    def test_md_accepted(self, client):
        """AC (issue #374): Markdown files are accepted by bulk create."""
        md_bytes = b"# Feature\n\nDetails here.\n"
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[("files", ("spec.md", md_bytes, "text/markdown"))],
            )
        assert res.status_code == 202

    def test_oversized_file_returns_422(self, client):
        """AC4: File exceeding 25 MB returns 422."""
        big_bytes = b"\x00" * (26 * 1024 * 1024)
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[("files", ("huge.png", big_bytes, "image/png"))],
            )
        assert res.status_code == 422
        assert "25 mb" in res.text.lower() or "25" in res.text

    def test_no_files_still_works(self, client):
        """AC2: Job without attachments still creates successfully."""
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
            )
        assert res.status_code == 202

    def test_job_has_attachments_flag_when_files_uploaded(self, client):
        """AC3: Job stores has_attachments=True when files provided."""
        import server
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[("files", ("test.png", png_bytes, "image/png"))],
            )
        assert res.status_code == 202
        job_id = res.json()["job_id"]
        job = server._bulk_jobs.get(job_id)
        assert job is not None
        assert job.get("has_attachments") is True

    def test_job_stores_attachment_filenames(self, client):
        """AC3: Job records original filenames for all uploaded attachments."""
        import server
        png_bytes = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        jpg_bytes = b'\xff\xd8\xff' + b'\x00' * 50
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
                files=[
                    ("files", ("a.png", png_bytes, "image/png")),
                    ("files", ("b.jpg", jpg_bytes, "image/jpeg")),
                ],
            )
        assert res.status_code == 202
        job_id = res.json()["job_id"]
        job = server._bulk_jobs.get(job_id)
        assert "a.png" in job.get("attachment_filenames", [])
        assert "b.jpg" in job.get("attachment_filenames", [])

    def test_no_files_job_has_no_attachments_flag(self, client):
        """AC2: Job without files has has_attachments=False."""
        import server
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test prompt"]),
                    "concurrency": "3",
                },
            )
        assert res.status_code == 202
        job_id = res.json()["job_id"]
        job = server._bulk_jobs.get(job_id)
        assert job.get("has_attachments") is False


# ---------------------------------------------------------------------------
# AC5: Attachment errors surface per-issue
# ---------------------------------------------------------------------------

class TestBulkAttachPerIssueError:
    def test_attachment_warning_field_in_ticket(self, client):
        """AC5: Ticket object has attachment_warning field."""
        import server
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test"]),
                    "concurrency": "3",
                },
            )
        job_id = res.json()["job_id"]
        for t in server._bulk_jobs[job_id]["tickets"]:
            assert "attachment_warning" in t

    def test_ticket_warning_exposed_in_get_job(self, client):
        """AC5: attachment_warning is exposed via GET /api/tickets/bulk/{job_id}."""
        with patch("asyncio.create_task"):
            res = client.post(
                "/api/tickets/bulk",
                data={
                    "repo": "zealchaiwut/commander",
                    "prompts": json.dumps(["test"]),
                    "concurrency": "3",
                },
            )
        job_id = res.json()["job_id"]
        get_res = client.get(f"/api/tickets/bulk/{job_id}")
        for t in get_res.json()["tickets"]:
            assert "attachment_warning" in t
