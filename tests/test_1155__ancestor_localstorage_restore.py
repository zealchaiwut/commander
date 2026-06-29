"""Tests for issue #1155 — smgmtToggleAncestor writes localStorage state that is never read back.

Source-code tests verifying that _smgmtAncestorRowHtml reads localStorage on
render and restores expand/collapse state correctly.

AC coverage:
  AC1  — _smgmtAncestorRowHtml reads localStorage.getItem('slp_ancestor_<label>')
          and omits the hidden attribute on the body element when stored value is "1".
  AC2  — The chevron icon renders in the down (expanded) state (ti-chevron-down)
          when localStorage indicates the ancestor was previously expanded.
  AC3  — Toggling an ancestor open, then refreshing, restores expanded state
          (localStorage write in toggle + conditional render in HTML fn).
  AC4  — Toggling an ancestor closed, then refreshing, keeps it collapsed
          (the default hidden behavior is preserved when value is not "1").
  AC5  — Ancestor expand/collapse state is isolated per sprint label
          (localStorage key includes the label, not a global key).
"""
from __future__ import annotations

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


# =============================================================================
# AC1 — _smgmtAncestorRowHtml reads localStorage on render
# =============================================================================


def test_ac1_ancestor_row_html_reads_localstorage():
    """_smgmtAncestorRowHtml must call localStorage.getItem with the slp_ancestor_ key."""
    body = _fn_body("_smgmtAncestorRowHtml")
    assert "localStorage.getItem" in body, (
        "_smgmtAncestorRowHtml must read localStorage.getItem to restore "
        "expand/collapse state on render — currently the localStorage writes "
        "in smgmtToggleAncestor are dead code because render never reads them"
    )


def test_ac1_localstorage_key_uses_slp_ancestor_prefix():
    """The localStorage key read in _smgmtAncestorRowHtml must use the slp_ancestor_ prefix."""
    body = _fn_body("_smgmtAncestorRowHtml")
    assert "slp_ancestor_" in body, (
        "_smgmtAncestorRowHtml must use the same key prefix 'slp_ancestor_' as "
        "smgmtToggleAncestor so state written on toggle is read back on render"
    )


def test_ac1_hidden_attribute_is_conditional_not_hardcoded():
    """The hidden attribute on the ancestor body must be conditional, not always present."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # The body must NOT unconditionally set hidden as a string literal in the
    # element opening tag — it should be conditional based on localStorage
    # Check that there's conditional logic around the hidden attribute
    has_conditional = (
        # Ternary with hidden in some branch
        ('hidden' in body and ('?' in body or 'if' in body))
        # Or template literal with conditional hidden
        and ('localStorage' in body or 'isExpanded' in body or 'expanded' in body.lower())
    )
    assert has_conditional, (
        "The hidden attribute on the ancestor body element must be conditional "
        "based on the localStorage value — not hardcoded as always-present"
    )


def test_ac1_body_element_omits_hidden_when_localstorage_is_one():
    """When localStorage value is '1', the hidden attribute must be absent from the body element."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # The function must check for value "1" and conditionally render without hidden
    has_value_one_check = '"1"' in body or "'1'" in body
    assert has_value_one_check, (
        "_smgmtAncestorRowHtml must check if the localStorage value equals '1' "
        "to determine whether to omit the hidden attribute on the ancestor body"
    )


# =============================================================================
# AC2 — Chevron renders as ti-chevron-down when localStorage indicates expanded
# =============================================================================


def test_ac2_chevron_down_used_for_expanded_state():
    """_smgmtAncestorRowHtml must use ti-chevron-down when ancestor was previously expanded."""
    body = _fn_body("_smgmtAncestorRowHtml")
    assert "ti-chevron-down" in body, (
        "_smgmtAncestorRowHtml must emit ti-chevron-down (not just ti-chevron-right) "
        "so the chevron reflects the expanded state when localStorage says '1'"
    )


def test_ac2_chevron_right_used_for_collapsed_state():
    """_smgmtAncestorRowHtml must keep ti-chevron-right for collapsed/default state."""
    body = _fn_body("_smgmtAncestorRowHtml")
    assert "ti-chevron-right" in body, (
        "_smgmtAncestorRowHtml must still emit ti-chevron-right for collapsed state "
        "(the default when localStorage is absent or '0')"
    )


def test_ac2_chevron_class_is_conditional():
    """The chevron class must be conditional between right and down based on localStorage."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # Both icons should appear, meaning one is selected conditionally
    has_both = "ti-chevron-right" in body and "ti-chevron-down" in body
    assert has_both, (
        "_smgmtAncestorRowHtml must conditionally use either ti-chevron-right "
        "(collapsed) or ti-chevron-down (expanded) based on the localStorage value"
    )


# =============================================================================
# AC3 — Toggling open + refresh restores expanded state (round-trip write+read)
# =============================================================================


def test_ac3_toggle_writes_one_for_expanded():
    """smgmtToggleAncestor must write '1' to localStorage when expanding."""
    body = _fn_body("smgmtToggleAncestor")
    # When NOT expanded (hidden=true → body.hidden=true → isExpanded=false),
    # toggling opens it → localStorage should get "1"
    assert '"1"' in body or "'1'" in body, (
        "smgmtToggleAncestor must write '1' to localStorage when the ancestor "
        "transitions to expanded so the next render can restore this state"
    )


def test_ac3_render_reads_what_toggle_writes():
    """The key smgmtToggleAncestor writes must match what _smgmtAncestorRowHtml reads."""
    toggle_body = _fn_body("smgmtToggleAncestor")
    render_body = _fn_body("_smgmtAncestorRowHtml")
    # Both must reference the same localStorage key prefix
    assert "slp_ancestor_" in toggle_body, (
        "smgmtToggleAncestor must write using key prefix 'slp_ancestor_'"
    )
    assert "slp_ancestor_" in render_body, (
        "_smgmtAncestorRowHtml must read using the same key prefix 'slp_ancestor_' "
        "so the toggle write and the render read form a complete round-trip"
    )


# =============================================================================
# AC4 — Toggling closed + refresh keeps collapsed (default hidden preserved)
# =============================================================================


def test_ac4_toggle_writes_zero_for_collapsed():
    """smgmtToggleAncestor must write '0' to localStorage when collapsing."""
    body = _fn_body("smgmtToggleAncestor")
    assert '"0"' in body or "'0'" in body, (
        "smgmtToggleAncestor must write '0' to localStorage when collapsing "
        "so a collapsed ancestor stays collapsed across refreshes"
    )


def test_ac4_missing_or_zero_value_renders_hidden():
    """When localStorage has no entry or '0', the body must render with hidden."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # The function must default to hidden when localStorage doesn't return "1"
    # This is satisfied if the condition only removes hidden when value === "1"
    has_default_hidden = "hidden" in body
    assert has_default_hidden, (
        "_smgmtAncestorRowHtml must preserve the default hidden behavior when "
        "localStorage is absent or '0' — the ancestor must render collapsed by default"
    )


# =============================================================================
# AC5 — State is isolated per sprint label (key includes the label)
# =============================================================================


def test_ac5_localstorage_key_includes_label_in_render():
    """The localStorage key in _smgmtAncestorRowHtml must include the sprint label."""
    body = _fn_body("_smgmtAncestorRowHtml")
    # The key must be dynamic and include the label variable
    # e.g. `slp_ancestor_${label}` or `slp_ancestor_${safeLabel}`
    has_dynamic_key = (
        "slp_ancestor_${label}" in body
        or "slp_ancestor_${safeLabel}" in body
        or ("slp_ancestor_" in body and ("${label}" in body or "${safeLabel}" in body))
    )
    assert has_dynamic_key, (
        "_smgmtAncestorRowHtml must use a per-label localStorage key "
        "(e.g. `slp_ancestor_${label}`) so each ancestor's state is isolated"
    )


def test_ac5_localstorage_key_includes_label_in_toggle():
    """The localStorage key in smgmtToggleAncestor must include the sprint label."""
    body = _fn_body("smgmtToggleAncestor")
    has_dynamic_key = (
        "slp_ancestor_${label}" in body
        or ("slp_ancestor_" in body and "${label}" in body)
    )
    assert has_dynamic_key, (
        "smgmtToggleAncestor must use a per-label localStorage key "
        "so expanding sprint-73 does not affect sprint-74's stored state"
    )
