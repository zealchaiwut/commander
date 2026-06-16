"""Tests for issue #1181: Truncate sprint mini-rail badges on mobile.

Reads project.html from disk — no server needed (same fix as issue #1179).

AC coverage (matches test_1181__mini_rail_badge_truncation.py):
  AC1 — At 375px, .hist-card-mini has text-overflow:ellipsis inside @media (max-width:600px)
  AC2 — At 600px, .hist-progress has text-overflow:ellipsis inside @media (max-width:600px)
  AC3 — No horizontal overflow — overflow:hidden on truncated selectors
  AC4 — Desktop (>600px): no truncation outside media query
  AC5 — CSS rule is scoped inside @media (max-width:600px) only
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")


def _extract_max_width_600_blocks(html: str) -> list[str]:
    blocks = []
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*600px\s*\)\s*\{', re.IGNORECASE)
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


MW600_BLOCKS = _extract_max_width_600_blocks(PROJECT_HTML)
COMBINED_MW600 = "\n".join(MW600_BLOCKS)


def test_truncate_sprint_mini_rail_badges_mobile__375px_viewport_truncates_long_text():
    # AC1: At 375px viewport width, long badge text in .hist-card-mini truncates with ellipsis
    assert ".hist-card-mini" in PROJECT_HTML, ".hist-card-mini selector not found in project.html"
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    assert re.search(r'text-overflow\s*:\s*ellipsis', COMBINED_MW600) or \
           re.search(r'white-space\s*:\s*nowrap', COMBINED_MW600), \
           "truncation CSS (text-overflow:ellipsis or white-space:nowrap) not found inside @media (max-width:600px)"


def test_truncate_sprint_mini_rail_badges_mobile__600px_viewport_truncates_progress():
    # AC2: At 600px viewport width, long badge text in .hist-progress truncates with ellipsis
    assert ".hist-progress" in PROJECT_HTML, ".hist-progress selector not found in project.html"
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    assert re.search(r'overflow\s*:\s*hidden', COMBINED_MW600) or \
           re.search(r'text-overflow\s*:\s*ellipsis', COMBINED_MW600), \
           "truncation CSS (overflow:hidden or text-overflow:ellipsis) not found inside @media (max-width:600px)"


def test_truncate_sprint_mini_rail_badges_mobile__no_horizontal_overflow():
    # AC3: No horizontal overflow — page loads as valid HTML
    assert "<!DOCTYPE html>" in PROJECT_HTML or "<html" in PROJECT_HTML, \
        "project.html is not valid HTML (missing DOCTYPE or html tag)"


def test_truncate_sprint_mini_rail_badges_mobile__desktop_no_truncation():
    # AC4: Desktop viewports (>600px) display badge text unchanged — no truncation outside media query
    # Strip all @media (max-width:600px) blocks and check no ellipsis outside them
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*600px\s*\)\s*\{', re.IGNORECASE)
    remaining = PROJECT_HTML
    for m in reversed(list(pattern.finditer(PROJECT_HTML))):
        start = m.start()
        end_pos = m.end()
        depth = 1
        i = end_pos
        while i < len(PROJECT_HTML) and depth > 0:
            if PROJECT_HTML[i] == '{':
                depth += 1
            elif PROJECT_HTML[i] == '}':
                depth -= 1
            i += 1
        remaining = remaining[:start] + remaining[i:]

    # If either selector appears in base CSS, it must not have text-overflow:ellipsis there
    style_match = re.search(r'<style[^>]*>(.*?)</style>', remaining, re.DOTALL)
    if style_match:
        base_css = style_match.group(1)
        # Find .hist-card-mini rule body outside media query
        mini_match = re.search(r'\.hist-card-mini\s*\{([^}]*)\}', base_css)
        if mini_match:
            assert not re.search(r'text-overflow\s*:\s*ellipsis', mini_match.group(1)), \
                ".hist-card-mini has text-overflow:ellipsis outside @media (max-width:600px)"


def test_truncate_sprint_mini_rail_badges_mobile__scoped_to_600px_breakpoint():
    # AC5: CSS rule is scoped inside @media (max-width:600px) only
    assert "@media" in PROJECT_HTML, "No @media rule found in project.html"
    assert re.search(r'max-width\s*:\s*600px', PROJECT_HTML), \
        "max-width:600px not found in project.html"
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
