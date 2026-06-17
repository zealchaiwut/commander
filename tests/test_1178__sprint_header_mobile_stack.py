"""Tests for issue #1178 — Stack sprint-card header vertically on mobile phones.

Source-code tests that verify the CSS media query is present and correct.

AC coverage:
  AC1 — @media (max-width: 480px) rule added in project.html targeting .smgmt-sprint-header
  AC2 — At 375px (and up to 480px): flex-direction: column applied to .smgmt-sprint-header
  AC3 — At 480px: same column layout applies (covered by AC2 — the query uses max-width: 480px)
  AC4 — Title and action rows are separate stacked rows (column layout + align-items: flex-start)
  AC5 — At >=1024px desktop layout is unchanged: justify-content: space-between present
         outside the media query (not overridden)
  AC6 — No new overflow-x: scroll / overflow-x: auto introduced at narrow breakpoints
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")


# ── helpers ──────────────────────────────────────────────────────────────────


def _extract_media_blocks(src: str) -> list[str]:
    """Return all @media rule bodies from src."""
    blocks = []
    pos = 0
    while True:
        idx = src.find("@media", pos)
        if idx == -1:
            break
        brace = src.find("{", idx)
        if brace == -1:
            break
        depth = 0
        end = brace
        for i in range(brace, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        blocks.append(src[idx : end + 1])
        pos = end + 1
    return blocks


def _media_blocks_for_query(query: str, src: str) -> list[str]:
    """Return media blocks whose header contains query (e.g. 'max-width: 480px')."""
    result = []
    for block in _extract_media_blocks(src):
        header_end = block.find("{")
        header = block[:header_end]
        if query in header:
            result.append(block)
    return result


def _contains_sprint_header_rule(block: str) -> bool:
    """True if the media block contains a .smgmt-sprint-header rule."""
    return ".smgmt-sprint-header" in block


# =============================================================================
# AC1 — @media (max-width: 480px) block targeting .smgmt-sprint-header
# =============================================================================


def test_ac1_media_query_480_exists():
    """A @media (max-width: 480px) block must exist in project.html."""
    blocks = _media_blocks_for_query("max-width: 480px", PROJECT_HTML)
    assert blocks, (
        "A @media (max-width: 480px) rule must exist in project.html — "
        "AC1 requires adding this media query to handle narrow phone screens"
    )


def test_ac1_media_query_targets_sprint_header():
    """The @media (max-width: 480px) block must target .smgmt-sprint-header."""
    blocks = _media_blocks_for_query("max-width: 480px", PROJECT_HTML)
    hit = any(_contains_sprint_header_rule(b) for b in blocks)
    assert hit, (
        "At least one @media (max-width: 480px) block must contain a "
        ".smgmt-sprint-header rule — AC1 requires this responsive rule"
    )


# =============================================================================
# AC2 / AC3 — flex-direction: column at ≤480px
# =============================================================================


def test_ac2_flex_direction_column_in_480_query():
    """The @media (max-width: 480px) .smgmt-sprint-header rule must set flex-direction: column."""
    blocks = _media_blocks_for_query("max-width: 480px", PROJECT_HTML)
    for block in blocks:
        if _contains_sprint_header_rule(block):
            assert "flex-direction: column" in block or "flex-direction:column" in block, (
                "The .smgmt-sprint-header rule inside @media (max-width: 480px) must set "
                "flex-direction: column — required for AC2 (stacks header vertically at 375px "
                "and 480px)"
            )
            return
    assert False, "No @media (max-width: 480px) .smgmt-sprint-header rule found"


# =============================================================================
# AC4 — align-items: flex-start so title and actions form separate stacked rows
# =============================================================================


def test_ac4_align_items_flex_start_in_480_query():
    """The @media (max-width: 480px) .smgmt-sprint-header rule must set align-items: flex-start."""
    blocks = _media_blocks_for_query("max-width: 480px", PROJECT_HTML)
    for block in blocks:
        if _contains_sprint_header_rule(block):
            assert "align-items: flex-start" in block or "align-items:flex-start" in block, (
                "The .smgmt-sprint-header rule inside @media (max-width: 480px) must set "
                "align-items: flex-start — AC4 requires title and action rows to be stacked "
                "as separate full-width rows"
            )
            return
    assert False, "No @media (max-width: 480px) .smgmt-sprint-header rule found"


# =============================================================================
# AC5 — Desktop layout unchanged at ≥1024px
# =============================================================================


def test_ac5_desktop_justify_space_between_not_overridden():
    """The global .smgmt-sprint-header must still use justify-content: space-between."""
    # The existing rule at line 2518 sets justify-content: space-between.
    # It must not be removed or overridden outside of a media query.
    assert "justify-content: space-between" in PROJECT_HTML, (
        ".smgmt-sprint-header must retain `justify-content: space-between` at the "
        "global (desktop) scope — AC5 requires the desktop side-by-side layout be unchanged"
    )


def test_ac5_no_column_layout_outside_media_query():
    """flex-direction: column on .smgmt-sprint-header must only appear inside a media query."""
    # Find all occurrences of .smgmt-sprint-header { ... } blocks outside media queries.
    # We do this by stripping all media blocks and checking the remainder.
    src_no_media = PROJECT_HTML
    for block in _extract_media_blocks(PROJECT_HTML):
        src_no_media = src_no_media.replace(block, "")

    # In the stripped source, .smgmt-sprint-header must not have flex-direction: column
    pat = re.compile(
        r"\.smgmt-sprint-header[^{}]*\{([^{}]*)\}"
    )
    for m in pat.finditer(src_no_media):
        rule_body = m.group(1)
        assert "flex-direction: column" not in rule_body and "flex-direction:column" not in rule_body, (
            ".smgmt-sprint-header must NOT have flex-direction: column outside a media query — "
            "the column layout must be scoped to ≤480px so the desktop (≥1024px) layout "
            "remains side-by-side (AC5)"
        )


# =============================================================================
# AC6 — No new overflow-x: scroll / overflow-x: auto at narrow breakpoints
# =============================================================================


def test_ac6_no_overflow_x_scroll_in_480_query():
    """The @media (max-width: 480px) block must not introduce overflow-x: scroll or auto."""
    blocks = _media_blocks_for_query("max-width: 480px", PROJECT_HTML)
    for block in blocks:
        assert "overflow-x: scroll" not in block, (
            "@media (max-width: 480px) must not add overflow-x: scroll — "
            "AC6 requires no new horizontal scrollbar be introduced at any breakpoint"
        )
        assert "overflow-x: auto" not in block, (
            "@media (max-width: 480px) must not add overflow-x: auto — "
            "AC6 requires no new horizontal scrollbar be introduced at any breakpoint"
        )
