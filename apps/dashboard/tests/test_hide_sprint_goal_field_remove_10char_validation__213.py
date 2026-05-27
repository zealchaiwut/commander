"""Tests for issue #213: Hide sprint goal field and remove 10-char validation"""
import os
import re
import pytest
import httpx

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8000")

APP_JS = os.path.join(os.path.dirname(__file__), "../static/app.js")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def _app_js_text():
    with open(APP_JS, encoding="utf-8") as f:
        return f.read()


def _extract_function(source: str, fn_name: str) -> str:
    """Extract the body of a top-level JS function by name."""
    pattern = rf"function {re.escape(fn_name)}\b"
    m = re.search(pattern, source)
    if not m:
        return ""
    start = m.start()
    depth = 0
    i = source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "{":
            depth -= 1
            if depth == 0:
                return source[start : j + 1]
    return source[start:]


# --- AC 1: Sprint goal row hidden via display:none; DOM element remains ---

def test_sprint_goal_row_hidden_via_display_none():
    """AC: smgmt-sprint-goal-row has style="display:none" in app.js HTML template."""
    src = _app_js_text()
    assert 'class="smgmt-sprint-goal-row"' in src, "smgmt-sprint-goal-row element must remain in DOM"
    # The row must carry display:none
    assert 'class="smgmt-sprint-goal-row" style="display:none"' in src, (
        "smgmt-sprint-goal-row must have style=\"display:none\" to hide the field"
    )


# --- AC 2: canRun does not depend on goal length ---

def test_can_run_ignores_goal_value():
    """AC: canRun logic does not gate on goal length — only hasCompleted and ticket count."""
    src = _app_js_text()
    # goalValid must not appear anywhere in the JS source after the feature
    assert "goalValid" not in src, (
        "goalValid still present in app.js — goal-length gate was not fully removed"
    )


# --- AC 3: smgmtSprintBlockHtml canRun has no goalValid reference ---

def test_smgmt_sprint_block_html_canrun_no_goal_valid():
    """AC: canRun in smgmtSprintBlockHtml no longer references goalValid."""
    src = _app_js_text()
    # Find smgmtSprintBlockHtml function and verify canRun formula
    fn_start = src.find("function smgmtSprintBlockHtml(")
    assert fn_start != -1, "smgmtSprintBlockHtml function not found in app.js"
    # Grab up to 100 lines after the function start
    fn_snippet = src[fn_start: fn_start + 4000]
    # goalValid must not appear in this function
    assert "goalValid" not in fn_snippet, (
        "goalValid still referenced in smgmtSprintBlockHtml"
    )
    # canRun must be present with the correct formula
    assert "const canRun = !hasCompleted && tickets.length >= 1;" in fn_snippet, (
        "canRun formula in smgmtSprintBlockHtml must be: "
        "const canRun = !hasCompleted && tickets.length >= 1;"
    )


# --- AC 4: smgmtApplyRunState canRun has no goalValid reference ---

def test_smgmt_apply_run_state_canrun_no_goal_valid():
    """AC: canRun in smgmtApplyRunState no longer references goalValid."""
    src = _app_js_text()
    fn_start = src.find("function smgmtApplyRunState(")
    assert fn_start != -1, "smgmtApplyRunState function not found in app.js"
    # Function is ~220 lines; use a generous window to capture the full body
    fn_snippet = src[fn_start: fn_start + 15000]
    assert "goalValid" not in fn_snippet, (
        "goalValid still referenced in smgmtApplyRunState"
    )
    assert "const canRun = hasTickets;" in fn_snippet, (
        "canRun in smgmtApplyRunState must be: const canRun = hasTickets;"
    )


# --- AC 5: Exact canRun formula in smgmtSprintBlockHtml ---

def test_canrun_exact_formula_in_sprint_block_html():
    """AC: canRun evaluates to: const canRun = !hasCompleted && tickets.length >= 1;"""
    src = _app_js_text()
    assert "const canRun = !hasCompleted && tickets.length >= 1;" in src, (
        "Exact canRun formula 'const canRun = !hasCompleted && tickets.length >= 1;' "
        "not found in app.js"
    )


# --- AC 6 & 7: Backend sprint goal endpoints preserve data and remain functional ---

def test_post_sprint_goal_saves_and_get_returns_it(client):
    """AC: POST /api/sprints/goal saves a goal; GET /api/sprints/goal returns it (data preserved)."""
    project = "zealchaiwut/commander"
    sprint = "sprint-213"   # valid format: sprint-<digits>
    goal_text = "Fix it"    # short goal — previously blocked by 10-char rule

    r = client.post("/api/sprints/goal", json={
        "project": project,
        "sprint_label": sprint,
        "goal": goal_text,
    })
    assert r.status_code == 200, f"POST /api/sprints/goal failed: {r.text}"
    assert r.json().get("ok") is True

    r2 = client.get("/api/sprints/goal", params={"project": project, "sprint": sprint})
    assert r2.status_code == 200, f"GET /api/sprints/goal failed: {r2.text}"
    assert r2.json().get("goal") == goal_text, (
        f"Goal was not preserved — expected {goal_text!r}, got {r2.json().get('goal')!r}"
    )


def test_get_sprint_goal_returns_empty_for_unknown_sprint(client):
    """AC: GET /api/sprints/goal returns an empty string for a sprint with no saved goal."""
    r = client.get("/api/sprints/goal", params={
        "project": "zealchaiwut/commander",
        "sprint": "sprint-999-nonexistent",
    })
    assert r.status_code == 200, f"GET /api/sprints/goal unexpected status: {r.status_code}"
    body = r.json()
    assert "goal" in body, "Response must have a 'goal' field"
    assert body["goal"] == "", f"Expected empty goal, got: {body['goal']!r}"


def test_post_sprint_goal_endpoint_accessible(client):
    """AC: POST /api/sprints/goal endpoint continues to function (does not 404)."""
    r = client.post("/api/sprints/goal", json={
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-999",   # valid format: sprint-<digits>
        "goal": "Smoke test goal",
    })
    assert r.status_code != 404, "POST /api/sprints/goal returned 404 — endpoint removed?"
    assert r.status_code == 200


def test_get_sprint_goal_endpoint_accessible(client):
    """AC: GET /api/sprints/goal endpoint continues to function (does not 404)."""
    r = client.get("/api/sprints/goal", params={
        "project": "zealchaiwut/commander",
        "sprint": "sprint-999",
    })
    assert r.status_code != 404, "GET /api/sprints/goal returned 404 — endpoint removed?"
    assert r.status_code == 200
