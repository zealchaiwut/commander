"""Tests for issue #1181 — Truncate sprint mini-rail badges on mobile.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — At 375px, .hist-card-mini has text-overflow:ellipsis inside @media (max-width:600px)
  AC2 — At 600px, .hist-progress has text-overflow:ellipsis inside @media (max-width:600px)
  AC3 — Both selectors have max-width and overflow:hidden inside @media (max-width:600px)
  AC4 — Desktop (>600px): no text-overflow:ellipsis on .hist-card-mini or .hist-progress
  AC5 — Truncation CSS is scoped inside @media (max-width:600px) only
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
    """Return the contents of all @media (max-width:600px) / (max-width: 600px) blocks."""
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

TRUNCATION_SELECTORS = [".hist-card-mini", ".hist-progress"]


def test_max_width_600_block_exists():
    """AC1/AC2/AC5: At least one @media (max-width:600px) block must exist in project.html."""
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"


def test_hist_card_mini_text_overflow_ellipsis_in_600_block():
    """AC1: .hist-card-mini has text-overflow:ellipsis inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-card-mini")
    assert body is not None, (
        ".hist-card-mini rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'text-overflow\s*:\s*ellipsis', body), (
        ".hist-card-mini does not have text-overflow:ellipsis inside @media (max-width:600px)"
    )


def test_hist_progress_text_overflow_ellipsis_in_600_block():
    """AC2: .hist-progress has text-overflow:ellipsis inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-progress")
    assert body is not None, (
        ".hist-progress rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'text-overflow\s*:\s*ellipsis', body), (
        ".hist-progress does not have text-overflow:ellipsis inside @media (max-width:600px)"
    )


def test_hist_card_mini_overflow_hidden_in_600_block():
    """AC3: .hist-card-mini has overflow:hidden inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-card-mini")
    assert body is not None, (
        ".hist-card-mini rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'overflow\s*:\s*hidden', body), (
        ".hist-card-mini does not have overflow:hidden inside @media (max-width:600px)"
    )


def test_hist_progress_overflow_hidden_in_600_block():
    """AC3: .hist-progress has overflow:hidden inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-progress")
    assert body is not None, (
        ".hist-progress rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'overflow\s*:\s*hidden', body), (
        ".hist-progress does not have overflow:hidden inside @media (max-width:600px)"
    )


def test_hist_card_mini_max_width_in_600_block():
    """AC3: .hist-card-mini has max-width set inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-card-mini")
    assert body is not None, (
        ".hist-card-mini rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'max-width\s*:', body), (
        ".hist-card-mini does not have max-width inside @media (max-width:600px)"
    )


def test_hist_progress_max_width_in_600_block():
    """AC3: .hist-progress has max-width set inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-progress")
    assert body is not None, (
        ".hist-progress rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'max-width\s*:', body), (
        ".hist-progress does not have max-width inside @media (max-width:600px)"
    )


def test_hist_card_mini_white_space_nowrap_in_600_block():
    """AC3: .hist-card-mini has white-space:nowrap inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-card-mini")
    assert body is not None, (
        ".hist-card-mini rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'white-space\s*:\s*nowrap', body), (
        ".hist-card-mini does not have white-space:nowrap inside @media (max-width:600px)"
    )


def test_hist_progress_white_space_nowrap_in_600_block():
    """AC3: .hist-progress has white-space:nowrap inside @media (max-width:600px)."""
    body = _get_rule_body(COMBINED_MW600, ".hist-progress")
    assert body is not None, (
        ".hist-progress rule not found inside @media (max-width:600px)"
    )
    assert re.search(r'white-space\s*:\s*nowrap', body), (
        ".hist-progress does not have white-space:nowrap inside @media (max-width:600px)"
    )


def test_desktop_hist_card_mini_no_truncation():
    """AC4: .hist-card-mini must NOT have text-overflow:ellipsis outside @media (max-width:600px)."""
    remaining = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(remaining, ".hist-card-mini")
    if body is not None:
        assert not re.search(r'text-overflow\s*:\s*ellipsis', body), (
            ".hist-card-mini has text-overflow:ellipsis outside @media (max-width:600px) "
            "— desktop badge text must be unconstrained"
        )


def test_desktop_hist_progress_no_truncation():
    """AC4: .hist-progress must NOT have text-overflow:ellipsis outside @media (max-width:600px)."""
    remaining = _strip_600px_blocks(PROJECT_HTML)
    body = _get_rule_body(remaining, ".hist-progress")
    if body is not None:
        assert not re.search(r'text-overflow\s*:\s*ellipsis', body), (
            ".hist-progress has text-overflow:ellipsis outside @media (max-width:600px) "
            "— desktop badge text must be unconstrained"
        )
