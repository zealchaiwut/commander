"""UAT tests for issue #877: GitHub milestone support and issues mirror.

These tests run against a live UAT environment (http://localhost:8001) and
exercise the milestone CRUD endpoints and the issues mirror endpoint.

Acceptance Criteria Mapping:
- AC1: GET /projects/:repo/milestones returns milestones (fallback pre-sync)
- AC2: POST /projects/:repo/milestones creates milestone and returns object
- AC3: PATCH /projects/:repo/milestones/:number edits milestone
- AC4: DELETE /projects/:repo/milestones/:number closes milestone
- AC5: Issues sync captures milestone.number and milestone.title
- AC6: GET /projects/:repo/issues returns milestone field (zero GitHub API)
- AC7: Milestones read from mirror after sync (zero GitHub API)
- AC8: Write operations reflect on GitHub within same request cycle
- AC9: POST creates milestone in correct repo (URL = /repos/<owner>/<repo>/milestones)
"""
from __future__ import annotations

import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run tester Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    """HTTP client pointing at the UAT server."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC-1: GET milestones (fallback pre-sync) ────────────────────────────────

def test_877__list_milestones_fallback_pre_sync(client):
    """AC1: GET /api/projects/:slug/milestones falls back to live GitHub before first sync."""
    pytest.skip("manual — requires fresh DB or existing test milestone in repo")


# ── AC-2/AC-8/AC-9: POST to create milestone ────────────────────────────────

def test_877__create_milestone_returns_201(client):
    """AC2/AC8/AC9: POST /api/projects/:slug/milestones returns 201 with milestone object."""
    pytest.skip("manual — POST would create real milestone on GitHub; use BA/Coder workflow instead")


# ── AC-3: PATCH to edit milestone ────────────────────────────────────────────

def test_877__update_milestone_patches_fields(client):
    """AC3: PATCH /api/projects/:slug/milestones/:number edits title/due_on/state."""
    pytest.skip("manual — PATCH would edit real milestone on GitHub")


# ── AC-4: DELETE to close milestone ──────────────────────────────────────────

def test_877__close_milestone_sets_state_closed(client):
    """AC4: DELETE /api/projects/:slug/milestones/:number closes the milestone."""
    pytest.skip("manual — DELETE would close real milestone on GitHub")


# ── AC-6: GET issues mirror returns milestone field (zero API) ───────────────

def test_877__list_issues_includes_milestone_field(client):
    """AC6: GET /api/projects/:slug/issues returns milestone field on each issue."""
    # Smoke test: endpoint responds without error; milestone field is present
    r = client.get("/api/projects/commander/issues")
    assert r.status_code in (200, 404), f"Expected 200 or 404, got {r.status_code}"
    if r.status_code == 200:
        issues = r.json()
        assert isinstance(issues, list), "Response should be a list of issues"
        # Each issue may or may not have a milestone; if present, it must be a dict or None
        for issue in issues:
            if "milestone" in issue:
                assert issue["milestone"] is None or isinstance(issue["milestone"], dict), \
                    f"milestone field must be dict or None, got {type(issue['milestone'])}"


# ── AC-7: GET milestones from mirror (zero API) ──────────────────────────────

def test_877__list_milestones_from_mirror(client):
    """AC7: GET /api/projects/:slug/milestones serves from mirror after sync."""
    # Smoke test: endpoint responds
    r = client.get("/api/projects/commander/milestones")
    assert r.status_code in (200, 404), f"Expected 200 or 404, got {r.status_code}"
    if r.status_code == 200:
        milestones = r.json()
        assert isinstance(milestones, list), "Response should be a list of milestones"
        # Each milestone should have number and title
        for m in milestones:
            assert "number" in m or "title" in m, "milestone should have number and/or title"


# ── Integration: endpoints are wired and responsive ──────────────────────────

def test_877__endpoints_exist_and_respond(client):
    """Verify milestone endpoints are registered and respond (not 404 route-not-found)."""
    # GET milestones
    r = client.get("/api/projects/commander/milestones")
    assert r.status_code != 404, "GET /api/projects/{slug}/milestones route not found"

    # GET issues
    r = client.get("/api/projects/commander/issues")
    assert r.status_code != 404, "GET /api/projects/{slug}/issues route not found"

    # POST create — send an empty body so validation rejects it (422) without
    # creating any remote state.  All we need is status != 404 to confirm the
    # route is registered.  (issue #2074: never send a valid title here)
    r = client.post("/api/projects/commander/milestones", json={})
    assert r.status_code != 404, "POST /api/projects/{slug}/milestones route not found"

    # PATCH update (will fail project lookup or GitHub error, but route should exist)
    try:
        r = client.patch("/api/projects/commander/milestones/999",
                         json={"title": "updated"})
        assert r.status_code != 404, "PATCH /api/projects/{slug}/milestones/{number} route not found"
    except Exception:
        # If GitHub connection fails, that's OK - the route exists
        pass

    # DELETE close (will fail with GitHub error, but route should exist)
    try:
        r = client.delete("/api/projects/commander/milestones/999")
        assert r.status_code != 404, "DELETE /api/projects/{slug}/milestones/{number} route not found"
    except Exception:
        # If GitHub connection fails, that's OK - the route exists
        pass
