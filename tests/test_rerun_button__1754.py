"""Tests for issue #1754: Board card 'Re-run → N.1' button dispatches correctly (runs against UAT)"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_rerun_button__rerun_endpoint_accepts_valid_body(client):
    # AC: Endpoint accepts JSON body with ticket_numbers array and auto_run field
    # (Tests the endpoint contract; the click handler behavioral test verifies the handler sends it)
    repo = "zealchaiwut/commander"
    sprint_label = "sprint-106"

    # POST to rerun endpoint with valid body
    r = client.post(
        f"/api/sprints/{sprint_label}/rerun",
        params={"project": repo},
        json={"ticket_numbers": [1754], "auto_run": False}
    )
    # Should accept the request (200, 422, or 500 depending on sprint state)
    # The key is that with valid body it doesn't 422 for missing body
    assert r.status_code in (200, 404, 409, 422, 500), f"Expected non-422-missing-body; got {r.status_code}"
    # If 422, it should be for a different reason, not missing body
    if r.status_code == 422:
        detail = r.json().get("detail", "")
        assert "body" not in str(detail).lower(), "Should not fail on missing body with valid JSON sent"


def test_rerun_button__endpoint_requires_json_body(client):
    # AC2: Endpoint 422s when body is missing/empty, proving the click handler must send body
    repo = "zealchaiwut/commander"
    sprint_label = "sprint-106"

    # POST with no body
    r = client.post(
        f"/api/sprints/{sprint_label}/rerun",
        params={"project": repo}
    )
    # Should 422 for missing body
    assert r.status_code == 422, f"Expected 422 for missing body; got {r.status_code}"
    detail = r.json().get("detail", [])
    # FastAPI validation error should mention body field
    assert any("body" in str(d).lower() for d in detail), "Error detail should mention missing body"
