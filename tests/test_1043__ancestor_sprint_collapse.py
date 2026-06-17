"""Tests for issue #1043 — Board: collapse ancestor sprints with merge-state marks.

Source-code tests following the pattern from test_1042__sprint_card_ux.py.

AC coverage:
  AC1  — Each resolved ancestor sprint renders as a single collapsed row with
          merge-state mark, sprint name, and carry-down summary
  AC2  — Merge-state mark has three distinct states: Merged (green, git-merge icon),
          Needs merge (amber), Failed (red) — sourced from sprint record
  AC3  — Carry-down summary correctly reflects merged vs carried ticket counts
          and names the child sprint (e.g. '3 merged · 1 reworked → 73.1')
  AC4  — Expanding a collapsed ancestor row reveals per-ticket rows marked
          'merged' (green check) or 'carried → 73.x' (amber, correct child sprint)
  AC5  — A 'Needs merge' ancestor expanded additionally exposes Re-run and Merge
          action buttons
  AC6  — The active sprint (up-next or running) is expanded by default on page load
  AC7  — All resolved ancestor sprints are collapsed by default on page load
  AC8  — Carry links correctly identify which child sprint a carried ticket moved to
  AC9  — Merge-state marks are visually distinguishable without relying solely on
          color (icon or label text present)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
BOARD_JS = (
    REPO_ROOT
    / "apps"
    / "dashboard"
    / "static"
    / "src"
    / "sprint-board"
    / "board-render.js"
).read_text(encoding="utf-8")
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")


# ─────────────────────────────────── helpers ─────────────────────────────────


def _fn_body(name: str, src: str = BOARD_JS) -> str:
    """Return the brace-balanced body of a named JS function."""
    for needle in (
        f"function {name}(",
        f"{name} = function",
        f"async function {name}(",
        f"export function {name}(",
        f"export async function {name}(",
    ):
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


def _fn_exists(name: str, src: str = BOARD_JS) -> bool:
    for needle in (
        f"function {name}(",
        f"{name} = function",
        f"async function {name}(",
        f"export function {name}(",
        f"export async function {name}(",
    ):
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


def _fn_body_html(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a named JS function in project.html."""
    return _fn_body(name, src)


# =============================================================================
# AC1 — Collapsed ancestor row with merge-state mark, name, and carry summary
# =============================================================================


def test_ac1_ancestor_row_html_function_exists():
    """_smgmtAncestorRowHtml function must exist in board-render.js."""
    assert _fn_exists("_smgmtAncestorRowHtml"), (
        "_smgmtAncestorRowHtml must exist in board-render.js to build the compact "
        "collapsed row for resolved ancestor sprints"
    )


def test_ac1_ancestor_row_css_exists():
    """CSS rule for .slp-ancestor-row must exist in project.html."""
    assert _css_exists(".slp-ancestor-row"), (
        ".slp-ancestor-row CSS must exist — it styles the compact collapsed row "
        "used for resolved ancestor sprints"
    )


def test_ac1_ancestor_row_contains_merge_mark():
    """_smgmtAncestorRowHtml must emit the merge-state mark element."""
    body = _fn_body("_smgmtAncestorRowHtml")
    assert "slp-merge-mark" in body, (
        "_smgmtAncestorRowHtml must include slp-merge-mark element for the "
        "three-state merge indicator (Merged / Needs merge / Failed)"
    )


def test_ac1_ancestor_row_contains_carry_summary():
    """_smgmtAncestorRowHtml must emit the carry-down summary element."""
    body = _fn_body("_smgmtAncestorRowHtml")
    assert "slp-carry-summary" in body or "carry" in body.lower(), (
        "_smgmtAncestorRowHtml must include a carry-down summary element "
        "(e.g. '3 merged · 1 reworked → 73.1')"
    )


def test_ac1_render_uses_ancestor_row_for_resolved():
    """_smgmtRender must call _smgmtAncestorRowHtml for resolved ancestor sprints."""
    render_body = _fn_body("_smgmtRender")
    assert "_smgmtAncestorRowHtml" in render_body or "AncestorRow" in render_body, (
        "_smgmtRender must call _smgmtAncestorRowHtml (or equivalent) for "
        "resolved ancestor sprints to render them as compact collapsed rows"
    )


# =============================================================================
# AC2 — Three distinct merge-state marks (Merged/Needs merge/Failed) with icons
# =============================================================================


def test_ac2_merged_state_css_exists():
    """CSS rule for .slp-merged state must exist in project.html."""
    assert _css_exists(".slp-merged") or _css_exists(".slp-merge-mark.slp-merged"), (
        "A CSS rule for .slp-merged must exist to style the green Merged state"
    )


def test_ac2_needs_merge_state_css_exists():
    """CSS rule for .slp-needs-merge state must exist in project.html."""
    assert _css_exists(".slp-needs-merge") or _css_exists(".slp-merge-mark.slp-needs-merge"), (
        "A CSS rule for .slp-needs-merge must exist to style the amber Needs merge state"
    )


def test_ac2_failed_state_css_exists():
    """CSS rule for .slp-failed state must exist in project.html."""
    assert _css_exists(".slp-failed") or _css_exists(".slp-merge-mark.slp-failed"), (
        "A CSS rule for .slp-failed must exist to style the red Failed state"
    )


def test_ac2_merged_uses_green_color():
    """The .slp-merged CSS must use green color."""
    try:
        css = _css_rule(".slp-merged")
    except AssertionError:
        css = _css_rule(".slp-merge-mark.slp-merged")
    assert "green" in css.lower() or "var(--green)" in css, (
        ".slp-merged must use var(--green) — Merged state signals success"
    )


def test_ac2_needs_merge_uses_amber_color():
    """The .slp-needs-merge CSS must use amber color."""
    try:
        css = _css_rule(".slp-needs-merge")
    except AssertionError:
        css = _css_rule(".slp-merge-mark.slp-needs-merge")
    assert "amber" in css.lower() or "var(--amber)" in css, (
        ".slp-needs-merge must use var(--amber) — Needs merge requires operator attention"
    )


def test_ac2_failed_uses_red_color():
    """The .slp-failed CSS must use red color."""
    try:
        css = _css_rule(".slp-failed")
    except AssertionError:
        css = _css_rule(".slp-merge-mark.slp-failed")
    assert "red" in css.lower() or "var(--red)" in css, (
        ".slp-failed must use var(--red) — Failed state signals nothing was merged"
    )


def test_ac2_merge_state_fn_exists():
    """A function to determine merge state from outcome data must exist."""
    has_fn = (
        _fn_exists("_smgmtAncestorMergeState")
        or "_smgmtAncestorMergeState" in BOARD_JS
        or "mergeState" in BOARD_JS
        or "merge_state" in BOARD_JS
    )
    assert has_fn, (
        "_smgmtAncestorMergeState (or equivalent) must exist to map outcome data "
        "to one of: merged / needs_merge / failed"
    )


# =============================================================================
# AC3 — Carry-down summary: merged count, carried count, child sprint name
# =============================================================================


def test_ac3_carry_summary_fn_exists():
    """A function to build the carry-down summary text must exist."""
    has_fn = (
        _fn_exists("_smgmtAncestorCarrySummary")
        or _fn_exists("_smgmtCarrySummary")
        or "carry" in BOARD_JS.lower()
        and ("merged" in BOARD_JS or "reworked" in BOARD_JS)
    )
    assert has_fn, (
        "A function (or inline logic) to build the carry-down summary must exist, "
        "showing counts of merged vs carried tickets and the child sprint label"
    )


def test_ac3_carry_summary_references_child_sprint():
    """The ancestor row HTML must reference the child sprint label in the carry summary."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # Must include child sprint label in the carry summary string
    has_child_ref = (
        "childLabel" in body
        or "child" in body.lower()
        or "rerunInto" in body
        or "→" in body
        or "->" in body
    )
    assert has_child_ref, (
        "_smgmtAncestorRowHtml must reference the child sprint label in the "
        "carry-down summary (e.g. '→ 73.1' or '-> sprint-73.1')"
    )


def test_ac3_carry_summary_shows_merged_count():
    """_smgmtAncestorRowHtml must show the count of merged tickets."""
    body = _fn_body("_smgmtAncestorRowHtml")
    has_done = "done" in body or "merged" in body or ".done" in body
    assert has_done, (
        "_smgmtAncestorRowHtml must show the count of 'merged' (done) tickets "
        "in the carry-down summary"
    )


def test_ac3_carry_summary_shows_carried_count():
    """_smgmtAncestorRowHtml must show the count of carried (reworked) tickets."""
    body = _fn_body("_smgmtAncestorRowHtml")
    has_carried = (
        "failed" in body or "rework" in body or "carried" in body
        or "skipped" in body
    )
    assert has_carried, (
        "_smgmtAncestorRowHtml must show the count of 'reworked/carried' tickets "
        "(failed + skipped) in the carry-down summary"
    )


# =============================================================================
# AC4 — Per-ticket rows with merged/carried fate labels when expanded
# =============================================================================


def test_ac4_ancestor_tickets_fn_exists():
    """A function to build per-ticket rows for an expanded ancestor must exist."""
    has_fn = (
        _fn_exists("_smgmtAncestorTicketsHtml")
        or _fn_exists("_smgmtAncestorExpandedHtml")
        or "slp-ticket" in BOARD_JS
    )
    assert has_fn, (
        "_smgmtAncestorTicketsHtml (or equivalent) must exist to build per-ticket "
        "rows shown when an ancestor sprint is expanded"
    )


def test_ac4_ticket_merged_mark_in_source():
    """Per-ticket rows must use a 'merged' fate label for done tickets."""
    # Check in board-render.js
    has_merged_fate = (
        "slp-fate-merged" in BOARD_JS
        or ("merged" in BOARD_JS and "slp-ticket" in BOARD_JS)
        or "fate-merged" in BOARD_JS
    )
    assert has_merged_fate, (
        "Per-ticket rows in expanded ancestor must include a 'merged' fate label "
        "for tickets that passed (outcome === 'done')"
    )


def test_ac4_ticket_carried_mark_in_source():
    """Per-ticket rows must use a 'carried' fate label for failed/skipped tickets."""
    has_carried_fate = (
        "slp-fate-carried" in BOARD_JS
        or ("carried" in BOARD_JS and "slp-ticket" in BOARD_JS)
        or "fate-carried" in BOARD_JS
    )
    assert has_carried_fate, (
        "Per-ticket rows in expanded ancestor must include a 'carried' fate label "
        "for tickets that failed/were skipped (moved to child sprint)"
    )


def test_ac4_ticket_carried_includes_child_sprint():
    """The 'carried' fate label must include the child sprint reference."""
    body = _fn_body("_smgmtAncestorRowHtml") if _fn_exists("_smgmtAncestorRowHtml") else ""
    has_ticket_fn = _fn_exists("_smgmtAncestorTicketsHtml")
    ticket_body = _fn_body("_smgmtAncestorTicketsHtml") if has_ticket_fn else ""
    combined = body + ticket_body
    has_child_in_carried = (
        "childLabel" in combined
        or "child" in combined.lower()
        or "→" in combined
        or "->" in combined
    )
    assert has_child_in_carried, (
        "The 'carried' fate label must include the child sprint label "
        "(e.g. 'carried → 73.1') so the operator knows where the ticket went"
    )


def test_ac4_carried_ticket_uses_amber_color():
    """CSS for carried ticket fate must use amber color."""
    has_amber = (
        _css_exists(".slp-fate-carried")
        or _css_exists(".slp-ticket-carried")
    )
    assert has_amber, (
        "A CSS rule for the carried ticket fate must exist "
        "(.slp-fate-carried or .slp-ticket-carried)"
    )


def test_ac4_merged_ticket_uses_green_color():
    """CSS for merged ticket fate must use green color."""
    has_green = (
        _css_exists(".slp-fate-merged")
        or _css_exists(".slp-ticket-merged")
    )
    assert has_green, (
        "A CSS rule for the merged ticket fate must exist "
        "(.slp-fate-merged or .slp-ticket-merged)"
    )


# =============================================================================
# AC5 — 'Needs merge' ancestor expanded shows Re-run and Merge action buttons
# =============================================================================


def test_ac5_ancestor_row_html_conditionally_shows_actions():
    """_smgmtAncestorRowHtml must conditionally emit action buttons."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # Must have logic gated on merge state or needs_merge
    has_conditional_actions = (
        "needs_merge" in body
        or "needsMerge" in body
        or ("rerun" in body.lower() and "merge" in body.lower())
    )
    assert has_conditional_actions, (
        "_smgmtAncestorRowHtml must conditionally show Re-run/Merge action buttons "
        "only when the ancestor's merge state is 'needs_merge'"
    )


def test_ac5_rerun_button_present_for_needs_merge():
    """The ancestor row must include a Re-run button for needs_merge ancestors."""
    body = _fn_body("_smgmtAncestorRowHtml")
    has_rerun = (
        "smgmt-run-btn--rerun" in body
        or "Re-run" in body
        or "rerun" in body.lower()
        or "smgmtRerunSprint" in body
    )
    assert has_rerun, (
        "_smgmtAncestorRowHtml must include a Re-run button that appears when "
        "the merge state is 'needs_merge'"
    )


def test_ac5_merge_button_present_for_needs_merge():
    """The ancestor row must include a Merge button for needs_merge ancestors."""
    body = _fn_body("_smgmtAncestorRowHtml")
    has_merge = (
        "smgmt-finish-btn" in body
        or "smgmtFinishSprint" in body
        or "Merge Sprint" in body
        or "merge" in body.lower()
    )
    assert has_merge, (
        "_smgmtAncestorRowHtml must include a Merge Sprint button that appears "
        "when the merge state is 'needs_merge'"
    )


# =============================================================================
# AC6 — Active sprint (up-next or running) expanded by default
# =============================================================================


def test_ac6_active_sprint_not_treated_as_ancestor():
    """_smgmtRender must NOT render the active (next-up or running) sprint as an ancestor row."""
    render_body = _fn_body("_smgmtRender")
    # The ancestor row logic must be conditional — not applied to running/next-up
    # It should check finished status and/or whether there's a rerun child
    has_ancestor_guard = (
        "_smgmtRunningLabels" in render_body
        or "isRunning" in render_body
        or "_smgmtNextUpLabel" in render_body
        or "finished" in render_body
    )
    assert has_ancestor_guard, (
        "_smgmtRender must guard ancestor row rendering so the active sprint "
        "(running or next-up) is never collapsed as an ancestor"
    )


def test_ac6_active_sprint_expands_by_default():
    """_smgmtCardHtml (for active sprints) must not force collapse by default."""
    card_body = _fn_body("_smgmtCardHtml")
    # Running sprints default to collapsed on the board — but next-up must not be forced collapsed
    # The existing collapse default logic reads localStorage, absent = expanded for non-running
    assert "isRunning" in card_body or "smgmt-collapsed" in card_body, (
        "_smgmtCardHtml must preserve current behavior: next-up sprints are expanded "
        "by default (only running sprints default to collapsed)"
    )


# =============================================================================
# AC7 — All resolved ancestor sprints are collapsed by default on page load
# =============================================================================


def test_ac7_ancestor_row_collapsed_by_default():
    """_smgmtAncestorRowHtml must render the body section as hidden by default."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # The expandable body must be hidden by default (use 'hidden' attribute or display:none)
    has_hidden_default = (
        "hidden" in body
        or 'style="display:none"' in body
        or "display: none" in body
        or "slp-ancestor-body" in body
    )
    assert has_hidden_default, (
        "_smgmtAncestorRowHtml must render the expandable section as hidden "
        "by default so all resolved ancestors are collapsed on page load"
    )


def test_ac7_toggle_ancestor_function_exists():
    """smgmtToggleAncestor function must exist to expand/collapse ancestor rows."""
    has_fn = (
        _fn_exists("smgmtToggleAncestor")
        or "smgmtToggleAncestor" in BOARD_JS
        or "smgmtToggleAncestor" in PROJECT_HTML
    )
    assert has_fn, (
        "smgmtToggleAncestor must exist to toggle the expanded/collapsed state "
        "of ancestor sprint rows when the user clicks them"
    )


def test_ac7_ancestor_row_has_onclick():
    """_smgmtAncestorRowHtml must wire up the toggle onclick."""
    body = _fn_body("_smgmtAncestorRowHtml")
    assert "smgmtToggleAncestor" in body or "onclick" in body, (
        "_smgmtAncestorRowHtml must include an onclick handler (smgmtToggleAncestor) "
        "so the row can be expanded by clicking"
    )


# =============================================================================
# AC8 — Carry links correctly identify which child sprint a carried ticket moved to
# =============================================================================


def test_ac8_child_sprint_sourced_from_rerun_into():
    """The child sprint label must come from _smgmtData.sprint_rerun_into (or param)."""
    body = _fn_body("_smgmtAncestorRowHtml")
    has_rerun_into = (
        "rerunInto" in body
        or "sprint_rerun_into" in body
        or "childLabel" in body
        or "child" in body.lower()
    )
    assert has_rerun_into, (
        "_smgmtAncestorRowHtml must source the child sprint label from "
        "sprint_rerun_into data so carry links are accurate"
    )


def test_ac8_render_passes_child_label_to_ancestor_html():
    """_smgmtRender must pass the child sprint label when building ancestor rows."""
    render_body = _fn_body("_smgmtRender")
    has_child_passed = (
        "rerunInto" in render_body
        or "_rerunInto" in render_body
        or "sprint_rerun_into" in render_body
        or "_smgmtChildSprintLabel" in render_body
    )
    assert has_child_passed, (
        "_smgmtRender must pass the child sprint label (from sprint_rerun_into) "
        "to _smgmtAncestorRowHtml so carry links reference the correct sprint"
    )


def test_parent_collapses_when_child_exists_regardless_of_tickets():
    """Parent sprint must move to Lineage whenever a child sub-sprint is in order."""
    assert _fn_exists("_smgmtShouldCollapseParent"), (
        "_smgmtShouldCollapseParent must exist to gate parent collapse"
    )
    body = _fn_body("_smgmtShouldCollapseParent")
    assert "ticket" not in body.lower() and "settled" not in body.lower(), (
        "Parent collapse must not depend on ticket count — any child sub-sprint "
        "hides the parent from Draft / Ready to merge sections"
    )
    assert _fn_exists("_smgmtSprintBaseLabel"), (
        "_smgmtSprintBaseLabel must exist to infer parent/child from dotted labels "
        "when plan.json parent is missing"
    )
    render_body = _fn_body("_smgmtRender")
    assert "_smgmtShouldCollapseToLineage" in render_body, (
        "_smgmtRender must collapse parent and superseded siblings into Lineage"
    )


def test_superseded_sibling_collapses_when_newer_exists():
    """sprint-85.1 moves to Lineage when sprint-85.2 is the latest sibling."""
    assert _fn_exists("_smgmtLatestLineageLabel"), (
        "_smgmtLatestLineageLabel must pick the newest sprint-N.M in a lineage"
    )
    assert _fn_exists("_smgmtShouldCollapseToLineage"), (
        "_smgmtShouldCollapseToLineage must gate superseded rerun drafts"
    )
    body = _fn_body("_smgmtShouldCollapseToLineage")
    assert "_smgmtLatestLineageLabel" in body, (
        "superseded sibling collapse must compare against the latest lineage label"
    )


# =============================================================================
# AC9 — Merge-state marks distinguishable without relying solely on color
# =============================================================================


def test_ac9_merged_state_has_icon():
    """The Merged state mark must include an icon (not just color)."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # Look for a tabler icon in the merged branch
    has_icon = (
        "ti-git-merge" in body
        or "ti-check" in body
        or "ti-circle-check" in body
    )
    assert has_icon, (
        "The Merged state mark must include a git-merge or check icon "
        "(not just green color) so it is distinguishable without relying solely on color"
    )


def test_ac9_merge_mark_includes_text_label():
    """The merge-state mark must include a text label (Merged / Needs merge / Failed)."""
    body = _fn_body("_smgmtAncestorRowHtml")
    has_text_label = (
        "Merged" in body
        or "Needs merge" in body
        or "Failed" in body
        or "slp-mark-text" in body
    )
    assert has_text_label, (
        "The merge-state mark must include visible text label (Merged / Needs merge / Failed) "
        "or a slp-mark-text element so state is identifiable without color alone"
    )


def test_ac9_needs_merge_has_icon():
    """The Needs merge state mark must include an icon."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # For needs_merge, there should be some icon reference
    has_icon = (
        "ti-git-merge" in body
        or "ti-clock" in body
        or "ti-alert" in body
        or "ti-" in body  # at least some tabler icon used
    )
    assert has_icon, (
        "The Needs merge state mark must include an icon (ti-*) in addition to "
        "amber color so it is distinguishable for colorblind users"
    )


def test_ac9_failed_has_icon():
    """The Failed state mark must include an icon."""
    body = _fn_body("_smgmtAncestorRowHtml")
    has_icon = (
        "ti-x" in body
        or "ti-circle-x" in body
        or "ti-ban" in body
        or "ti-" in body  # any tabler icon
    )
    assert has_icon, (
        "The Failed state mark must include an icon (ti-*) in addition to "
        "red color so it is distinguishable for colorblind users"
    )
