"""Tests for issue #1971: force query parameter behavior on GET /api/dev-report.

Verifies that the `force` parameter behaves exactly as documented in
docs/features/api.md — boolean, optional, defaults to false.
"""
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

def test_force_defaults_false__missing_artifact_returns_404(client):
    """AC: `force` defaults to false; omitting it for a non-existent date yields 404."""
    # A date so far in the past that no artifact will ever exist for it.
    r = client.get("/api/dev-report?date=2019-01-15")
    assert r.status_code == 404
    body = r.json()
    assert "error" in body


def test_force_1_regenerates_and_returns_200(client):
    """AC: force=1 (boolean coercion) triggers inline regeneration and returns 200."""
    # Use force=1 to generate on-demand — endpoint must return 200 with a valid payload.
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    payload = r.json()
    assert "for_date" in payload


def test_force_true_regenerates_and_returns_200(client):
    """AC: force=true (FastAPI boolean coercion) also triggers regeneration."""
    r = client.get("/api/dev-report?force=true")
    assert r.status_code == 200
    payload = r.json()
    assert "for_date" in payload


def test_force_false_explicit_behaves_like_omitted(client):
    """AC: force=false (explicit) behaves identically to omitting force — no regeneration."""
    past_date = "2019-01-16"
    # Without force: 404 for a non-existent artifact date
    r_no_force = client.get(f"/api/dev-report?date={past_date}")
    assert r_no_force.status_code == 404

    # With explicit force=false: same 404 behavior (no regeneration)
    r_false = client.get(f"/api/dev-report?date={past_date}&force=false")
    assert r_false.status_code == 404


def test_force_persists_artifact_for_subsequent_non_force_request(client):
    """AC: force regenerates AND persists; a subsequent non-force request returns the stored result."""
    test_date = "2026-07-14"
    # Regenerate and store
    r_force = client.get(f"/api/dev-report?date={test_date}&force=1")
    assert r_force.status_code == 200
    generated = r_force.json()

    # Retrieve without force — must use the persisted artifact
    r_cached = client.get(f"/api/dev-report?date={test_date}")
    assert r_cached.status_code == 200
    assert r_cached.json()["for_date"] == generated["for_date"]
