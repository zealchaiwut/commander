"""Tests for issue #611: Remove finish-card div, Sort button; rename badges."""
import re
from pathlib import Path

PROJECT_HTML = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# AC: No .finish-card div exists in the DOM when Sprint Mgmt loads
def test_no_finish_card_div_in_template():
    src = _html()
    # The JS template that injects finish-card divs must not be present
    assert 'smgmt-finish-card' not in src or 'id="smgmt-finish-card-' not in src, (
        "finish-card div template still present in JS"
    )


# AC: No Sort button appears in any sprint pane header row
def test_no_sort_btn_call_in_card_html():
    src = _html()
    assert '_smgmtSortBtnHtml(' not in src, (
        "_smgmtSortBtnHtml() still called in sprint card HTML template"
    )


def test_no_sort_btn_element_generated():
    src = _html()
    # The function that generates smgmt-sort-btn HTML must not generate visible buttons
    assert 'class="smgmt-sort-btn' not in src, (
        "smgmt-sort-btn element HTML still generated in template strings"
    )


# AC: CSS rules .finish-card, .finish-card-head, .finish-card-sub, .fc-link removed
def test_finish_card_css_rule_removed():
    src = _html()
    assert '.smgmt-finish-card {' not in src, (
        ".smgmt-finish-card CSS rule still present"
    )


def test_sfc_header_css_removed():
    src = _html()
    assert '.sfc-header' not in src, (
        ".sfc-header CSS rule still present (represents .finish-card-head)"
    )


def test_sfc_title_css_removed():
    src = _html()
    assert '.sfc-title' not in src, (
        ".sfc-title CSS rule still present (represents .finish-card-sub)"
    )


def test_sfc_link_css_removed():
    src = _html()
    assert '.sfc-branch-link' not in src and '.sfc-summary-link' not in src, (
        "sfc-*-link CSS rules still present (represent .fc-link)"
    )


# AC: Needs-rework badge reads NEEDS REWORK (unified lifecycle, P4)
def test_badge_has_rework_text():
    src = _html()
    assert "'NEEDS REWORK'" in src or '"NEEDS REWORK"' in src, (
        "Badge text for needs_rework state is not 'NEEDS REWORK'"
    )
    assert "'HAS REWORK'" not in src and '"HAS REWORK"' not in src, (
        "Legacy badge text 'HAS REWORK' still present"
    )


def test_badge_need_fixed_sprint_removed():
    src = _html()
    assert 'Need Fixed Sprint' not in src, (
        "Old badge text 'Need Fixed Sprint' still present"
    )


# AC: Finished sprint pane header badge reads COMPLETED
def test_badge_completed_text():
    src = _html()
    assert "'COMPLETED'" in src or '"COMPLETED"' in src, (
        "Badge text for completed state is not 'COMPLETED'"
    )


def test_badge_finished_text_removed():
    src = _html()
    # 'Finished' as a badge value (in JS object or template string) must be gone
    # Must not appear as badge content 'Finished' in the META map or fallback template
    assert "badge: 'Finished'" not in src and 'badge: "Finished"' not in src, (
        "Old badge text 'Finished' still used in _smgmtStateMeta META map"
    )


def test_fallback_finished_badge_uses_completed():
    src = _html()
    # The fallback path (line 9015 equivalent) must use COMPLETED not Finished
    assert 'state-finished">Finished<' not in src, (
        "Fallback finished badge still renders 'Finished' instead of 'COMPLETED'"
    )
