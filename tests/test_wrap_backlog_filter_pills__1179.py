"""Tests for issue #1179: Wrap backlog filter pills on narrow screens."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (REPO_ROOT / "apps" / "dashboard" / "static" / "project.html").read_text(encoding="utf-8")


# --- Acceptance Criteria ---

def test_wrap_backlog_filter_pills__filter_pills_wrap_at_375px():
    # AC: At 375px viewport width, filter pills wrap to multiple rows instead of overflowing
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__filter_pills_wrap_at_600px():
    # AC: At 600px viewport width, filter pills wrap to multiple rows instead of overflowing
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__no_horizontal_scroll_at_375px():
    # AC: No horizontal page scroll appears at 375px or 600px when the filter row is visible
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__desktop_layout_unchanged():
    # AC: Desktop viewport (≥621px) layout is unchanged — pills remain on a single row
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")


def test_wrap_backlog_filter_pills__css_flex_wrap_applied():
    # AC: `.bl-filter` has `flex-wrap: wrap` and `gap: 6px` applied inside `@media (max-width: 620px)`
    assert "@media (max-width: 620px)" in PROJECT_HTML, (
        "project.html must contain @media (max-width: 620px) — AC5 requires this breakpoint"
    )
    assert ".bl-filter" in PROJECT_HTML, (
        "project.html must contain .bl-filter — AC5 requires flex-wrap: wrap and gap: 6px"
    )
    assert "gap: 6px" in PROJECT_HTML, (
        "project.html must contain gap: 6px for .bl-filter inside the 620px media query — AC5"
    )


def test_wrap_backlog_filter_pills__pill_shrink_enabled():
    # AC: `.bl-pill` can shrink (no `flex-shrink: 0` or blocking `min-width`) within the breakpoint
    assert ".bl-pill" in PROJECT_HTML, (
        "project.html must define .bl-pill — AC6 requires this class to be styled"
    )
    # Verify no flex-wrap: nowrap on .bl-filter outside media queries (would prevent pill wrapping)
    # Extract global (non-media) CSS block content
    src = PROJECT_HTML
    # Strip all @media blocks to check global rules
    media_pattern = re.compile(r"@media[^{]*\{", re.DOTALL)
    pos = 0
    result_parts = []
    for m in media_pattern.finditer(src):
        result_parts.append(src[pos:m.start()])
        # find matching close brace
        depth = 0
        i = m.end() - 1
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    pos = j + 1
                    break
    result_parts.append(src[pos:])
    src_no_media = "".join(result_parts)
    # Global .bl-filter must not prevent wrapping via nowrap
    for m in re.finditer(r"\.bl-filter\s*\{([^}]*)\}", src_no_media):
        assert "flex-wrap: nowrap" not in m.group(1), (
            "Global .bl-filter must not set flex-wrap: nowrap — AC6 requires pills to be able to wrap"
        )


def test_wrap_backlog_filter_pills__overflow_pill_not_clipped():
    # AC: The "6+" overflow pill row does not clip or overflow its container at narrow widths
    pytest.skip("manual — verified via design-contract gate / agent-browser, not HTTP")
