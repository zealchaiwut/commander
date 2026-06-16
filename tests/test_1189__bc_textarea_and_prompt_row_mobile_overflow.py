"""Tests for issue #1189 — Fix Bulk Create textarea and prompt-row mobile overflow.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — .bc-textarea has max-width:100% (never wider than its container)
  AC2 — At ≤600px, .bc-prompt-row has flex-direction:column inside @media (max-width:600px)
  AC3 — No horizontal page scroll at 375px (ensured by max-width:100% and column layout)
  AC4 — No horizontal page scroll at 600px (same mobile media query covers this)
  AC5 — Desktop layout (≥601px) unchanged — .bc-prompt-row base must NOT have flex-direction:column
  AC6 — Textarea is fully usable on mobile (overflow-x:hidden or scroll allowed; not clipped)
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# AC1 — .bc-textarea has max-width:100%
# ---------------------------------------------------------------------------

def test_bc_textarea_has_max_width_100_percent():
    """AC1: .bc-textarea must have max-width:100% so it never overflows its container."""
    body = _get_rule_body(PROJECT_HTML, ".bc-textarea")
    assert body is not None, (
        ".bc-textarea rule not found in project.html"
    )
    assert re.search(r'max-width\s*:\s*100%', body), (
        ".bc-textarea does not have max-width:100% — textarea can exceed viewport width on mobile"
    )


# ---------------------------------------------------------------------------
# AC2 — .bc-prompt-row switches to flex-direction:column at ≤600px
# ---------------------------------------------------------------------------

def test_max_width_600_block_exists():
    """Prerequisite: at least one @media (max-width:600px) block must exist."""
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"


def test_bc_prompt_row_flex_direction_column_in_600_block():
    """AC2: .bc-prompt-row must have flex-direction:column inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".bc-prompt-row")
    assert body is not None, (
        ".bc-prompt-row rule not found inside any @media (max-width:600px) block"
    )
    assert re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-prompt-row does not have flex-direction:column inside @media (max-width:600px) "
        "— prompt row items will not stack vertically on mobile"
    )


# ---------------------------------------------------------------------------
# AC3 & AC4 — No horizontal page scroll at 375px or 600px
# (ensured by the combination of max-width:100% on textarea and column layout on rows)
# ---------------------------------------------------------------------------

def test_bc_prompt_rows_no_overflow_x_scroll_in_base():
    """AC3/AC4: .bc-prompt-rows must not force overflow-x:scroll in base styles.

    The mobile column layout prevents overflow; no clipping needed.
    """
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-prompt-rows")
    if body is not None:
        assert not re.search(r'overflow-x\s*:\s*scroll', body), (
            ".bc-prompt-rows has overflow-x:scroll in base styles — "
            "this forces a horizontal scrollbar instead of preventing overflow"
        )


def test_bc_textarea_box_sizing_border_box():
    """AC3/AC4: .bc-textarea must use box-sizing:border-box so padding doesn't add width."""
    body = _get_rule_body(PROJECT_HTML, ".bc-textarea")
    assert body is not None, ".bc-textarea rule not found"
    assert re.search(r'box-sizing\s*:\s*border-box', body), (
        ".bc-textarea does not have box-sizing:border-box — "
        "padding will push the textarea wider than 100% on mobile"
    )


# ---------------------------------------------------------------------------
# AC5 — Desktop layout (≥601px) unchanged
# ---------------------------------------------------------------------------

def test_bc_prompt_row_desktop_not_flex_direction_column():
    """AC5: .bc-prompt-row must NOT have flex-direction:column in base (desktop) styles."""
    base_html = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(base_html, ".bc-prompt-row")
    assert body is not None, (
        ".bc-prompt-row base rule not found in project.html"
    )
    assert not re.search(r'flex-direction\s*:\s*column', body), (
        ".bc-prompt-row has flex-direction:column in base (desktop) styles — "
        "desktop layout would be broken; column direction must only appear inside "
        "@media (max-width:600px)"
    )


# ---------------------------------------------------------------------------
# AC6 — Textarea remains usable (scrollable, not clipped) on mobile
# ---------------------------------------------------------------------------

def test_bc_textarea_width_100_percent_base():
    """AC6: .bc-textarea must have width:100% in base styles to fill its container."""
    body = _get_rule_body(PROJECT_HTML, ".bc-textarea")
    assert body is not None, ".bc-textarea rule not found"
    assert re.search(r'(?:^|;|\s)width\s*:\s*100%', body), (
        ".bc-textarea does not have width:100% — textarea may not fill container on mobile"
    )


def test_bc_textarea_no_overflow_x_hidden():
    """AC6: .bc-textarea must not have overflow-x:hidden — that would clip typed content."""
    body = _get_rule_body(PROJECT_HTML, ".bc-textarea")
    if body is not None:
        assert not re.search(r'overflow-x\s*:\s*hidden', body), (
            ".bc-textarea has overflow-x:hidden — typed content would be clipped on mobile; "
            "use overflow-x:auto or let the browser default (scroll if needed)"
        )
