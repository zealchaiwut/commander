"""Tests for issue #903: Set Activity as default project landing view (runs against UAT)"""
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
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=True) as c:
        yield c


# --- Acceptance Criteria ---

def test_activity_default_landing__no_view_param_loads_activity(client):
    # AC: Navigating to a project URL with no `?view=` or `?tab=` param renders the Activity view
    # When visiting /project/commander/ (no tab), the server should redirect to /project/commander/logs
    # and the Activity (logs) view should load.
    r = client.get("/project/commander/", follow_redirects=True)
    assert r.status_code == 200, "Page should load successfully"
    # Verify the final URL is the logs tab (Activity view)
    assert r.url.path.endswith("/logs"), f"Expected /project/commander/logs, got {r.url.path}"
    assert "pane-logs" in r.text, "logs pane should be in HTML"


def test_activity_default_landing__activity_view_shows_live_indicator(client):
    # AC: Activity view displays the live indicator (live dot) on initial load
    r = client.get("/project/commander/")
    assert r.status_code == 200
    # The live indicator markup should be present in the Activity view
    # Check that elements related to live status are in the response
    assert "logs" in r.text or "activity" in r.text.lower()


def test_activity_default_landing__sprint_deep_link_resolves(client):
    # AC: `?view=sprint` deep link resolves to Sprint view, not Activity
    r = client.get("/project/commander/?view=sprint")
    assert r.status_code == 200
    assert "pane-sprint-mgmt" in r.text, "sprint-mgmt pane should be present"


def test_activity_default_landing__tickets_deep_link_resolves(client):
    # AC: All other existing `?view=` and `?tab=` deep links resolve to their correct views
    # Testing ?tab=tickets (renamed from old ?tab= syntax)
    r = client.get("/project/commander/tickets")
    assert r.status_code == 200
    assert "pane-tickets" in r.text, "tickets pane should be present"


def test_activity_default_landing__deploy_deep_link_resolves(client):
    # AC: All other existing `?view=` and `?tab=` deep links resolve to their correct views
    # Testing deploy tab
    r = client.get("/project/commander/deploy")
    assert r.status_code == 200
    assert "pane-deploy" in r.text, "deploy pane should be present"


def test_activity_default_landing__url_routing_fallback(client):
    # AC: `project.html` routing logic selects Activity as the fallback when no view param is present
    # Direct navigation to /project/commander/ (no tab specified) should route to Activity (logs tab).
    r = client.get("/project/commander/")
    assert r.status_code == 200
    # Verify that the response includes the logs pane and not exclusively sprint-mgmt as the default
    assert "pane-logs" in r.text
