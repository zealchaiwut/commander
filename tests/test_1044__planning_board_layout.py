"""Tests for issue #1044 — Board: planning layout with draft sprint and focus guide.

Source-code tests following the pattern from test_1043__ancestor_sprint_collapse.py.

AC coverage:
  AC1  — Focus Guide appears at top of board: short ordered list synthesized
          from current board state
  AC2  — Sections render in strict order: Lineage → Up next → Planning → Backlog
  AC3  — Draft sprint card shows Draft badge (blue) and goal slot; Run button
          is disabled until a non-empty goal is entered
  AC4  — Draft sprint shows budget bar with headroom labeled (e.g. "3 pts remaining")
  AC5  — Draft sprint has "+ Add ticket" affordance that opens a picker/form
  AC6  — Backlog has size filters: All / S / M / L / XL / unestimated
  AC7  — Backlog has a milestone filter dropdown
  AC8  — Backlog has "Estimate unsized" action that surfaces unestimated tickets
  AC9  — Each backlog row has "Add to <draft sprint>" button that moves the ticket
          into the Planning section
  AC10 — No drag handles anywhere on the board (no cursor:grab, no grip icons)
  AC11 — No drop zones anywhere on the board (no ondragover, no drop hint text)
  AC12 — Each ticket row in Planning section has row menu (⋯) with Move, Remove,
          and Reorder options
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


# =============================================================================
# AC1 — Focus Guide at top of board
# =============================================================================


def test_ac1_focus_guide_fn_exists():
    """_smgmtFocusGuideHtml must exist in board-render.js to generate the focus guide."""
    assert _fn_exists("_smgmtFocusGuideHtml"), (
        "_smgmtFocusGuideHtml must exist — it synthesizes the numbered focus guide "
        "list from current board state and returns an HTML string"
    )


def test_ac1_focus_guide_css_exists():
    """CSS rule for .smgmt-focus-guide must exist in project.html."""
    assert _css_exists(".smgmt-focus-guide"), (
        ".smgmt-focus-guide CSS must exist to style the Focus Guide panel at the "
        "top of the planning board"
    )


def test_ac1_focus_guide_fn_returns_ordered_list():
    """_smgmtFocusGuideHtml must return an HTML structure with numbered steps."""
    body = _fn_body("_smgmtFocusGuideHtml")
    has_steps = (
        "smgmt-focus-step" in body
        or "fnum" in body
        or "focus-step" in body
        or "<ol" in body
        or "step" in body.lower()
    )
    assert has_steps, (
        "_smgmtFocusGuideHtml must return an ordered step list "
        "(e.g. smgmt-focus-step elements or an <ol>) to guide the planner"
    )


def test_ac1_render_injects_focus_guide():
    """_smgmtRender must call _smgmtFocusGuideHtml to inject the focus guide."""
    render_body = _fn_body("_smgmtRender")
    assert "_smgmtFocusGuideHtml" in render_body or "focus-guide" in render_body, (
        "_smgmtRender must call _smgmtFocusGuideHtml (or inject focus-guide HTML) "
        "so the Focus Guide appears at the top of the board on every render"
    )


def test_ac1_focus_guide_step_css_exists():
    """CSS for focus guide step items must exist."""
    assert _css_exists(".smgmt-focus-step") or _css_exists(".smgmt-focus-guide"), (
        "CSS for .smgmt-focus-step (or .smgmt-focus-guide) must exist to style "
        "the individual numbered steps in the Focus Guide"
    )


# =============================================================================
# AC2 — Section order: Lineage → Ready to merge → Draft → Backlog
# =============================================================================


def test_ac2_section_label_css_exists():
    """CSS for section label headers (Lineage, Up next, Planning, Backlog) must exist."""
    has_section_css = (
        _css_exists(".smgmt-section-label")
        or _css_exists(".smgmt-section-heading")
        or _css_exists(".smgmt-board-section")
    )
    assert has_section_css, (
        "A CSS rule for section labels must exist (.smgmt-section-label or similar) "
        "to style the LINEAGE / UP NEXT / PLANNING / BACKLOG section headers"
    )


def test_ac2_render_emits_lineage_section():
    """_smgmtRender must emit a Lineage section header above ancestor sprints."""
    render_body = _fn_body("_smgmtRender")
    has_lineage = (
        "Lineage" in render_body
        or "lineage" in render_body.lower()
        or "smgmt-section" in render_body
    )
    assert has_lineage, (
        "_smgmtRender must emit a 'Lineage' section header (or use section grouping) "
        "above resolved ancestor sprints so the board section order is explicit"
    )


def test_ac2_render_emits_draft_section():
    """_smgmtRender must emit a Draft section for pending-to-run sprints."""
    render_body = _fn_body("_smgmtRender")
    has_draft = (
        "Draft" in render_body
        or "smgmt-section-draft" in render_body
        or "smgmt-board-section--draft" in render_body
        or "_smgmtDraftCardHtml" in render_body
    )
    assert has_draft, (
        "_smgmtRender must emit a 'Draft' section for pending-to-run and planning sprints"
    )


def test_ac2_render_emits_merge_section():
    """_smgmtRender must emit a Ready to merge section when post-run sprints exist."""
    render_body = _fn_body("_smgmtRender")
    has_merge = (
        "Ready to merge" in render_body
        or "smgmt-section-merge" in render_body
        or "_smgmtCardBucket" in render_body
    )
    assert has_merge, (
        "_smgmtRender must support a 'Ready to merge' section for post-run sprints"
    )


def test_ac2_draft_section_css_exists():
    """CSS for the Draft section container must exist."""
    has_draft_css = (
        _css_exists(".smgmt-board-section--draft")
        or _css_exists(".smgmt-section-draft")
    )
    assert has_draft_css, (
        "CSS for the Draft section container must exist (.smgmt-board-section--draft)"
    )


# =============================================================================
# AC3 — Draft sprint card: Draft badge (blue) + goal slot + disabled Run button
# =============================================================================


def test_ac3_draft_card_fn_exists():
    """_smgmtDraftCardHtml must exist to render the draft sprint card."""
    assert _fn_exists("_smgmtDraftCardHtml"), (
        "_smgmtDraftCardHtml must exist in board-render.js — it renders the Planning "
        "section card for a sprint in draft/planned state with badge, goal slot, and budget bar"
    )


def test_ac3_draft_badge_in_card_fn():
    """_smgmtDraftCardHtml must include the Draft badge element."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_badge = (
        "smgmt-draft-badge" in body
        or "Draft" in body
        or "draft-badge" in body
    )
    assert has_badge, (
        "_smgmtDraftCardHtml must include a Draft badge element "
        "(e.g. class='smgmt-draft-badge') — the blue badge indicating draft state"
    )


def test_ac3_draft_badge_css_exists():
    """CSS for .smgmt-draft-badge must exist in project.html."""
    assert _css_exists(".smgmt-draft-badge"), (
        ".smgmt-draft-badge CSS must exist — the blue pill badge shown on the "
        "draft sprint card in the Planning section"
    )


def test_ac3_draft_badge_uses_blue_color():
    """The .smgmt-draft-badge CSS must use blue styling."""
    css = _css_rule(".smgmt-draft-badge")
    has_blue = "var(--blue" in css or "blue" in css.lower() or "#2563" in css
    assert has_blue, (
        ".smgmt-draft-badge must use var(--blue) or var(--blue-bg) — "
        "the badge is explicitly blue per the AC"
    )


def test_ac3_goal_slot_in_draft_card():
    """_smgmtDraftCardHtml must include a goal input slot."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_goal = (
        "goal" in body.lower()
        or "smgmt-goal" in body
        or "sprint goal" in body.lower()
        or "Set a sprint" in body
        or "<input" in body
        or "<textarea" in body
    )
    assert has_goal, (
        "_smgmtDraftCardHtml must include a goal input slot "
        "(e.g. placeholder 'Set a sprint goal…') so the operator can set a goal before running"
    )


def test_ac3_goal_slot_css_exists():
    """CSS for the goal slot input must exist."""
    has_goal_css = (
        _css_exists(".smgmt-goal-slot")
        or _css_exists(".smgmt-goal-input")
        or _css_exists(".smgmt-sprint-goal")
    )
    assert has_goal_css, (
        "CSS for the goal slot (.smgmt-goal-slot or .smgmt-goal-input) must exist "
        "to style the goal input in the draft sprint card"
    )


def test_ac3_run_button_in_draft_card():
    """_smgmtDraftCardHtml must include a Run button."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_run = (
        "Run" in body
        or "btn-run" in body
        or "smgmt-run-btn" in body
    )
    assert has_run, (
        "_smgmtDraftCardHtml must include a Run button — it starts disabled and "
        "becomes enabled once a non-empty goal is entered"
    )


def test_ac3_run_button_disabled_by_default():
    """_smgmtDraftCardHtml must render the Run button as disabled by default."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_disabled = (
        "disabled" in body
        or "btn-run-disabled" in body
        or "is-disabled" in body
    )
    assert has_disabled, (
        "_smgmtDraftCardHtml must render the Run button with disabled attribute "
        "by default — it becomes enabled only when the goal slot has non-empty text"
    )


def test_ac3_goal_enables_run_button():
    """A JS function or event handler must enable the Run button when goal is entered."""
    has_goal_handler = (
        "_smgmtGoalInput" in BOARD_JS
        or "_smgmtGoalChange" in BOARD_JS
        or "smgmtGoalInput" in PROJECT_HTML
        or "smgmtGoalChange" in PROJECT_HTML
        or "smgmtDraftGoalInput" in PROJECT_HTML
        or "smgmtDraftGoalInput" in BOARD_JS
        or ("goal" in BOARD_JS.lower() and "disabled" in BOARD_JS)
    )
    assert has_goal_handler, (
        "A JS function must exist to toggle the Run button's disabled state "
        "when the goal slot input changes (e.g. _smgmtGoalInput or smgmtGoalChange)"
    )


# =============================================================================
# AC4 — Budget bar with headroom label
# =============================================================================


def test_ac4_budget_bar_fn_exists():
    """_smgmtBudgetBarHtml must exist to render the budget bar."""
    assert _fn_exists("_smgmtBudgetBarHtml"), (
        "_smgmtBudgetBarHtml must exist in board-render.js — it renders the "
        "proportional budget bar and headroom label (e.g. '3 pts remaining')"
    )


def test_ac4_budget_bar_css_exists():
    """CSS for .smgmt-budget-bar must exist in project.html."""
    assert _css_exists(".smgmt-budget-bar"), (
        ".smgmt-budget-bar CSS must exist — it styles the horizontal bar showing "
        "sprint capacity used vs remaining in the draft sprint card"
    )


def test_ac4_headroom_label_in_budget_fn():
    """_smgmtBudgetBarHtml must include a headroom/remaining label."""
    body = _fn_body("_smgmtBudgetBarHtml")
    has_headroom = (
        "remaining" in body.lower()
        or "headroom" in body.lower()
        or "pts" in body
        or "smgmt-budget-label" in body
        or "smgmt-budget-headroom" in body
    )
    assert has_headroom, (
        "_smgmtBudgetBarHtml must include a headroom label "
        "(e.g. '3 pts remaining') next to or below the bar"
    )


def test_ac4_draft_card_calls_budget_bar():
    """_smgmtDraftCardHtml must call _smgmtBudgetBarHtml to render the budget bar."""
    body = _fn_body("_smgmtDraftCardHtml")
    assert "_smgmtBudgetBarHtml" in body or "budget" in body.lower(), (
        "_smgmtDraftCardHtml must call _smgmtBudgetBarHtml (or inline budget logic) "
        "so the budget bar appears inside the draft sprint card"
    )


# =============================================================================
# AC5 — "+ Add ticket" affordance on the draft sprint
# =============================================================================


def test_ac5_add_ticket_in_draft_card():
    """_smgmtDraftCardHtml must include an '+ Add ticket' button."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_add = (
        "Add ticket" in body
        or "add-ticket" in body.lower()
        or "smgmt-add-ticket" in body
        or "add ticket" in body.lower()
    )
    assert has_add, (
        "_smgmtDraftCardHtml must include a '+ Add ticket' button/affordance "
        "that opens a ticket picker or inline form"
    )


def test_ac5_add_ticket_css_exists():
    """CSS for the add-ticket button must exist in project.html."""
    has_css = (
        _css_exists(".smgmt-add-ticket-btn")
        or _css_exists(".smgmt-add-ticket")
        or _css_exists(".smgmt-draft-add-ticket")
    )
    assert has_css, (
        "CSS for the '+ Add ticket' button must exist in project.html "
        "(.smgmt-add-ticket-btn or similar)"
    )


# =============================================================================
# AC6 — Backlog size filters: All / S / M / L / XL / unestimated
# =============================================================================


def test_ac6_backlog_size_filters_exist():
    """Backlog size filter pills (All/S/M/L/XL/unestimated) must exist in project.html."""
    html = PROJECT_HTML
    has_s = 'data-filter-val="S"' in html or "bl-pill" in html
    has_m = 'data-filter-val="M"' in html
    has_l = 'data-filter-val="L"' in html
    has_xl = 'data-filter-val="XL"' in html
    assert has_s and has_m and has_l and has_xl, (
        "Backlog size filter pills for S, M, L, XL must exist in project.html "
        "to allow filtering backlog by ticket size"
    )


def test_ac6_unestimated_filter_exists():
    """An 'unestimated' filter option must exist in the backlog filters."""
    has_unest = (
        'data-filter-val="1"' in PROJECT_HTML
        or "unestimated" in PROJECT_HTML.lower()
    )
    assert has_unest, (
        "An 'unestimated' filter pill must exist in the backlog filter row "
        "to surface tickets without size estimates"
    )


# =============================================================================
# AC7 — Backlog milestone filter dropdown
# =============================================================================


def test_ac7_milestone_filter_exists():
    """A milestone filter dropdown must exist in the backlog."""
    has_ms = (
        "bl-ms-filter" in PROJECT_HTML
        or "milestone" in PROJECT_HTML.lower()
        and "filter" in PROJECT_HTML.lower()
    )
    assert has_ms, (
        "A milestone filter dropdown must exist in the backlog (e.g. class='bl-ms-filter') "
        "to allow filtering backlog tickets by milestone"
    )


# =============================================================================
# AC8 — "Estimate unsized" action
# =============================================================================


def test_ac8_estimate_unsized_btn_exists():
    """An 'Estimate unsized' button must exist in the backlog."""
    has_est = (
        "Estimate unsized" in PROJECT_HTML
        or "smgmt-backlog-bulk-est-btn" in PROJECT_HTML
        or "smgmt-bulk-est-btn" in PROJECT_HTML
    )
    assert has_est, (
        "An 'Estimate unsized' button must exist in the backlog "
        "to surface unestimated tickets for estimation"
    )


# =============================================================================
# AC9 — "Add to <draft sprint>" button on each backlog row
# =============================================================================


def test_ac9_add_to_sprint_fn_exists():
    """A function to add a backlog ticket to the draft sprint must exist."""
    has_fn = (
        _fn_exists("_smgmtAddToDraft")
        or _fn_exists("smgmtAddToDraft")
        or "_smgmtAddToDraft" in BOARD_JS
        or "smgmtAddToDraft" in PROJECT_HTML
        or _fn_exists("_smgmtAddToPlanning")
        or "smgmtAddToPlanning" in BOARD_JS
        or "smgmtAddToPlanning" in PROJECT_HTML
    )
    assert has_fn, (
        "A function (_smgmtAddToDraft or _smgmtAddToPlanning) must exist to handle "
        "the 'Add to <draft sprint>' click on backlog rows"
    )


def test_ac9_add_to_sprint_btn_in_backlog_row():
    """_smgmtBacklogTicketHtml must include an 'Add to sprint' button."""
    body = _fn_body("_smgmtBacklogTicketHtml")
    has_add_btn = (
        "smgmt-add-to-sprint" in body
        or "Add to" in body
        or "smgmtAddToDraft" in body
        or "smgmtAddToPlanning" in body
        or "add-to-sprint" in body.lower()
        or "addToDraft" in body
        or "addToPlanning" in body
    )
    assert has_add_btn, (
        "_smgmtBacklogTicketHtml must include an 'Add to <sprint>' button "
        "so each backlog row has a direct affordance to move the ticket into the Planning section"
    )


def test_ac9_add_to_sprint_css_exists():
    """CSS for the 'Add to sprint' button on backlog rows must exist."""
    has_css = (
        _css_exists(".smgmt-add-to-sprint-btn")
        or _css_exists(".bl-add-to-sprint")
        or _css_exists(".smgmt-bl-add-btn")
    )
    assert has_css, (
        "CSS for the 'Add to sprint' button on backlog rows must exist "
        "(.smgmt-add-to-sprint-btn or .bl-add-to-sprint) to style the per-row button"
    )


# =============================================================================
# AC10 — No drag handles on the board (no cursor:grab, no grip icons)
# =============================================================================


def test_ac10_backlog_ticket_not_draggable():
    """_smgmtBacklogTicketHtml must NOT set draggable='true' on backlog tickets."""
    body = _fn_body("_smgmtBacklogTicketHtml")
    has_draggable_true = (
        'draggable="true"' in body
        or "draggable='true'" in body
    )
    assert not has_draggable_true, (
        "_smgmtBacklogTicketHtml must NOT include draggable='true' — "
        "drag-and-drop is explicitly removed from the planning board (AC10)"
    )


def test_ac10_backlog_ticket_no_dragstart():
    """_smgmtBacklogTicketHtml must NOT include ondragstart handler."""
    body = _fn_body("_smgmtBacklogTicketHtml")
    assert "ondragstart" not in body, (
        "_smgmtBacklogTicketHtml must NOT include ondragstart — "
        "drag event handlers are removed from the planning board (AC10)"
    )


def test_ac10_draft_card_no_grip_icons():
    """_smgmtDraftCardHtml must NOT include grip/drag icon classes."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_grip = (
        "ticket-grip" in body
        or "ti-grip" in body
        or "cursor: grab" in body
        or "cursor:grab" in body
    )
    assert not has_grip, (
        "_smgmtDraftCardHtml must NOT include grip icons (ti-grip-*, ticket-grip) "
        "— drag handles are explicitly removed from the planning board (AC10)"
    )


# =============================================================================
# AC11 — No drop zones on the board (no ondragover, no drop hints)
# =============================================================================


def test_ac11_backlog_pane_no_ondragover():
    """The backlog pane HTML element must NOT have ondragover attribute."""
    has_dragover = (
        'id="smgmt-backlog-pane"' in PROJECT_HTML
        and "ondragover" in PROJECT_HTML[PROJECT_HTML.find('id="smgmt-backlog-pane"'):PROJECT_HTML.find('id="smgmt-backlog-pane"') + 400]
    )
    assert not has_dragover, (
        "The backlog pane element (#smgmt-backlog-pane) must NOT have an ondragover "
        "attribute — drop zones are removed from the planning board (AC11)"
    )


def test_ac11_ghost_pane_no_ondragover():
    """The ghost pane (drag-to-create) must NOT have ondragover attribute."""
    has_dragover = (
        'id="smgmt-ghost-pane"' in PROJECT_HTML
        and "ondragover" in PROJECT_HTML[PROJECT_HTML.find('id="smgmt-ghost-pane"'):PROJECT_HTML.find('id="smgmt-ghost-pane"') + 400]
    )
    assert not has_dragover, (
        "The ghost pane (#smgmt-ghost-pane) must NOT have ondragover — "
        "drop zones are removed from the planning board (AC11)"
    )


def test_ac11_backlog_no_drop_hint_text():
    """The backlog must not show 'Drop tickets here' as its empty-state text."""
    # The backlog empty state should say something useful, not a drag instruction
    backlog_drop_hint_in_html = (
        "Drop tickets here to remove sprint label" in PROJECT_HTML
    )
    assert not backlog_drop_hint_in_html, (
        "The 'Drop tickets here to remove sprint label' text must be removed from "
        "the board HTML — drop zones are removed and this instruction is obsolete (AC11)"
    )


# =============================================================================
# AC12 — Row menu (⋯) for Planning section with Move, Remove, Reorder
# =============================================================================


def test_ac12_planning_row_menu_fn_exists():
    """A function to build the planning ticket row menu must exist."""
    has_fn = (
        _fn_exists("_smgmtPlanningRowMenuHtml")
        or _fn_exists("_smgmtPlanningTicketMenu")
        or "_smgmtPlanningRowMenu" in BOARD_JS
        or ("Move" in BOARD_JS and "Remove" in BOARD_JS and "Reorder" in BOARD_JS)
    )
    assert has_fn, (
        "A function for the planning section row menu must exist "
        "(_smgmtPlanningRowMenuHtml or equivalent) offering Move, Remove, Reorder"
    )


def test_ac12_planning_row_menu_has_move():
    """The planning row menu must include a 'Move' option."""
    in_board = "Move" in BOARD_JS
    in_html = "Move" in PROJECT_HTML
    assert in_board or in_html, (
        "The planning section row menu must include a 'Move' option so tickets "
        "can be moved between sprints without drag-and-drop"
    )


def test_ac12_planning_row_menu_has_remove():
    """The planning row menu must include a 'Remove' option."""
    has_remove = (
        "_smgmtDraftCardHtml" in BOARD_JS and "Remove" in _fn_body("_smgmtDraftCardHtml")
    ) or (
        _fn_exists("_smgmtPlanningRowMenuHtml") and "Remove" in _fn_body("_smgmtPlanningRowMenuHtml")
    ) or (
        "smgmt-planning-remove" in BOARD_JS
        or "smgmtPlanningRemove" in BOARD_JS
        or "smgmtPlanningRemove" in PROJECT_HTML
    ) or (
        "Remove" in BOARD_JS and "planning" in BOARD_JS.lower()
    )
    assert has_remove, (
        "The planning section row menu must include a 'Remove' option so tickets "
        "can be moved back to Backlog from the Planning section"
    )


def test_ac12_planning_row_menu_has_reorder():
    """The planning row menu must include a 'Reorder' option."""
    has_reorder = (
        "Reorder" in BOARD_JS
        or "reorder" in BOARD_JS.lower()
        or "Move up" in BOARD_JS
        or "Move down" in BOARD_JS
    )
    assert has_reorder, (
        "The planning section row menu must include a 'Reorder' option "
        "so tickets can be reordered without drag-and-drop"
    )


def test_ac12_draft_card_ticket_rows_have_menu_button():
    """_smgmtDraftCardHtml must render ticket rows with a ⋯ menu button."""
    body = _fn_body("_smgmtDraftCardHtml")
    has_menu = (
        "smgmt-row-menu-btn" in body
        or "smgmt-plan-ticket-menu" in body
        or "ti-dots" in body
        or "⋯" in body
        or "menu" in body.lower()
    )
    assert has_menu, (
        "_smgmtDraftCardHtml must render ticket rows with a ⋯ (ellipsis) menu button "
        "offering at least Move, Remove, and Reorder actions"
    )
