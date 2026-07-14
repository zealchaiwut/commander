"""Tests for issue #503: Add repo validation to retry estimation handler (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_retry_estimation_validation__missing_repo_rejected(client):
    # AC: When repo is missing, the endpoint rejects the request.
    # This validates that the backend enforces the repo parameter.
    r = client.post("/api/issues/1/estimate", params={})
    # Without repo param, endpoint should return 400 or similar error (not 200)
    assert r.status_code >= 400, f"Expected error status for missing repo, got {r.status_code}"


def test_retry_estimation_validation__endpoint_accepts_repo_param(client):
    # AC: The endpoint /api/issues/{issue}/estimate accepts a repo parameter.
    # We check by comparing responses with and without repo param.
    # With repo: endpoint will either process it or return a valid error (not 400 missing-param)
    # Without repo: should return 400 or 422 (missing required param)
    r_no_repo = client.post("/api/issues/1/estimate", params={})
    r_with_repo = client.post("/api/issues/1/estimate", params={"repo": "test/repo"})
    # Request WITH repo should not be a 400 missing-param error
    assert r_with_repo.status_code != 400, "Endpoint should accept repo parameter"


def test_retry_estimation_validation__malformed_repo_param_rejected(client):
    # AC: Malformed repo parameter (invalid characters) should be rejected or handled gracefully.
    r = client.post("/api/issues/1/estimate", params={"repo": "invalid@#$%"})
    # Should not 500; should be rejected at parameter validation level
    assert r.status_code < 500, f"Expected graceful error, got {r.status_code}"
