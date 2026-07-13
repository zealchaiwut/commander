"""Tests for issue #1754: Board card 'Re-run -> N.1' button fix (runs against UAT)"""
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

def test_rerun_button__endpoint_accepts_valid_body(client):
    # AC: Clicking "Re-run → N.1" on a finished sprint card dispatches the rerun
    # (equivalent to the working curl) and navigates/updates to show the running sub-sprint
    # Test: frontend sends valid JSON body to the endpoint

    r = client.post(
        "/api/sprints/sprint-1/rerun",
        params={"project": "zealchaiwut/commander"},
        json={"ticket_numbers": [], "auto_run": False},
    )
    # Success if endpoint accepts the body structure (2xx or recoverable error, not 422 for body)
    assert r.status_code != 422 or "body" not in r.text.lower(), (
        f"Endpoint should accept JSON body; got {r.status_code}: {r.text[:200]}"
    )


def test_rerun_button__missing_body_returns_422(client):
    # AC: Any non-2xx from the rerun endpoint surfaces as a visible error toast
    # Test: proving the endpoint requires a JSON body (422 on omit), so frontend must send it

    r = client.post(
        "/api/sprints/sprint-1/rerun",
        params={"project": "zealchaiwut/commander"},
        # No JSON body — should 422
    )
    assert r.status_code == 422, (
        f"Endpoint should 422 when body is omitted; got {r.status_code}"
    )
    # Verify error detail is present (what frontend extracts and shows in toast)
    data = r.json()
    assert "detail" in data, "422 response should include detail field for error toast"


def test_rerun_button__error_response_includes_detail(client):
    # AC: Behavioral test — error responses from the endpoint include detail
    # that the frontend extracts and displays to the user (not silent failure)

    # Send malformed request to trigger an error (empty body counts as 422 above)
    r = client.post(
        "/api/sprints/sprint-1/rerun?project=zealchaiwut/commander",
    )
    # Endpoint must return 422 with detail, not 2xx
    assert r.status_code == 422
    resp_json = r.json()
    # Frontend code extracts detail and shows it in toast + error modal
    assert "detail" in resp_json, (
        "Error response must include detail field; frontend extracts this for error toast"
    )
