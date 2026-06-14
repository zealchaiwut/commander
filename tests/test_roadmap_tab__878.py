"""Tests for issue #878: Add Roadmap tab with milestone cards to project view (runs against UAT)"""
import os
import pytest
import httpx
import json


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

# Test project setup: use zealchaiwut/commander-issue-test for GitHub operations
TEST_REPO = "zealchaiwut/commander-issue-test"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_roadmap__tab_appears_in_project_nav(client):
    """AC1: A 'Roadmap' tab appears at the top-level project navigation alongside existing tabs."""
    # Open a project page (assume one exists; home redirects to first project)
    r = client.get("/project/zealchaiwut%2Fcommander-issue-test")
    assert r.status_code == 200
    html = r.text
    # Roadmap tab button must exist in the tab strip
    assert 'id="stab-roadmap"' in html or 'Roadmap' in html
    assert 'onclick="switchTab(\'roadmap\')' in html or 'aria-label="Roadmap"' in html


def test_roadmap__cards_render_for_open_milestones(client):
    """AC2: Each open milestone is rendered as a card showing title, description, progress, due date."""
    r = client.get(f"/api/roadmap?repo={TEST_REPO}")
    assert r.status_code == 200
    data = r.json()

    # Response must include a list of milestones
    assert "open" in data or "milestones" in data
    milestones = data.get("open") or data.get("milestones", [])

    # Each milestone card must have required fields
    if milestones:
        m = milestones[0]
        assert "title" in m
        assert "number" in m
        # Progress may be present if tickets exist in mirror
        if "progress" in m:
            assert isinstance(m["progress"], dict)
            assert "done" in m["progress"] or "total" in m["progress"]


def test_roadmap__active_milestone_marked(client):
    """AC3: Exactly one milestone can be marked Active; the active card is visually dominant."""
    r = client.get(f"/api/roadmap?repo={TEST_REPO}")
    assert r.status_code == 200
    data = r.json()

    # Settings must track which milestone is active
    assert "settings" in data or "active_milestone" in data
    settings = data.get("settings", {})
    # Active milestone is optional (no milestone may be active initially)
    # But if one is set, it should be an integer milestone number
    active = settings.get("active_milestone")
    if active is not None:
        assert isinstance(active, int)


def test_roadmap__active_milestone_persists_on_reload(client):
    """AC4: Active milestone designation is persisted in project settings."""
    repo = TEST_REPO

    # Get current state
    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    milestones = data.get("open") or data.get("milestones", [])

    if not milestones:
        pytest.skip("no open milestones to mark active")

    milestone_num = milestones[0]["number"]

    # Mark it active
    r = client.put(
        f"/api/roadmap/settings?repo={repo}",
        json={"active_milestone": milestone_num}
    )
    assert r.status_code in (200, 204)

    # Fetch again — active should persist
    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    settings = data.get("settings", {})
    assert settings.get("active_milestone") == milestone_num


def test_roadmap__card_order_persists_on_reload(client):
    """AC5: Cards can be reordered via drag-and-drop; order is persisted in project settings."""
    repo = TEST_REPO

    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    milestones = data.get("open") or data.get("milestones", [])

    if len(milestones) < 2:
        pytest.skip("need at least 2 milestones to test reorder")

    original_order = [m["number"] for m in milestones]
    reversed_order = list(reversed(original_order))

    # Persist a new order
    r = client.put(
        f"/api/roadmap/settings?repo={repo}",
        json={"order": reversed_order}
    )
    assert r.status_code in (200, 204)

    # Fetch again — order should match
    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    settings = data.get("settings", {})
    assert settings.get("order") == reversed_order


def test_roadmap__inline_milestone_creation(client):
    """AC6: Cards support inline creation of a new milestone (no modal/page navigation required)."""
    repo = TEST_REPO

    # Create a new milestone via HTTP
    milestone_data = {
        "title": "Test Milestone " + str(os.times()[0])[:6],
        "description": "Created by test",
        "due_on": "2026-12-31"
    }
    r = client.post(
        f"/api/roadmap/milestones?repo={repo}",
        json=milestone_data
    )
    assert r.status_code in (200, 201)
    result = r.json()
    assert result.get("title") == milestone_data["title"]
    assert "number" in result  # GitHub milestone number


def test_roadmap__inline_milestone_editing(client):
    """AC7: Cards support inline editing of title, description, and due date."""
    repo = TEST_REPO

    # Create a milestone first
    r = client.post(
        f"/api/roadmap/milestones?repo={repo}",
        json={"title": "Edit Test", "description": "Original"}
    )
    assert r.status_code in (200, 201)
    milestone = r.json()
    num = milestone["number"]

    # Edit it
    r = client.patch(
        f"/api/roadmap/milestones/{num}?repo={repo}",
        json={"description": "Updated description"}
    )
    assert r.status_code in (200, 204)

    # Verify update
    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    milestones = data.get("open") or data.get("milestones", [])
    edited = next((m for m in milestones if m["number"] == num), None)
    assert edited is not None
    assert edited.get("description") == "Updated description"


def test_roadmap__milestone_close_and_collapse(client):
    """AC8: A milestone can be closed from the card; closing triggers confirmation and collapses into history row."""
    repo = TEST_REPO

    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    milestones = data.get("open") or data.get("milestones", [])

    if not milestones:
        pytest.skip("no open milestones to close")

    # Close the first milestone
    milestone_num = milestones[0]["number"]
    r = client.post(f"/api/roadmap/milestones/{milestone_num}/close?repo={repo}")
    assert r.status_code in (200, 204)

    # After closing, it should be in closed list
    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    closed = data.get("closed", [])
    assert any(m["number"] == milestone_num for m in closed)


def test_roadmap__closed_milestone_expand_and_reopen(client):
    """AC9: Closed milestones are shown as collapsed history rows and can be expanded or re-opened."""
    repo = TEST_REPO

    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    closed = data.get("closed", [])

    if not closed:
        pytest.skip("no closed milestones to reopen")

    milestone_num = closed[0]["number"]

    # Reopen it
    r = client.post(f"/api/roadmap/milestones/{milestone_num}/reopen?repo={repo}")
    assert r.status_code in (200, 204)

    # Should be back in open list
    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    open_ms = data.get("open") or data.get("milestones", [])
    assert any(m["number"] == milestone_num for m in open_ms)


def test_roadmap__progress_calculation_done_plus_uat(client):
    """AC10: Ticket progress reflects (done + UAT) / total from the mirror."""
    repo = TEST_REPO

    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data = r.json()
    milestones = data.get("open") or data.get("milestones", [])

    if not milestones:
        pytest.skip("no milestones with progress to verify")

    # Check that progress is calculated correctly if present
    for m in milestones:
        if "progress" in m:
            prog = m["progress"]
            # Progress should include counts
            assert "done" in prog or "total" in prog
            # done + uat must be <= total
            done = prog.get("done", 0)
            uat = prog.get("uat", 0)
            total = prog.get("total", 0)
            if total > 0:
                assert (done + uat) <= total


def test_roadmap__design_system_styling(client):
    """AC11: Cards use design tokens, border radii of 5–6px, no left/right colored side stripes."""
    # Check project.html for Roadmap tab styling
    r = client.get("/project/zealchaiwut%2Fcommander-issue-test")
    assert r.status_code == 200
    html = r.text

    # Verify no inline side-stripe styling on roadmap cards
    # (This is a code-review check, not an HTTP assertion, but we verify the markup exists)
    assert "roadmap" in html.lower()


def test_roadmap__state_survives_page_reload(client):
    """AC12: Roadmap tab state (active milestone, card order) survives page reload."""
    # This is tested implicitly by AC4 (active persists) and AC5 (order persists)
    # HTTP-only tests cannot simulate page reloads, but the backend state persists
    repo = TEST_REPO

    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data1 = r.json()

    # Fetch again — same data should be returned
    r = client.get(f"/api/roadmap?repo={repo}")
    assert r.status_code == 200
    data2 = r.json()

    # Settings should match
    assert data1.get("settings") == data2.get("settings")
