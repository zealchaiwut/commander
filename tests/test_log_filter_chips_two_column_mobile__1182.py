"""Tests for issue #1182 — Log filter chips: two-column layout on mobile.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — At 375px, .logs-filter-bar renders in 2 columns (grid-template-columns:1fr 1fr)
  AC2 — At 640px, .logs-filter-bar renders in 2 columns (grid-template-columns:1fr 1fr)
  AC3 — No horizontal page scroll: grid constrains chips to viewport width
  AC4 — Desktop (>640px) layout unchanged (.logs-filter-bar keeps flex outside the media query)
  AC5 — @media (max-width:640px) applies display:grid; grid-template-columns:1fr 1fr; gap:8px
  AC6 — Chip text does not overflow: .logs-chip has white-space:nowrap (already set) and grid
         column widths are equal fractions, preventing overflow
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


def _extract_max_width_640_blocks(html: str) -> list[str]:
    """Return the contents of all @media (max-width:640px) blocks."""
    blocks = []
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*640px\s*\)\s*\{', re.IGNORECASE)
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


def _strip_640px_blocks(html: str) -> str:
    """Return html with all @media (max-width:640px) blocks removed."""
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*640px\s*\)\s*\{', re.IGNORECASE)
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


MW640_BLOCKS = _extract_max_width_640_blocks(PROJECT_HTML)
COMBINED_MW640 = "\n".join(MW640_BLOCKS)


def test_max_width_640_block_with_logs_filter_bar_exists():
    """AC5: A @media (max-width:640px) block containing .logs-filter-bar must exist."""
    assert MW640_BLOCKS, "No @media (max-width:640px) block found in project.html"
    body = _get_rule_body(COMBINED_MW640, ".logs-filter-bar")
    assert body is not None, (
        ".logs-filter-bar rule not found inside any @media (max-width:640px) block"
    )


def test_logs_filter_bar_display_grid_in_640_block():
    """AC5: .logs-filter-bar has display:grid inside @media (max-width:640px)."""
    body = _get_rule_body(COMBINED_MW640, ".logs-filter-bar")
    assert body is not None, (
        ".logs-filter-bar rule not found inside @media (max-width:640px)"
    )
    assert re.search(r'display\s*:\s*grid', body), (
        ".logs-filter-bar does not have display:grid inside @media (max-width:640px)"
    )


def test_logs_filter_bar_two_columns_in_640_block():
    """AC1/AC2/AC5: .logs-filter-bar has grid-template-columns:1fr 1fr inside @media (max-width:640px)."""
    body = _get_rule_body(COMBINED_MW640, ".logs-filter-bar")
    assert body is not None, (
        ".logs-filter-bar rule not found inside @media (max-width:640px)"
    )
    assert re.search(r'grid-template-columns\s*:\s*1fr\s+1fr', body), (
        ".logs-filter-bar does not have grid-template-columns:1fr 1fr inside "
        "@media (max-width:640px) — needed for two-column layout at 375px and 640px"
    )


def test_logs_filter_bar_gap_8px_in_640_block():
    """AC5: .logs-filter-bar has gap:8px inside @media (max-width:640px)."""
    body = _get_rule_body(COMBINED_MW640, ".logs-filter-bar")
    assert body is not None, (
        ".logs-filter-bar rule not found inside @media (max-width:640px)"
    )
    assert re.search(r'gap\s*:\s*8px', body), (
        ".logs-filter-bar does not have gap:8px inside @media (max-width:640px)"
    )


def test_desktop_logs_filter_bar_keeps_flex():
    """AC4: .logs-filter-bar keeps display:flex outside @media (max-width:640px)."""
    remaining = _strip_640px_blocks(PROJECT_HTML)
    body = _get_rule_body(remaining, ".logs-filter-bar")
    assert body is not None, (
        ".logs-filter-bar base rule not found in project.html outside media queries"
    )
    assert re.search(r'display\s*:\s*flex', body), (
        ".logs-filter-bar base rule does not have display:flex — desktop layout must be unchanged"
    )


def test_desktop_logs_filter_bar_no_grid_outside_media():
    """AC4: .logs-filter-bar must NOT have display:grid outside @media (max-width:640px)."""
    remaining = _strip_640px_blocks(PROJECT_HTML)
    body = _get_rule_body(remaining, ".logs-filter-bar")
    if body is not None:
        assert not re.search(r'display\s*:\s*grid', body), (
            ".logs-filter-bar has display:grid outside @media (max-width:640px) "
            "— desktop layout must be unchanged"
        )


def test_logs_chip_white_space_nowrap_present():
    """AC6: .logs-chip has white-space:nowrap to prevent chip text overflow in grid cells."""
    base_rule = _get_rule_body(PROJECT_HTML, ".logs-chip")
    assert base_rule is not None, ".logs-chip rule not found in project.html"
    assert re.search(r'white-space\s*:\s*nowrap', base_rule), (
        ".logs-chip does not have white-space:nowrap — chip text may overflow grid cells"
    )
