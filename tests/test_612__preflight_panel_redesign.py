"""Tests for issue #612: Redesign sprint pre-flight panel with grouped summary chips."""
import re
from pathlib import Path

PROJECT_HTML = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


# AC: Two chips side-by-side — amber `N missing AC` and green `N conflicts · order planned`
def test_amber_chip_missing_ac_exists():
    src = _html()
    assert "missing AC" in src, (
        "Amber chip with 'missing AC' text not found in pre-flight banner template"
    )


def test_green_chip_conflicts_order_planned_exists():
    src = _html()
    assert "conflicts" in src and "order planned" in src, (
        "Green chip with 'conflicts · order planned' text not found in pre-flight banner template"
    )


def test_chips_use_amber_and_green_css():
    src = _html()
    # The banner must reference pf-chip CSS classes with amber and green styling
    assert "pf-chip" in src, (
        "pf-chip CSS class not found — chip styling not implemented"
    )
    assert "pf-chip--amber" in src or ("pf-chip" in src and "amber" in src), (
        "Amber chip variant not found in pre-flight banner"
    )
    assert "pf-chip--green" in src or ("pf-chip" in src and "green" in src), (
        "Green chip variant not found in pre-flight banner"
    )


# AC: No individual ticket rows rendered in collapsed state; old "Show N more" removed
def test_no_show_n_more_pattern():
    src = _html()
    # The old "Show N more" toggle must be gone from _preflightBannerHtml
    assert "Show ${hiddenCount} more" not in src and "Show N more" not in src, (
        "Old 'Show N more' pattern still present in pre-flight banner template"
    )


def test_no_preflight_item_rows_in_collapsed_template():
    src = _html()
    # smgmt-preflight-item was the old per-ticket row class — must be removed from banner HTML
    assert 'class="smgmt-preflight-item"' not in src, (
        "Old smgmt-preflight-item per-ticket row still rendered in banner template"
    )


# AC: "Show details" button with chevron-down icon, flush right in chip row
def test_show_details_button_exists():
    src = _html()
    assert "Show details" in src, (
        "'Show details' button label not found in pre-flight banner"
    )


def test_show_details_chevron_down_icon():
    src = _html()
    assert "ti-chevron-down" in src, (
        "chevron-down icon not found in pre-flight 'Show details' button"
    )


# AC: Clicking "Show details" expands .pf-details, changes label to "Hide details", flips chevron up
def test_pf_details_class_exists():
    src = _html()
    assert "pf-details" in src, (
        ".pf-details class not found — collapsible detail section not implemented"
    )


def test_hide_details_label_exists():
    src = _html()
    assert "Hide details" in src, (
        "'Hide details' label not found — toggle state not implemented"
    )


def test_chevron_up_icon_for_expanded_state():
    src = _html()
    assert "ti-chevron-up" in src, (
        "chevron-up icon not found — expanded state chevron flip not implemented"
    )


def test_toggle_function_exists():
    src = _html()
    # A JS function must handle the expand/collapse toggle
    assert "_preflightToggleDetails(" in src or "preflightToggle" in src, (
        "Toggle function for Show/Hide details not found"
    )


# AC: Expanded section row 1 — pencil-off icon + mono issue numbers + "missing acceptance criteria"
def test_detail_row_pencil_off_icon():
    src = _html()
    assert "ti-pencil-off" in src, (
        "pencil-off icon not found in pre-flight detail row 1"
    )


def test_detail_row_missing_ac_text():
    src = _html()
    assert "missing acceptance criteria" in src, (
        "'missing acceptance criteria' text not found in pre-flight detail row 1"
    )


# AC: Expanded section row 2 — git-branch icon + conflict resolution summary
def test_detail_row_git_branch_icon():
    src = _html()
    assert "ti-git-branch" in src, (
        "git-branch icon not found in pre-flight detail row 2"
    )


# AC: Run Sprint button remains enabled regardless of pre-flight issues
def test_run_sprint_not_disabled_by_preflight():
    src = _html()
    # Pre-flight code must NOT set disabled on smgmt-run-btn based on preflight items
    # Find the _preflightInjectBanner / _preflightBannerHtml functions and ensure
    # they don't touch smgmt-run-btn disabled state
    pf_banner_match = re.search(
        r'function _preflightBannerHtml\b.*?(?=\nfunction |\nconst |\nlet |\nvar |\n\/\/\s*──)',
        src, re.DOTALL
    )
    if pf_banner_match:
        banner_fn = pf_banner_match.group(0)
        assert 'smgmt-run-btn' not in banner_fn, (
            "_preflightBannerHtml modifies Run Sprint button state"
        )
        assert 'disabled' not in banner_fn or 'smgmt-run-btn' not in banner_fn, (
            "_preflightBannerHtml sets disabled state on run button"
        )


# AC: New CSS is added inside existing <style> block (no new <style> tag)
def test_no_new_style_tag_added():
    src = _html()
    # Count <style> opening tags — should remain the same as before (one main block)
    # The key check: no <style> tag appears after the main </style> closing for pf- classes
    pf_chip_pos = src.find("pf-chip")
    if pf_chip_pos == -1:
        # If pf-chip not found yet, test will catch it via test_chips_use_amber_and_green_css
        return
    # Find the <style> tag before pf-chip position
    style_before = src.rfind("<style", 0, pf_chip_pos)
    style_close_before = src.rfind("</style>", 0, pf_chip_pos)
    # pf-chip CSS must be inside a <style> block (style_before > style_close_before)
    assert style_before > style_close_before, (
        "pf-chip CSS appears outside a <style> block — new <style> tag may have been added"
    )


# AC: Panel does not render when both counts are 0
def test_panel_not_rendered_when_both_zero():
    src = _html()
    # The banner function must return empty string when missingAC=0 and conflicts=0
    # Check for a guard that checks both counts before rendering
    pf_banner_match = re.search(
        r'function _preflightBannerHtml\b.*?(?=\nfunction |\nconst |\nlet |\nvar |\n\/\/\s*──)',
        src, re.DOTALL
    )
    assert pf_banner_match, "_preflightBannerHtml function not found"
    banner_fn = pf_banner_match.group(0)
    # Must have a return '' / return "" guard when counts are 0
    assert "return ''" in banner_fn or 'return ""' in banner_fn, (
        "_preflightBannerHtml has no early-return guard for zero counts"
    )


# AC: Old smgmt-preflight-item CSS rule removed (no longer needed)
def test_old_preflight_item_css_removed():
    src = _html()
    assert '.smgmt-preflight-item {' not in src, (
        ".smgmt-preflight-item CSS rule still present — old per-ticket row CSS not cleaned up"
    )


def test_old_preflight_toggle_more_css_removed():
    src = _html()
    assert '.smgmt-preflight-toggle-more {' not in src, (
        ".smgmt-preflight-toggle-more CSS rule still present — old Show N more CSS not cleaned up"
    )
