"""Tests for issue #1193: Make inspector a bottom sheet on mobile (≤640px)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")


# ── AC1 & AC2: bottom-sheet layout at ≤375px and ≤640px ──────────────────────

def test_inspector_bottom_sheet__css_position_fixed_in_640px_query():
    """AC1/AC2: @media (max-width: 640px) must set position:fixed on .inspector."""
    # Find the 640px media block that targets .inspector
    pattern = re.compile(r"@media\s*\(max-width:\s*640px\)[^{]*\{(.*?)(?=@media|\Z)", re.DOTALL)
    blocks = pattern.findall(PROJECT_HTML)
    assert blocks, "No @media (max-width: 640px) block found in project.html"

    combined = "\n".join(blocks)
    assert "position: fixed" in combined or "position:fixed" in combined, (
        "@media (max-width: 640px) must set position: fixed for the bottom-sheet inspector — AC1/AC2"
    )
    assert ".inspector" in combined, (
        "@media (max-width: 640px) must target .inspector — AC1/AC2"
    )


def test_inspector_bottom_sheet__css_left_right_bottom_in_640px_query():
    """AC1/AC2: mobile media query must pin the sheet with left:0; right:0; bottom:0."""
    pattern = re.compile(r"@media\s*\(max-width:\s*640px\)[^{]*\{(.*?)(?=@media|\Z)", re.DOTALL)
    blocks = pattern.findall(PROJECT_HTML)
    combined = "\n".join(blocks)

    assert ("left: 0" in combined or "left:0" in combined), (
        "@media (max-width: 640px) must include left: 0 for the inspector sheet — AC1/AC2"
    )
    assert ("right: 0" in combined or "right:0" in combined), (
        "@media (max-width: 640px) must include right: 0 for the inspector sheet — AC1/AC2"
    )
    assert ("bottom: 0" in combined or "bottom:0" in combined), (
        "@media (max-width: 640px) must include bottom: 0 for the inspector sheet — AC1/AC2"
    )


def test_inspector_bottom_sheet__css_max_height_55vh_in_640px_query():
    """AC1/AC2: mobile media query must constrain sheet height to 55vh."""
    pattern = re.compile(r"@media\s*\(max-width:\s*640px\)[^{]*\{(.*?)(?=@media|\Z)", re.DOTALL)
    blocks = pattern.findall(PROJECT_HTML)
    combined = "\n".join(blocks)

    assert "55vh" in combined, (
        "@media (max-width: 640px) must include max-height: 55vh for the inspector sheet — AC1/AC2"
    )


# ── AC3: inspector content scrolls vertically ─────────────────────────────────

def test_inspector_bottom_sheet__css_overflow_y_auto_in_640px_query():
    """AC3: inspector must have overflow-y: auto inside the mobile query."""
    pattern = re.compile(r"@media\s*\(max-width:\s*640px\)[^{]*\{(.*?)(?=@media|\Z)", re.DOTALL)
    blocks = pattern.findall(PROJECT_HTML)
    combined = "\n".join(blocks)

    assert ("overflow-y: auto" in combined or "overflow-y:auto" in combined), (
        "@media (max-width: 640px) must include overflow-y: auto so inspector content scrolls — AC3"
    )


# ── AC5: header/action buttons wrap ──────────────────────────────────────────

def test_inspector_bottom_sheet__inspector_head_flex_wrap():
    """AC5: .inspector-head must have flex-wrap: wrap so buttons don't overflow."""
    assert "flex-wrap: wrap" in PROJECT_HTML, (
        ".inspector-head must include flex-wrap: wrap — AC5 requires buttons to wrap at mobile widths"
    )
    # Confirm it's actually on .inspector-head (not just elsewhere)
    # Find the .inspector-head rule
    found = False
    for m in re.finditer(r"\.inspector-head\s*\{([^}]*)\}", PROJECT_HTML):
        if "flex-wrap" in m.group(1) and "wrap" in m.group(1):
            found = True
            break
    assert found, (
        ".inspector-head rule must include flex-wrap: wrap — AC5 requires buttons to wrap on mobile"
    )


# ── AC6: .log-tabs scrolls horizontally ──────────────────────────────────────

def test_inspector_bottom_sheet__log_tabs_overflow_x_in_640px_query():
    """AC6: .log-tabs must scroll horizontally inside the 640px media query."""
    pattern = re.compile(r"@media\s*\(max-width:\s*640px\)[^{]*\{(.*?)(?=@media|\Z)", re.DOTALL)
    blocks = pattern.findall(PROJECT_HTML)
    combined = "\n".join(blocks)

    assert "log-tabs" in combined, (
        "@media (max-width: 640px) must override .log-tabs for horizontal scroll — AC6"
    )
    assert ("overflow-x: auto" in combined or "overflow-x:auto" in combined), (
        "@media (max-width: 640px) must set overflow-x: auto on .log-tabs — AC6"
    )


def test_inspector_bottom_sheet__log_tabs_nowrap_in_640px_query():
    """AC6: .log-tabs must not wrap tabs (flex-wrap: nowrap) so they scroll instead."""
    pattern = re.compile(r"@media\s*\(max-width:\s*640px\)[^{]*\{(.*?)(?=@media|\Z)", re.DOTALL)
    blocks = pattern.findall(PROJECT_HTML)
    combined = "\n".join(blocks)

    assert ("flex-wrap: nowrap" in combined or "flex-wrap:nowrap" in combined), (
        "@media (max-width: 640px) must set flex-wrap: nowrap on .log-tabs — AC6 requires horizontal scroll, not line-wrap"
    )


# ── AC7: desktop layout unchanged ────────────────────────────────────────────

def test_inspector_bottom_sheet__desktop_inspector_not_fixed():
    """AC7: outside mobile media queries, .inspector must NOT have position: fixed."""
    # Strip all @media blocks and check the base .inspector rule
    src = PROJECT_HTML
    media_pattern = re.compile(r"@media[^{]*\{", re.DOTALL)
    pos = 0
    result_parts = []
    for m in media_pattern.finditer(src):
        result_parts.append(src[pos:m.start()])
        depth = 0
        i = m.end() - 1
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    pos = j + 1
                    break
    result_parts.append(src[pos:])
    src_no_media = "".join(result_parts)

    for m in re.finditer(r"\.inspector\s*\{([^}]*)\}", src_no_media):
        assert "position: fixed" not in m.group(1) and "position:fixed" not in m.group(1), (
            "Global .inspector must not set position: fixed — desktop layout must be unchanged (AC7)"
        )


# ── AC8: closed sheet does not obscure content ───────────────────────────────

def test_inspector_bottom_sheet__inspector_element_has_hidden():
    """AC8: smgmt-inspector element in HTML must start hidden so it doesn't block page content."""
    assert 'id="smgmt-inspector"' in PROJECT_HTML, (
        "project.html must contain the smgmt-inspector element — required for inspector feature"
    )
    # The element must carry the hidden attribute (or have class inspector so JS can toggle it)
    # Confirm it starts hidden in the HTML source
    idx = PROJECT_HTML.index('id="smgmt-inspector"')
    surrounding = PROJECT_HTML[max(0, idx - 60):idx + 80]
    assert "hidden" in surrounding, (
        'smgmt-inspector element must start with the hidden attribute so it does not block page content (AC8)'
    )


# ── AC4: no horizontal page scroll (structural) ───────────────────────────────

def test_inspector_bottom_sheet__inspector_in_run_shell():
    """Structural: smgmt-inspector must be in the HTML so the JS can find it."""
    assert 'id="smgmt-inspector"' in PROJECT_HTML, (
        "smgmt-inspector element must be present in project.html so the inspector JS can mount content"
    )


def test_inspector_bottom_sheet__375px_bottom_sheet_visual():
    """AC1: at 375px, inspector appears as bottom sheet — visual verification."""
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")


def test_inspector_bottom_sheet__641px_desktop_sidebar_visual():
    """AC7: at 641px+, inspector reverts to sidebar layout — visual verification."""
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")


def test_inspector_bottom_sheet__no_horizontal_page_scroll_visual():
    """AC4: page body does not scroll horizontally at any tested mobile width — visual verification."""
    pytest.skip("visual/CSS test — verified via design contract or agent-browser, not HTTP")
