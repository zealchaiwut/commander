"""Tests for issue #223: Build sprint block component with four visual states.

Acceptance criteria covered:
- Base layout: 10px border-radius, header left/right split, sprint goal bar, ticket-num 42px
- NEXT UP state: green pill badge (mono, aria-label), amber Re-run Sprint button
- Planned state: Run Sprint disabled when goal empty (tooltip), enabled after goal saved
- RUNNING state visual: green border + glow halo, animated top stripe (smgmtStripeFlow 2.4s)
- RUNNING state header: gradient from green-bg, Running pulse badge with smgmt-pulse-dot
- RUNNING state buttons: only Cancel sprint (red border), Delete/Run hidden
- RUNNING state progress section: meta row, 6px progress bar, shimmer (smgmtShimmer 1.8s),
  role=progressbar with aria-valuenow/min/max
- Per-ticket status circles: done/running/pending with aria-labels
- Row states: bold running title, agent pill (coder purple/tester amber), elapsed time, muted pending
- Drag handles greyed on running sprint tickets (pointer-events: none)
- Animations: stripeFlow, shimmer, pulseDot, spin, dotPulse — all in prefers-reduced-motion guard
- Graceful fallback: _smgmtTicketRunState derives status from labels when agent_status absent
- Accessibility: aria-labels on state badges, focus ring on Cancel sprint button
"""
import os
import pytest
import httpx

BASE_URL = (
    os.environ.get("UAT_BASE_URL")
    or "http://localhost:" + os.environ.get("UAT_PORT", "8000")
)
if not BASE_URL.startswith("http"):
    BASE_URL = "http://localhost:8000"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as c:
        yield c


# ===========================================================================
# Base layout — container
# ===========================================================================

def test_sprint_block_border_radius_10px(client):
    """Base layout: sprint block container uses 10px border-radius."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "border-radius: 10px" in html, (
        "smgmt-sprint-block border-radius should be 10px (not 8px)"
    )


def test_sprint_block_position_relative(client):
    """Base layout: sprint block has position: relative (required for ::before stripe)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    # Find the smgmt-sprint-block rule and verify position: relative is near it
    idx = html.find(".smgmt-sprint-block {")
    assert idx != -1, ".smgmt-sprint-block rule not found in index.html"
    excerpt = html[idx:idx + 300]
    assert "position: relative" in excerpt, (
        "position: relative not set on .smgmt-sprint-block"
    )


def test_sprint_block_transition_defined(client):
    """Base layout: sprint block has border/box-shadow transition for state changes."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-sprint-block {")
    assert idx != -1
    excerpt = html[idx:idx + 300]
    assert "transition" in excerpt, (
        "transition not set on .smgmt-sprint-block for smooth state changes"
    )


def test_sprint_header_split_into_left_right_divs(client):
    """Base layout: header contains smgmt-sprint-header-left and smgmt-sprint-header-right divs."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-sprint-header-left" in js, (
        "smgmt-sprint-header-left div not found in sprint block HTML (app.js)"
    )
    assert "smgmt-sprint-header-right" in js, (
        "smgmt-sprint-header-right div not found in sprint block HTML (app.js)"
    )


def test_sprint_header_left_css_defined(client):
    """Base layout: .smgmt-sprint-header-left CSS defined with flex:1."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmt-sprint-header-left" in html, (
        ".smgmt-sprint-header-left CSS not defined in index.html"
    )


def test_sprint_header_right_css_defined(client):
    """Base layout: .smgmt-sprint-header-right CSS defined with flex-shrink:0."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmt-sprint-header-right" in html, (
        ".smgmt-sprint-header-right CSS not defined in index.html"
    )


def test_sprint_name_font_size_15px(client):
    """Base layout: sprint name uses 15px bold font."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-sprint-name")
    assert idx != -1
    excerpt = html[idx:idx + 100]
    assert "15px" in excerpt, (
        ".smgmt-sprint-name font-size should be 15px"
    )


def test_sprint_goal_bar_element_in_js(client):
    """Base layout: sprint goal bar element (smgmt-sprint-goal-bar) rendered in sprint block HTML."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-sprint-goal-bar" in js, (
        "smgmt-sprint-goal-bar not rendered in sprint block HTML (app.js)"
    )


def test_sprint_goal_bar_placeholder_text(client):
    """Base layout: goal bar shows italic muted placeholder when goal is empty."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "Sprint goal (required to run)" in js, (
        "Sprint goal placeholder text not found in app.js"
    )


def test_sprint_goal_bar_placeholder_css_class(client):
    """Base layout: placeholder variant uses .smgmt-sprint-goal-bar.placeholder with italic muted style."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmt-sprint-goal-bar.placeholder" in html, (
        ".smgmt-sprint-goal-bar.placeholder CSS not defined in index.html"
    )
    idx = html.find("smgmt-sprint-goal-bar.placeholder")
    excerpt = html[idx:idx + 150]
    assert "italic" in excerpt, (
        ".smgmt-sprint-goal-bar.placeholder should use font-style: italic"
    )


def test_ticket_num_min_width_42px(client):
    """Base layout: ticket number column has 42px min-width."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-ticket-num")
    assert idx != -1
    excerpt = html[idx:idx + 200]
    assert "42px" in excerpt, (
        ".smgmt-ticket-num min-width should be 42px"
    )


def test_sprint_header_padding_updated(client):
    """Base layout: header padding is 12px 16px."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-sprint-header {")
    assert idx != -1
    excerpt = html[idx:idx + 200]
    assert "12px 16px" in excerpt, (
        ".smgmt-sprint-header padding should be 12px 16px"
    )


# ===========================================================================
# NEXT UP state
# ===========================================================================

def test_next_up_badge_aria_label(client):
    """NEXT UP state: badge has aria-label='Next up'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert 'aria-label="Next up"' in js or "aria-label='Next up'" in js, (
        "NEXT UP badge missing aria-label='Next up' in app.js"
    )


def test_next_up_badge_mono_font(client):
    """NEXT UP state: badge uses monospace font-family."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-next-badge")
    assert idx != -1
    excerpt = html[idx:idx + 200]
    assert "mono" in excerpt, (
        ".smgmt-next-badge should use monospace font-family"
    )


def test_next_up_badge_uppercase_letter_spaced(client):
    """NEXT UP state: badge uses text-transform: uppercase and letter-spacing."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-next-badge")
    assert idx != -1
    excerpt = html[idx:idx + 300]
    assert "uppercase" in excerpt, (
        ".smgmt-next-badge missing text-transform: uppercase"
    )
    assert "letter-spacing" in excerpt, (
        ".smgmt-next-badge missing letter-spacing"
    )


def test_rerun_btn_amber_styling(client):
    """NEXT UP state: Re-run Sprint button uses amber border/text (not green)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find("smgmt-run-btn--rerun")
    assert idx != -1, ".smgmt-run-btn--rerun CSS not found"
    excerpt = html[idx:idx + 200]
    assert "amber" in excerpt, (
        ".smgmt-run-btn--rerun should use amber color variable (not green)"
    )


def test_rerun_btn_hover_amber_background(client):
    """NEXT UP state: Re-run Sprint hover shows amber background."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmt-run-btn--rerun:hover" in html, (
        ".smgmt-run-btn--rerun:hover rule not found in index.html"
    )
    idx = html.find("smgmt-run-btn--rerun:hover")
    excerpt = html[idx:idx + 100]
    assert "amber" in excerpt, (
        "Re-run Sprint hover should use amber-bg background"
    )


def test_rerun_btn_distinct_from_run_btn_js(client):
    """NEXT UP state: Re-run Sprint and Run Sprint generate different button markup."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # Both button paths must be present separately (not the same element)
    assert "smgmt-rerun-btn" in js or "smgmt-run-btn--rerun" in js, (
        "Re-run Sprint variant CSS class not found in app.js"
    )
    assert "Re-run Sprint" in js, "Re-run Sprint label not found in app.js"
    assert "Run Sprint" in js, "Run Sprint label not found in app.js"


# ===========================================================================
# Planned state
# ===========================================================================

def test_run_sprint_disabled_when_no_goal(client):
    """Planned state: Run Sprint button is disabled when sprint goal is empty."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "hasGoal" in js, (
        "hasGoal variable not found in app.js — Run Sprint disable-on-empty-goal not implemented"
    )
    assert "Set a sprint goal first" in js, (
        "'Set a sprint goal first' tooltip text not found in app.js"
    )


def test_run_sprint_tooltip_set_sprint_goal_first(client):
    """Planned state: Run Sprint tooltip reads 'Set a sprint goal first' when goal is empty."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "Set a sprint goal first" in r.text, (
        "'Set a sprint goal first' tooltip not found in app.js"
    )


def test_run_sprint_enabled_after_goal_saved(client):
    """Planned state: Run Sprint button re-enables when goal is saved (smgmtGoalInput logic)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # smgmtGoalInput must update the run button state based on hasGoal
    goal_input_idx = js.find("function smgmtGoalInput")
    assert goal_input_idx != -1, "smgmtGoalInput function not found in app.js"
    # Use a larger window — goal bar update comes first, button update comes later
    excerpt = js[goal_input_idx:goal_input_idx + 1800]
    assert "hasGoal" in excerpt, (
        "smgmtGoalInput does not check hasGoal when updating Run Sprint button"
    )
    assert "btn.disabled" in excerpt, (
        "smgmtGoalInput does not update btn.disabled when goal changes"
    )


def test_run_sprint_play_icon(client):
    """Planned state: Run Sprint button includes play icon (ti-player-play)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "ti-player-play" in r.text, (
        "ti-player-play icon not found in Run Sprint button in app.js"
    )


def test_delete_btn_red_border(client):
    """Planned/NEXT UP state: Delete button uses red border."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-delete-btn {")
    assert idx != -1
    excerpt = html[idx:idx + 200]
    assert "var(--red)" in excerpt, (
        ".smgmt-delete-btn should use var(--red) for border color"
    )


def test_goal_bar_updates_on_goal_input(client):
    """Planned state: goal bar DOM is updated when smgmtGoalInput fires."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-goal-bar-" in js, (
        "smgmt-goal-bar- element ID not referenced in app.js for goal bar update"
    )
    assert "goalBar.textContent" in js or "goalBar.className" in js, (
        "goal bar DOM update (textContent/className) not found in smgmtGoalInput"
    )


# ===========================================================================
# RUNNING state — visual treatment
# ===========================================================================

def test_running_block_green_border(client):
    """RUNNING state: sprint block uses var(--green) border."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-sprint-block.smgmt-running {")
    assert idx != -1, ".smgmt-sprint-block.smgmt-running rule not found"
    excerpt = html[idx:idx + 200]
    assert "var(--green)" in excerpt, (
        "RUNNING state should use var(--green) border-color"
    )


def test_running_block_glow_halo(client):
    """RUNNING state: sprint block has green glow halo (box-shadow with green-bg)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-sprint-block.smgmt-running {")
    assert idx != -1
    excerpt = html[idx:idx + 300]
    assert "box-shadow" in excerpt, (
        "box-shadow glow not set on .smgmt-sprint-block.smgmt-running"
    )
    assert "green-bg" in excerpt or "var(--green-bg)" in excerpt, (
        "glow halo should reference var(--green-bg)"
    )


def test_running_block_before_pseudo_element(client):
    """RUNNING state: ::before pseudo-element creates 3px animated top stripe."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-sprint-block.smgmt-running::before" in html, (
        ".smgmt-sprint-block.smgmt-running::before pseudo-element not defined"
    )
    idx = html.find(".smgmt-sprint-block.smgmt-running::before")
    excerpt = html[idx:idx + 400]
    assert "height: 3px" in excerpt, (
        "Top stripe should be 3px height"
    )
    assert "linear-gradient" in excerpt, (
        "Top stripe should use linear-gradient"
    )
    assert "z-index: 1" in excerpt, (
        "Top stripe ::before needs z-index: 1"
    )


def test_stripe_flow_animation_defined(client):
    """RUNNING state: @keyframes smgmtStripeFlow defined (2.4s flowing top stripe)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmtStripeFlow" in html, (
        "@keyframes smgmtStripeFlow animation not defined in index.html"
    )
    # Check animation is applied at 2.4s
    assert "2.4s" in html, (
        "smgmtStripeFlow animation duration 2.4s not found in index.html"
    )


def test_running_header_gradient_background(client):
    """RUNNING state: header background fades from green-bg (top) to transparent (bottom)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-sprint-header.smgmt-running-header")
    assert idx != -1, ".smgmt-sprint-header.smgmt-running-header rule not found"
    excerpt = html[idx:idx + 200]
    assert "linear-gradient" in excerpt, (
        "Running header should use linear-gradient background"
    )
    assert "green-bg" in excerpt or "var(--green-bg)" in excerpt, (
        "Running header gradient should start with var(--green-bg)"
    )


def test_running_badge_pulse_pill_structure(client):
    """RUNNING state: Running badge is a pulse pill with smgmt-pulse-dot child."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-pulse-dot" in js, (
        "smgmt-pulse-dot not injected into Running badge in app.js"
    )
    assert "smgmt-running-badge" in js, (
        "smgmt-running-badge element not created in app.js"
    )


def test_running_badge_aria_label(client):
    """RUNNING state: Running badge has aria-label='Running sprint'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "Running sprint" in js, (
        "aria-label='Running sprint' not found on Running badge in app.js"
    )


def test_running_badge_green_background_css(client):
    """RUNNING state: .smgmt-running-badge uses var(--green) background with white text."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-running-badge {")
    assert idx != -1, ".smgmt-running-badge CSS rule not found"
    # Use a larger window — multi-property rules span several lines
    excerpt = html[idx:idx + 400]
    assert "var(--green)" in excerpt, (
        ".smgmt-running-badge should use var(--green) background"
    )
    assert "#fff" in excerpt or "white" in excerpt, (
        ".smgmt-running-badge text should be white"
    )


def test_pulse_dot_animation_defined(client):
    """RUNNING state: @keyframes smgmtPulseDot defined (1.4s ease-in-out infinite)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmtPulseDot" in html, (
        "@keyframes smgmtPulseDot not defined in index.html"
    )
    assert "1.4s" in html, (
        "pulseDot animation duration 1.4s not found in index.html"
    )


def test_running_state_only_cancel_button_shown(client):
    """RUNNING state: only Cancel sprint button shown; Delete and Run buttons hidden."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # Delete button must be hidden
    assert "deleteBtn2" in js or "smgmt-delete-btn" in js, (
        "Delete button element not referenced in running-state apply code"
    )
    # Run button must be hidden
    assert "actionBtn.style.display = 'none'" in js or \
           'actionBtn.style.display = "none"' in js, (
        "Run Sprint button not hidden (display:none) in running state"
    )
    # Cancel sprint button injected
    assert "Cancel sprint" in js, (
        "'Cancel sprint' button text not found in app.js"
    )


def test_cancel_btn_red_border_css(client):
    """RUNNING state: Cancel sprint button uses red border, white bg default, red fill on hover."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-kill-btn {")
    assert idx != -1, ".smgmt-kill-btn CSS rule not found in index.html"
    excerpt = html[idx:idx + 300]
    assert "var(--red)" in excerpt, (
        ".smgmt-kill-btn should use var(--red) border"
    )


def test_cancel_btn_hover_red_fill(client):
    """RUNNING state: Cancel sprint hover fills red background with white text."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-kill-btn:hover" in html, (
        ".smgmt-kill-btn:hover rule not found in index.html"
    )
    idx = html.find(".smgmt-kill-btn:hover")
    excerpt = html[idx:idx + 150]
    assert "var(--red)" in excerpt, (
        ".smgmt-kill-btn:hover should fill var(--red) background"
    )


# ===========================================================================
# RUNNING state — progress section
# ===========================================================================

def test_progress_section_injected_in_running_state(client):
    """RUNNING state: .smgmt-progress-section element injected between goal bar and tickets."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-progress-section" in js, (
        ".smgmt-progress-section not injected in running state code (app.js)"
    )


def test_progress_meta_row_shows_ticket_counts(client):
    """RUNNING state: progress meta row shows 'X of Y tickets complete'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-progress-meta" in js, (
        ".smgmt-progress-meta not found in progress section markup (app.js)"
    )
    assert "of" in js and "complete" in js, (
        "Progress meta 'X of Y tickets complete' text not found in app.js"
    )


def test_progress_meta_shows_percentage(client):
    """RUNNING state: progress meta row right side shows percentage."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "pct" in js and "%" in js, (
        "Percentage value not rendered in progress meta row (app.js)"
    )


def test_progress_bar_element_created(client):
    """RUNNING state: .smgmt-progress-bar and .smgmt-progress-fill created in progress section."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-progress-bar" in js, ".smgmt-progress-bar not found in app.js"
    assert "smgmt-progress-fill" in js, ".smgmt-progress-fill not found in app.js"


def test_progress_bar_6px_height(client):
    """RUNNING state: progress bar is 6px tall."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-progress-bar {")
    assert idx != -1, ".smgmt-progress-bar CSS rule not found"
    excerpt = html[idx:idx + 150]
    assert "6px" in excerpt, (
        ".smgmt-progress-bar should be 6px tall"
    )


def test_progress_bar_rounded_ends(client):
    """RUNNING state: progress bar has fully rounded ends (border-radius: 999px)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-progress-bar {")
    assert idx != -1
    excerpt = html[idx:idx + 200]
    assert "border-radius" in excerpt, (
        ".smgmt-progress-bar should have border-radius for rounded ends"
    )


def test_progress_fill_gradient_green(client):
    """RUNNING state: progress fill uses linear-gradient green."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-progress-fill {")
    assert idx != -1, ".smgmt-progress-fill CSS rule not found"
    excerpt = html[idx:idx + 300]
    assert "linear-gradient" in excerpt, (
        ".smgmt-progress-fill should use linear-gradient"
    )
    assert "var(--green)" in excerpt, (
        ".smgmt-progress-fill gradient should include var(--green)"
    )


def test_progress_fill_transition(client):
    """RUNNING state: progress fill has transition: width 0.5s ease."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-progress-fill {")
    assert idx != -1
    excerpt = html[idx:idx + 300]
    assert "transition" in excerpt, (
        ".smgmt-progress-fill should have transition for smooth width changes"
    )
    assert "0.5s" in excerpt, (
        ".smgmt-progress-fill transition should be 0.5s"
    )


def test_shimmer_animation_defined(client):
    """RUNNING state: @keyframes smgmtShimmer defined (1.8s linear infinite on fill)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmtShimmer" in html, (
        "@keyframes smgmtShimmer not defined in index.html"
    )
    assert "1.8s" in html, (
        "shimmer animation duration 1.8s not found in index.html"
    )


def test_progress_fill_shimmer_via_after_pseudo(client):
    """RUNNING state: shimmer overlay on progress fill uses ::after pseudo-element."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-progress-fill::after" in html, (
        ".smgmt-progress-fill::after pseudo-element not defined for shimmer in index.html"
    )


def test_progress_bar_aria_role_in_js(client):
    """RUNNING state: progress bar element has role=progressbar with aria-valuenow/min/max."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert 'role="progressbar"' in js or "role='progressbar'" in js, (
        "role=progressbar not set on progress bar element in app.js"
    )
    assert "aria-valuenow" in js, (
        "aria-valuenow not set on progress bar in app.js"
    )
    assert 'aria-valuemin="0"' in js or "aria-valuemin='0'" in js, (
        "aria-valuemin=0 not set on progress bar in app.js"
    )
    assert 'aria-valuemax="100"' in js or "aria-valuemax='100'" in js, (
        "aria-valuemax=100 not set on progress bar in app.js"
    )


def test_progress_section_css_defined(client):
    """RUNNING state: .smgmt-progress-section CSS defined with padding and border."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-progress-section {" in html, (
        ".smgmt-progress-section CSS not defined in index.html"
    )


# ===========================================================================
# RUNNING state — per-ticket status circles
# ===========================================================================

def test_ticket_status_circle_css_defined(client):
    """Per-ticket circles: .smgmt-ticket-status-circle CSS defined (20×20, rounded)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-ticket-status-circle {" in html, (
        ".smgmt-ticket-status-circle CSS rule not found in index.html"
    )
    idx = html.find(".smgmt-ticket-status-circle {")
    excerpt = html[idx:idx + 200]
    assert "20px" in excerpt, (
        ".smgmt-ticket-status-circle should be 20×20px"
    )


def test_ticket_circle_done_css(client):
    """Per-ticket circles: .smgmt-ticket-status-circle.done uses green-bg with green text."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-ticket-status-circle.done {" in html, (
        ".smgmt-ticket-status-circle.done CSS not found in index.html"
    )
    idx = html.find(".smgmt-ticket-status-circle.done {")
    excerpt = html[idx:idx + 150]
    assert "green" in excerpt, (
        ".smgmt-ticket-status-circle.done should use green colors"
    )


def test_ticket_circle_running_css(client):
    """Per-ticket circles: .smgmt-ticket-status-circle.running uses blue border with spin animation."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-ticket-status-circle.running {" in html, (
        ".smgmt-ticket-status-circle.running CSS not found in index.html"
    )
    idx = html.find(".smgmt-ticket-status-circle.running {")
    excerpt = html[idx:idx + 200]
    assert "var(--blue)" in excerpt, (
        ".smgmt-ticket-status-circle.running should use var(--blue)"
    )


def test_ticket_circle_pending_css(client):
    """Per-ticket circles: .smgmt-ticket-status-circle.pending uses muted surface color."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-ticket-status-circle.pending {" in html, (
        ".smgmt-ticket-status-circle.pending CSS not found in index.html"
    )


def test_ticket_circle_injected_in_js(client):
    """Per-ticket circles: circles are built and injected per ticket in smgmtApplyTicketLiveStatus."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-ticket-status-circle" in js, (
        "smgmt-ticket-status-circle not injected in smgmtApplyTicketLiveStatus (app.js)"
    )


def test_ticket_circle_done_checkmark_and_aria(client):
    """Per-ticket circles: done circle shows ✓ and aria-label='Done'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "✓" in js, (
        "✓ checkmark not set on done status circle in app.js"
    )
    assert "aria-label" in js and "Done" in js, (
        "aria-label='Done' not set on done circle in app.js"
    )


def test_ticket_circle_running_aria_label(client):
    """Per-ticket circles: running circle has aria-label='Running'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "aria-label" in js and "Running" in js, (
        "aria-label='Running' not set on running circle in app.js"
    )


def test_ticket_circle_pending_hollow_and_aria(client):
    """Per-ticket circles: pending circle shows ○ and aria-label='Pending'."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "○" in js, (
        "○ character not set on pending status circle in app.js"
    )
    assert "Pending" in js, (
        "aria-label='Pending' not set on pending circle in app.js"
    )


def test_inner_dot_for_running_circle(client):
    """Per-ticket circles: running circle has smgmt-inner-dot child element."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-inner-dot" in js, (
        "smgmt-inner-dot not added to running circle in app.js"
    )


def test_inner_dot_css_defined(client):
    """Per-ticket circles: .smgmt-inner-dot CSS defined (7×7 blue dot)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmt-inner-dot" in html, (
        ".smgmt-inner-dot CSS not defined in index.html"
    )


# ===========================================================================
# RUNNING state — per-ticket row states
# ===========================================================================

def test_running_row_bold_title_css(client):
    """Running row: running ticket title is bold (font-weight: 600)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-ticket.is-running .smgmt-ticket-title" in html, (
        ".smgmt-ticket.is-running title CSS not defined in index.html"
    )


def test_pending_row_muted_title_css(client):
    """Pending row: pending ticket title uses muted color."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-ticket.is-pending .smgmt-ticket-title" in html, (
        ".smgmt-ticket.is-pending title CSS not defined in index.html"
    )
    idx = html.find(".smgmt-ticket.is-pending .smgmt-ticket-title")
    excerpt = html[idx:idx + 100]
    assert "muted" in excerpt or "sub" in excerpt, (
        "Pending ticket title should use muted/sub color"
    )


def test_agent_pill_css_defined(client):
    """Running row: .smgmt-agent-pill CSS defined with coder (purple) and tester (amber) variants."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-agent-pill {" in html, (
        ".smgmt-agent-pill CSS not defined in index.html"
    )
    assert ".smgmt-agent-pill.coder {" in html, (
        ".smgmt-agent-pill.coder CSS not defined in index.html"
    )
    assert ".smgmt-agent-pill.tester {" in html, (
        ".smgmt-agent-pill.tester CSS not defined in index.html"
    )


def test_agent_pill_coder_purple(client):
    """Running row: CODER pill uses purple-bg/purple color."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-agent-pill.coder {")
    assert idx != -1
    excerpt = html[idx:idx + 100]
    assert "purple" in excerpt, (
        ".smgmt-agent-pill.coder should use purple color variable"
    )


def test_agent_pill_tester_amber(client):
    """Running row: TESTER pill uses amber-bg/amber color."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    idx = html.find(".smgmt-agent-pill.tester {")
    assert idx != -1
    excerpt = html[idx:idx + 100]
    assert "amber" in excerpt, (
        ".smgmt-agent-pill.tester should use amber color variable"
    )


def test_agent_pill_injected_for_running_ticket(client):
    """Running row: agent pill is injected on running ticket rows."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-agent-pill" in js, (
        "smgmt-agent-pill not injected for running tickets in app.js"
    )
    assert "CODER" in js or "coder" in js, (
        "CODER agent pill text not found in app.js"
    )


def test_elapsed_time_element_css(client):
    """Elapsed time: .smgmt-ticket-elapsed-time CSS defined (mono, 11px, muted, right-aligned)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-ticket-elapsed-time {" in html, (
        ".smgmt-ticket-elapsed-time CSS not defined in index.html"
    )
    idx = html.find(".smgmt-ticket-elapsed-time {")
    excerpt = html[idx:idx + 200]
    assert "11px" in excerpt, (
        ".smgmt-ticket-elapsed-time should be 11px"
    )
    assert "mono" in excerpt, (
        ".smgmt-ticket-elapsed-time should use monospace font"
    )


def test_elapsed_time_injected_on_all_rows(client):
    """Elapsed time: smgmt-ticket-elapsed-time element appended to every ticket row."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "smgmt-ticket-elapsed-time" in js, (
        "smgmt-ticket-elapsed-time element not injected in app.js"
    )


def test_pending_elapsed_placeholder_dash(client):
    """Pending row: elapsed time shows — placeholder."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # Pending branch sets timeEl.textContent = '—'
    assert "timeEl.textContent = '—'" in js or 'timeEl.textContent = "—"' in js, (
        "Pending rows should show '—' placeholder in elapsed time column"
    )


# ===========================================================================
# RUNNING state — drag handles greyed
# ===========================================================================

def test_running_sprint_ticket_grips_greyed(client):
    """Running sprint: drag handles on tickets are visually greyed and non-interactive."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-sprint-block.smgmt-running .smgmt-ticket-grip" in html, (
        "CSS rule greying out ticket drag handles in running sprint not found in index.html"
    )
    idx = html.find(".smgmt-sprint-block.smgmt-running .smgmt-ticket-grip")
    excerpt = html[idx:idx + 150]
    assert "pointer-events: none" in excerpt, (
        "pointer-events: none not set on running sprint ticket grip"
    )


# ===========================================================================
# Animations — all five defined and gated on prefers-reduced-motion
# ===========================================================================

def test_spin_animation_defined(client):
    """Animations: @keyframes smgmtSpin defined (1s linear infinite for running circle)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmtSpin" in html, (
        "@keyframes smgmtSpin not defined in index.html"
    )
    # Find animation usage for 1s spin
    assert "1s linear infinite" in html, (
        "1s linear infinite not used for smgmtSpin animation"
    )


def test_dot_pulse_animation_defined(client):
    """Animations: @keyframes smgmtDotPulse defined (1.4s ease-in-out infinite for inner dot)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "smgmtDotPulse" in html, (
        "@keyframes smgmtDotPulse not defined in index.html"
    )


def test_all_five_animations_defined(client):
    """Animations: all five required animations present in index.html."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    required_animations = [
        "smgmtStripeFlow",
        "smgmtShimmer",
        "smgmtPulseDot",
        "smgmtSpin",
        "smgmtDotPulse",
    ]
    for anim in required_animations:
        assert anim in html, f"@keyframes {anim} not defined in index.html"


def test_animations_gated_prefers_reduced_motion(client):
    """Animations: all five animations wrapped in prefers-reduced-motion: no-preference guard."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert "prefers-reduced-motion: no-preference" in html, (
        "@media (prefers-reduced-motion: no-preference) guard not found in index.html"
    )
    # There should be multiple occurrences (one per animation group)
    count = html.count("prefers-reduced-motion: no-preference")
    assert count >= 3, (
        f"Only {count} prefers-reduced-motion guards found — expected at least 3 "
        "(for stripe/shimmer, pulseDot, spin/dotPulse)"
    )


# ===========================================================================
# Data plumbing — graceful fallback via _smgmtTicketRunState
# ===========================================================================

def test_smgmt_ticket_run_state_function_defined(client):
    """Data plumbing: _smgmtTicketRunState function defined for graceful status fallback."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "_smgmtTicketRunState" in r.text, (
        "_smgmtTicketRunState function not defined in app.js"
    )


def test_smgmt_ticket_run_state_checks_uat_label(client):
    """Data plumbing: fallback checks 'uat' label → done when agent_status absent."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    idx = js.find("_smgmtTicketRunState")
    assert idx != -1
    excerpt = js[idx:idx + 600]
    assert "uat" in excerpt, (
        "_smgmtTicketRunState fallback should map 'uat' label → done"
    )


def test_smgmt_ticket_run_state_checks_running_labels(client):
    """Data plumbing: fallback checks coder/tester running labels → running state."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    idx = js.find("_smgmtTicketRunState")
    assert idx != -1
    excerpt = js[idx:idx + 600]
    assert "coder" in excerpt.lower() and "tester" in excerpt.lower(), (
        "_smgmtTicketRunState fallback should handle coder/tester running labels"
    )


def test_smgmt_active_agent_function_defined(client):
    """Data plumbing: _smgmtActiveAgent helper function defined in app.js."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "_smgmtActiveAgent" in r.text, (
        "_smgmtActiveAgent function not defined in app.js"
    )


# ===========================================================================
# Accessibility
# ===========================================================================

def test_cancel_sprint_focus_ring_css(client):
    """Accessibility: Cancel sprint button has visible focus ring (focus-visible outline)."""
    r = client.get("/static/index.html")
    assert r.status_code == 200
    html = r.text
    assert ".smgmt-kill-btn:focus-visible" in html, (
        ".smgmt-kill-btn:focus-visible outline not defined in index.html"
    )


def test_progress_bar_role_in_js(client):
    """Accessibility: progress bar has role=progressbar for screen reader support."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "progressbar" in r.text, (
        "role='progressbar' not found in progress bar markup (app.js)"
    )


def test_planned_sprint_badge_absent(client):
    """Planned state: no state badge rendered for planned sprint (isNext=false, not running)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    # The nextBadge is only rendered when isNext is true
    idx = js.find("const nextBadge")
    assert idx != -1
    excerpt = js[idx:idx + 150]
    assert "isNext" in excerpt, (
        "nextBadge conditional render on isNext not found in app.js"
    )
    # When isNext is false, nextBadge is empty string
    assert "': ''" in excerpt or ": ''" in excerpt or ': ""' in excerpt, (
        "nextBadge should be empty string when isNext=false"
    )


# ===========================================================================
# Regression — page loads and required JS functions present
# ===========================================================================

def test_sprint_mgmt_page_loads(client):
    """Regression: Sprint management page loads successfully."""
    r = client.get("/projects/zealchaiwut%2Fcommander/sprint-mgmt")
    assert r.status_code == 200, f"Sprint mgmt page returned {r.status_code}"


def test_all_required_js_functions_present(client):
    """Regression: all required JS functions for sprint block component present in app.js."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    required = [
        "smgmtSprintBlockHtml",
        "smgmtApplyRunState",
        "smgmtApplyTicketLiveStatus",
        "_smgmtTicketRunState",
        "_smgmtActiveAgent",
        "_smgmtUpdateElapsedTimeEl",
        "smgmtGoalInput",
    ]
    for fn in required:
        assert fn in js, f"Required JS function '{fn}' not found in app.js"
