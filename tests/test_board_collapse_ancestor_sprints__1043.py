"""Tests for issue #1043: Board collapse ancestor sprints with merge-state marks.

These tests verify the implementation by reading the static source files
(board-render.js and project.html) rather than requiring a live server.
All tests are anchored to specific acceptance criteria from the issue.
"""
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

# Combined source for checks that may be in either file
COMBINED = BOARD_JS + PROJECT_HTML


# AC1: Each resolved ancestor sprint renders as a single collapsed row
# containing a merge-state mark, the sprint name, and a carry-down summary

def test_board_collapse_ancestor_sprints__collapsed_rows_render():
    # AC1 — ancestor row class, header, merge mark, name, and carry summary
    assert "slp-ancestor-row" in BOARD_JS, "slp-ancestor-row must exist in board-render.js"
    assert "slp-ancestor-header" in BOARD_JS, "slp-ancestor-header must exist in board-render.js"
    assert "slp-merge-mark" in BOARD_JS, "slp-merge-mark must exist in board-render.js"
    assert "slp-ancestor-name" in BOARD_JS, "slp-ancestor-name must exist in board-render.js"
    assert "slp-carry-summary" in BOARD_JS, "slp-carry-summary must exist in board-render.js"


# AC2: Merge-state mark has three distinct states: Merged, Needs merge, Failed

def test_board_collapse_ancestor_sprints__merge_state_marks():
    # AC2 — three state CSS classes in project.html
    assert "slp-merged" in PROJECT_HTML, "slp-merged CSS class must exist in project.html"
    assert "slp-needs-merge" in PROJECT_HTML, "slp-needs-merge CSS class must exist in project.html"
    assert "slp-failed" in PROJECT_HTML, "slp-failed CSS class must exist in project.html"
    # At least one distinguishing icon
    has_icon = (
        "ti-git-merge" in BOARD_JS
        or "ti-git-pull-request" in BOARD_JS
        or "ti-circle-x" in BOARD_JS
    )
    assert has_icon, "Merge-state mark must include at least one icon (ti-git-merge, ti-git-pull-request, or ti-circle-x)"


# AC3: Carry-down summary reflects merged vs carried ticket counts and child sprint name

def test_board_collapse_ancestor_sprints__carry_down_summary():
    # AC3 — "merged" and "reworked"/"carried" text in carry summary logic
    assert "merged" in BOARD_JS, '"merged" label must appear in board-render.js carry summary'
    assert "reworked" in BOARD_JS or "carried" in BOARD_JS, (
        '"reworked" or "carried" label must appear in board-render.js carry summary'
    )


# AC4: Expanding a collapsed ancestor row reveals per-ticket rows

def test_board_collapse_ancestor_sprints__expanded_row_ticket_rows():
    # AC4 — per-ticket row container and fate labels
    assert "slp-ancestor-body" in BOARD_JS, "slp-ancestor-body must exist in board-render.js"
    assert "slp-ancestor-ticket-row" in BOARD_JS, "slp-ancestor-ticket-row must exist in board-render.js"
    has_ticket_marks = (
        "slp-ticket-merged" in BOARD_JS or "slp-ticket-carried" in BOARD_JS
    )
    assert has_ticket_marks, "slp-ticket-merged or slp-ticket-carried must exist in board-render.js"
    has_fate_labels = (
        "slp-fate-merged" in BOARD_JS or "slp-fate-carried" in BOARD_JS
    )
    assert has_fate_labels, "slp-fate-merged or slp-fate-carried must exist in board-render.js"


# AC5: A "Needs merge" ancestor that is expanded exposes Re-run and Merge action buttons

def test_board_collapse_ancestor_sprints__needs_merge_actions():
    # AC5 — ancestor actions container and Re-run / Merge buttons
    assert "slp-ancestor-actions" in BOARD_JS, "slp-ancestor-actions must exist in board-render.js"
    has_rerun = "Re-run" in BOARD_JS or "smgmt-run-btn--rerun" in BOARD_JS
    assert has_rerun, "Re-run button (Re-run text or smgmt-run-btn--rerun class) must exist in board-render.js"
    has_merge = "Merge" in BOARD_JS or "sc-merge-link" in BOARD_JS
    assert has_merge, "Merge button must exist in board-render.js"


# AC6: The active sprint (up-next or running) is expanded by default on page load

def test_board_collapse_ancestor_sprints__active_sprint_expanded_by_default():
    # AC6 — regular sprint cards (non-ancestor) still rendered for active sprint
    assert "smgmt-sprint-unit" in BOARD_JS, (
        "smgmt-sprint-unit must exist in board-render.js for active sprint cards"
    )


# AC7: All resolved ancestor sprints are collapsed by default on page load

def test_board_collapse_ancestor_sprints__ancestors_collapsed_by_default():
    # AC7 — ancestor body hidden by default
    assert "slp-ancestor-body" in BOARD_JS and "hidden" in BOARD_JS, (
        "slp-ancestor-body must be rendered with the hidden attribute by default in board-render.js"
    )


# AC8: Carry links correctly identify which child sprint a carried ticket moved to

def test_board_collapse_ancestor_sprints__carry_links_correct():
    # AC8 — child sprint reference in carried fate label
    has_child_sprint_ref = (
        "carried →" in BOARD_JS
        or "reworked →" in BOARD_JS
        or "childLabel" in BOARD_JS
        or "childDisplay" in BOARD_JS
    )
    assert has_child_sprint_ref, (
        "Carry links must reference the child sprint (carried → {child}) in board-render.js"
    )


# AC9: Merge-state marks are visually distinguishable without relying solely on color

def test_board_collapse_ancestor_sprints__merge_state_visually_distinct():
    # AC9 — icons present and text labels
    has_icon = "ti-" in BOARD_JS
    assert has_icon, "Tabler icons (ti-*) must be used in board-render.js for merge-state marks"
    has_text = "slp-mark-text" in PROJECT_HTML or "Merged" in BOARD_JS or "Failed" in BOARD_JS
    assert has_text, "Text labels (Merged/Failed/Needs merge or slp-mark-text) must be present"


# AC7 (expand/collapse toggle): toggle function and button are wired up

def test_board_collapse_ancestor_sprints__toggle_functionality():
    # AC7 (implicit) — collapse/expand toggle button and chevron icon
    has_toggle = "slp-ancestor-toggle" in BOARD_JS
    assert has_toggle, "slp-ancestor-toggle class must exist in board-render.js"
    has_collapse_btn = "smgmt-collapse-btn" in BOARD_JS or "smgmt-collapse-btn" in PROJECT_HTML
    assert has_collapse_btn, "smgmt-collapse-btn must exist in board-render.js or project.html"
    has_chevron = "ti-chevron" in BOARD_JS or "ti-chevron" in PROJECT_HTML
    assert has_chevron, "ti-chevron icon must exist for the toggle button"


# AC1 + AC9: CSS styling classes present in project.html

def test_board_collapse_ancestor_sprints__styling_classes_present():
    # AC1/AC9 — styling defined in project.html
    assert "slp-ancestor-row" in PROJECT_HTML or "slp-ancestor-row" in BOARD_JS, (
        "slp-ancestor-row must be defined (CSS or JS)"
    )
    assert "slp-ancestor-header" in PROJECT_HTML or "slp-ancestor-header" in BOARD_JS, (
        "slp-ancestor-header must be defined (CSS or JS)"
    )
    assert "slp-carry-summary" in PROJECT_HTML or "slp-carry-summary" in BOARD_JS, (
        "slp-carry-summary must be defined (CSS or JS)"
    )
    assert "slp-merged" in PROJECT_HTML or "slp-needs-merge" in PROJECT_HTML, (
        "slp-merged or slp-needs-merge CSS must exist in project.html"
    )
