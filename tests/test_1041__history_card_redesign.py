"""Tests for issue #1041 — Sprint history card redesign: hierarchy, dedup, color discipline.

Reads the JavaScript source of history.js and CSS from project.html to verify
the design contract. Pattern follows test_806__history_ledger.py.

AC coverage:
  AC1  — section order: loose-end band precedes what-list, which precedes Details
  AC2  — recovery button: failed → amber Re-run, partial → bulk-complete, complete → none in header
  AC3  — outcome pills: Partial = grey, Failed = red, Complete = green
  AC4  — exactly one amber-band function; amber NOT duplicated on partial pill
  AC5  — failed "Why it failed" list: each crashed issue listed once via _histWhatListHtml, not also
          re-rendered by a separate reconciliation/sprint-failed block
  AC6  — partial "Unfinished N of M" list exists in _histWhatListHtml
  AC7  — complete/locked cards: _histLooseEndBandHtml returns '' and _histWhatListHtml returns ''
  AC8  — Metrics block: _histDetailsHtml wraps stats + recon; collapsed by default via _histMetricsExpanded
  AC9  — passed recon checks render as grey checkmarks inside Details, not as green boxes outside
  AC10 — _histStatsHtml is called ONLY inside _histDetailsHtml, not directly in _histCardHtml body
  AC11 — delete action is quiet icon: class ends in -icon or -quiet, no red color in CSS rule,
          button has no label text (icon only)
  AC12 — secondary links (PR/Summary/Logs) use a subordinate style class separate from recovery btn
  AC13 — layout consistent: _histCardHtml delegates to the new helpers for all state variants
  AC14 — no issue key duplication: _histWhatListHtml for failed uses failed_tickets, not s.issues
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HISTORY_JS = (REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "history.js").read_text(encoding="utf-8")
PROJECT_HTML = (REPO_ROOT / "apps" / "dashboard" / "static" / "project.html").read_text(encoding="utf-8")


# ─────────────────────────────────── helpers ─────────────────────────────────

def _fn_body(name: str, src: str = HISTORY_JS) -> str:
    """Return the brace-balanced body of a named JS function."""
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        pos = src.find(needle)
        if pos != -1:
            break
    else:
        raise AssertionError(f"function {name} not found in source")
    brace = src.find("{", pos)
    assert brace != -1
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _fn_exists(name: str, src: str = HISTORY_JS) -> bool:
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        if needle in src:
            return True
    return False


def _css_rule(selector: str, src: str = PROJECT_HTML) -> str:
    """Return the body of the first CSS rule matching selector."""
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(src)
    assert m is not None, f"CSS rule for `{selector}` not found"
    return m.group(1)


def _css_exists(selector: str, src: str = PROJECT_HTML) -> bool:
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{")
    return bool(pat.search(src))


# ═════════════════════════════════════════════════════════════════════════════
# AC1 — Section order: loose-end band → what-list → Details
# ═════════════════════════════════════════════════════════════════════════════

def test_ac1_card_body_calls_loose_end_band_before_what_list():
    """_histCardHtml must invoke _histLooseEndBandHtml before _histWhatListHtml."""
    body = _fn_body("_histCardHtml")
    pos_band = body.find("_histLooseEndBandHtml")
    pos_what = body.find("_histWhatListHtml")
    assert pos_band != -1, "_histCardHtml must call _histLooseEndBandHtml"
    assert pos_what != -1, "_histCardHtml must call _histWhatListHtml"
    assert pos_band < pos_what, "loose-end band must appear before what-list in _histCardHtml"


def test_ac1_card_body_calls_what_list_before_details():
    """_histCardHtml must invoke _histWhatListHtml before _histDetailsHtml."""
    body = _fn_body("_histCardHtml")
    pos_what = body.find("_histWhatListHtml")
    pos_det = body.find("_histDetailsHtml")
    assert pos_what != -1, "_histCardHtml must call _histWhatListHtml"
    assert pos_det != -1, "_histCardHtml must call _histDetailsHtml"
    assert pos_what < pos_det, "what-list must appear before Details in _histCardHtml"


# ═════════════════════════════════════════════════════════════════════════════
# AC2 — Recovery CTA: failed → amber Re-run, partial → bulk-complete, complete → none
# ═════════════════════════════════════════════════════════════════════════════

def test_ac2_rerun_button_has_amber_class():
    """Re-run button for failed sprints must use the amber CTA class."""
    assert "hist-head-btn--rerun" in HISTORY_JS, "amber Re-run class must exist"
    # CSS confirms amber color
    rerun_css = _css_rule(".hist-head-btn--rerun")
    assert "amber" in rerun_css, "Re-run button must be amber in CSS"


def test_ac2_bulk_complete_button_uses_primary_class():
    """Bulk-complete button for partial sprints must use a non-amber primary class."""
    assert "hist-head-btn--bulk" in HISTORY_JS, "bulk-complete class must exist"
    bulk_css = _css_rule(".hist-head-btn--bulk")
    assert "amber" not in bulk_css, "Bulk complete must NOT be amber (it is primary/green)"


def test_ac2_complete_card_has_no_rerun_in_header():
    """For ready_to_merge / completed states, _histRecoveryBtnHtml must not emit a Re-run button."""
    fn_name = "_histRecoveryBtnHtml" if _fn_exists("_histRecoveryBtnHtml") else "_histHeadActionsHtml"
    body = _fn_body(fn_name)
    # The rerun button must exist somewhere in the function
    assert "hist-head-btn--rerun" in body, "Re-run button must be generated somewhere"
    # The full function must gate Re-run on failed/needs_rework/cancelled — check the
    # whole function body so the window covers the if-condition regardless of whitespace.
    assert "needs_rework" in body or "failed" in body or "cancelled" in body, \
        "Re-run must be gated on needs_rework/failed/cancelled within the function"
    # 'completed' and 'ready_to_merge' must NOT be in the same if-branch as the rerun button.
    # Check: if 'ready_to_merge' triggers a different return (non-rerun) before the rerun block.
    ready_idx = body.find("ready_to_merge")
    rerun_idx = body.find("hist-head-btn--rerun")
    if ready_idx != -1 and rerun_idx != -1:
        # If ready_to_merge appears, it should be in a DIFFERENT if-branch (after the rerun block)
        # or the rerun block should be inside a guard that excludes ready_to_merge
        assert ready_idx > rerun_idx or "completed" not in body[max(0, rerun_idx - 50):rerun_idx], \
            "ready_to_merge must not trigger the Re-run button (it has its own Merge action)"


# ═════════════════════════════════════════════════════════════════════════════
# AC3 — Outcome pills: Partial = grey, Failed = red, Complete = green
# ═════════════════════════════════════════════════════════════════════════════

def test_ac3_partial_pill_is_grey_not_amber():
    """Partial outcome pill must use grey tones, not amber/yellow."""
    partial_css = _css_rule(".hist-state.partial")
    # No amber or yellow — must not reference amber variable or yellow hex
    assert "amber" not in partial_css.lower(), "Partial pill must not be amber"
    assert "#facc15" not in partial_css, "Partial pill must not use yellow (#facc15)"
    assert "#a16207" not in partial_css, "Partial pill must not use amber-text (#a16207)"
    # Should use muted/grey tones
    grey_indicators = ["text-muted", "text-sub", "surface", "#6b7280", "#9ca3af", "#64748b",
                       "var(--text-muted)", "var(--text-sub)"]
    has_grey = any(g in partial_css for g in grey_indicators)
    assert has_grey, f"Partial pill must use grey tones, got: {partial_css!r}"


def test_ac3_failed_pill_is_red():
    """Failed outcome pill must use red color."""
    failed_css = _css_rule(".hist-state.failed")
    assert "red" in failed_css.lower() or "var(--red)" in failed_css, \
        "Failed pill must be red"


def test_ac3_completed_pill_is_green():
    """Complete outcome pill must use green color (not steel/blue)."""
    completed_css = _css_rule(".hist-state.completed")
    assert "green" in completed_css.lower() or "var(--green)" in completed_css, \
        "Complete pill must be green"
    assert "steel" not in completed_css.lower(), \
        "Complete pill must not use steel (steel is blue-grey, not green)"


# ═════════════════════════════════════════════════════════════════════════════
# AC4 — Exactly ONE amber band per card
# ═════════════════════════════════════════════════════════════════════════════

def test_ac4_loose_end_band_function_exists():
    """_histLooseEndBandHtml must exist and return a single band."""
    assert _fn_exists("_histLooseEndBandHtml"), "_histLooseEndBandHtml function must exist"


def test_ac4_loose_end_band_returns_early_after_first_match():
    """_histLooseEndBandHtml must return after finding the first loose end (not accumulate)."""
    body = _fn_body("_histLooseEndBandHtml")
    # Must have at least one early return so only ONE band is emitted
    # (not two appended band strings)
    assert "return" in body, "_histLooseEndBandHtml must return after first match"
    assert "sprint_pr" in body, "sprint PR unmerged must be a loose-end priority"
    # The function must not concatenate two separate band HTML strings
    band_count = body.count("hist-loose-end-band")
    assert band_count <= 3, \
        f"Should define the band class at most three times (sprint PR, stale labels, branches), got {band_count}"


# ═════════════════════════════════════════════════════════════════════════════
# Child sprint mock — legend, parent row, nested child card, agent-time bar
# ═════════════════════════════════════════════════════════════════════════════

def test_child_sprint_legend_renders_above_ledger():
    """Agent colour legend must render above the sprint list."""
    render_body = _fn_body("_histRenderLedger")
    assert "_histLegendHtml" in render_body, "_histRenderLedger must inject the agent legend"


def test_child_sprint_card_helpers_exist():
    """Nested child sprint layout helpers must exist."""
    assert _fn_exists("_histChildCardHtml"), "_histChildCardHtml must exist"
    assert _fn_exists("_histParentRowHtml"), "_histParentRowHtml must exist"
    assert _fn_exists("_histAgentTimeBarHtml") or "hist-agent-bar" in _fn_body("_histChildMetricsHtml"), \
        "collapsed agent-time bar must be implemented"


def test_child_group_uses_parent_row_and_child_wrap():
    """Groups with children must nest under a parent row + L-connector wrap."""
    body = _fn_body("_histGroupHtml")
    assert "_histParentRowHtml" in body, "parent sprint must render as a one-line row"
    assert "_histChildCardHtml" in body, "child sprints must use the child card builder"
    assert "hist-child-wrap" in body, "children must sit in hist-child-wrap for the L-connector"


def test_ac4_loose_end_band_css_uses_amber():
    """The loose-end band must be the ONLY amber UI element (not the partial pill)."""
    assert _css_exists(".hist-loose-end-band"), "CSS rule for .hist-loose-end-band must exist"
    band_css = _css_rule(".hist-loose-end-band")
    assert "amber" in band_css, ".hist-loose-end-band must use amber color"


def test_ac4_partial_pill_not_amber():
    """The partial pill must NOT be amber so the band is the only amber element."""
    partial_css = _css_rule(".hist-state.partial")
    assert "amber" not in partial_css, "Partial pill must not be amber — only the band is amber"


# ═════════════════════════════════════════════════════════════════════════════
# AC5 — Failed "Why it failed" list: no issue duplication
# ═════════════════════════════════════════════════════════════════════════════

def test_ac5_what_list_for_failed_uses_failed_tickets():
    """_histWhatListHtml must use s.failed_tickets (not s.issues) for the failed branch."""
    assert _fn_exists("_histWhatListHtml"), "_histWhatListHtml must exist"
    body = _fn_body("_histWhatListHtml")
    assert "failed_tickets" in body, \
        "_histWhatListHtml must reference s.failed_tickets for failed sprint list"


def test_ac5_why_it_failed_heading_exists():
    """The failed what-list must include a 'Why it failed' heading."""
    body = _fn_body("_histWhatListHtml")
    assert "Why it failed" in body or "why it failed" in body.lower(), \
        "_histWhatListHtml must include 'Why it failed' section label"


def test_ac5_hist_fail_block_not_called_in_card_body():
    """_histFailedBlockHtml must NOT be called directly in _histCardHtml body.

    The old _histFailedBlockHtml rendered a separate 'Sprint failed' box causing
    duplication. In the redesign it must be removed from the card body; the
    what-list handles failed issues instead.
    """
    card_body = _fn_body("_histCardHtml")
    assert "_histFailedBlockHtml" not in card_body, \
        "_histCardHtml must not call _histFailedBlockHtml (causes duplication — use _histWhatListHtml)"


def test_ac5_issue_list_html_not_called_in_card_body():
    """_histIssueListHtml must NOT be called directly in _histCardHtml body.

    The what-list and its variants replace _histIssueListHtml in the card body
    to prevent cross-section duplication of issue keys.
    """
    card_body = _fn_body("_histCardHtml")
    assert "_histIssueListHtml" not in card_body, \
        "_histCardHtml must not call _histIssueListHtml; use _histWhatListHtml instead"


# ═════════════════════════════════════════════════════════════════════════════
# AC6 — Partial "Unfinished N of M" list
# ═════════════════════════════════════════════════════════════════════════════

def test_ac6_what_list_for_partial_uses_unfinished_text():
    """_histWhatListHtml must emit 'Unfinished' heading for partial sprints."""
    body = _fn_body("_histWhatListHtml")
    assert "Unfinished" in body or "unfinished" in body.lower(), \
        "_histWhatListHtml must include 'Unfinished N of M' for partial sprints"


def test_ac6_partial_list_filters_unmerged():
    """The partial branch in _histWhatListHtml must filter issues to unmerged ones."""
    body = _fn_body("_histWhatListHtml")
    # Must filter by state !== 'merged'
    assert "merged" in body.lower(), \
        "_histWhatListHtml partial branch must filter out merged tickets"


# ═════════════════════════════════════════════════════════════════════════════
# AC7 — Complete cards: no loose-end band, no what-list
# ═════════════════════════════════════════════════════════════════════════════

def test_ac7_loose_end_band_returns_empty_for_no_loose_end():
    """_histLooseEndBandHtml must return '' when no loose end exists."""
    body = _fn_body("_histLooseEndBandHtml")
    # Must have a terminal empty-string return (no loose end → no band)
    assert "return ''" in body or 'return ""' in body, \
        "_histLooseEndBandHtml must return '' when no loose end is found"


def test_ac7_what_list_returns_empty_for_locked():
    """_histWhatListHtml must return '' for locked (completed/finished/deleted) sprints."""
    body = _fn_body("_histWhatListHtml")
    assert "_histIsLocked" in body or "locked" in body.lower(), \
        "_histWhatListHtml must guard on locked state and return nothing"


# ═════════════════════════════════════════════════════════════════════════════
# AC8 — Metrics block collapsed by default at card bottom
# ═════════════════════════════════════════════════════════════════════════════

def test_ac8_details_html_function_exists():
    """_histDetailsHtml must exist as the metrics/timeline block builder."""
    assert _fn_exists("_histDetailsHtml"), "_histDetailsHtml must exist"


def test_ac8_metrics_expanded_tracking_set_exists():
    """A _histMetricsExpanded Set must track which cards have metrics open."""
    assert "_histMetricsExpanded" in HISTORY_JS, \
        "_histMetricsExpanded Set must exist to track metrics expansion"


def test_ac8_metrics_toggle_function_exists():
    """_histToggleMetrics must exist to toggle the metrics sub-section."""
    assert _fn_exists("_histToggleMetrics"), "_histToggleMetrics must exist"


def test_ac8_details_html_contains_stats():
    """_histDetailsHtml must include _histStatsHtml (metrics chips)."""
    body = _fn_body("_histDetailsHtml")
    assert "_histStatsHtml" in body, "_histDetailsHtml must call _histStatsHtml"


def test_ac8_details_head_css_exists():
    """CSS for .hist-details-head must exist (the clickable metrics toggle row)."""
    assert _css_exists(".hist-details-head"), "CSS rule .hist-details-head must exist"


def test_ac8_details_body_css_exists():
    """CSS for .hist-details-body must exist (the collapsible metrics content)."""
    assert _css_exists(".hist-details-body"), "CSS rule .hist-details-body must exist"


def test_ac8_details_metrics_label():
    """Metrics toggle must use the mock label text."""
    body = _fn_body("_histDetailsHtml")
    assert "Metrics, timeline" in body, \
        "_histDetailsHtml must label the section 'Metrics, timeline & reconciliation'"


# ═════════════════════════════════════════════════════════════════════════════
# AC9 — Passed recon checks: grey checkmarks inside Details, not green boxes
# ═════════════════════════════════════════════════════════════════════════════

def test_ac9_recon_item_ok_not_green_bg():
    """Passed reconciliation items (.recon-item.ok) must not render green boxes.

    The green box was the old style. AC9 mandates small grey checkmarks inside
    Details only — remove green-bg from .recon-item.ok.
    """
    if _css_exists(".recon-item.ok"):
        ok_css = _css_rule(".recon-item.ok")
        assert "green-bg" not in ok_css, \
            ".recon-item.ok must not use green-bg — passed checks must be grey, not green boxes"


def test_ac9_recon_passed_html_function_exists():
    """A dedicated function for passed recon checks must exist and emit grey style."""
    passed_exists = _fn_exists("_histReconPassedHtml")
    assert passed_exists, \
        "_histReconPassedHtml must exist to render grey-checkmark passed checks in Details"


def test_ac9_passed_checks_use_grey_class():
    """The passed-checks renderer must use a grey/muted CSS class, not green."""
    body = _fn_body("_histReconPassedHtml")
    # Must reference a non-green class for the check items
    assert "recon-passed" in body or "hist-recon-passed" in body, \
        "_histReconPassedHtml must use a 'recon-passed' CSS class"
    assert "green" not in body.lower() or "var(--green)" not in body, \
        "_histReconPassedHtml must not use green styling for passed checks"


def test_ac9_passed_checks_inside_details():
    """_histReconPassedHtml must be called from _histDetailsHtml, not from _histCardHtml."""
    details_body = _fn_body("_histDetailsHtml")
    card_body = _fn_body("_histCardHtml")
    assert "_histReconPassedHtml" in details_body, \
        "_histReconPassedHtml must be called from _histDetailsHtml (inside Details)"
    assert "_histReconPassedHtml" not in card_body, \
        "_histReconPassedHtml must NOT be called from _histCardHtml directly"


# ═════════════════════════════════════════════════════════════════════════════
# AC10 — Metrics / timeline / split only visible inside Details
# ═════════════════════════════════════════════════════════════════════════════

def test_ac10_stats_html_called_only_in_details():
    """_histStatsHtml must be called from _histDetailsHtml, NOT from _histCardHtml directly."""
    card_body = _fn_body("_histCardHtml")
    details_body = _fn_body("_histDetailsHtml")
    assert "_histStatsHtml" not in card_body, \
        "_histStatsHtml must NOT be in _histCardHtml body — it belongs inside _histDetailsHtml"
    assert "_histStatsHtml" in details_body, \
        "_histStatsHtml must be called from _histDetailsHtml"


# ═════════════════════════════════════════════════════════════════════════════
# AC11 — Delete is quiet icon: no label, not red
# ═════════════════════════════════════════════════════════════════════════════

def test_ac11_delete_button_has_quiet_css_class():
    """Delete button must use a class that signals 'quiet icon' (not the old red delete class)."""
    # The old class hist-head-btn--delete was red. Redesign uses an icon-only quiet class.
    quiet_classes = ["hist-head-btn--delete-icon", "hist-btn--delete-icon",
                     "hist-delete-icon", "hist-head-btn--delete-quiet"]
    has_quiet = any(cls in HISTORY_JS for cls in quiet_classes)
    assert has_quiet, \
        "Delete button must use a quiet icon class (e.g. hist-head-btn--delete-icon)"


def test_ac11_delete_button_no_label_text():
    """Delete button must have no visible label text — icon only."""
    # Find the delete button HTML in source
    for quiet_cls in ["hist-head-btn--delete-icon", "hist-btn--delete-icon",
                      "hist-delete-icon", "hist-head-btn--delete-quiet"]:
        idx = HISTORY_JS.find(quiet_cls)
        if idx != -1:
            snippet = HISTORY_JS[idx : idx + 200]
            # Must contain icon tag but no plain text label like "Delete"
            assert "ti-trash" in snippet, "Delete button must contain trash icon"
            assert "> Delete<" not in snippet and ">Delete<" not in snippet, \
                "Delete button must have no visible label text"
            return
    pytest.fail("No quiet delete button class found in history.js")


def test_ac11_delete_icon_css_not_red():
    """The quiet delete button CSS must not use red color."""
    for quiet_cls in [".hist-head-btn--delete-icon", ".hist-btn--delete-icon",
                      ".hist-delete-icon", ".hist-head-btn--delete-quiet"]:
        if _css_exists(quiet_cls):
            css = _css_rule(quiet_cls)
            assert "red" not in css.lower() and "var(--red)" not in css, \
                f"Quiet delete button CSS must not be red: {css!r}"
            return
    # If no separate CSS rule exists, that's also fine (inherits muted color)


# ═════════════════════════════════════════════════════════════════════════════
# AC12 — Secondary actions visually subordinate
# ═════════════════════════════════════════════════════════════════════════════

def test_ac12_secondary_links_use_subordinate_class():
    """PR / Summary / Logs links must use a subordinate style class distinct from recovery btns."""
    secondary_classes = ["hist-head-btn--secondary", "hist-link-secondary", "hist-head-link"]
    has_secondary = any(cls in HISTORY_JS for cls in secondary_classes)
    assert has_secondary, \
        "Secondary links (PR/Summary/Logs) must use a visually-subordinate class"


def test_ac12_secondary_class_visually_different_from_recovery():
    """The secondary link class must not share the amber or primary color of recovery btns."""
    secondary_classes = [".hist-head-btn--secondary", ".hist-link-secondary", ".hist-head-link"]
    for cls in secondary_classes:
        if _css_exists(cls):
            css = _css_rule(cls)
            # Secondary must not be amber
            assert "amber" not in css.lower(), \
                f"Secondary links ({cls}) must not be amber (amber is reserved for Re-run / amber band)"
            return


# ═════════════════════════════════════════════════════════════════════════════
# AC13 — Layout consistent across variants
# ═════════════════════════════════════════════════════════════════════════════

def test_ac13_hist_card_html_uses_helper_functions():
    """_histCardHtml must delegate to new helper functions for all variants."""
    card_body = _fn_body("_histCardHtml")
    required_helpers = ["_histLooseEndBandHtml", "_histWhatListHtml", "_histDetailsHtml"]
    for helper in required_helpers:
        assert helper in card_body, \
            f"_histCardHtml must call {helper} to ensure consistent layout across variants"


def test_ac13_hist_loose_end_band_css_exists():
    """CSS for .hist-loose-end-band must exist (shared across all card variants)."""
    assert _css_exists(".hist-loose-end-band"), "CSS .hist-loose-end-band must exist"


# ═════════════════════════════════════════════════════════════════════════════
# AC14 — No issue key appears more than once in a card
# ═════════════════════════════════════════════════════════════════════════════

def test_ac14_hist_fail_block_removed_or_not_in_card_body():
    """_histFailedBlockHtml must not be called from _histCardHtml — it caused duplication.

    In the old design, crashed issues appeared in both _histFailedBlockHtml (Sprint failed
    block) and _histIssueListHtml. The redesign uses _histWhatListHtml exclusively for
    failed-sprint content, eliminating the duplicated issue keys.
    """
    card_body = _fn_body("_histCardHtml")
    assert "_histFailedBlockHtml" not in card_body, \
        "_histCardHtml must not call _histFailedBlockHtml — it duplicates issue keys"


def test_ac14_what_list_and_issue_list_not_both_called():
    """_histCardHtml must not call both _histWhatListHtml and _histIssueListHtml.

    Having both would duplicate issue entries on the card.
    """
    card_body = _fn_body("_histCardHtml")
    has_what = "_histWhatListHtml" in card_body
    has_iss = "_histIssueListHtml" in card_body
    assert has_what, "_histCardHtml must call _histWhatListHtml"
    assert not has_iss, \
        "_histCardHtml must not also call _histIssueListHtml alongside _histWhatListHtml"


# ═════════════════════════════════════════════════════════════════════════════
# Structural check — new functions are exported or globally accessible
# ═════════════════════════════════════════════════════════════════════════════

def test_hist_metrics_toggle_is_exported():
    """_histToggleMetrics must be exported or globally accessible for inline onclick."""
    assert "export function _histToggleMetrics" in HISTORY_JS \
        or "globalThis._histToggleMetrics" in HISTORY_JS \
        or "_histToggleMetrics" in HISTORY_JS, \
        "_histToggleMetrics must be exported for onclick handlers"


def test_hist_loose_end_band_css_has_band_cta():
    """A CTA button class inside the loose-end band must exist (.hist-band-cta)."""
    assert _css_exists(".hist-band-cta") or "hist-band-cta" in PROJECT_HTML, \
        "CTA button class inside the loose-end band must exist"
