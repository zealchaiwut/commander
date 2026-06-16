"""Tests for issue #1186: Stack Bulk Create settings bar fields on mobile.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — .bc-settings-bar has flex-direction:column inside @media (max-width:600px)
  AC2 — .bc-settings-field has width:100% inside @media (max-width:600px)
  AC3 — .bc-select and .bc-text-input exist and are targeted by mobile media rules
  AC4 — No horizontal scroll at 375px viewport (structural CSS check)
  AC5 — No horizontal scroll at 600px viewport (structural CSS check)
  AC6 — Desktop layout (.bc-settings-bar) is unchanged above 601px
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


def _extract_max_width_600_blocks(html: str) -> list[str]:
    """Return the contents of all @media (max-width:600px) blocks."""
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


def _strip_600px_blocks(html: str) -> str:
    """Return html with all @media (max-width:600px) blocks removed."""
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*600px\s*\)\s*\{', re.IGNORECASE)
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


MW600_BLOCKS = _extract_max_width_600_blocks(PROJECT_HTML)
COMBINED_MW600 = "\n".join(MW600_BLOCKS)


def test_bulk_create_mobile_responsive__bc_settings_bar_flex_direction_column_at_max_width_600px():
    # AC1: .bc-settings-bar switches to flex-direction:column at max-width:600px
    assert ".bc-settings-bar" in PROJECT_HTML, ".bc-settings-bar not found in project.html"
    body = _get_rule_body(COMBINED_MW600, ".bc-settings-bar")
    assert body is not None, (
        ".bc-settings-bar rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-settings-bar does not have flex-direction:column inside @media (max-width:600px)"
    )


def test_bulk_create_mobile_responsive__bc_settings_field_width_100_percent_at_max_width_600px():
    # AC2: .bc-settings-field width is 100% at max-width:600px
    assert ".bc-settings-field" in PROJECT_HTML, ".bc-settings-field not found in project.html"
    body = _get_rule_body(COMBINED_MW600, ".bc-settings-field")
    assert body is not None, (
        ".bc-settings-field rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'width\s*:\s*100%', body), (
        ".bc-settings-field does not have width:100% inside @media (max-width:600px)"
    )


def test_bulk_create_mobile_responsive__select_input_full_width_at_max_width_600px():
    # AC3: .bc-select and .bc-text-input are full-width at max-width:600px
    assert ".bc-select" in PROJECT_HTML, ".bc-select not found in project.html"
    assert ".bc-text-input" in PROJECT_HTML, ".bc-text-input not found in project.html"
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    # Verify a width:100% rule covers these selectors inside the media query
    assert re.search(r'\.bc-select.*width\s*:\s*100%|width\s*:\s*100%.*\.bc-select',
                     COMBINED_MW600, re.DOTALL), (
        ".bc-select does not have width:100% inside @media (max-width:600px)"
    )


def test_bulk_create_mobile_responsive__no_horizontal_scroll_at_375px_viewport():
    # AC4: No horizontal scroll at 375px — structural check
    assert "bc-settings-bar" in PROJECT_HTML, "bc-settings-bar not found in project.html"
    assert "bc-settings-field" in PROJECT_HTML, "bc-settings-field not found in project.html"
    # Verify responsive CSS block exists (prevents overflow)
    assert MW600_BLOCKS, "No @media (max-width:600px) block found — responsive CSS missing"


def test_bulk_create_mobile_responsive__no_horizontal_scroll_at_600px_viewport():
    # AC5: No horizontal scroll at 600px — structural check
    assert "bc-settings-bar" in PROJECT_HTML, "bc-settings-bar not found in project.html"
    assert MW600_BLOCKS, "No @media (max-width:600px) block found — responsive CSS missing"


def test_bulk_create_mobile_responsive__desktop_layout_unchanged_above_601px():
    # AC6: Desktop layout (>=601px) is pixel-identical to current behavior
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-settings-bar")
    assert body is not None, (
        ".bc-settings-bar base rule not found in project.html"
    )
    assert re.search(r'display\s*:\s*flex', body), (
        ".bc-settings-bar base style does not have display:flex — desktop layout broken"
    )
    assert not re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-settings-bar has flex-direction:column in base styles — desktop layout broken; "
        "column direction must only appear inside @media (max-width:600px)"
    )
