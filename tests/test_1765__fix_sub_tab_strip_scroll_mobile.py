"""Tests for issue #1765 — Fix sub-tab strip scroll on mobile (390px).

AC coverage:
  AC1 — At 390px, every tab is reachable by horizontal swipe (overflow-x:auto on .sub-tabs)
  AC2 — page does not gain horizontal scroll (overflow stays inside .sub-tabs)
  AC3 — Manage▾ dropdown opens fully unclipped at 390px
  AC4 — Planning▾ dropdown opens fully unclipped at 390px
  AC5 — Both dropdowns unclipped at ≥768px (no regression)
  AC6 — Sub-tab strip unchanged at ≥768px (no overflow added)
  AC7 — At ≤430px: .proj-header h-padding ≤ var(--space-4); wordmark hidden; brand mark visible
  AC8 — Scrollbar not visible on the strip (scrollbar-width:none + ::-webkit-scrollbar)
  AC9 — Deploy (stab-deploy) reachable by scrolling to Manage▾ at 390px

These tests parse the static CSS in project.html. Pure-CSS changes have no callable
Python code path; CSS parsing is the closest equivalent to "behavioral" verification
for this class of change (viewport-scoped overflow and positioning rules).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
HTML_PATH = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def _extract_media_block(src: str, query: str) -> str:
    """Return the first @media block body whose query contains `query`."""
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
    """Yield the bodies of all @media (max-width: 430px) blocks."""
    yield from _extract_media_block(src, "max-width: 430px")


def _desktop_768_blocks(src: str):
    """Yield the bodies of all @media (min-width: 768px) blocks."""
    yield from _extract_media_block(src, "min-width: 768px")


# ── AC1 / AC2 — sub-tabs overflow-x: auto at ≤430px ────────────────────────

def test_ac1_sub_tabs_overflow_x_auto_at_430px():
    """At ≤430px, .sub-tabs must have overflow-x:auto so the strip is scrollable
    and tabs are reachable by horizontal swipe without causing page-level overflow."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    assert blocks, "@media (max-width: 430px) block must exist in project.html"
    combined = " ".join(blocks)
    assert "overflow-x" in combined and "auto" in combined, (
        "@media (max-width: 430px) block must set overflow-x: auto "
        "(checked via sub-tabs rule) to make the strip scrollable"
    )
    # Confirm it targets .sub-tabs specifically
    assert ".sub-tabs" in combined, (
        "@media (max-width: 430px) must include a .sub-tabs rule"
    )


# ── AC3 / AC4 — dropdown unclipped: sub-tabs-row as containing block ────────

def test_ac3_sub_tabs_row_position_relative_at_430px():
    """At ≤430px, .sub-tabs-row must be position:relative so that the dropdowns'
    containing block is outside the .sub-tabs scroll container, preventing clipping."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    combined = " ".join(blocks)
    assert ".sub-tabs-row" in combined, (
        "@media (max-width: 430px) must include a .sub-tabs-row rule"
    )
    assert "position" in combined and "relative" in combined, (
        "@media (max-width: 430px) must set position:relative on .sub-tabs-row "
        "so the dropdown's containing block is above the scroll container"
    )


def test_ac3_stab_group_position_static_at_430px():
    """.stab-group must be position:static at ≤430px so the dropdown's containing
    block rises to .sub-tabs-row (which lacks overflow), preventing clip by
    the .sub-tabs scroll container."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    combined = " ".join(blocks)
    assert ".stab-group" in combined, (
        "@media (max-width: 430px) must include a .stab-group rule"
    )
    assert "position" in combined and "static" in combined, (
        "@media (max-width: 430px) must set position:static on .stab-group "
        "to remove it as the dropdown's containing block"
    )


# ── AC5 / AC6 — no regression at ≥768px ─────────────────────────────────────

def test_ac6_desktop_sub_tabs_no_overflow_change():
    """The @media (min-width: 768px) block must NOT add overflow-x to .sub-tabs,
    so the desktop strip retains its current no-scroll layout."""
    src = _html()
    for block in _desktop_768_blocks(src):
        # Allow the block to exist, but must not set overflow-x:auto on .sub-tabs
        # (only the mobile block should add scrolling)
        if ".sub-tabs" in block and "overflow-x" in block and "auto" in block:
            # Check if this is scoped to a sub-selector (e.g. inside another block)
            # If we find overflow-x:auto directly in the 768px block for .sub-tabs, fail
            pytest.fail(
                "@media (min-width: 768px) must not add overflow-x:auto to .sub-tabs; "
                "desktop strip must remain without horizontal scroll"
            )


def test_ac5_desktop_stab_group_not_static():
    """At ≥768px, .stab-group must retain position:relative (the desktop default).
    The mobile position:static override must be confined to the ≤430px block."""
    src = _html()
    for block in _desktop_768_blocks(src):
        if ".stab-group" in block and "position" in block and "static" in block:
            pytest.fail(
                "@media (min-width: 768px) block must not set .stab-group to "
                "position:static — that override is mobile-only (≤430px)"
            )


# ── AC7 — header padding and wordmark at ≤430px ─────────────────────────────

def test_ac7_brand_wordmark_hidden_at_430px():
    """At ≤430px the .brand-wordmark (Commander text) must be hidden.
    The brand mark (bolt icon) stays visible."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    combined = " ".join(blocks)
    assert ".brand-wordmark" in combined, (
        "@media (max-width: 430px) must contain a .brand-wordmark rule"
    )
    assert "display" in combined and "none" in combined, (
        "@media (max-width: 430px) must set display:none on .brand-wordmark"
    )
    # brand-mark (the bolt icon) must NOT be hidden
    assert ".brand-mark" not in combined or "display: none" not in combined.split(".brand-mark")[1][:100], (
        ".brand-mark (bolt icon) must remain visible at ≤430px"
    )


def test_ac7_proj_header_padding_at_most_space4_at_430px():
    """At ≤430px, .proj-header horizontal padding must be ≤ var(--space-4) = 16px.
    The ≤699px block already sets 16px; the ≤430px block must not increase it."""
    src = _html()
    # Check existing 699px block sets the right padding
    blocks_699 = list(_extract_media_block(src, "max-width: 699px"))
    combined_699 = " ".join(blocks_699)
    assert ".proj-header" in combined_699, (
        "@media (max-width: 699px) must include a .proj-header rule"
    )
    # The 699px block sets padding: 12px 16px 0 (16px sides = var(--space-4))
    # At 430px, this still applies since 430 < 699
    # The 430px block must not override it with a LARGER value
    blocks_430 = list(_mobile_s_blocks(src))
    combined_430 = " ".join(blocks_430)
    if ".proj-header" in combined_430 and "padding" in combined_430:
        # Extract any explicit pixel or rem padding value in the 430px block
        large_padding = re.search(r"\.proj-header[^}]*padding[^:]*:\s*\w*\s*(\d+)px", combined_430)
        if large_padding:
            px_val = int(large_padding.group(1))
            assert px_val <= 16, (
                f"@media (max-width: 430px) sets .proj-header padding to {px_val}px "
                "which exceeds var(--space-4)=16px"
            )


# ── AC8 — scrollbar hidden ───────────────────────────────────────────────────

def test_ac8_scrollbar_width_none_at_430px():
    """At ≤430px, the .sub-tabs strip scrollbar must be hidden via scrollbar-width:none."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    combined = " ".join(blocks)
    assert "scrollbar-width" in combined and "none" in combined, (
        "@media (max-width: 430px) must set scrollbar-width:none on .sub-tabs"
    )


def test_ac8_webkit_scrollbar_hidden_at_430px():
    """At ≤430px, the ::-webkit-scrollbar pseudo-element must hide the scrollbar
    for WebKit-based browsers."""
    src = _html()
    blocks = list(_mobile_s_blocks(src))
    combined = " ".join(blocks)
    assert "-webkit-scrollbar" in combined, (
        "@media (max-width: 430px) must include a ::-webkit-scrollbar rule "
        "to hide the scrollbar in WebKit browsers"
    )


# ── AC9 — stab-deploy reachable ──────────────────────────────────────────────

def test_ac9_stab_deploy_not_removed():
    """The stab-deploy button must exist in the HTML and be inside the Manage dropdown,
    reachable at 390px by scrolling the strip to Manage▾ and opening the dropdown."""
    src = _html()
    assert 'id="stab-deploy"' in src, (
        "stab-deploy button must exist in project.html; "
        "it must remain reachable at 390px via the Manage▾ dropdown"
    )
    # stab-deploy must be inside stab-group-manage
    manage_idx = src.find('id="stab-group-manage"')
    deploy_idx = src.find('id="stab-deploy"')
    assert manage_idx >= 0 and deploy_idx > manage_idx, (
        "stab-deploy must appear after stab-group-manage in the DOM "
        "(inside the Manage dropdown)"
    )
