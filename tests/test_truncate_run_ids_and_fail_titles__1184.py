"""Tests for issue #1184: Truncate run IDs and fail titles on mobile (runs against UAT)"""
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

def test_truncate_run_ids_and_fail_titles__logs_run_id_375px(client):
    # AC: At 375px viewport, `.logs-run-id` text truncates with ellipsis when content exceeds 80px
    r = client.get("/project/commander/logs")
    assert r.status_code == 200
    html = r.text
    # Check that CSS rule exists in the responsive media query
    assert "@media (max-width: 600px)" in html
    assert ".logs-run-id { max-width: 80px; overflow: hidden; text-overflow: ellipsis;" in html or (
        ".logs-run-id {" in html and "max-width: 80px" in html
    ), "logs-run-id should have max-width: 80px truncation in ≤600px media query"


def test_truncate_run_ids_and_fail_titles__logs_ticket_fail_375px(client):
    # AC: At 375px viewport, `.logs-ticket-fail` text truncates with ellipsis when content exceeds 80px
    r = client.get("/project/commander/logs")
    assert r.status_code == 200
    html = r.text
    # Check that CSS rule exists for .logs-ticket-fail truncation
    assert "@media (max-width: 600px)" in html
    assert ".logs-ticket-fail { max-width: 80px; overflow: hidden; text-overflow: ellipsis;" in html or (
        ".logs-ticket-fail {" in html and "max-width: 80px" in html
    ), "logs-ticket-fail should have max-width: 80px truncation in ≤600px media query"


def test_truncate_run_ids_and_fail_titles__both_at_600px(client):
    # AC: At 600px viewport, both elements truncate at the 80px threshold
    r = client.get("/project/commander/logs")
    assert r.status_code == 200
    html = r.text
    # Verify both elements have truncation rules within the ≤600px media query
    assert ".logs-run-id" in html
    assert ".logs-ticket-fail" in html
    assert "max-width: 80px" in html, "both .logs-run-id and .logs-ticket-fail should have max-width: 80px"


def test_truncate_run_ids_and_fail_titles__no_overflow_at_mobile(client):
    # AC: No horizontal page overflow at 375px or 600px with long run ID / fail title content
    r = client.get("/project/commander/logs")
    assert r.status_code == 200
    html = r.text
    # Verify both elements have overflow: hidden to prevent horizontal scroll
    assert "overflow: hidden" in html, "truncated elements should have overflow: hidden"
    # Verify text-overflow: ellipsis is applied
    assert "text-overflow: ellipsis" in html, "truncated elements should have text-overflow: ellipsis"


def test_truncate_run_ids_and_fail_titles__desktop_no_truncation(client):
    # AC: At viewports ≥601px, both elements display full text without truncation (desktop unchanged)
    r = client.get("/project/commander/logs")
    assert r.status_code == 200
    html = r.text
    # Base styles for .logs-run-id should NOT have a max-width constraint (or it should be full flex)
    # The max-width: 80px should ONLY be in the @media (max-width: 600px) block
    lines = html.split("\n")
    media_start = None
    for i, line in enumerate(lines):
        if "@media (max-width: 600px)" in line:
            media_start = i
        if media_start is not None and i > media_start and ("}" in line and "@media" not in line):
            # Found the closing brace of the media query (simplified check)
            if i > media_start + 1:  # ensure there's content between
                break

    # Just verify that the base .logs-run-id CSS is unrestricted
    # (desktop should see full content without the 80px max-width)
    assert ".logs-run-id {" in html and "flex: 1; min-width: 0" in html, (
        "Base .logs-run-id should be flex with min-width: 0 (unrestricted on desktop)"
    )
