"""Tests for issue #2070: POST /api/tickets/create accepts JSON body.

AC1 - endpoint accepts application/json body with a typed model; returns 201
AC2 - multipart/form-data (existing callers) still returns 201 (regression)
AC3 - JSON path is covered by response_model: response has number (int) and url (str)
AC4 - behavioral tests per CLAUDE.md §1746 — call create_issue boundary, assert result
AC5 - live caller audit: one caller found (project.html uses FormData); multipart kept

Consumers found:
  - apps/dashboard/static/project.html:23067 — uses FormData → multipart preserved
  - No headless/script callers to /api/tickets/create identified.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure apps/dashboard is on the path (mirrors how other test files work)
_DASHBOARD = Path(__file__).parent.parent
sys.path.insert(0, str(_DASHBOARD))


def _make_mock_srv(tmp_path: Path) -> MagicMock:
    """Minimal fake server for create_ticket_from_draft.

    github_client.create_issue is the boundary under test.
    get_repo_for_operation raises so no background estimator tasks fire.
    """
    srv = MagicMock()
    srv.github_client.create_issue.return_value = (99, "https://github.com/test/repo/issues/99")
    srv.github_client.get_repo_for_operation.side_effect = RuntimeError("test: no repo")
    srv.github_client.invalidate.return_value = None
    srv._DRAFT_UPLOAD_DIR = tmp_path / "drafts"
    srv._DRAFT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    srv._ESTIMATE_ISSUE_SCRIPT = tmp_path / "estimate_issue.py"  # does not exist
    srv._ALLOWED_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".html"}
    srv._MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
    srv._MAX_BATCH_SIZE_BYTES = 50 * 1024 * 1024
    return srv


@pytest.fixture
def client(tmp_path):
    """TestClient with _server() stubbed; no real GitHub or DB calls."""
    os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
    os.environ.setdefault("DB_PATH", str(tmp_path / "test.db"))

    from server import app  # noqa: PLC0415

    mock_srv = _make_mock_srv(tmp_path)
    with patch("routers.bulk_tickets._server", return_value=mock_srv):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c, mock_srv


# ── AC1: JSON body accepted ───────────────────────────────────────────────────

class TestJsonBodyAccepted:
    def test_json_body_returns_201(self, client):
        """AC1: POST with application/json body returns 201."""
        tc, mock_srv = client
        resp = tc.post(
            "/api/tickets/create",
            json={"title": "Test JSON ticket", "body": "Body text", "project": ""},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_json_body_calls_create_issue_with_correct_title(self, client):
        """AC4: github_client.create_issue is called with the title from JSON body."""
        tc, mock_srv = client
        tc.post(
            "/api/tickets/create",
            json={"title": "My JSON Title", "body": "Some body"},
        )
        call_kwargs = mock_srv.github_client.create_issue.call_args
        assert call_kwargs is not None, "create_issue was not called"
        # title is passed as keyword argument
        actual_title = call_kwargs.kwargs.get("title") or call_kwargs.args[0]
        assert actual_title == "My JSON Title"

    def test_json_body_passes_extra_labels(self, client):
        """AC1: extra_labels from JSON body are forwarded to create_issue."""
        tc, mock_srv = client
        tc.post(
            "/api/tickets/create",
            json={"title": "Label test", "extra_labels": ["frontend", "urgent"]},
        )
        call_kwargs = mock_srv.github_client.create_issue.call_args
        labels = call_kwargs.kwargs.get("labels") or call_kwargs.args[2]
        assert "frontend" in labels
        assert "urgent" in labels
        assert "backlog" in labels  # always added

    def test_json_body_missing_title_returns_400(self, client):
        """AC1: title is required; missing → 400."""
        tc, _ = client
        resp = tc.post(
            "/api/tickets/create",
            json={"body": "No title here"},
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400 or 422 for missing title, got {resp.status_code}: {resp.text}"
        )

    def test_json_body_sprint_label_included_in_labels(self, client):
        """AC1: sprint_label from JSON body is included in the labels list."""
        tc, mock_srv = client
        tc.post(
            "/api/tickets/create",
            json={"title": "Sprint label test", "sprint_label": "sprint-42"},
        )
        call_kwargs = mock_srv.github_client.create_issue.call_args
        labels = call_kwargs.kwargs.get("labels") or call_kwargs.args[2]
        assert "sprint-42" in labels

    def test_json_body_milestone_forwarded(self, client):
        """AC1: milestone field in JSON body is forwarded to create_issue."""
        tc, mock_srv = client
        tc.post(
            "/api/tickets/create",
            json={"title": "Milestone test", "milestone": "v2.0"},
        )
        call_kwargs = mock_srv.github_client.create_issue.call_args
        ms = call_kwargs.kwargs.get("milestone")
        assert ms == "v2.0"


# ── AC3: Response model ───────────────────────────────────────────────────────

class TestJsonResponseModel:
    def test_response_has_number_and_url(self, client):
        """AC3: JSON response contains 'number' (int) and 'url' (str)."""
        tc, _ = client
        resp = tc.post(
            "/api/tickets/create",
            json={"title": "Response model test"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "number" in data, "Response must include 'number'"
        assert isinstance(data["number"], int), f"'number' must be int, got {type(data['number'])}"
        assert "url" in data, "Response must include 'url'"
        assert isinstance(data["url"], str), f"'url' must be str, got {type(data['url'])}"

    def test_response_values_match_create_issue_return(self, client):
        """AC3: number and url values come from github_client.create_issue."""
        tc, mock_srv = client
        mock_srv.github_client.create_issue.return_value = (
            42, "https://github.com/zealchaiwut/commander/issues/42"
        )
        resp = tc.post("/api/tickets/create", json={"title": "Value check"})
        data = resp.json()
        assert data["number"] == 42
        assert data["url"] == "https://github.com/zealchaiwut/commander/issues/42"


# ── AC2: Multipart / form-urlencoded regression ───────────────────────────────

class TestMultipartRegression:
    def test_form_urlencoded_still_returns_201(self, client):
        """AC2: existing form-urlencoded callers (project.html) still get 201."""
        tc, mock_srv = client
        resp = tc.post(
            "/api/tickets/create",
            data={"title": "Form test", "body": "Form body"},
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_form_urlencoded_calls_create_issue(self, client):
        """AC4: form path also exercises github_client.create_issue boundary."""
        tc, mock_srv = client
        mock_srv.github_client.create_issue.reset_mock()
        tc.post(
            "/api/tickets/create",
            data={"title": "Regression form title", "body": "Some body"},
        )
        assert mock_srv.github_client.create_issue.called, "create_issue must be called on form path"

    def test_multipart_with_file_still_returns_201(self, client):
        """AC2: multipart/form-data with file upload still returns 201."""
        tc, mock_srv = client
        # Ensure srv has the file handling attrs set up
        mock_srv.github_client.get_repo_for_operation.side_effect = None
        mock_srv.github_client.get_repo_for_operation.return_value = "zealchaiwut/commander"
        mock_srv._ensure_attachments_branch.side_effect = Exception("no git in test")
        resp = tc.post(
            "/api/tickets/create",
            data={"title": "Multipart with file"},
            files=[("files", ("test.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 50, "image/png"))],
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

    def test_form_missing_title_returns_400(self, client):
        """AC2: form path also enforces title-required validation."""
        tc, _ = client
        resp = tc.post(
            "/api/tickets/create",
            data={"body": "No title"},
        )
        assert resp.status_code == 400, (
            f"Expected 400 for missing title on form path, got {resp.status_code}"
        )
