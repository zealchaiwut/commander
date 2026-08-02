"""Tests for issue #2136: bare-slug project= on canonical sprint finish routes (runs against UAT)"""
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

def test_bare_slug_project_resolves_on_finish_preview(client):
    """AC: Bare-slug project= on finish-preview resolves correctly to owner/repo"""
    # Test using a bare slug (e.g. "commander") instead of "owner/repo"
    # The endpoint should resolve this to the tracked project and return 200, not 404
    r = client.get("/api/sprints/sprint-1011.1/finish-preview?project=commander")
    # A 404 means the bare slug was NOT resolved; 200 or 422 (missing sprint data) means it was
    # We're testing resolution, not the sprint state, so 200/422/400 are all acceptable
    assert r.status_code != 404, f"Bare slug 'commander' should resolve, but got 404. Full response: {r.text}"


def test_bare_slug_project_resolves_on_bulk_complete_preview(client):
    """AC: Bare-slug project= on bulk-complete-preview resolves correctly to owner/repo"""
    r = client.get("/api/sprints/sprint-1011.1/bulk-complete-preview?project=commander")
    assert r.status_code != 404, "Bare slug 'commander' should resolve on bulk-complete-preview, but got 404"


def test_bare_slug_project_resolves_on_finish_bg(client):
    """AC: Bare-slug project= on finish-bg resolves correctly to owner/repo"""
    r = client.post(
        "/api/sprints/sprint-1011.1/finish-bg?project=commander",
        json={"confirmed": False, "move_non_uat_to": "", "selected_tickets": []}
    )
    # 400 is expected (confirmed=False), 404 would indicate resolution failed
    assert r.status_code != 404, "Bare slug 'commander' should resolve on finish-bg, but got 404"


def test_bare_slug_project_raises_404_for_unknown_project(client):
    """AC: Bare-slug project= raises 404 for unknown projects (not silent fallback)"""
    # Use an unknown slug that definitely won't resolve
    r = client.get("/api/sprints/sprint-1011.1/finish-preview?project=nonexistent_unknown_project_xyz")
    assert r.status_code == 404, f"Unknown project should raise 404, but got {r.status_code}"
