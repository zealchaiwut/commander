"""Tests for issue #1185 — Mobile: horizontal-scroll raw log stream on small screens.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — At 375px, long log lines scroll horizontally within .logs-raw-stream
  AC2 — At 640px, same horizontal-scroll behavior applies inside the panel
  AC3 — Page body does not scroll horizontally at 375px or 640px
  AC4 — Desktop (>640px) layout unchanged — no regression
  AC5 — .logs-raw-stream has overflow-x:auto inside @media (max-width:640px)
  AC6 — .logs-raw-line has white-space:pre inside @media (max-width:640px)
  AC7 — Horizontal scrollbar inside the panel is visible (overflow-x:auto present)
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


def test_max_width_640_block_exists():
    """AC1/AC2: A @media (max-width:640px) block must exist in project.html."""
    assert MW640_BLOCKS, "No @media (max-width:640px) block found in project.html"


def test_logs_raw_stream_overflow_x_auto_in_640_block():
    """AC5: .logs-raw-stream must have overflow-x:auto inside @media (max-width:640px)."""
    body = _get_rule_body(COMBINED_MW640, ".logs-raw-stream")
    assert body is not None, (
        ".logs-raw-stream rule not found inside any @media (max-width:640px) block"
    )
    assert re.search(r'overflow-x\s*:\s*auto', body), (
        ".logs-raw-stream does not have overflow-x:auto inside @media (max-width:640px) "
        "— long log lines will not scroll horizontally within the panel"
    )


def test_logs_raw_line_white_space_pre_in_640_block():
    """AC6: .logs-raw-line must have white-space:pre inside @media (max-width:640px)."""
    body = _get_rule_body(COMBINED_MW640, ".logs-raw-line")
    assert body is not None, (
        ".logs-raw-line rule not found inside any @media (max-width:640px) block"
    )
    assert re.search(r'white-space\s*:\s*pre(?!\s*-wrap|\s*-line|\s*-nowrap)', body), (
        ".logs-raw-line does not have white-space:pre inside @media (max-width:640px) "
        "— lines will wrap instead of enabling horizontal scroll"
    )


def test_desktop_logs_raw_stream_no_overflow_x_auto_outside_media():
    """AC4: overflow-x:auto on .logs-raw-stream must be inside a media query, not base styles."""
    remaining = _strip_640px_blocks(PROJECT_HTML)
    # Strip other mobile media blocks too (768px)
    remaining = re.sub(
        r'@media\s*\(\s*max-width\s*:\s*768px\s*\)\s*\{[^}]*\}',
        '',
        remaining,
    )
    body = _get_rule_body(remaining, ".logs-raw-stream")
    if body is not None:
        # overflow-x:auto should NOT be in the base rule (it's desktop-safe without it)
        assert not re.search(r'overflow-x\s*:\s*auto', body), (
            ".logs-raw-stream has overflow-x:auto in base (desktop) styles — "
            "should only appear inside @media (max-width:640px)"
        )


def test_desktop_logs_raw_line_no_white_space_pre_outside_media():
    """AC4: white-space:pre on .logs-raw-line must be inside the 640px media query only.

    The base rule uses white-space:pre-wrap (allowing line breaks on desktop).
    The mobile override changes it to white-space:pre for horizontal scrolling.
    """
    remaining = _strip_640px_blocks(PROJECT_HTML)
    body = _get_rule_body(remaining, ".logs-raw-line")
    if body is not None:
        # Base rule should not have bare white-space:pre (it uses pre-wrap)
        # This regex matches 'pre' that is NOT followed by '-wrap', '-line', '-nowrap'
        assert not re.search(r'white-space\s*:\s*pre(?!\s*-wrap|\s*-line|\s*-nowrap)', body), (
            ".logs-raw-line has white-space:pre (not pre-wrap) in base styles — "
            "desktop line wrapping would be broken"
        )


def test_logs_raw_stream_panel_horizontal_scroll_enabled():
    """AC1/AC2/AC7: overflow-x:auto on .logs-raw-stream enables a scrollbar inside the panel."""
    body = _get_rule_body(COMBINED_MW640, ".logs-raw-stream")
    assert body is not None, (
        ".logs-raw-stream rule not found inside any @media (max-width:640px) block"
    )
    # overflow-x:auto shows a scrollbar when content overflows
    assert re.search(r'overflow-x\s*:\s*auto', body), (
        ".logs-raw-stream does not have overflow-x:auto — horizontal scrollbar "
        "inside the panel will not appear on mobile"
    )
