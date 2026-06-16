"""Tests for issue #1179 — Wrap backlog filter pills on narrow screens.

Source-code tests verifying the CSS media query at max-width: 620px is
present and correct for the backlog filter pill row.

AC coverage:
  AC1 — At 375px viewport width, filter pills wrap (620px query enables this)
  AC2 — At 600px viewport width, filter pills wrap (620px query covers 600px)
  AC3 — No horizontal page scroll at 375px/600px (no overflow-x added)
  AC4 — Desktop viewport (>=621px) layout unchanged — no flex-wrap override outside media query
  AC5 — .bl-filter has flex-wrap: wrap and gap: 6px inside @media (max-width: 620px)
  AC6 — .bl-pill has no flex-shrink: 0 or min-width preventing shrink inside the 620px block
  AC7 — No clip or overflow on container at narrow widths (no overflow-x: hidden on .bl-filter)
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
    """Return media blocks whose header contains query."""
    result = []
    for block in _extract_media_blocks(src):
        header_end = block.find("{")
        header = block[:header_end]
        if query in header:
            result.append(block)
    return result


def _strip_media_blocks(src: str) -> str:
    """Return src with all @media blocks removed (for checking global rules)."""
    result = src
    for block in _extract_media_blocks(src):
        result = result.replace(block, "")
    return result


# =============================================================================
# AC1 / AC2 — @media (max-width: 620px) exists and covers both 375px and 600px
# =============================================================================


def test_ac1_ac2_media_query_620px_exists():
    """A @media (max-width: 620px) block must exist in project.html.

    This single breakpoint covers both 375px (AC1) and 600px (AC2) since
    both values are less than 620px.
    """
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    assert blocks, (
        "A @media (max-width: 620px) rule must exist in project.html — "
        "AC1 and AC2 require that filter pills wrap at 375px and 600px, which "
        "is enabled by this breakpoint"
    )


def test_ac1_ac2_620px_block_targets_bl_filter():
    """The @media (max-width: 620px) block must contain a .bl-filter rule."""
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    hit = any(".bl-filter" in b for b in blocks)
    assert hit, (
        "The @media (max-width: 620px) block must contain a .bl-filter rule — "
        "AC5 requires flex-wrap: wrap and gap: 6px applied at this breakpoint"
    )


# =============================================================================
# AC5 — .bl-filter has flex-wrap: wrap and gap: 6px inside max-width: 620px
# =============================================================================


def test_ac5_bl_filter_flex_wrap_wrap_in_620px():
    """The .bl-filter rule inside @media (max-width: 620px) must set flex-wrap: wrap."""
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    for block in blocks:
        if ".bl-filter" in block:
            assert "flex-wrap: wrap" in block or "flex-wrap:wrap" in block, (
                "The .bl-filter rule inside @media (max-width: 620px) must include "
                "`flex-wrap: wrap` — AC5 requires pills to wrap at narrow viewports"
            )
            return
    assert False, "No @media (max-width: 620px) block with .bl-filter found"


def test_ac5_bl_filter_gap_6px_in_620px():
    """The .bl-filter rule inside @media (max-width: 620px) must set gap: 6px."""
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    for block in blocks:
        if ".bl-filter" in block:
            assert "gap: 6px" in block or "gap:6px" in block, (
                "The .bl-filter rule inside @media (max-width: 620px) must include "
                "`gap: 6px` — AC5 requires this gap value at the narrow breakpoint"
            )
            return
    assert False, "No @media (max-width: 620px) block with .bl-filter found"


# =============================================================================
# AC4 — Desktop layout (>=621px) unchanged
# =============================================================================


def test_ac4_global_bl_filter_has_display_flex():
    """The global (desktop) .bl-filter must still use display: flex."""
    src_no_media = _strip_media_blocks(PROJECT_HTML)
    assert ".bl-filter" in src_no_media, (
        ".bl-filter global rule must exist outside any media query — "
        "AC4 requires the desktop layout to be unchanged"
    )
    pat = re.compile(r"\.bl-filter\s*\{([^{}]*)\}")
    for m in pat.finditer(src_no_media):
        rule_body = m.group(1)
        if "display: flex" in rule_body or "display:flex" in rule_body:
            return
    assert False, (
        ".bl-filter global rule must have `display: flex` — "
        "desktop layout depends on this"
    )


def test_ac4_flex_wrap_not_overriding_to_nowrap_outside_media():
    """Global .bl-filter must not override flex-wrap to nowrap (would break desktop)."""
    src_no_media = _strip_media_blocks(PROJECT_HTML)
    pat = re.compile(r"\.bl-filter\s*\{([^{}]*)\}")
    for m in pat.finditer(src_no_media):
        rule_body = m.group(1)
        assert "flex-wrap: nowrap" not in rule_body and "flex-wrap:nowrap" not in rule_body, (
            ".bl-filter global rule must not set flex-wrap: nowrap — "
            "AC4 requires desktop layout to remain unchanged (single row)"
        )


# =============================================================================
# AC6 — .bl-pill can shrink within the 620px breakpoint
# =============================================================================


def test_ac6_no_flex_shrink_zero_on_bl_pill_in_620px():
    """The @media (max-width: 620px) block must not set flex-shrink: 0 on .bl-pill."""
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    for block in blocks:
        if ".bl-pill" in block:
            # Check that .bl-pill rule in this block doesn't set flex-shrink: 0
            pill_rules = re.findall(r"\.bl-pill[^{]*\{([^}]*)\}", block)
            for rule in pill_rules:
                assert "flex-shrink: 0" not in rule and "flex-shrink:0" not in rule, (
                    ".bl-pill inside @media (max-width: 620px) must not set "
                    "flex-shrink: 0 — AC6 requires pills to be able to shrink at narrow widths"
                )


def test_ac6_no_min_width_preventing_shrink_on_bl_pill_in_620px():
    """The @media (max-width: 620px) block must not add a wide min-width to .bl-pill."""
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    for block in blocks:
        if ".bl-pill" in block:
            pill_rules = re.findall(r"\.bl-pill[^{]*\{([^}]*)\}", block)
            for rule in pill_rules:
                # min-width: 0 or no min-width is fine; any positive value is problematic
                mw = re.search(r"min-width\s*:\s*([^;]+)", rule)
                if mw:
                    val = mw.group(1).strip()
                    assert val == "0" or val == "0px", (
                        f".bl-pill inside @media (max-width: 620px) has min-width: {val} "
                        "which prevents wrapping — AC6 requires pills to shrink freely"
                    )


# =============================================================================
# AC3 / AC7 — No overflow-x introduced at the narrow breakpoint
# =============================================================================


def test_ac3_ac7_no_overflow_x_scroll_in_620px():
    """The @media (max-width: 620px) block must not introduce overflow-x: scroll."""
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    for block in blocks:
        assert "overflow-x: scroll" not in block, (
            "@media (max-width: 620px) must not add overflow-x: scroll — "
            "AC3 requires no horizontal page scroll at 375px or 600px"
        )


def test_ac3_ac7_no_overflow_x_auto_on_bl_filter_in_620px():
    """The @media (max-width: 620px) block must not set overflow-x: auto on .bl-filter."""
    blocks = _media_blocks_for_query("max-width: 620px", PROJECT_HTML)
    for block in blocks:
        if ".bl-filter" in block:
            filter_rules = re.findall(r"\.bl-filter\s*\{([^}]*)\}", block)
            for rule in filter_rules:
                assert "overflow-x: auto" not in rule and "overflow-x:auto" not in rule, (
                    ".bl-filter inside @media (max-width: 620px) must not set "
                    "overflow-x: auto — AC7 requires the filter row not to clip/overflow"
                )
                assert "overflow-x: hidden" not in rule and "overflow-x:hidden" not in rule, (
                    ".bl-filter inside @media (max-width: 620px) must not set "
                    "overflow-x: hidden — AC7 requires pills not to be clipped"
                )
