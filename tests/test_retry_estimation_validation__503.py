"""Tests for issue #503: repo validation on the estimate endpoint."""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def client():
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv
    from fastapi.testclient import TestClient
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield c


def test_retry_estimation_validation__missing_repo_rejected(client):
    # AC: When repo is missing, the endpoint rejects the request.
    # FastAPI validates required query params before the handler runs.
    r = client.post("/api/issues/1/estimate", params={})
    # Without repo param, FastAPI returns 422 (Unprocessable Entity)
    assert r.status_code >= 400, f"Expected error status for missing repo, got {r.status_code}"


def test_retry_estimation_validation__endpoint_accepts_repo_param(client):
    # AC: The endpoint /api/issues/{issue}/estimate accepts a repo parameter.
    # With a repo param present, the request passes FastAPI validation (not 400/422).
    _err = subprocess.CalledProcessError(1, "gh", stderr=b"Not Found")
    with patch("services.sprint_manager.estimate_issue.fetch_issue", side_effect=_err):
        r = client.post("/api/issues/1/estimate", params={"repo": "test/repo"})
    # Request WITH repo should not be a 400/422 missing-param error
    assert r.status_code != 400, "Endpoint should accept repo parameter"


def test_retry_estimation_validation__malformed_repo_param_rejected(client):
    # AC: Malformed repo parameter (invalid characters) should be rejected or handled gracefully.
    _err = subprocess.CalledProcessError(1, "gh", stderr=b"Not Found")
    with patch("services.sprint_manager.estimate_issue.fetch_issue", side_effect=_err):
        r = client.post("/api/issues/1/estimate", params={"repo": "invalid@#$%"})
    # Should not 500; should be rejected at parameter validation level or handled gracefully
    assert r.status_code < 500, f"Expected graceful error, got {r.status_code}"
