"""
Tests for issue #1850: Remove Logs Timeline (Runs) view.

AC1: Timeline segment removed from Logs view switcher; logs-view-timeline,
     _logsRunRowHtml, logsRenderTimeline, _logsFilteredRuns, _logsAppendRunRow
     dead paths deleted.
AC2: Anything that previously deep-linked to the Logs Timeline view now
     redirects to Sprint History (logsSetView('timeline') navigates to
     sprint-mgmt/history sub-view).
AC3: _logsAppendRunRow has no other consumers — verified and deleted.
AC4: Activity and Raw views work unchanged.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent
PROJECT_HTML = ROOT / "apps/dashboard/static/project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# ── AC1: Timeline segment and dead-code functions removed ─────────────────────

def test_ac1_logs_view_timeline_button_removed():
    """AC1: The Runs/Timeline segment button must not exist in the Logs toolbar."""
    assert 'id="logs-view-timeline"' not in _html(), (
        'logs-view-timeline button still present — remove it from the Logs toolbar'
    )


def test_ac1_logs_timeline_container_removed():
    """AC1: The #logs-timeline container div must be removed from the Logs body."""
    assert 'id="logs-timeline"' not in _html(), (
        '#logs-timeline container still present — remove it from the Logs body'
    )


def test_ac1_logs_run_row_html_function_removed():
    """AC1: _logsRunRowHtml function must be deleted."""
    assert 'function _logsRunRowHtml(' not in _html(), (
        '_logsRunRowHtml still defined — delete this dead function'
    )


def test_ac1_logs_render_timeline_function_removed():
    """AC1: logsRenderTimeline function must be deleted."""
    assert 'function logsRenderTimeline(' not in _html(), (
        'logsRenderTimeline still defined — delete this dead function'
    )


def test_ac1_logs_filtered_runs_function_removed():
    """AC1: _logsFilteredRuns function must be deleted."""
    assert 'function _logsFilteredRuns(' not in _html(), (
        '_logsFilteredRuns still defined — delete this dead function'
    )


def test_ac1_logs_append_run_row_function_removed():
    """AC1: _logsAppendRunRow function must be deleted (AC3 verified: no other consumers)."""
    assert 'function _logsAppendRunRow(' not in _html(), (
        '_logsAppendRunRow still defined — verified no other consumers; delete it'
    )


def test_ac1_no_calls_to_timeline_functions():
    """AC1: No remaining calls to the deleted timeline functions."""
    html = _html()
    assert 'logsRenderTimeline()' not in html, 'stale call to logsRenderTimeline() found'
    assert '_logsAppendRunRow(' not in html, 'stale call to _logsAppendRunRow( found'
    assert '_logsFilteredRuns(' not in html, 'stale call to _logsFilteredRuns( found'


# ── AC2: logsSetView('timeline') redirects to Sprint History ──────────────────

def test_ac2_logs_set_view_timeline_redirects_to_sprint_history():
    """AC2: logsSetView('timeline') must navigate to Sprint History, not render timeline."""
    html = _html()
    assert "logsSetView('timeline')" not in html, (
        "logsSetView('timeline') still called from the HTML — "
        "the Runs button must be removed"
    )


def test_ac2_logs_set_view_contains_sprint_mgmt_redirect():
    """AC2: logsSetView must redirect timeline mode to Sprint History (sprint-mgmt/history)."""
    html = _html()
    # Find the logsSetView function body
    start = html.find("function logsSetView(")
    assert start != -1, "logsSetView function not found in project.html"
    # Extract the function — find the closing brace by counting braces
    brace_depth = 0
    fn_start = html.index("{", start)
    fn_end = fn_start
    for i in range(fn_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                fn_end = i
                break
    fn_body = html[fn_start:fn_end + 1]
    assert "sprint-mgmt" in fn_body, (
        "logsSetView must redirect 'timeline' mode to sprint-mgmt (Sprint History). "
        "Add: if (mode === 'timeline') { switchTab('sprint-mgmt'); _smgmtShowSubView('history'); return; }"
    )


# ── AC4: Activity and Raw views work unchanged ────────────────────────────────

def test_ac4_logs_view_activity_button_present():
    """AC4: Activity segment button must remain in the Logs toolbar."""
    assert 'id="logs-view-activity"' in _html(), (
        'logs-view-activity button is missing — Activity view must be preserved'
    )


def test_ac4_logs_view_raw_button_present():
    """AC4: Raw segment button must remain in the Logs toolbar."""
    assert 'id="logs-view-raw"' in _html(), (
        'logs-view-raw button is missing — Raw view must be preserved'
    )


def test_ac4_logs_activity_container_present():
    """AC4: #logs-activity container must remain in the Logs body."""
    assert 'id="logs-activity"' in _html(), (
        '#logs-activity container is missing — Activity view must be preserved'
    )


def test_ac4_logs_raw_container_present():
    """AC4: #logs-raw container must remain in the Logs body."""
    assert 'id="logs-raw"' in _html(), (
        '#logs-raw container is missing — Raw view must be preserved'
    )


def test_ac4_logs_set_view_function_present():
    """AC4: logsSetView function must still exist."""
    assert 'function logsSetView(' in _html(), (
        'logsSetView is missing — required for Activity and Raw views'
    )


def test_ac4_logs_activity_handled_in_set_view():
    """AC4: logsSetView must still handle 'activity' mode by calling evlRender or evlFetch."""
    html = _html()
    start = html.find("function logsSetView(")
    fn_start = html.index("{", start)
    fn_end = fn_start
    brace_depth = 0
    for i in range(fn_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                fn_end = i
                break
    fn_body = html[fn_start:fn_end + 1]
    assert "activity" in fn_body, "logsSetView no longer handles 'activity' mode"
    assert "evlRender" in fn_body or "evlFetch" in fn_body, (
        "logsSetView activity branch must call evlRender or evlFetch"
    )


def test_ac4_logs_raw_handled_in_set_view():
    """AC4: logsSetView must still handle 'raw' mode."""
    html = _html()
    start = html.find("function logsSetView(")
    fn_start = html.index("{", start)
    fn_end = fn_start
    brace_depth = 0
    for i in range(fn_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                fn_end = i
                break
    fn_body = html[fn_start:fn_end + 1]
    assert "raw" in fn_body, "logsSetView no longer handles 'raw' mode"
    assert "logsLoadRawView" in fn_body, (
        "logsSetView raw branch must call logsLoadRawView"
    )


def test_ac4_logs_apply_filters_handles_activity_and_raw():
    """AC4: _logsApplyFilters must still route to activity and raw, without timeline."""
    html = _html()
    start = html.find("function _logsApplyFilters(")
    assert start != -1, "_logsApplyFilters function is missing"
    fn_start = html.index("{", start)
    fn_end = fn_start
    brace_depth = 0
    for i in range(fn_start, len(html)):
        if html[i] == "{":
            brace_depth += 1
        elif html[i] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                fn_end = i
                break
    fn_body = html[fn_start:fn_end + 1]
    assert "evlRender" in fn_body, "_logsApplyFilters must still call evlRender for activity"
    assert "_logsRenderRawStream" in fn_body, "_logsApplyFilters must still call _logsRenderRawStream for raw"
    assert "logsRenderTimeline" not in fn_body, "_logsApplyFilters must not call logsRenderTimeline (deleted)"
