"""Tests for issue #614: Replace inline badges with expandable Details panel on ticket rows."""
import re
from pathlib import Path

PROJECT_HTML = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _fn(src: str, name: str) -> str:
    m = re.search(rf'function {re.escape(name)}\b.*?(?=\nfunction |\n\/\/ ──)', src, re.DOTALL)
    assert m, f"{name} function not found"
    return m.group(0)


# ── AC1: Inline conflict-badge and dep-badge removed from ticket row template ──

def test_no_conflict_badge_call_in_ticket_row():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert '_smgmtConflictBadgeHtml' not in fn, (
        "_smgmtConflictBadgeHtml still called in _smgmtTicketRowHtml — inline conflict badge must be removed"
    )


def test_no_dep_order_badge_call_in_ticket_row():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert '_smgmtDepOrderBadgeHtml' not in fn, (
        "_smgmtDepOrderBadgeHtml still called in _smgmtTicketRowHtml — inline dep-order badge must be removed"
    )


def test_update_conflict_badge_does_not_insert_inline():
    src = _html()
    fn = _fn(src, '_smgmtUpdateConflictBadge')
    assert 'appendChild' not in fn and 'insertBefore' not in fn and '.before(' not in fn, (
        "_smgmtUpdateConflictBadge still inserts an inline badge — must be a no-op after badge removal"
    )


def test_update_dep_order_badge_does_not_insert_inline():
    src = _html()
    fn = _fn(src, '_smgmtUpdateDepOrderBadge')
    assert 'appendChild' not in fn and 'insertBefore' not in fn and '.before(' not in fn, (
        "_smgmtUpdateDepOrderBadge still inserts an inline badge — must be a no-op after badge removal"
    )


# ── AC2: Non-running rows have Details button ─────────────────────────────────

def test_ticket_row_has_t_details_btn():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 't-details-btn' in fn, (
        "t-details-btn not found in _smgmtTicketRowHtml — Details button missing from non-running rows"
    )


def test_finished_row_has_t_details_btn():
    src = _html()
    fn = _fn(src, '_smgmtOutcomeTicketListHtml')
    assert 't-details-btn' in fn, (
        "t-details-btn not found in _smgmtOutcomeTicketListHtml — Details button missing from finished rows"
    )


# ── AC3: Clicking Details expands .ticket-expand panel ───────────────────────

def test_ticket_expand_panel_in_ticket_row():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 'ticket-expand' in fn, (
        "ticket-expand class not found in _smgmtTicketRowHtml — expand panel missing"
    )


def test_expand_panel_has_conflicts_line():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 'Conflicts' in fn, (
        "'Conflicts' text not found in _smgmtTicketRowHtml expand panel"
    )


def test_expand_panel_has_execution_line():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 'Execution' in fn, (
        "'Execution' text not found in _smgmtTicketRowHtml expand panel"
    )


def test_expand_panel_has_reestimate_button():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 'Re-estimate' in fn, (
        "'Re-estimate' action button not found in _smgmtTicketRowHtml expand panel"
    )


def test_expand_panel_has_move_to_sprint_button():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 'Move to sprint' in fn, (
        "'Move to sprint' action button not found in _smgmtTicketRowHtml expand panel"
    )


def test_expand_panel_has_close_ticket_button():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 'Close ticket' in fn, (
        "'Close ticket' action button not found in _smgmtTicketRowHtml expand panel"
    )


# ── AC4: Button toggles label Close/Details and caret ▲/▼ ───────────────────

def test_toggle_fn_has_down_caret():
    src = _html()
    fn = _fn(src, 'toggleTicketRow')
    assert '▼' in fn, "▼ caret not found in toggleTicketRow — collapsed state caret missing"


def test_toggle_fn_has_up_caret():
    src = _html()
    fn = _fn(src, 'toggleTicketRow')
    assert '▲' in fn, "▲ caret not found in toggleTicketRow — expanded state caret missing"


def test_toggle_fn_sets_close_label():
    src = _html()
    fn = _fn(src, 'toggleTicketRow')
    assert 'Close' in fn, "'Close' label not found in toggleTicketRow — open state label missing"


def test_toggle_fn_sets_details_label():
    src = _html()
    fn = _fn(src, 'toggleTicketRow')
    assert 'Details' in fn, "'Details' label not found in toggleTicketRow — collapsed state label missing"


# ── AC5: Row div click goes to _smgmtRowClick, not toggleTicketRow ────────────

def test_row_div_onclick_is_not_toggle():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    # Find the main smgmt-ticket div's onclick attribute
    div_onclick_match = re.search(
        r'<div class="smgmt-ticket[^"]*"[^>]*onclick="([^"]*)"', fn
    )
    if div_onclick_match:
        onclick_val = div_onclick_match.group(1)
        assert 'toggleTicketRow' not in onclick_val, (
            "Row div onclick calls toggleTicketRow — only the Details button should trigger the toggle"
        )


# ── AC6: Running-state rows have no Details button or expand panel ────────────

def test_running_row_no_details_button():
    src = _html()
    fn = _fn(src, '_smgmtRunningCardHtml')
    assert 't-details-btn' not in fn, (
        "t-details-btn found in _smgmtRunningCardHtml — running rows must not have a Details button"
    )


def test_running_row_no_expand_panel():
    src = _html()
    fn = _fn(src, '_smgmtRunningCardHtml')
    assert 'ticket-expand' not in fn, (
        "ticket-expand found in _smgmtRunningCardHtml — running rows must not have an expand panel"
    )


# ── AC7: Scoped IDs ex-{state}-{ticketId} and caret-{state}-{ticketId} ───────

def test_expand_panel_uses_ex_prefixed_id():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert re.search(r'id=["\`]ex-', fn) or 'id=`ex-' in fn or "id=\"ex-" in fn or 'id="ex-' in fn, (
        "expand panel ID not prefixed with 'ex-' — must use ex-{state}-{ticketId} format"
    )


def test_caret_element_uses_caret_prefixed_id():
    src = _html()
    fn = _fn(src, '_smgmtTicketRowHtml')
    assert 'caret-' in fn, (
        "caret-prefixed ID not found in _smgmtTicketRowHtml — must use caret-{state}-{ticketId} for toggle caret"
    )


def test_toggle_fn_uses_ex_id_lookup():
    src = _html()
    fn = _fn(src, 'toggleTicketRow')
    assert 'ex-' in fn, (
        "toggleTicketRow does not reference 'ex-' ID prefix — must look up panel by ex-{state}-{n} ID"
    )


def test_toggle_fn_uses_caret_id_lookup():
    src = _html()
    fn = _fn(src, 'toggleTicketRow')
    assert 'caret-' in fn, (
        "toggleTicketRow does not reference 'caret-' ID prefix — must look up caret by caret-{state}-{n} ID"
    )


# ── AC8: toggleTicketRow defined exactly once ─────────────────────────────────

def test_toggle_ticket_row_defined_once():
    src = _html()
    count = len(re.findall(r'function toggleTicketRow\s*\(', src))
    assert count == 1, (
        f"toggleTicketRow defined {count} times — must be declared exactly once on the page"
    )


# ── AC9: All new CSS classes present in stylesheet ───────────────────────────

def test_css_t_details_btn():
    src = _html()
    assert '.t-details-btn' in src, ".t-details-btn CSS class not defined in stylesheet"


def test_css_ticket_expand():
    src = _html()
    assert '.ticket-expand' in src, ".ticket-expand CSS class not defined in stylesheet"


def test_css_ex_row():
    src = _html()
    assert '.ex-row' in src, ".ex-row CSS class not defined in stylesheet"


def test_css_ex_label():
    src = _html()
    assert '.ex-label' in src, ".ex-label CSS class not defined in stylesheet"


def test_css_ex_actions():
    src = _html()
    assert '.ex-actions' in src, ".ex-actions CSS class not defined in stylesheet"


def test_css_ex_btn():
    src = _html()
    assert '.ex-btn' in src, ".ex-btn CSS class not defined in stylesheet"


def test_css_ex_btn_danger():
    src = _html()
    assert '.ex-btn-danger' in src, ".ex-btn-danger CSS class not defined in stylesheet"
