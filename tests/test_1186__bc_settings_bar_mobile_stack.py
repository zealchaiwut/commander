"""Tests for issue #1186 — Stack Bulk Create settings bar fields on mobile.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — .bc-settings-bar has flex-direction:column inside @media (max-width:600px)
  AC2 — .bc-settings-field has width:100% inside @media (max-width:600px)
  AC3 — .bc-select and .bc-text-input are full-width inside @media (max-width:600px)
  AC4 — No horizontal overflow on 375px viewport (overflow-x is not set on .bc-settings-bar in base)
  AC5 — No horizontal overflow on 600px viewport (same mobile media query applies)
  AC6 — Desktop layout (>=601px) unchanged — .bc-settings-bar base styles keep flex-direction:row (or unset)
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


def test_max_width_600_block_exists():
    """AC1/AC5: A @media (max-width:600px) block must exist in project.html."""
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"


def test_bc_settings_bar_flex_direction_column_in_600_block():
    """AC1: .bc-settings-bar must have flex-direction:column inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".bc-settings-bar")
    assert body is not None, (
        ".bc-settings-bar rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-settings-bar does not have flex-direction:column inside @media (max-width:600px) "
        "— settings bar fields will not stack vertically on mobile"
    )


def test_bc_settings_field_width_100_in_600_block():
    """AC2: .bc-settings-field must have width:100% inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".bc-settings-field")
    assert body is not None, (
        ".bc-settings-field rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'width\s*:\s*100%', body), (
        ".bc-settings-field does not have width:100% inside @media (max-width:600px) "
        "— fields will not span full width on mobile"
    )


def test_bc_select_and_input_full_width_in_600_block():
    """AC3: .bc-select and .bc-text-input must be full-width inside @media (max-width:600px)."""
    # Check combined selector or individual selectors
    combined_body = _get_rule_body(COMBINED_MW600, ".bc-select, .bc-text-input")
    bc_select_body = _get_rule_body(COMBINED_MW600, ".bc-select")
    bc_input_body = _get_rule_body(COMBINED_MW600, ".bc-text-input")

    def _has_full_width(body: str | None) -> bool:
        if body is None:
            return False
        return bool(re.search(r'width\s*:\s*100%', body))

    # Accept: combined rule OR both individual rules having width:100%
    if combined_body is not None and _has_full_width(combined_body):
        return  # AC3 satisfied via combined selector

    select_ok = _has_full_width(bc_select_body)
    input_ok = _has_full_width(bc_input_body)

    assert select_ok and input_ok, (
        ".bc-select and/or .bc-text-input do not have width:100% inside "
        "@media (max-width:600px) — select/input controls will not fill the full width on mobile. "
        f"(.bc-select ok={select_ok}, .bc-text-input ok={input_ok})"
    )


def test_bc_settings_bar_no_overflow_x_in_base_styles():
    """AC4/AC5: .bc-settings-bar must not have overflow-x set in base (desktop) styles.

    The mobile column layout prevents overflow; no explicit overflow-x needed globally.
    """
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-settings-bar")
    if body is not None:
        assert not re.search(r'overflow-x\s*:\s*(?:auto|scroll|hidden)', body), (
            ".bc-settings-bar has overflow-x set in base styles — "
            "this would clip content on desktop; overflow prevention must come from the column layout"
        )


def test_bc_settings_bar_desktop_flex_direction_not_column():
    """AC6: .bc-settings-bar must NOT have flex-direction:column in base (desktop) styles.

    Desktop layout must be pixel-identical to current behavior (horizontal row).
    """
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-settings-bar")
    assert body is not None, (
        ".bc-settings-bar base rule not found in project.html — "
        "the selector may have been removed or renamed"
    )
    assert not re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-settings-bar has flex-direction:column in base (desktop) styles — "
        "desktop layout would be broken; column direction must only appear inside "
        "@media (max-width:600px)"
    )
