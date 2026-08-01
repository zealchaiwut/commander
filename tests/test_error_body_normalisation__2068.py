"""Tests for issue #2068: Error bodies normalised to HTTPException {detail: …}.

AC1: dev_report.py raises HTTPException(404, detail=...) — body uses "detail" key, not "error".
AC2: sprint_crud.py raises HTTPException(409, detail=...) for a running-sprint delete — same shape.
AC4: Behavioral tests confirm 404 / 409 response body shape via TestClient.
"""
from __future__ import annotations

import re
import sys
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


# ──────────────────────────────────────────────────────────────────────────────
# AC1 + AC4: GET /api/dev-report 404 body uses "detail" key
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def dev_report_client():
    from routers.dev_report import router as dev_router
    app = FastAPI()
    app.include_router(dev_router)
    return TestClient(app, raise_server_exceptions=False)


def test_dev_report_404_uses_detail_key(dev_report_client):
    """AC1+AC4: missing artifact returns 404 with body {"detail": "..."} not {"error": "..."}."""
    with patch("routers.dev_report_service.get_dev_report_artifact", return_value=None):
        with patch("routers.dev_report_service._bkk_today", return_value="2020-01-01"):
            r = dev_report_client.get("/api/dev-report?date=2020-01-01")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body, f"Expected 'detail' key in 404 body, got: {body}"
    assert "error" not in body, f"Unexpected 'error' key in 404 body: {body}"
    assert "2020-01-01" in body["detail"]


def test_dev_report_404_detail_mentions_no_artifact(dev_report_client):
    """AC1: detail message is descriptive (mentions the date or 'No dev report')."""
    with patch("routers.dev_report_service.get_dev_report_artifact", return_value=None):
        r = dev_report_client.get("/api/dev-report?date=2099-12-31")
    assert r.status_code == 404
    body = r.json()
    assert "detail" in body
    detail_lower = body["detail"].lower()
    assert "no dev report" in detail_lower or "not found" in detail_lower or "2099-12-31" in body["detail"]


# ──────────────────────────────────────────────────────────────────────────────
# AC2 + AC4: DELETE /api/sprints/{label} 409 body uses "detail" key
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def sprint_crud_client():
    from routers.sprint_crud import router as crud_router
    app = FastAPI()
    app.include_router(crud_router)
    return TestClient(app, raise_server_exceptions=False)


def test_sprint_delete_running_409_uses_detail_key(sprint_crud_client):
    """AC2+AC4: DELETE running sprint returns 409 with {"detail": "..."} not {"error": "..."}."""
    mock_srv = MagicMock()
    mock_srv._SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")
    mock_srv._is_sprint_running.return_value = True
    mock_srv._project_root_path.return_value = "/fake/path"

    with patch("routers.sprint_crud._server", return_value=mock_srv):
        r = sprint_crud_client.delete("/api/sprints/sprint-42?project=zealchaiwut/commander")
    assert r.status_code == 409
    body = r.json()
    assert "detail" in body, f"Expected 'detail' key in 409 body, got: {body}"
    assert "error" not in body, f"Unexpected 'error' key in 409 body: {body}"
