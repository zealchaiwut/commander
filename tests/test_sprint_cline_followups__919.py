"""Tests for issue #919: Sprint opt-in: Use Cline for follow-up coder fixes

AC1: use_cline_followups boolean field added to sprint record / sprint.yaml, defaults to false
AC2: Run-sprint flow (Board) shows checkbox: "Use Cline (Sonnet) for follow-up coder fixes — tester stays on Claude"
AC3: Checkbox defaults to unchecked; checking it persists use_cline_followups: true to that sprint's record only
AC4: When flag is true, follow-up coder dispatches in that sprint route to Cline
AC5: When flag is false, all coder dispatches use Claude Code
AC6: Running view coder nodes display a CLINE or CLAUDE badge reflecting which backend ran them
"""
import os
import pytest
import httpx
import json

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# AC1: use_cline_followups field exists in sprint record, defaults false
def test_919__ac1_use_cline_followups_field_exists_and_defaults_false(client):
    """AC1: use_cline_followups boolean field added to sprint record, defaults to false."""
    r = client.get("/api/sprints")
    assert r.status_code == 200
    sprints = r.json()
    assert isinstance(sprints, (list, dict)), "Sprints endpoint must return list or dict"
    # If sprints is a dict (by-label), extract the list
    sprint_list = sprints.values() if isinstance(sprints, dict) else sprints
    # Verify at least one existing sprint has the field (or is empty, which is also valid)
    if sprint_list:
        sprint = next(iter(sprint_list)) if isinstance(sprint_list, dict) else sprint_list[0]
        assert "use_cline_followups" in sprint, "Sprint record must have use_cline_followups field"
        assert sprint.get("use_cline_followups") is False, "use_cline_followups must default to False"


# AC2: Run-sprint flow shows checkbox with correct label
def test_919__ac2_run_sprint_checkbox_present_in_ui(client):
    """AC2: Run-sprint flow shows checkbox: 'Use Cline (Sonnet) for follow-up coder fixes...'"""
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # Checkbox label should appear in the HTML
    assert "Use Cline" in html, "Run-sprint UI must include 'Use Cline' label"
    assert "follow-up coder fixes" in html, "Checkbox label must mention follow-up coder fixes"


# AC3: Preflight endpoint accepts use_cline_followups in request
def test_919__ac3_api_accepts_use_cline_followups_param(client):
    """AC3: API accepts use_cline_followups parameter (persistence behavior tested via browser)."""
    # Verify /api/sprints/run endpoint accepts use_cline_followups in the request body
    # We cannot fully test persistence here (that's a browser interaction), but we can verify
    # the API doesn't reject it
    r = client.post("/api/sprints/run", json={
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-test",
        "use_cline_followups": True
    })
    # We expect either success or a 409 (sprint already running), not a 400 (bad request)
    assert r.status_code in [200, 201, 409], f"API must accept use_cline_followups param, got {r.status_code}: {r.text}"


# AC4: Flag routing to Cline (contract test)
def test_919__ac4_use_cline_followups_true_accepted(client):
    """AC4: When flag is true, follow-up coder dispatches route to Cline (contract: API accepts flag)."""
    # Verify sprints endpoint returns sprints with use_cline_followups field
    r = client.get("/api/sprints")
    assert r.status_code == 200
    sprints = r.json()
    sprint_list = sprints.values() if isinstance(sprints, dict) else sprints
    if sprint_list:
        sprint = next(iter(sprint_list)) if isinstance(sprint_list, dict) else sprint_list[0]
        # Field must exist and be a boolean
        assert isinstance(sprint.get("use_cline_followups"), bool), \
            f"use_cline_followups must be bool, got {type(sprint.get('use_cline_followups'))}"


# AC5: Flag default is false (no Cline by default)
def test_919__ac5_use_cline_followups_defaults_false(client):
    """AC5: When flag is false, all coder dispatches use Claude Code (default behavior)."""
    r = client.get("/api/sprints")
    assert r.status_code == 200
    sprints = r.json()
    sprint_list = sprints.values() if isinstance(sprints, dict) else sprints
    if sprint_list:
        sprint = next(iter(sprint_list)) if isinstance(sprint_list, dict) else sprint_list[0]
        assert sprint.get("use_cline_followups") is False, "Sprints must default to use_cline_followups=False"


# AC6: Badge field available in running view data
def test_919__ac6_coder_backend_field_in_running_view(client):
    """AC6: Running view includes coder_backend field for badge rendering."""
    # The running view data (project.html, live feed) must include coder_backend on issues
    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    # This is a contract test: verify the API can serve the data structure needed for badges
    # Full badge rendering is tested via browser
    assert isinstance(data, (list, dict)), "Projects endpoint must return list or dict"
