"""Tests for issue #1184: Truncate run IDs and fail titles on mobile.

Static analysis of project.html CSS — no server needed.

AC coverage:
  AC1 — At 375px, .logs-run-id truncates with ellipsis when content exceeds 80px
  AC2 — At 375px, .logs-ticket-fail truncates with ellipsis when content exceeds 80px
  AC3 — At 600px, both elements truncate at the 80px threshold
  AC4 — No horizontal page overflow at 375px or 600px (overflow:hidden on both)
  AC5 — At ≥601px, both elements display full text without truncation (desktop unchanged)
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
    escaped = re.escape(selector)
    m = re.search(escaped + r'\s*\{([^}]*)\}', css)
    return m.group(1) if m else None


def _strip_600px_blocks(html: str) -> str:
    pattern = re.compile(r'@media\s*\(\s*max-width\s*:\s*600px\s*\)\s*\{', re.IGNORECASE)
    result = html
    for m in reversed(list(pattern.finditer(html))):
        start = m.start()
        end_pos = m.end()
        depth = 1
        i = end_pos
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


def test_truncate_run_ids_and_fail_titles__logs_run_id_375px():
    # AC1: At 375px viewport, .logs-run-id text truncates with ellipsis when content exceeds 80px
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    body = _rule_body_for(".logs-run-id", COMBINED_MW600)
    assert body is not None, ".logs-run-id rule not found inside @media (max-width:600px)"
    assert re.search(r'max-width\s*:\s*80px', body), \
        ".logs-run-id missing max-width:80px inside @media (max-width:600px)"
    assert re.search(r'text-overflow\s*:\s*ellipsis', body) or re.search(
        r'\.logs-run-id\s*\{[^}]*text-overflow\s*:\s*ellipsis', PROJECT_HTML
    ), ".logs-run-id missing text-overflow:ellipsis"


def test_truncate_run_ids_and_fail_titles__logs_ticket_fail_375px():
    # AC2: At 375px viewport, .logs-ticket-fail text truncates with ellipsis when content exceeds 80px
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    body = _rule_body_for(".logs-ticket-fail", COMBINED_MW600)
    assert body is not None, ".logs-ticket-fail rule not found inside @media (max-width:600px)"
    assert re.search(r'max-width\s*:\s*80px', body), \
        ".logs-ticket-fail missing max-width:80px inside @media (max-width:600px)"
    assert re.search(r'text-overflow\s*:\s*ellipsis', body), \
        ".logs-ticket-fail missing text-overflow:ellipsis inside @media (max-width:600px)"


def test_truncate_run_ids_and_fail_titles__both_at_600px():
    # AC3: At 600px viewport, both elements truncate at the 80px threshold
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    run_id_body = _rule_body_for(".logs-run-id", COMBINED_MW600)
    fail_body = _rule_body_for(".logs-ticket-fail", COMBINED_MW600)
    assert run_id_body is not None, ".logs-run-id rule not found inside @media (max-width:600px)"
    assert fail_body is not None, ".logs-ticket-fail rule not found inside @media (max-width:600px)"
    assert re.search(r'max-width\s*:\s*80px', run_id_body), \
        ".logs-run-id missing max-width:80px inside @media (max-width:600px)"
    assert re.search(r'max-width\s*:\s*80px', fail_body), \
        ".logs-ticket-fail missing max-width:80px inside @media (max-width:600px)"


def test_truncate_run_ids_and_fail_titles__no_overflow_at_mobile():
    # AC4: No horizontal page overflow at 375px or 600px (overflow:hidden on both)
    assert MW600_BLOCKS, "No @media (max-width:600px) block found in project.html"
    run_id_body = _rule_body_for(".logs-run-id", COMBINED_MW600)
    fail_body = _rule_body_for(".logs-ticket-fail", COMBINED_MW600)
    assert run_id_body is not None, ".logs-run-id rule not found inside @media (max-width:600px)"
    assert fail_body is not None, ".logs-ticket-fail rule not found inside @media (max-width:600px)"
    assert re.search(r'overflow\s*:\s*hidden', run_id_body) or re.search(
        r'\.logs-run-id\s*\{[^}]*overflow\s*:\s*hidden', PROJECT_HTML
    ), ".logs-run-id missing overflow:hidden"
    assert re.search(r'overflow\s*:\s*hidden', fail_body), \
        ".logs-ticket-fail missing overflow:hidden inside @media (max-width:600px)"


def test_truncate_run_ids_and_fail_titles__desktop_no_truncation():
    # AC5: At ≥601px, both elements display full text without truncation (desktop unchanged)
    base_html = _strip_600px_blocks(PROJECT_HTML)
    run_id_body = _rule_body_for(".logs-run-id", base_html)
    assert run_id_body is not None, ".logs-run-id base rule not found in project.html"
    assert not re.search(r'max-width\s*:\s*80px', run_id_body), \
        ".logs-run-id has max-width:80px in base styles — breaks desktop"
    fail_body = _rule_body_for(".logs-ticket-fail", base_html)
    if fail_body:
        assert not re.search(r'max-width\s*:\s*80px', fail_body), \
            ".logs-ticket-fail has max-width:80px in base styles — breaks desktop"
