"""Tests for issue #1180 — Add 44px tap targets to backlog header controls.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — .bl-target has min-height: 44px inside the (hover: none) media query
  AC2 — .bl-add-header-btn has min-height: 44px inside the (hover: none) media query
  AC3 — .bl-select has min-height: 44px inside the (hover: none) media query
  AC4 — .smgmt-bulk-est-btn has min-height: 44px inside the (hover: none) media query
  AC5 — No existing (hover: none) rules are removed or overridden unintentionally
  AC6 — Desktop rules for the four selectors do not include min-height: 44px
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


def _extract_hover_none_blocks(html: str) -> list[str]:
    """Return the contents of all @media (hover: none) { ... } blocks."""
    blocks = []
    pattern = re.compile(r'@media\s*\(\s*hover\s*:\s*none\s*\)\s*\{', re.IGNORECASE)
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
        blocks.append(html[start:i - 1])
    return blocks


def _selector_has_min_height_44_in_block(block: str, selector: str) -> bool:
    """True if `selector` has a rule with min-height: 44px inside `block`."""
    # Match  .selector { ... min-height: 44px ... }
    # The selector may appear among comma-separated selectors.
    escaped = re.escape(selector)
    # Look for a rule block where the selector appears and min-height: 44px is set
    rule_pattern = re.compile(
        r'(?:^|[{};,\s])' + escaped + r'(?:\s*[{,])',
        re.MULTILINE,
    )
    for rule_m in rule_pattern.finditer(block):
        # Find the opening brace after this selector match
        brace_start = block.find('{', rule_m.start())
        if brace_start == -1:
            continue
        # Walk to the matching close brace
        depth = 1
        j = brace_start + 1
        while j < len(block) and depth > 0:
            if block[j] == '{':
                depth += 1
            elif block[j] == '}':
                depth -= 1
            j += 1
        rule_body = block[brace_start + 1:j - 1]
        if re.search(r'min-height\s*:\s*44px', rule_body):
            return True
    return False


HOVER_NONE_BLOCKS = _extract_hover_none_blocks(PROJECT_HTML)
COMBINED_HOVER_NONE = "\n".join(HOVER_NONE_BLOCKS)

TOUCH_SELECTORS = [
    ".bl-target",
    ".bl-add-header-btn",
    ".bl-select",
    ".smgmt-bulk-est-btn",
]

# Pre-existing rules that must still exist in the (hover: none) block
PREEXISTING_RULES = [
    (".smgmt-row-menu-btn", "opacity"),
    (".smgmt-rename-btn", "opacity"),
    (".smgmt-summary-row-action", "opacity"),
    (".smgmt-ticket-cb", "opacity"),
    (".smgmt-ticket", "padding"),
]


def test_hover_none_block_exists():
    """At least one @media (hover: none) block must exist in project.html."""
    assert HOVER_NONE_BLOCKS, "No @media (hover: none) block found in project.html"


def test_bl_target_min_height_44_in_hover_none():
    """AC1: .bl-target has min-height: 44px inside (hover: none)."""
    assert _selector_has_min_height_44_in_block(COMBINED_HOVER_NONE, ".bl-target"), (
        ".bl-target does not have min-height: 44px inside @media (hover: none)"
    )


def test_bl_add_header_btn_min_height_44_in_hover_none():
    """AC2: .bl-add-header-btn has min-height: 44px inside (hover: none)."""
    assert _selector_has_min_height_44_in_block(COMBINED_HOVER_NONE, ".bl-add-header-btn"), (
        ".bl-add-header-btn does not have min-height: 44px inside @media (hover: none)"
    )


def test_bl_select_min_height_44_in_hover_none():
    """AC3: .bl-select has min-height: 44px inside (hover: none)."""
    assert _selector_has_min_height_44_in_block(COMBINED_HOVER_NONE, ".bl-select"), (
        ".bl-select does not have min-height: 44px inside @media (hover: none)"
    )


def test_smgmt_bulk_est_btn_min_height_44_in_hover_none():
    """AC4: .smgmt-bulk-est-btn has min-height: 44px inside (hover: none)."""
    assert _selector_has_min_height_44_in_block(COMBINED_HOVER_NONE, ".smgmt-bulk-est-btn"), (
        ".smgmt-bulk-est-btn does not have min-height: 44px inside @media (hover: none)"
    )


def test_preexisting_hover_none_rules_intact():
    """AC5: Pre-existing (hover: none) rules are not removed."""
    for selector, prop in PREEXISTING_RULES:
        pattern = re.compile(re.escape(selector), re.MULTILINE)
        assert pattern.search(COMBINED_HOVER_NONE), (
            f"Pre-existing rule for {selector} was removed from @media (hover: none)"
        )


def test_desktop_selectors_not_forced_to_44px():
    """AC6: The four selectors must NOT have min-height: 44px outside (hover: none)."""
    # Strip all (hover: none) blocks from the HTML and check the remainder
    remaining = PROJECT_HTML
    pattern = re.compile(r'@media\s*\(\s*hover\s*:\s*none\s*\)\s*\{', re.IGNORECASE)
    # Replace each block with an empty placeholder
    for m in reversed(list(pattern.finditer(PROJECT_HTML))):
        start = m.start()
        brace_start = m.end() - 1
        depth = 1
        i = brace_start + 1
        while i < len(PROJECT_HTML) and depth > 0:
            if PROJECT_HTML[i] == '{':
                depth += 1
            elif PROJECT_HTML[i] == '}':
                depth -= 1
            i += 1
        remaining = remaining[:start] + remaining[i:]

    for selector in TOUCH_SELECTORS:
        # Look for this selector followed by a rule body containing min-height: 44px
        escaped = re.escape(selector)
        rule_pattern = re.compile(
            r'(?:^|[{};,\s])' + escaped + r'(?:\s*[{,])',
            re.MULTILINE,
        )
        for rule_m in rule_pattern.finditer(remaining):
            brace_start = remaining.find('{', rule_m.start())
            if brace_start == -1:
                continue
            depth = 1
            j = brace_start + 1
            while j < len(remaining) and depth > 0:
                if remaining[j] == '{':
                    depth += 1
                elif remaining[j] == '}':
                    depth -= 1
                j += 1
            rule_body = remaining[brace_start + 1:j - 1]
            assert not re.search(r'min-height\s*:\s*44px', rule_body), (
                f"{selector} has min-height: 44px outside @media (hover: none) — "
                "desktop layout must not be affected"
            )
