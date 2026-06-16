"""Tests for issue #1184: Truncate run IDs and fail titles on mobile.

Reads project.html from disk — no server needed.

AC coverage:
  AC1 — At 375px, .logs-run-id truncates with ellipsis when content exceeds 80px
  AC2 — At 375px, .logs-ticket-fail truncates with ellipsis when content exceeds 80px
  AC3 — At 600px, both truncate at the 80px threshold
  AC4 — No horizontal overflow at 375px or 600px (overflow:hidden on both)
  AC5 — At ≥601px, no max-width:80px truncation (desktop unchanged)
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


def _rule_body_for(selector: str, css: str) -> str | None:
    """Return the rule body for a selector inside a CSS string, or None."""
    escaped = re.escape(selector)
    m = re.search(escaped + r'\s*\{([^}]*)\}', css)
    return m.group(1) if m else None


MW600_BLOCKS = _extract_max_width_600_blocks(PROJECT_HTML)
COMBINED_MW600 = "\n".join(MW600_BLOCKS)


def test_1184__run_id_has_max_width_80px_inside_600px_media_query():
    # AC1 + AC3: .logs-run-id must cap at 80px inside @media (max-width:600px)
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    body = _rule_body_for(".logs-run-id", COMBINED_MW600)
    assert body is not None, ".logs-run-id rule not found inside @media (max-width:600px)"
    assert re.search(r'max-width\s*:\s*80px', body), \
        ".logs-run-id missing max-width:80px inside @media (max-width:600px)"


def test_1184__run_id_has_ellipsis_inside_600px_media_query():
    # AC1 + AC3: .logs-run-id must truncate with ellipsis on mobile
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    body = _rule_body_for(".logs-run-id", COMBINED_MW600)
    assert body is not None, ".logs-run-id rule not found inside @media (max-width:600px)"
    has_ellipsis = re.search(r'text-overflow\s*:\s*ellipsis', body)
    has_ellipsis_global = re.search(
        r'\.logs-run-id\s*\{[^}]*text-overflow\s*:\s*ellipsis',
        PROJECT_HTML
    )
    assert has_ellipsis or has_ellipsis_global, \
        ".logs-run-id missing text-overflow:ellipsis (in media query or base CSS)"


def test_1184__run_id_prevents_overflow_inside_600px_media_query():
    # AC4: overflow:hidden on .logs-run-id to prevent horizontal scroll
    body = _rule_body_for(".logs-run-id", COMBINED_MW600)
    has_hidden_in_mq = body and re.search(r'overflow\s*:\s*hidden', body)
    has_hidden_globally = re.search(
        r'\.logs-run-id\s*\{[^}]*overflow\s*:\s*hidden',
        PROJECT_HTML
    )
    assert has_hidden_in_mq or has_hidden_globally, \
        ".logs-run-id missing overflow:hidden (in media query or base CSS)"


def test_1184__ticket_fail_has_max_width_80px_inside_600px_media_query():
    # AC2 + AC3: .logs-ticket-fail must cap at 80px inside @media (max-width:600px)
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    body = _rule_body_for(".logs-ticket-fail", COMBINED_MW600)
    assert body is not None, ".logs-ticket-fail rule not found inside @media (max-width:600px)"
    assert re.search(r'max-width\s*:\s*80px', body), \
        ".logs-ticket-fail missing max-width:80px inside @media (max-width:600px)"


def test_1184__ticket_fail_has_ellipsis_inside_600px_media_query():
    # AC2 + AC3: .logs-ticket-fail must truncate with ellipsis on mobile
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    body = _rule_body_for(".logs-ticket-fail", COMBINED_MW600)
    assert body is not None, ".logs-ticket-fail rule not found inside @media (max-width:600px)"
    assert re.search(r'text-overflow\s*:\s*ellipsis', body), \
        ".logs-ticket-fail missing text-overflow:ellipsis inside @media (max-width:600px)"


def test_1184__ticket_fail_prevents_overflow_inside_600px_media_query():
    # AC4: overflow:hidden on .logs-ticket-fail to prevent horizontal scroll
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    body = _rule_body_for(".logs-ticket-fail", COMBINED_MW600)
    assert body is not None, ".logs-ticket-fail rule not found inside @media (max-width:600px)"
    assert re.search(r'overflow\s*:\s*hidden', body), \
        ".logs-ticket-fail missing overflow:hidden inside @media (max-width:600px)"


def test_1184__desktop_no_max_width_on_run_id():
    # AC5: Outside @media (max-width:600px), .logs-run-id must not have max-width:80px
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

    body = _rule_body_for(".logs-run-id", remaining)
    if body:
        assert not re.search(r'max-width\s*:\s*80px', body), \
            ".logs-run-id has max-width:80px outside @media (max-width:600px) — breaks desktop"


def test_1184__desktop_no_max_width_on_ticket_fail():
    # AC5: Outside @media (max-width:600px), .logs-ticket-fail must not have max-width:80px
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

    body = _rule_body_for(".logs-ticket-fail", remaining)
    if body:
        assert not re.search(r'max-width\s*:\s*80px', body), \
            ".logs-ticket-fail has max-width:80px outside @media (max-width:600px) — breaks desktop"


def test_1184__selectors_exist_in_html():
    # Sanity: both selectors must exist in the HTML
    assert ".logs-run-id" in PROJECT_HTML, ".logs-run-id not found in project.html"
    assert ".logs-ticket-fail" in PROJECT_HTML, ".logs-ticket-fail not found in project.html"
