"""Tests for issue #1183 — Stack logs search and view toggle on narrow screens.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — At 375px, .logs-toolbar-row2 items stack vertically (flex-direction:column)
  AC2 — At 375px, .logs-search-wrap spans full container width
  AC3 — At 375px, .logs-view-toggle spans full container width
  AC4 — At 620px, same stacking behavior (flex-direction:column, full-width items)
  AC5 — Search input fully usable: .logs-search has width:100% inside .logs-search-wrap
  AC6 — Desktop (>620px): .logs-toolbar-row2 layout unchanged (no flex-direction:column outside media)
  AC7 — No horizontal overflow: .logs-toolbar-row2 stretches items rather than overflowing
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HTML_PATH = DASHBOARD_DIR / "static" / "project.html"
PROJECT_HTML = _HTML_PATH.read_text(encoding="utf-8")


def _extract_max_width_620_blocks(html: str) -> list[str]:
    """Return the contents of all @media (max-width:620px) blocks."""
    blocks = []
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*620px\s*\)\s*\{', re.IGNORECASE)
    for m in pattern.finditer(html):
        start = m.end()
        depth = 1
        i = start
        while i < len(html) and depth > 0:
            if html[i] == '{':
                depth += 1
            elif html[i] == '}':
                depth -= 1
            i += 1
        blocks.append(html[start : i - 1])
    return blocks


def _get_rule_body(block: str, selector: str) -> str | None:
    """Return the rule body for `selector` inside `block`, or None if not found."""
    escaped = re.escape(selector)
    rule_pattern = re.compile(
        r'(?:^|[{};,\s])' + escaped + r'(?:\s*[{,])',
        re.MULTILINE,
    )
    for rule_m in rule_pattern.finditer(block):
        brace_start = block.find('{', rule_m.start())
        if brace_start == -1:
            continue
        depth = 1
        j = brace_start + 1
        while j < len(block) and depth > 0:
            if block[j] == '{':
                depth += 1
            elif block[j] == '}':
                depth -= 1
            j += 1
        return block[brace_start + 1 : j - 1]
    return None


def _strip_620px_blocks(html: str) -> str:
    """Return html with all @media (max-width:620px) blocks removed."""
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*620px\s*\)\s*\{', re.IGNORECASE)
    result = html
    for m in reversed(list(pattern.finditer(html))):
        start = m.start()
        end = m.end()
        depth = 1
        i = end
        while i < len(html) and depth > 0:
            if html[i] == '{':
                depth += 1
            elif html[i] == '}':
                depth -= 1
            i += 1
        result = result[:start] + result[i:]
    return result


MW620_BLOCKS = _extract_max_width_620_blocks(PROJECT_HTML)
COMBINED_MW620 = "\n".join(MW620_BLOCKS)


def test_max_width_620_block_exists():
    """AC1/AC4: A @media (max-width:620px) block must exist in project.html."""
    assert MW620_BLOCKS, "No @media (max-width:620px) block found in project.html"


def test_logs_toolbar_row2_flex_direction_column_in_620_block():
    """AC1/AC4: .logs-toolbar-row2 must have flex-direction:column inside @media (max-width:620px)."""
    body = _get_rule_body(COMBINED_MW620, ".logs-toolbar-row2")
    assert body is not None, (
        ".logs-toolbar-row2 rule not found inside any @media (max-width:620px) block"
    )
    assert re.search(r'flex-direction\s*:\s*column', body), (
        ".logs-toolbar-row2 does not have flex-direction:column inside @media (max-width:620px)"
    )


def test_logs_toolbar_row2_align_items_stretch_in_620_block():
    """AC2/AC3/AC7: .logs-toolbar-row2 must have align-items:stretch inside @media (max-width:620px)
    so children expand to full container width."""
    body = _get_rule_body(COMBINED_MW620, ".logs-toolbar-row2")
    assert body is not None, (
        ".logs-toolbar-row2 rule not found inside any @media (max-width:620px) block"
    )
    assert re.search(r'align-items\s*:\s*stretch', body), (
        ".logs-toolbar-row2 does not have align-items:stretch inside @media (max-width:620px) "
        "— children won't expand to full width"
    )


def test_logs_search_wrap_full_width_in_620_block():
    """AC2: .logs-search-wrap must span full container width at ≤620px."""
    body = _get_rule_body(COMBINED_MW620, ".logs-search-wrap")
    assert body is not None, (
        ".logs-search-wrap rule not found inside any @media (max-width:620px) block"
    )
    has_full_width = re.search(r'max-width\s*:\s*(?:100%|none)', body) or \
                     re.search(r'width\s*:\s*100%', body)
    assert has_full_width, (
        ".logs-search-wrap does not have max-width:100% or width:100% inside "
        "@media (max-width:620px) — search input won't be full width"
    )


def test_logs_view_toggle_full_width_in_620_block():
    """AC3: .logs-view-toggle must span full container width at ≤620px."""
    body = _get_rule_body(COMBINED_MW620, ".logs-view-toggle")
    assert body is not None, (
        ".logs-view-toggle rule not found inside any @media (max-width:620px) block"
    )
    assert re.search(r'width\s*:\s*100%', body), (
        ".logs-view-toggle does not have width:100% inside @media (max-width:620px)"
    )


def test_desktop_logs_toolbar_row2_no_column_direction():
    """AC6: .logs-toolbar-row2 must NOT have flex-direction:column outside @media (max-width:620px)."""
    remaining = _strip_620px_blocks(PROJECT_HTML)
    body = _get_rule_body(remaining, ".logs-toolbar-row2")
    if body is not None:
        assert not re.search(r'flex-direction\s*:\s*column', body), (
            ".logs-toolbar-row2 has flex-direction:column outside @media (max-width:620px) "
            "— desktop layout must be unchanged"
        )


def test_desktop_logs_toolbar_row2_keeps_flex():
    """AC6: .logs-toolbar-row2 must keep display:flex outside media query."""
    remaining = _strip_620px_blocks(PROJECT_HTML)
    body = _get_rule_body(remaining, ".logs-toolbar-row2")
    assert body is not None, (
        ".logs-toolbar-row2 base rule not found in project.html outside media queries"
    )
    assert re.search(r'display\s*:\s*flex', body), (
        ".logs-toolbar-row2 base rule does not have display:flex — desktop layout must use flex"
    )


def test_logs_search_width_100_percent_base():
    """AC5: .logs-search must have width:100% in the base rule so it fills .logs-search-wrap."""
    body = _get_rule_body(PROJECT_HTML, ".logs-search")
    assert body is not None, ".logs-search rule not found in project.html"
    assert re.search(r'width\s*:\s*100%', body), (
        ".logs-search does not have width:100% — input won't fill the wrapper at narrow widths"
    )
