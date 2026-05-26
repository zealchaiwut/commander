"""Tests for issue #100: Batch approve for UAT issues (CLI + dashboard)."""
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

REPO_ROOT = Path(__file__).parent.parent.parent.parent  # commander/tester/
BATCH_APPROVE_SCRIPT = REPO_ROOT / "scripts" / "batch_approve.py"
TEST_REPO = "zealchaiwut/commander"
NONEXISTENT_SPRINT = 9999  # guaranteed to have no UAT issues


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


# --- AC-1: Script exists and runs with --repo flag ---

def test_batch_approve__ac1_script_exists():
    """AC-1: batch_approve.py script exists in scripts/."""
    assert BATCH_APPROVE_SCRIPT.exists(), f"Script not found at {BATCH_APPROVE_SCRIPT}"


def test_batch_approve__ac1_script_has_repo_arg():
    """AC-1: Script accepts --repo argument (visible via --help)."""
    result = subprocess.run(
        [sys.executable, str(BATCH_APPROVE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--repo" in result.stdout


def test_batch_approve__ac1_output_format_with_no_issues():
    """AC-1: With zero UAT issues in a non-existent sprint, script prints correct message."""
    result = subprocess.run(
        [sys.executable, str(BATCH_APPROVE_SCRIPT),
         "--repo", TEST_REPO,
         "--sprint", str(NONEXISTENT_SPRINT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "No open UAT issues found." in result.stdout


# --- AC-2: --sprint filter only approves matching sprint issues ---

def test_batch_approve__ac2_sprint_flag_exists():
    """AC-2: Script accepts --sprint argument (visible via --help)."""
    result = subprocess.run(
        [sys.executable, str(BATCH_APPROVE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--sprint" in result.stdout


def test_batch_approve__ac2_sprint_filter_no_match_exits_0():
    """AC-2: --sprint with no matching UAT+sprint-N issues prints no-issues message, exits 0."""
    result = subprocess.run(
        [sys.executable, str(BATCH_APPROVE_SCRIPT),
         "--repo", TEST_REPO,
         "--sprint", str(NONEXISTENT_SPRINT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "No open UAT issues found." in result.stdout
    assert result.stderr == ""


# --- AC-3: Zero qualifying issues prints message and exits 0 ---

def test_batch_approve__ac3_no_issues_message_and_exit_0():
    """AC-3: With zero qualifying issues, prints 'No open UAT issues found.' and exits 0."""
    result = subprocess.run(
        [sys.executable, str(BATCH_APPROVE_SCRIPT),
         "--repo", TEST_REPO,
         "--sprint", str(NONEXISTENT_SPRINT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "No open UAT issues found."


# --- AC-4: Idempotent — running again when no open UAT issues is safe ---

def test_batch_approve__ac4_idempotent_no_error():
    """AC-4: Running batch_approve.py twice with no qualifying issues prints the message each time, exits 0."""
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(BATCH_APPROVE_SCRIPT),
             "--repo", TEST_REPO,
             "--sprint", str(NONEXISTENT_SPRINT)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "No open UAT issues found." in result.stdout
        assert result.stderr == ""


# --- AC-5: POST /api/projects/{repo}/approve-batch endpoint ---

def test_batch_approve__ac5_endpoint_exists(client):
    """AC-5: POST /api/projects/{repo}/approve-batch exists and returns 200."""
    encoded_repo = TEST_REPO
    r = client.post(f"/api/projects/{encoded_repo}/approve-batch")
    assert r.status_code == 200, f"Expected 200 but got {r.status_code}: {r.text}"


def test_batch_approve__ac5_response_format(client):
    """AC-5: Endpoint returns {\"approved\": [...], \"count\": N} JSON format."""
    encoded_repo = TEST_REPO
    r = client.post(f"/api/projects/{encoded_repo}/approve-batch")
    assert r.status_code == 200
    data = r.json()
    assert "approved" in data, f"Response missing 'approved' key: {data}"
    assert "count" in data, f"Response missing 'count' key: {data}"
    assert isinstance(data["approved"], list), "'approved' must be a list"
    assert isinstance(data["count"], int), "'count' must be an int"
    assert data["count"] == len(data["approved"]), "'count' must equal len('approved')"


def test_batch_approve__ac5_empty_result_when_no_uat_issues(client):
    """AC-5: When no UAT issues are open, endpoint returns approved=[] and count=0."""
    encoded_repo = TEST_REPO
    # First call — approve any open UAT issues (safe to run)
    r1 = client.post(f"/api/projects/{encoded_repo}/approve-batch")
    assert r1.status_code == 200

    # Second call — no UAT issues remain, so should return empty
    r2 = client.post(f"/api/projects/{encoded_repo}/approve-batch")
    assert r2.status_code == 200
    data = r2.json()
    assert data["count"] == 0
    assert data["approved"] == []


# --- AC-6: Dashboard UI — "Approve all UAT" button in app.js ---

def test_batch_approve__ac6_button_in_app_js(client):
    """AC-6: app.js contains the 'Approve all UAT' button rendered in the UAT group header."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "Approve all UAT" in js, "'Approve all UAT' button text not found in app.js"


def test_batch_approve__ac6_approveAllUat_function_exists(client):
    """AC-6: approveAllUat() function is defined in app.js and calls the approve-batch endpoint."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "approveAllUat" in js, "approveAllUat function not found in app.js"
    assert "approve-batch" in js, "approve-batch endpoint call not found in app.js"


def test_batch_approve__ac6_button_disables_during_request(client):
    """AC-6: approveAllUat sets button disabled during request (btnEl.disabled = true)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "btnEl.disabled" in js or "disabled = true" in js, \
        "Button disable logic not found in approveAllUat function"


def test_batch_approve__ac6_triggers_refresh_on_success(client):
    """AC-6: On success, approveAllUat triggers a project-detail refresh."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # _refreshAfterAction or similar reload call must be present
    assert "_refreshAfterAction" in js or "loadProject" in js or "refresh" in js.lower(), \
        "No refresh call found after successful batch approve"


# --- AC-7: After batch approve, UAT tickets disappear on next refresh ---

def test_batch_approve__ac7_approved_issues_absent_from_subsequent_call(client):
    """AC-7: After POST approve-batch succeeds, a second call returns count=0 (approved tickets gone)."""
    encoded_repo = TEST_REPO

    # First pass: approve any open UAT issues
    r1 = client.post(f"/api/projects/{encoded_repo}/approve-batch")
    assert r1.status_code == 200
    first_pass = r1.json()

    # Second pass: no open UAT issues remain
    r2 = client.post(f"/api/projects/{encoded_repo}/approve-batch")
    assert r2.status_code == 200
    second_pass = r2.json()

    assert second_pass["count"] == 0, (
        f"Expected 0 UAT issues after batch approve, got {second_pass['count']}. "
        f"First pass approved: {first_pass['approved']}"
    )
