"""Tests for issue #1187 — Reflow Bulk Create draft-card header on mobile.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — .bc-card-head has flex-direction:column inside @media (max-width:600px)
  AC2 — .bc-card-actions has flex-wrap:wrap inside @media (max-width:600px)
  AC3 — Same 600px block covers both (same media query applies at both 375px and 600px)
  AC4 — .bc-card-head and .bc-card-actions have no overflow-x:hidden/scroll/auto in base styles
  AC5 — Desktop: .bc-card-head does NOT have flex-direction:column in base styles
  AC6 — CSS lives under @media (max-width:600px) targeting .bc-card-head and .bc-card-actions
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
    """AC6: A @media (max-width:600px) block must exist in project.html."""
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"


def test_bc_card_head_flex_direction_column_in_600_block():
    """AC1/AC3: .bc-card-head must have flex-direction:column inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".bc-card-head")
    assert body is not None, (
        ".bc-card-head rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-card-head does not have flex-direction:column inside @media (max-width:600px) "
        "— draft-card title and actions will not stack vertically on mobile"
    )


def test_bc_card_head_align_items_start_or_flex_start_in_600_block():
    """AC1: .bc-card-head must be left-aligned (align-items:flex-start or start) inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".bc-card-head")
    assert body is not None, (
        ".bc-card-head rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'align-items\s*:\s*(flex-start|start)', body), (
        ".bc-card-head does not have align-items:flex-start inside @media (max-width:600px) "
        "— title and actions will not be left-aligned on mobile"
    )


def test_bc_card_actions_flex_wrap_in_600_block():
    """AC2/AC3: .bc-card-actions must have flex-wrap:wrap inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".bc-card-actions")
    assert body is not None, (
        ".bc-card-actions rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'flex-wrap\s*:\s*wrap', body), (
        ".bc-card-actions does not have flex-wrap:wrap inside @media (max-width:600px) "
        "— action buttons will not wrap onto multiple lines on mobile"
    )


def test_bc_card_head_no_overflow_in_base_styles():
    """AC4: .bc-card-head must not have overflow-x:hidden/scroll/auto in base (desktop) styles."""
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-card-head")
    if body is not None:
        assert not re.search(r'overflow-x\s*:\s*(?:auto|scroll|hidden)', body), (
            ".bc-card-head has overflow-x set in base styles — "
            "this would clip content on desktop; overflow prevention must come from the column layout"
        )


def test_bc_card_actions_no_overflow_in_base_styles():
    """AC4: .bc-card-actions must not have overflow-x:hidden/scroll/auto in base (desktop) styles."""
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-card-actions")
    if body is not None:
        assert not re.search(r'overflow-x\s*:\s*(?:auto|scroll|hidden)', body), (
            ".bc-card-actions has overflow-x set in base styles — "
            "this would clip content on desktop"
        )


def test_bc_card_head_desktop_flex_direction_not_column():
    """AC5: .bc-card-head must NOT have flex-direction:column in base (desktop) styles.

    Desktop layout must be pixel-identical to current behavior (horizontal row).
    """
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-card-head")
    assert body is not None, (
        ".bc-card-head base rule not found in project.html — "
        "the selector may have been removed or renamed"
    )
    assert not re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-card-head has flex-direction:column in base (desktop) styles — "
        "desktop layout would be broken; column direction must only appear inside "
        "@media (max-width:600px)"
    )


def test_bc_card_actions_desktop_flex_shrink_not_removed():
    """AC5: .bc-card-actions must retain flex-shrink:0 in base (desktop) styles.

    The desktop layout relies on flex-shrink:0 to prevent action buttons from squeezing.
    This test verifies the base rule is not accidentally mutated.
    """
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-card-actions")
    assert body is not None, (
        ".bc-card-actions base rule not found in project.html — "
        "the selector may have been removed or renamed"
    )
    assert re.search(r'flex-shrink\s*:\s*0', body), (
        ".bc-card-actions lost flex-shrink:0 from base styles — "
        "desktop action buttons may be squeezed by long titles"
    )
