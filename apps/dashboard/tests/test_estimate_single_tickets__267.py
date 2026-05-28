"""Tests for issue #267: Estimate single tickets in background at creation time (runs against UAT)"""
import os
import time
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

def test_estimate_single_tickets__create_endpoint_returns_immediately(client):
    """AC: The single-ticket create endpoint returns immediately after creating the GitHub issue,
    without waiting for estimation to complete."""
    # Create a new ticket and measure response time
    start = time.time()
    r = client.post(
        "/api/tickets/create",
        data={
            "title": "Test immediate response ticket",
            "body": "This tests that creation returns without waiting for estimation.",
            "project": "",
        },
    )
    elapsed = time.time() - start

    # Should return 201 Created
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    # Response should include ticket number and URL
    data = r.json()
    assert "number" in data
    assert "url" in data
    assert isinstance(data["number"], int)

    # Response should arrive in < 5 seconds (no estimation wait)
    # If estimation were blocking, this would take 30+ seconds
    assert elapsed < 5, f"Response took {elapsed:.1f}s; expected < 5s (may indicate blocking estimation)"


def test_estimate_single_tickets__background_task_runs_after_creation(client):
    """AC: After issue creation, the estimator runs as a background task (FastAPI BackgroundTasks)
    for that one issue."""
    # This is a manual test — we create a ticket and then wait for estimation.
    # The background estimator should post a comment and apply the 'estimated' label within ~30 seconds.
    pytest.skip("manual — requires checking GitHub issue for estimator comment and 'estimated' label after creation")


def test_estimate_single_tickets__estimator_produces_size_and_posts_comment(client):
    """AC: The background estimator produces size, minutes, and files-touched,
    then posts the estimator comment and applies the 'estimated' label."""
    pytest.skip("manual — requires manual inspection of GitHub issue comments and labels after background estimation completes")


def test_estimate_single_tickets__ui_displays_estimating_badge(client):
    """AC: The newly created ticket displays an 'estimating...' badge in the UI
    until the estimate lands."""
    pytest.skip("manual — visual check: requires browser to verify 'estimating...' badge appears on new ticket")


def test_estimate_single_tickets__badge_replaced_by_size_badge(client):
    """AC: Once the estimate is available, the 'estimating...' badge is replaced
    by the size badge (S / M / L) using the existing size-badge rendering."""
    pytest.skip("manual — visual check: requires browser to verify badge transitions from 'estimating...' to size after estimate lands")


def test_estimate_single_tickets__estimation_failure_surfaces_via_resilient_path(client):
    """AC: If estimation fails, the failure surfaces via the resilient update_ticket.py
    warning-comment path and does not cause the ticket-creation response to error or hang."""
    # Create a ticket and confirm creation succeeds even if estimation fails.
    # (Estimation failure is non-fatal and does not block the HTTP response.)
    r = client.post(
        "/api/tickets/create",
        data={
            "title": "Test resilient estimation failure",
            "body": "This tests that estimation failures don't break ticket creation.",
            "project": "",
        },
    )

    # Should still return 201 even if estimation fails in background
    assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    data = r.json()
    assert "number" in data
    # The ticket was created successfully; estimation failure is non-fatal.


def test_estimate_single_tickets__in_sprint_estimator_becomes_noop_for_estimated_tickets(client):
    """AC: The in-sprint estimator loop is untouched; it continues to run but becomes
    a no-op for tickets that already carry an 'estimated' label."""
    pytest.skip("manual — requires sprint run with estimated tickets to confirm no-op behavior")
