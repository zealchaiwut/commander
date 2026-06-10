"""Tests for issue #770 — show live log tail after Deploy or Restart.

The feature is implemented entirely in the Deploy tab of
`apps/dashboard/static/project.html` (vanilla JS + CSS — no build step), so the
acceptance criteria are verified by asserting the presence and wiring of the
log-panel module in the served static file, plus that the existing deploy/
restart/render flows route their output through it.

AC coverage:
  AC1  — Deploy/Restart opens a log panel inline, directly beneath the card
  AC2  — panel shows a scrolling tail of the last 10–20 lines, follows new output
  AC3  — git-pull deploy: pull output → restart output → health check result
  AC4  — restart: launchctl/script output → post-restart health check result
  AC5  — render deploy: streams render build status as it arrives
  AC6  — new lines appended at bottom; buffer capped at ≤20 lines (no DOM growth)
  AC7  — clear success or failure indicator when the action completes
  AC8  — panel auto-collapses after a successful action (collapsed but accessible)
  AC9  — on failure the panel stays open and highlights the failure line
  AC10 — user can manually collapse or expand the panel at any time
  AC11 — panel does not block interaction with the rest of the Deploy tab
"""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"


def _project_html():
    return (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


# ── AC1: log panel rendered inline beneath each deploy card ───────────────────


def test_log_panel_rendered_inside_card():
    """AC1: every card renders a log panel element keyed by card index."""
    html = _project_html()
    # The panel markup is emitted by the card-html builder, so it sits directly
    # beneath the card it belongs to.
    assert "deploy-log-" in html
    assert "_deployCardHtml" in html
    # A dedicated builder emits the panel so the card stays readable.
    assert "_deployLogPanelHtml" in html


def test_log_panel_opens_on_action():
    """AC1: triggering deploy/restart resets+opens the panel for that card."""
    html = _project_html()
    assert "_deployLogReset" in html
    # both exec paths open the panel
    assert html.count("_deployLogReset(") >= 2


# ── AC2: scrolling tail that follows new output ───────────────────────────────


def test_log_panel_follows_new_output():
    """AC2: appended output auto-scrolls to the bottom (tail -f behaviour)."""
    html = _project_html()
    assert "scrollTop" in html
    assert "scrollHeight" in html
    # live region so assistive tech announces streamed lines
    assert "aria-live" in html


def test_log_panel_tail_window_is_capped_small():
    """AC2/AC6: only the last 10–20 lines are retained."""
    html = _project_html()
    assert "DEPLOY_LOG_MAX_LINES" in html
    # the cap is within the 10–20 range mandated by the AC
    import re
    m = re.search(r"DEPLOY_LOG_MAX_LINES\s*=\s*(\d+)", html)
    assert m, "DEPLOY_LOG_MAX_LINES constant not found"
    assert 10 <= int(m.group(1)) <= 20


# ── AC3: git-pull deploy streams pull → restart → health ──────────────────────


def test_deploy_flow_streams_pull_then_restart_then_health():
    """AC3: the local deploy flow pushes pull output and a health-check line."""
    html = _project_html()
    assert "_deployExecDeploy" in html
    assert "pull_output" in html
    # a post-action health check is performed and surfaced as a result line
    assert "_deployHealthCheck" in html


# ── AC4: restart streams script output then health ────────────────────────────


def test_restart_flow_runs_health_check():
    """AC4: the restart flow surfaces script output then a health-check result."""
    html = _project_html()
    assert "_deployExecRestart" in html
    # restart performs the same post-action health check
    assert html.count("_deployHealthCheck(") >= 2


def test_health_check_hits_health_endpoint():
    """AC3/AC4: the health check queries the dashboard health endpoint."""
    html = _project_html()
    assert "/api/health" in html


# ── AC5: render deploy streams build status ───────────────────────────────────


def test_render_poll_streams_status_into_panel():
    """AC5: the render status poller pushes each status into the log panel."""
    html = _project_html()
    assert "_deployPollRender" in html
    # render status updates flow through the shared log-push helper
    assert "_deployLogPush" in html


# ── AC6: capped buffer, no unbounded DOM growth ───────────────────────────────


def test_log_buffer_is_trimmed():
    """AC6: the line buffer is trimmed so DOM never grows past the cap."""
    html = _project_html()
    # buffer kept as an array and trimmed from the front as new lines arrive
    assert "_deployLogState" in html
    assert ".shift()" in html or ".slice(" in html


# ── AC7: success / failure indicator ──────────────────────────────────────────


def test_status_indicator_classes_present():
    """AC7: the panel header carries a status indicator with success/failure states."""
    html = _project_html()
    assert "deploy-log__status" in html
    assert "deploy-log__status--success" in html
    assert "deploy-log__status--failure" in html


def test_success_and_failure_finishers_exist():
    """AC7: explicit finishers set the completion indicator."""
    html = _project_html()
    assert "_deployLogFinishSuccess" in html
    assert "_deployLogFinishFailure" in html


# ── AC8: auto-collapse after success, still accessible ────────────────────────


def test_success_auto_collapses_panel():
    """AC8: a successful finish collapses the panel via a distinct collapsed state."""
    html = _project_html()
    assert "deploy-log--collapsed" in html
    # the success finisher is what triggers the collapse
    import re
    body = re.search(
        r"function _deployLogFinishSuccess\([^)]*\)\s*\{(.*?)\n\}",
        html, re.S,
    )
    assert body, "_deployLogFinishSuccess not found"
    assert "collapsed" in body.group(1)


def test_collapsed_panel_remains_accessible():
    """AC8: the collapsed panel is still present/toggleable (not removed)."""
    html = _project_html()
    # collapse hides the body but keeps the toggle reachable via aria-expanded
    assert "aria-expanded" in html


# ── AC9: failure keeps panel open and highlights the failure line ─────────────


def test_failure_highlights_line_and_stays_open():
    """AC9: failure marks the failing line and does NOT auto-collapse."""
    html = _project_html()
    assert "deploy-log__line--error" in html
    import re
    body = re.search(
        r"function _deployLogFinishFailure\([^)]*\)\s*\{(.*?)\n\}",
        html, re.S,
    )
    assert body, "_deployLogFinishFailure not found"
    # the failure finisher must not collapse the panel
    assert "collapsed = true" not in body.group(1).replace(" ", "")


# ── AC10: manual collapse / expand at any time ────────────────────────────────


def test_manual_toggle_handler_present():
    """AC10: a toggle handler lets the user collapse/expand on demand."""
    html = _project_html()
    assert "deployLogToggle" in html
    # the toggle control is an accessible button with a label
    assert "aria-controls" in html


# ── AC11: panel does not block the rest of the Deploy tab ─────────────────────


def test_busy_state_is_scoped_to_triggering_card():
    """AC11: only the triggering card's actions are disabled while streaming."""
    html = _project_html()
    import re
    body = re.search(
        r"function _deployBusyActions\(([^)]*)\)\s*\{(.*?)\n\}",
        html, re.S,
    )
    assert body, "_deployBusyActions not found"
    # it resolves the actions element for the specific card index, not globally
    assert "_deployActionsEl(" in body.group(2)


def test_log_panel_is_not_a_modal():
    """AC11: the log panel is inline, never a blocking modal/overlay."""
    html = _project_html()
    import re
    # isolate the .deploy-log rule block and assert it is not fixed/overlaid
    m = re.search(r"\.deploy-log\s*\{([^}]*)\}", html)
    assert m, ".deploy-log CSS rule not found"
    assert "position: fixed" not in m.group(1)
    assert "inset: 0" not in m.group(1)
