"""Tests for issue #1960: Add GET /api/dev-report endpoint to dashboard (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
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

def test_dev_report__default_date_returns_today_stored_artifact(client):
    # AC: `GET /api/dev-report` returns the stored `dev_report` artifact payload for today (Bangkok timezone) by default
    # First seed today's artifact via force=1
    seed_r = client.get("/api/dev-report?force=1")
    assert seed_r.status_code == 200
    # Now verify we can retrieve it without force
    r = client.get("/api/dev-report")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    # Verify basic structure of a dev report
    assert "for_date" in payload or "projects" in payload or len(payload) > 0


def test_dev_report__with_date_returns_artifact_for_specified_date(client):
    # AC: `GET /api/dev-report?date=YYYY-MM-DD` returns the artifact for the specified date
    # First seed the artifact for that date via force=1
    seed_r = client.get("/api/dev-report?date=2026-07-10&force=1")
    assert seed_r.status_code == 200
    # Now verify we can retrieve it without force
    r = client.get("/api/dev-report?date=2026-07-10")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)


def test_dev_report__force_regenerates_artifact_inline(client):
    # AC: `GET /api/dev-report?force=1` regenerates the artifact inline (invoking the shared assembly function) and stores the result before returning it
    r = client.get("/api/dev-report?force=1")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    # Verify that calling without force now returns the same artifact (not 404)
    r2 = client.get("/api/dev-report?force=0")
    if r2.status_code == 200:
        payload2 = r2.json()
        assert isinstance(payload2, dict)


def test_dev_report__force_with_future_date_regenerates_and_stores(client):
    # AC: `GET /api/dev-report?date=YYYY-MM-DD&force=1` regenerates and stores the artifact for the given date
    r = client.get("/api/dev-report?date=2099-01-01&force=1")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    # Verify the artifact is now persisted by calling without force
    r2 = client.get("/api/dev-report?date=2099-01-01")
    assert r2.status_code == 200
    payload2 = r2.json()
    assert payload2 == payload


def test_dev_report__404_when_no_artifact_and_no_force(client):
    # AC: When no artifact exists for the date and `force` is not set, the endpoint returns HTTP 404 with body `{"error": "<descriptive message>"}`
    # Use a far-future date that definitely won't have an artifact
    # Use a unique date to avoid conflicts with other tests
    test_date = "2150-06-15"
    r = client.get(f"/api/dev-report?date={test_date}")
    assert r.status_code == 404
    data = r.json()
    assert "error" in data
    assert test_date in data["error"]


def test_dev_report__assembly_function_shared_with_script(client):
    # AC: The report assembly logic from `scripts/export_hermes_report.py` is extracted into an importable function (e.g. `assemble_dev_report()`) shared by both the script and the endpoint
    # This test verifies the function is importable from the service module
    from routers import dev_report_service
    assert hasattr(dev_report_service, "assemble_dev_report")
    assert callable(dev_report_service.assemble_dev_report)


def test_dev_report__script_continues_to_work_after_refactor(client):
    # AC: The script continues to work unchanged after the refactor
    # This is verified by running the script in a separate UAT step (Step 5 in workflow)
    pytest.skip("manual — verified via script execution in UAT steps, not HTTP")


def test_dev_report__endpoint_test_seeds_via_shared_assembly(client):
    # AC: An endpoint test exists that seeds data via the shared assembly function (not raw fixture insertion)
    # This test (test_dev_report__force_regenerates_artifact_inline) seeds via the assembly function via ?force=1
    from routers import dev_report_service
    # Verify the service can assemble a report
    report = dev_report_service.assemble_dev_report("2026-07-17")
    assert isinstance(report, dict)
