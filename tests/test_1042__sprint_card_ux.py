"""Tests for issue #1042 — Sprint cards: one action, status line, budget color.

Reads JS/CSS source to verify the design contract.  Pattern follows
test_1041__history_card_redesign.py.

AC coverage:
  AC1  — exactly one primary action button per card state; delete is icon-only
  AC2  — delete rendered as trash icon only (no text, no red fill)
  AC3  — one-line status sentence under the sprint header
  AC4  — budget bar color keyed to real state (green/amber/red) + headroom label
  AC5  — badges are calm and state-coded (Next up→green, Draft→blue,
          needs-rework/needs-merge→amber)
  AC6  — run preview mini-rail present on cards with an active Run Sprint button
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

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
# AC1 — Exactly one primary action button per state; delete icon-only
# =============================================================================


def test_ac1_delete_button_has_no_label_text():
    """Delete button must not include label text — icon only.

    The old button had "> Delete<" in the template.  AC1 mandates trash icon
    only so delete is never a filled primary button.
    """
    card_body = _fn_body("_smgmtCardHtml")
    # Must still have the trash icon
    assert "ti-trash" in card_body, "Delete button must contain ti-trash icon"
    # Must NOT have visible " Delete" label text next to the icon
    # Acceptable: icon with aria-label or title but no inline text label
    assert "> Delete<" not in card_body and ">Delete<" not in card_body, (
        "Delete button must not include visible label text 'Delete' — "
        "icon only (ti-trash + aria-label/title)"
    )


def test_ac1_has_rework_rerun_is_primary_and_merge_is_secondary():
    """For has-rework state, Re-run must be primary; Merge must be quiet secondary.

    The old layout made smgmt-finish-btn an amber filled button alongside the
    Re-run button, giving two visually-primary actions.  AC1 allows only one
    primary — Re-run (amber) — with Merge as a text/link secondary.
    """
    card_body = _fn_body("_smgmtCardHtml")
    # Re-run button class must be present in the card
    assert "smgmt-run-btn--rerun" in card_body, (
        "_smgmtCardHtml must still emit the Re-run button with class smgmt-run-btn--rerun"
    )
    # smgmt-finish-btn must be demoted — must use a quiet secondary class
    # (sc-merge-link or smgmt-finish-btn--secondary or equivalent)
    quiet_merge_indicators = [
        "sc-merge-link",
        "smgmt-merge-link",
        "smgmt-finish-btn--quiet",
        "smgmt-finish-btn--secondary",
        "sc-merge-secondary",
    ]
    has_quiet_merge = any(cls in card_body for cls in quiet_merge_indicators)
    assert has_quiet_merge, (
        "Merge Sprint button must use a quiet secondary class "
        "(e.g. sc-merge-link) when Re-run is the primary action — "
        "not a filled smgmt-finish-btn"
    )


def test_ac1_run_blocked_shows_inline_reason():
    """When sprint is blocked (another running), the button must show an inline reason.

    Current 'blocked' state uses a title attribute only.  AC1 says the reason
    (e.g. 'blocked: S71.1 running') should be visible inline, not just a tooltip.
    """
    card_body = _fn_body("_smgmtCardHtml")
    # sc-blocked-hint or an equivalent must appear inline
    assert "sc-blocked-hint" in card_body, (
        "_smgmtCardHtml must include a sc-blocked-hint (or equivalent) for the "
        "blocked inline reason string when another sprint is running"
    )


# =============================================================================
# AC2 — Delete is trash icon only; no red fill
# =============================================================================


def test_ac2_delete_btn_css_not_red_border():
    """smgmt-delete-btn CSS must not use red border color.

    The old CSS had `color: var(--red); border: 1px solid var(--red)`.
    AC2 mandates a quiet icon — no red.
    """
    delete_css = _css_rule(".smgmt-delete-btn")
    # Must not render a red border (which screams 'destructive filled button')
    assert "var(--red)" not in delete_css or (
        "border" not in delete_css.split("var(--red)")[0][-50:]
        and "background" not in delete_css
    ), (
        ".smgmt-delete-btn must not use red border/fill — it must be a quiet icon. "
        f"Got: {delete_css!r}"
    )


def test_ac2_delete_btn_css_no_red_background():
    """smgmt-delete-btn CSS must not use red background (no filled red button)."""
    delete_css = _css_rule(".smgmt-delete-btn")
    # No filled red background on the button itself
    assert "background: var(--red)" not in delete_css, (
        ".smgmt-delete-btn must not have a red background — quiet icon only"
    )


def test_ac2_delete_icon_only_has_aria_label():
    """Icon-only delete button must have an accessible label (aria-label or title).

    Per WCAG, icon-only controls need a text alternative.
    """
    card_body = _fn_body("_smgmtCardHtml")
    # Find the delete button context (50 chars around ti-trash mention)
    idx = card_body.find("ti-trash")
    assert idx != -1, "ti-trash must appear in _smgmtCardHtml"
    snippet = card_body[max(0, idx - 200) : idx + 200]
    # Must have aria-label or title so screen readers know the purpose
    has_aria = "aria-label" in snippet or 'title="Delete' in snippet or "title='Delete" in snippet
    assert has_aria, (
        "Icon-only delete button must have aria-label or title attribute for accessibility"
    )


# =============================================================================
# AC3 — One-line status sentence under the sprint header
# =============================================================================


def test_ac3_status_sentence_function_exists():
    """A function must exist to generate the plain-language status sentence."""
    has_fn = _fn_exists("_smgmtCardStatusSentence") or _fn_exists("_smgmtStatusSentence")
    assert has_fn, (
        "A function _smgmtCardStatusSentence (or _smgmtStatusSentence) must exist "
        "to generate the status sentence for each card state"
    )


def test_ac3_status_line_css_exists():
    """CSS rule for .sc-status-line must exist (the status sentence element)."""
    assert _css_exists(".sc-status-line"), (
        "CSS rule .sc-status-line must exist — this element holds the plain-language "
        "status sentence that appears under the sprint header"
    )


def test_ac3_card_html_renders_status_line():
    """_smgmtCardHtml must include sc-status-line in its HTML output."""
    card_body = _fn_body("_smgmtCardHtml")
    assert "sc-status-line" in card_body, (
        "_smgmtCardHtml must emit a sc-status-line element directly under the "
        "sprint header containing the plain-language status sentence"
    )


def test_ac3_status_sentence_covers_ready_state():
    """Status sentence function must handle the 'ready to run' state."""
    fn_name = (
        "_smgmtCardStatusSentence"
        if _fn_exists("_smgmtCardStatusSentence")
        else "_smgmtStatusSentence"
    )
    body = _fn_body(fn_name)
    ready_indicators = ["Ready to run", "ready to run", "ready"]
    has_ready = any(r in body for r in ready_indicators)
    assert has_ready, (
        f"{fn_name} must include a 'Ready to run' case for planned/next-up sprints"
    )


def test_ac3_status_sentence_covers_rework_state():
    """Status sentence function must handle needs_rework state with ticket counts."""
    fn_name = (
        "_smgmtCardStatusSentence"
        if _fn_exists("_smgmtCardStatusSentence")
        else "_smgmtStatusSentence"
    )
    body = _fn_body(fn_name)
    rework_indicators = ["rework", "needs rework", "re-run", "rerun"]
    has_rework = any(r in body.lower() for r in rework_indicators)
    assert has_rework, (
        f"{fn_name} must include a needs_rework case describing the outcome "
        "in plain language (e.g. '3 of 4 passed, 1 needs rework')"
    )


# =============================================================================
# AC4 — Budget bar color follows real state: green / amber / red + headroom label
# =============================================================================


def test_ac4_cap_bar_green_css_exists():
    """CSS class for a green budget bar must exist (spend < budget)."""
    has_green = _css_exists(".cap-bar--green") or _css_exists(".cap-bar.green")
    assert has_green, (
        "A CSS class for the green budget bar must exist (e.g. .cap-bar--green) "
        "to signal spend < budget"
    )


def test_ac4_cap_bar_amber_css_exists():
    """CSS class for an amber budget bar must exist (spend = budget, 0 headroom)."""
    has_amber = _css_exists(".cap-bar--amber") or _css_exists(".cap-bar.amber")
    assert has_amber, (
        "A CSS class for the amber budget bar must exist (e.g. .cap-bar--amber) "
        "to signal spend = budget (0 headroom)"
    )


def test_ac4_cap_bar_red_css_exists():
    """CSS class for a red budget bar must exist (spend > budget, over budget)."""
    has_red = _css_exists(".cap-bar--red") or _css_exists(".cap-bar.red")
    assert has_red, (
        "A CSS class for the red budget bar must exist (e.g. .cap-bar--red) "
        "to signal spend > budget"
    )


def test_ac4_cap_bar_green_uses_green_color():
    """The green bar class must use var(--green) as background."""
    try:
        css = _css_rule(".cap-bar--green")
    except AssertionError:
        css = _css_rule(".cap-bar.green")
    assert "green" in css.lower() or "var(--green)" in css, (
        "The green budget bar CSS must reference var(--green)"
    )


def test_ac4_cap_bar_amber_uses_amber_color():
    """The amber bar class must use var(--amber) as background."""
    try:
        css = _css_rule(".cap-bar--amber")
    except AssertionError:
        css = _css_rule(".cap-bar.amber")
    assert "amber" in css.lower() or "var(--amber)" in css, (
        "The amber budget bar CSS must reference var(--amber)"
    )


def test_ac4_cap_bar_red_uses_red_color():
    """The red bar class must use var(--red) as background."""
    try:
        css = _css_rule(".cap-bar--red")
    except AssertionError:
        css = _css_rule(".cap-bar.red")
    assert "red" in css.lower() or "var(--red)" in css, (
        "The red budget bar CSS must reference var(--red)"
    )


def test_ac4_update_cap_bar_assigns_color_class():
    """_smgmtUpdateCapBar must assign a color class based on budget headroom."""
    cap_bar_body = _fn_body_html("_smgmtUpdateCapBar", PROJECT_HTML)
    # Must reference the color class variants (green/amber/red)
    color_classes = ["cap-bar--green", "cap-bar--amber", "cap-bar--red"]
    has_color_logic = any(cls in cap_bar_body for cls in color_classes)
    assert has_color_logic, (
        "_smgmtUpdateCapBar must assign a color class (cap-bar--green / "
        "cap-bar--amber / cap-bar--red) to the bar element based on budget state"
    )


def test_ac4_headroom_label_shown_when_positive():
    """_smgmtUpdateCapBar must show a headroom label when spend < budget."""
    cap_bar_body = _fn_body_html("_smgmtUpdateCapBar", PROJECT_HTML)
    headroom_indicators = ["headroom", "room for", "h headroom"]
    has_headroom = any(h in cap_bar_body.lower() for h in headroom_indicators)
    assert has_headroom, (
        "_smgmtUpdateCapBar must emit a headroom label (e.g. '2 h headroom — "
        "room for ~2 more XL tasks') when spend < budget"
    )


# =============================================================================
# AC5 — Badges: calm, state-coded; no loud filled style inconsistent with state
# =============================================================================


def test_ac5_next_up_badge_is_green():
    """smgmt-next-badge must use green color (Next up = go signal)."""
    next_css = _css_rule(".smgmt-next-badge")
    assert "green" in next_css.lower() or "var(--green)" in next_css, (
        ".smgmt-next-badge must use green — it signals the sprint is ready to run next"
    )


def test_ac5_draft_planned_badge_is_blue():
    """Draft / planned badge must use blue color (neutral planning state).

    The old sc-planned-badge was grey/muted.  AC5 says Draft → blue.
    """
    # May be sc-planned-badge (updated) or a new sc-draft-badge
    badge_classes = [".sc-planned-badge", ".sc-draft-badge"]
    found_css = None
    for cls in badge_classes:
        if _css_exists(cls):
            found_css = _css_rule(cls)
            break
    assert found_css is not None, (
        "A CSS rule for the Draft/planned badge must exist (.sc-planned-badge or .sc-draft-badge)"
    )
    assert "blue" in found_css.lower() or "var(--blue)" in found_css, (
        "The Draft badge must use blue color (var(--blue)) per AC5 — "
        f"not grey/muted. Got: {found_css!r}"
    )


def test_ac5_needs_rework_badge_is_amber():
    """state-rework badge must use amber color (Needs rework = attention required)."""
    # state-rework is the CSS class for needs_rework state badge
    rework_css = _css_rule(".smgmt-state-badge.state-rework")
    assert "amber" in rework_css.lower() or "var(--amber)" in rework_css, (
        ".smgmt-state-badge.state-rework must use amber — needs-rework demands operator attention. "
        f"Got: {rework_css!r}"
    )


def test_ac5_needs_merge_badge_is_amber():
    """state-finished badge must use amber color (Needs merge = awaiting operator action).

    The old state-finished used steel (blue-grey) which doesn't signal urgency.
    AC5 mandates amber for 'Needs merge' — it awaits operator action.
    """
    finished_css = _css_rule(".smgmt-state-badge.state-finished")
    assert "amber" in finished_css.lower() or "var(--amber)" in finished_css, (
        ".smgmt-state-badge.state-finished must use amber — 'Needs merge' awaits operator action. "
        f"Got: {finished_css!r}"
    )


def test_ac5_no_badge_uses_loud_red_fill():
    """No planning/status badge must use var(--red) as background fill.

    Red fill is for error/failed state only — planning badges (planned, next-up,
    draft, needs-merge, needs-rework) must use calm tones.
    """
    planning_badge_classes = [
        ".smgmt-next-badge",
        ".sc-planned-badge",
        ".sc-draft-badge",
        ".smgmt-state-badge.state-rework",
        ".smgmt-state-badge.state-finished",
        ".smgmt-state-badge.state-partial",
    ]
    for cls in planning_badge_classes:
        if not _css_exists(cls):
            continue
        css = _css_rule(cls)
        assert "background: var(--red)" not in css, (
            f"{cls} must not use a red background fill — red is reserved for "
            "error/failed states only"
        )


# =============================================================================
# AC6 — Run preview mini-rail visible on cards with active Run Sprint
# =============================================================================


def test_ac6_preview_slot_in_card_html():
    """_smgmtCardHtml must include a sc-preview-slot for the mini-rail."""
    card_body = _fn_body("_smgmtCardHtml")
    assert "sc-preview-slot" in card_body, (
        "_smgmtCardHtml must include a sc-preview-slot element where the "
        "run-preview mini-rail is injected for cards with active Run Sprint"
    )


def test_ac6_mini_rail_loaded_from_smgmt_render():
    """_smgmtRender must call _smgmtLoadMiniRail so preview appears on board rebuild."""
    render_body = _fn_body("_smgmtRender")
    assert "_smgmtLoadMiniRail" in render_body, (
        "_smgmtRender must call _smgmtLoadMiniRail so the run-preview mini-rail "
        "is injected on every board rebuild"
    )


def test_ac6_mini_rail_function_exists():
    """_smgmtMiniRailHtml must exist to render the run preview strip."""
    assert _fn_exists("_smgmtMiniRailHtml", PROJECT_HTML) or _fn_exists("_smgmtMiniRailHtml"), (
        "_smgmtMiniRailHtml must exist to render the dependency-check mini-rail"
    )


def test_ac6_mini_rail_shows_no_cycles_no_conflicts():
    """_smgmtMiniRailHtml must emit 'no cycles · no conflicts' when checks pass."""
    # Function body is in project.html inline JS
    body = _fn_body("_smgmtMiniRailHtml", PROJECT_HTML)
    assert "no cycles" in body, (
        "_smgmtMiniRailHtml must show 'no cycles' in the ok-state run preview"
    )


# =============================================================================
# Structural — card HTML has no multiple filled primary buttons side by side
# =============================================================================


def test_structural_finish_btn_not_primary_amber_alongside_rerun():
    """smgmt-finish-btn must not appear as an amber-filled button next to Re-run.

    When Re-run is the primary action (isHasRework), having smgmt-finish-btn
    with amber fill creates two visually-identical primary buttons.  The Merge
    Sprint button must become a quiet secondary in that context.
    """
    # The CSS for smgmt-finish-btn must not be an amber-filled primary button
    # (it can remain for the 'ready_to_merge' state where it IS the only action)
    finish_css = _css_rule(".smgmt-finish-btn")
    # Must not be a solid (filled) green or blue primary — those would clash
    # with the Re-run amber button as a second primary
    assert "background: var(--green)" not in finish_css, (
        ".smgmt-finish-btn must not be a green filled button (that would be a "
        "second primary alongside Re-run)"
    )


def test_structural_merge_secondary_css_exists():
    """A quiet secondary class for the Merge Sprint link must have CSS."""
    secondary_classes = [
        ".sc-merge-link",
        ".smgmt-merge-link",
        ".smgmt-finish-btn--quiet",
        ".smgmt-finish-btn--secondary",
        ".sc-merge-secondary",
    ]
    found = any(_css_exists(cls) for cls in secondary_classes)
    assert found, (
        "A quiet secondary CSS class must exist for the Merge Sprint action "
        "(e.g. .sc-merge-link) so it is visually subordinate to the Re-run primary"
    )
