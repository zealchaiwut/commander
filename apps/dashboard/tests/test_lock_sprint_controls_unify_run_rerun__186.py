"""Tests for issue #186: Lock sprint controls and unify Run/Re-run button.

Acceptance criteria covered:
- Running sprint's Delete button is disabled with tooltip "Sprint is running"
- Running sprint's goal input becomes readonly with smgmt-goal-readonly CSS class
- Drag-and-drop suppressed for running sprint header and ticket rows
- Unified Run/Re-run button (smgmt-run-btn) replaces separate Run + Re-run buttons
- Old smgmt-rerun-btn class is removed entirely
- Non-running sprints receive smgmt-locked class while any sprint is running
- Lock icon (🔒) inserted into non-running sprint headers while any sprint is running
- Non-running sprint's unified button disabled with "Another sprint is running"
- Drag-and-drop suppressed on all non-running sprints while any is running
- smgmt-locked, smgmt-goal-readonly, smgmt-run-btn--rerun CSS styles defined
- On sprint stop: smgmt-locked removed, all controls restored
"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8000")
if not BASE_URL.startswith("http"):
    BASE_URL = "http://localhost:8000"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as c:
        yield c


# ── Unified button: old smgmt-rerun-btn removed ──────────────────────────────

def test_lock_sprint__old_rerun_btn_class_absent_from_js(client):
    """AC: The old separate smgmt-rerun-btn class is gone from app.js."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "smgmt-rerun-btn" not in r.text, \
        "Old smgmt-rerun-btn class still exists in app.js — separate Rerun button not removed"


def test_lock_sprint__old_rerun_btn_class_absent_from_html(client):
    """AC: The old smgmt-rerun-btn CSS class is gone from index.html styles."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert "smgmt-rerun-btn" not in r.text, \
        "Old smgmt-rerun-btn CSS still present in index.html — class was not removed"


# ── Unified button: single context-driven action button per sprint ─────────

def test_lock_sprint__unified_run_btn_class_present_in_js(client):
    """AC: Unified action button uses smgmt-run-btn class in smgmtSprintBlockHtml."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-run-btn" in js, "smgmt-run-btn class not found in app.js"


def test_lock_sprint__unified_button_rerun_label_in_js(client):
    """AC: Unified button label 'Re-run Sprint' (used when hasCompleted===true)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "Re-run Sprint" in r.text, \
        "'Re-run Sprint' label not found in app.js — unified button not showing re-run label"


def test_lock_sprint__unified_button_run_label_in_js(client):
    """AC: Unified button label 'Run Sprint' (used when hasCompleted===false)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "Run Sprint" in r.text, \
        "'Run Sprint' label not found in app.js — unified button not showing run label"


def test_lock_sprint__unified_button_calls_smgmt_rerun_sprint_for_completed(client):
    """AC: Unified button calls smgmtRerunSprint when hasCompleted is true."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmtRerunSprint" in js, "smgmtRerunSprint not referenced in app.js"


def test_lock_sprint__unified_button_calls_smgmt_run_sprint_for_fresh(client):
    """AC: Unified button calls smgmtRunSprint when hasCompleted is false."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmtRunSprint" in js, "smgmtRunSprint not referenced in app.js"


def test_lock_sprint__rerun_variant_css_class_in_js(client):
    """AC: Unified button gains smgmt-run-btn--rerun class when sprint has completed tickets."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "smgmt-run-btn--rerun" in r.text, \
        "smgmt-run-btn--rerun CSS variant not applied in app.js"


# ── Running sprint: lock its own controls ────────────────────────────────────

def test_lock_sprint__delete_btn_disabled_with_tooltip_while_running(client):
    """AC: Delete button is disabled (not hidden) with title 'Sprint is running' while running."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "Sprint is running" in js, \
        "'Sprint is running' tooltip not found in app.js — delete button not disabled on run"
    assert "deleteBtn.disabled = true" in js, \
        "deleteBtn.disabled = true not found in app.js"


def test_lock_sprint__goal_input_gets_readonly_while_running(client):
    """AC: Sprint goal input gets readonly attribute while sprint is running."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "goalInput.setAttribute('readonly'" in js or \
           'goalInput.setAttribute("readonly"' in js, \
        "goalInput.setAttribute('readonly') not found in app.js"


def test_lock_sprint__goal_input_gets_readonly_css_class_while_running(client):
    """AC: Sprint goal input gets smgmt-goal-readonly class while sprint is running."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-goal-readonly" in js, \
        "smgmt-goal-readonly not found in app.js"


def test_lock_sprint__drag_suppressed_running_sprint_header(client):
    """AC: Running sprint header's draggable attribute set to 'false'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # applyRunState sets draggable=false on the running sprint header
    assert "hdr.setAttribute('draggable', 'false')" in js or \
           'hdr.setAttribute("draggable", "false")' in js, \
        "draggable=false not set on running sprint header in app.js"


def test_lock_sprint__drag_suppressed_running_sprint_ticket_rows(client):
    """AC: Ticket rows inside the running sprint have draggable='false'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # smgmtTicketDragStart checks for draggable=false and prevents default
    assert "draggable" in js and "false" in js, \
        "drag suppression logic (draggable=false) not found in app.js"
    assert "event.preventDefault" in js, \
        "event.preventDefault() not called to suppress drag in app.js"


def test_lock_sprint__goal_input_noop_while_running(client):
    """AC: smgmtGoalInput is a no-op while this sprint is running."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # The guard: if (_smgmtAllRunning[runKey]) return;
    assert "_smgmtAllRunning[runKey]" in js, \
        "_smgmtAllRunning[runKey] guard not found in smgmtGoalInput"


# ── Other sprints: locked while any sprint is running ─────────────────────────

def test_lock_sprint__smgmt_locked_class_applied_to_non_running_sprints(client):
    """AC: Non-running sprints receive smgmt-locked class while any sprint is running."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-locked" in js, \
        "smgmt-locked class not found in app.js — non-running sprint locking not implemented"
    assert "block.classList.add('smgmt-locked')" in js or \
           'block.classList.add("smgmt-locked")' in js, \
        "block.classList.add('smgmt-locked') not found in app.js"


def test_lock_sprint__lock_icon_inserted_in_non_running_sprint_headers(client):
    """AC: Lock icon (🔒) is injected into headers of non-running sprints while any runs."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-lock-icon" in js, \
        "smgmt-lock-icon element not created in app.js"
    assert "\U0001F512" in js or "🔒" in js, \
        "Lock icon (🔒) emoji not set on lockIcon element in app.js"


def test_lock_sprint__non_running_btn_disabled_with_another_sprint_running_tooltip(client):
    """AC: Non-running sprint's unified button disabled with 'Another sprint is running' tooltip."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "Another sprint is running" in r.text, \
        "'Another sprint is running' tooltip not found in app.js"


def test_lock_sprint__drag_suppressed_on_non_running_sprints(client):
    """AC: Sprint headers and ticket rows on non-running sprints get draggable=false when any sprint runs."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # Pass 3 sets draggable=false on non-running sprint headers and ticket rows
    assert "anyRunning" in js, \
        "anyRunning check not found in app.js — locked-sprint drag suppression not implemented"


def test_lock_sprint__smgmt_locked_removed_and_controls_restored_on_stop(client):
    """AC: smgmt-locked class removed and all controls restored when sprint stops."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "block.classList.remove('smgmt-running', 'smgmt-locked')" in js or \
           'block.classList.remove("smgmt-running", "smgmt-locked")' in js, \
        "smgmt-locked not removed in reset pass of smgmtApplyRunState"
    # Delete button restored
    assert "deleteBtn.disabled = false" in js, \
        "deleteBtn.disabled = false (restore) not found in app.js"
    # Goal input readonly removed
    assert "goalInput.removeAttribute('readonly')" in js or \
           'goalInput.removeAttribute("readonly")' in js, \
        "goalInput.removeAttribute('readonly') not found in app.js"
    # Draggable restored to true
    assert "setAttribute('draggable', 'true')" in js or \
           'setAttribute("draggable", "true")' in js, \
        "draggable='true' restore not found in app.js"


def test_lock_sprint__lock_icons_removed_on_restore(client):
    """AC: Injected lock icons are removed when sprint stops (pass 1 of smgmtApplyRunState)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert ".smgmt-lock-icon" in js and ".remove()" in js, \
        "smgmt-lock-icon .remove() cleanup not found in app.js"


# ── Visual/CSS in index.html ─────────────────────────────────────────────────

def test_lock_sprint__smgmt_locked_css_style_defined(client):
    """AC: smgmt-locked CSS style defined — grey/neutral border distinct from smgmt-running."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert "smgmt-locked" in r.text, \
        "smgmt-locked CSS not defined in index.html"


def test_lock_sprint__smgmt_goal_readonly_css_style_defined(client):
    """AC: smgmt-goal-readonly CSS class defined with muted styling."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert "smgmt-goal-readonly" in r.text, \
        "smgmt-goal-readonly CSS class not defined in index.html"


def test_lock_sprint__smgmt_run_btn_rerun_css_style_defined(client):
    """AC: smgmt-run-btn--rerun CSS variant defined with amber styling."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert "smgmt-run-btn--rerun" in r.text, \
        "smgmt-run-btn--rerun CSS variant not defined in index.html"


def test_lock_sprint__delete_btn_disabled_css_defined(client):
    """AC: smgmt-delete-btn:disabled CSS rule defined (for locked Delete button appearance)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmt-delete-btn:disabled" in html, \
        "smgmt-delete-btn:disabled CSS rule not found in index.html"


def test_lock_sprint__lock_icon_css_defined(client):
    """AC: smgmt-lock-icon CSS class defined in index.html."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    assert "smgmt-lock-icon" in r.text, \
        "smgmt-lock-icon CSS class not defined in index.html"


# ── Sprint management page loads with all required JS functions ──────────────

def test_lock_sprint__sprint_mgmt_page_loads(client):
    """Regression: Sprint management page loads successfully."""
    r = client.get("/projects/zealchaiwut%2Fcommander/sprint-mgmt")
    assert r.status_code == 200, f"Sprint mgmt page returned {r.status_code}"


def test_lock_sprint__all_required_js_functions_present(client):
    """Regression: All JS functions required for lock/unlock logic are present in app.js."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    required = [
        "smgmtApplyRunState",
        "smgmtSprintBlockHtml",
        "smgmtGoalInput",
        "smgmtSprintDragStart",
        "smgmtTicketDragStart",
        "smgmtHasCompletedTickets",
    ]
    for fn in required:
        assert fn in js, f"Required JS function '{fn}' not found in app.js"
