"""Tests for issue #1768 — Fix horizontal overflow on mobile viewports ≤430px.

AC coverage:
  AC1 — At 390px, document.documentElement.scrollWidth === 390 on all tabs
         → body overflow-x: hidden in ≤430px block prevents page-level overflow
  AC2 — No element border-box right edge exceeds 390px
         → same body overflow-x: hidden plus per-element fixes
  AC3 — .proj-sticky-chrome and .proj-header no longer overflow at 390px
         → existing ≤430px overrides + proj-header-actions flex-shrink:1
  AC4 — .subnav pill row (Board/Running/History + New Sprint) wraps or scrolls
         without pushing the page wider
         → .subnav gets flex-wrap: wrap in the ≤430px block
  AC5 — .smgmt-sprint-header-right wraps beneath sprint title at ≤430px
         (consistent with existing max-width:480px column layout on sc-header)
         → .smgmt-sprint-header-right gets flex-wrap: wrap in the ≤480px block
  AC6 — flex-shrink:0 elements in .proj-sticky-chrome (create/auto-refresh cluster)
         do not force the sticky bar wider than the viewport at 390px
         → .proj-header-actions flex-shrink: 1 in the ≤430px block
  AC7 — No visual regression at ≥768px; layouts are pixel-identical before/after
         → fix rules scoped to max-width blocks only, no min-width:768px changes
  AC8 — Fix is inside @media (max-width: 430px) or (max-width: 390px) blocks;
         does not alter the existing max-width: 480px block except to extend it
         for the sprint-header-right cluster

These tests parse the static CSS in project.html.  Pure-CSS changes have no
callable Python code path; CSS parsing is the closest equivalent to
"behavioral" verification for this class of change (viewport-scoped overflow
and flex-wrap rules).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HTML_PATH = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _extract_media_blocks(src: str, query: str):
    """Yield body strings of all @media blocks whose condition contains `query`."""
    pattern = re.compile(
        r"@media\s*\([^)]*" + re.escape(query) + r"[^)]*\)\s*\{",
        re.IGNORECASE,
    )
    for m in pattern.finditer(src):
        start = m.end()
        depth = 1
        i = start
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        if depth == 0:
            yield src[start : i - 1]


def _mobile_s_blocks(src: str):
    """Yield bodies of all @media (max-width: 430px) blocks."""
    yield from _extract_media_blocks(src, "max-width: 430px")


def _mobile_480_blocks(src: str):
    """Yield bodies of all @media (max-width: 480px) blocks."""
    yield from _extract_media_blocks(src, "max-width: 480px")


def _desktop_768_blocks(src: str):
    """Yield bodies of all @media (min-width: 768px) blocks."""
    yield from _extract_media_blocks(src, "min-width: 768px")


# ── AC1 / AC2 / AC3 — body overflow-x: hidden prevents page-level overflow ──


def test_ac1_body_overflow_x_hidden_at_430px():
    """At ≤430px body must have overflow-x: hidden so document.documentElement.scrollWidth
    equals the viewport width (390px) and no element's border-box right edge escapes."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    assert blocks, "@media (max-width: 430px) block must exist in project.html"
    combined = " ".join(blocks)
    assert "body" in combined, (
        "@media (max-width: 430px) must include a body rule to clamp horizontal overflow"
    )
    assert "overflow-x" in combined, (
        "@media (max-width: 430px) body rule must set overflow-x"
    )
    assert "hidden" in combined, (
        "@media (max-width: 430px) body rule must set overflow-x: hidden"
    )


# ── AC4 — subnav wraps or scrolls without pushing page wider ─────────────────


def test_ac4_subnav_flex_wrap_at_430px():
    """.subnav at ≤430px must use flex-wrap: wrap (or overflow-x: auto) so that the
    Board/Running/History + New Sprint buttons do not push the page past 390px."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    assert blocks, "@media (max-width: 430px) block must exist"
    combined = " ".join(blocks)
    assert ".subnav" in combined, (
        "@media (max-width: 430px) must include a .subnav rule"
    )
    # Accept either flex-wrap: wrap or overflow-x: auto — both prevent page overflow
    subnav_fix = "flex-wrap" in combined or (
        "overflow-x" in combined and ("auto" in combined or "hidden" in combined)
    )
    assert subnav_fix, (
        "@media (max-width: 430px) .subnav rule must add flex-wrap: wrap "
        "or overflow-x: auto/hidden to prevent the pill row from widening the page"
    )


def test_ac4_subnav_flex_wrap_value():
    """Verify the .subnav rule specifically sets flex-wrap: wrap (preferred approach)
    so the subnav-actions wraps to a second line rather than overflowing."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    combined = " ".join(blocks)
    # Extract the .subnav rule from the 430px block
    # Look for flex-wrap in context of .subnav
    subnav_section = re.search(r"\.subnav\s*\{([^}]*)\}", combined)
    if subnav_section:
        rule_body = subnav_section.group(1)
        assert "flex-wrap" in rule_body and "wrap" in rule_body, (
            ".subnav rule in @media (max-width: 430px) must set flex-wrap: wrap"
        )
    else:
        # The subnav rule may span multiple lines; check the block contains both
        assert "flex-wrap" in combined and "wrap" in combined, (
            "@media (max-width: 430px) block must contain flex-wrap: wrap "
            "to prevent .subnav overflow"
        )


# ── AC5 — smgmt-sprint-header-right wraps at ≤480px ─────────────────────────


def test_ac5_sprint_header_right_flex_wrap_at_480px():
    """.smgmt-sprint-header-right must have flex-wrap: wrap inside the @media (max-width: 480px)
    block so the run/rerun buttons wrap beneath the sprint title on narrow viewports."""
    src = _html()
    blocks = list(_mobile_480_blocks(src))
    assert blocks, "@media (max-width: 480px) block must exist"
    combined = " ".join(blocks)
    assert ".smgmt-sprint-header-right" in combined, (
        "@media (max-width: 480px) must include a .smgmt-sprint-header-right rule "
        "(AC5 requires it to wrap beneath the sprint title at ≤430px, and 430 < 480)"
    )
    # Check that flex-wrap: wrap is set for the right cluster
    rh_section = re.search(r"\.smgmt-sprint-header-right\s*\{([^}]*)\}", combined)
    if rh_section:
        rule_body = rh_section.group(1)
        assert "flex-wrap" in rule_body and "wrap" in rule_body, (
            ".smgmt-sprint-header-right in @media (max-width: 480px) must set flex-wrap: wrap"
        )
    else:
        assert "flex-wrap" in combined and "wrap" in combined, (
            "@media (max-width: 480px) must contain flex-wrap: wrap "
            "for .smgmt-sprint-header-right"
        )


def test_ac5_sprint_header_column_layout_still_present_at_480px():
    """The existing column-direction layout for .smgmt-sprint-header must remain
    in the 480px block (existing behavior must not be removed)."""
    src = _html()
    blocks = list(_mobile_480_blocks(src))
    combined = " ".join(blocks)
    assert ".smgmt-sprint-header" in combined, (
        "@media (max-width: 480px) must still contain the .smgmt-sprint-header rule"
    )
    assert "flex-direction" in combined and "column" in combined, (
        "Existing flex-direction: column on .smgmt-sprint-header must be preserved"
    )


# ── AC6 — flex-shrink:0 cluster in sticky chrome doesn't force overflow ──────


def test_ac6_proj_header_actions_flex_shrink_at_430px():
    """.proj-header-actions must have flex-shrink: 1 (overriding the default 0) in the
    ≤430px block so the create/auto-refresh cluster can shrink if the header is too narrow."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    assert blocks, "@media (max-width: 430px) block must exist"
    combined = " ".join(blocks)
    assert ".proj-header-actions" in combined, (
        "@media (max-width: 430px) must include a .proj-header-actions rule (AC6)"
    )
    actions_section = re.search(r"\.proj-header-actions\s*\{([^}]*)\}", combined)
    if actions_section:
        rule_body = actions_section.group(1)
        assert "flex-shrink" in rule_body and "1" in rule_body, (
            ".proj-header-actions in @media (max-width: 430px) must set flex-shrink: 1"
        )
    else:
        assert "flex-shrink" in combined and "1" in combined, (
            "@media (max-width: 430px) must set flex-shrink: 1 on .proj-header-actions"
        )


# ── AC7 — no visual regression at ≥768px ─────────────────────────────────────


def test_ac7_no_subnav_change_at_768px():
    """The @media (min-width: 768px) block must NOT modify .subnav flex-wrap,
    preserving the desktop layout unchanged."""
    src = _html()
    for block in _desktop_768_blocks(src):
        if ".subnav" in block and "flex-wrap" in block:
            pytest.fail(
                "@media (min-width: 768px) must not set flex-wrap on .subnav; "
                "the wrap rule is mobile-only (≤430px)"
            )


def test_ac7_no_sprint_header_right_change_at_768px():
    """The @media (min-width: 768px) block must not modify .smgmt-sprint-header-right,
    preserving the desktop layout unchanged."""
    src = _html()
    for block in _desktop_768_blocks(src):
        if ".smgmt-sprint-header-right" in block:
            pytest.fail(
                "@media (min-width: 768px) must not include .smgmt-sprint-header-right rules; "
                "that rule is mobile-only (≤480px)"
            )


def test_ac7_no_body_overflow_change_at_768px():
    """The @media (min-width: 768px) block must not set overflow-x: hidden on body,
    as that would affect desktop layout."""
    src = _html()
    for block in _desktop_768_blocks(src):
        if "body" in block and "overflow-x" in block and "hidden" in block:
            pytest.fail(
                "@media (min-width: 768px) must not set body overflow-x: hidden; "
                "the overflow clamp is mobile-only (≤430px)"
            )


# ── AC8 — existing 480px block preserved, new rules in 430px ─────────────────


def test_ac8_existing_480px_block_preserved():
    """The existing @media (max-width: 480px) block's .smgmt-sprint-header rule
    must be preserved intact — only the sprint-header-right addition is allowed."""
    src = _html()
    blocks = list(_mobile_480_blocks(src))
    assert blocks, "@media (max-width: 480px) block must exist"
    combined = " ".join(blocks)
    # Original rule: flex-direction:column on the sprint header container
    assert ".smgmt-sprint-header" in combined and "flex-direction" in combined, (
        "Original .smgmt-sprint-header flex-direction: column must remain in 480px block"
    )


def test_ac8_fix_in_430px_block_not_wide_breakpoints():
    """The AC1-AC4,AC6 fixes (body overflow-x, subnav flex-wrap, proj-header-actions
    flex-shrink) must appear inside a ≤430px or ≤390px block, not in a wider breakpoint."""
    src = _html()
    # Verify they are NOT in the 699px block
    blocks_699 = list(_extract_media_blocks(src, "max-width: 699px"))
    combined_699 = " ".join(blocks_699)
    # The subnav wrap should not be in the 699px block (it would affect too wide a range)
    if ".subnav" in combined_699 and "flex-wrap" in combined_699 and "wrap" in combined_699:
        pytest.fail(
            "@media (max-width: 699px) must not contain .subnav flex-wrap: wrap; "
            "that rule belongs in the ≤430px block"
        )


# ── Structural integrity — no pre-existing test helpers broken ────────────────


def test_project_html_exists():
    """Sanity check: project.html must exist at the expected path."""
    assert HTML_PATH.exists(), f"project.html not found at {HTML_PATH}"


def test_subnav_html_structure_intact():
    """The .subnav element must still exist in the HTML with Board/Running/History tabs."""
    src = _html()
    assert 'class="subnav"' in src or "class='subnav'" in src, (
        ".subnav element must remain in project.html"
    )
    assert "smgmt-subtab-board" in src, "Board subtab must remain in the subnav"
    assert "smgmt-subtab-running" in src, "Running subtab must remain in the subnav"
    assert "smgmt-subtab-history" in src, "History subtab must remain in the subnav"
    assert "smgmt-new-sprint-btn" in src, "New Sprint button must remain in the subnav"
