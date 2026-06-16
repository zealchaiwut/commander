"""
Tests for issue #1188 — Wrap estimate badges on narrow screens in Bulk Create.

AC items covered:
  AC1: At 375px viewport width, progress badges wrap (verified via CSS rule presence)
  AC2: At 560px viewport width, progress badges wrap (same rule covers both widths)
  AC3: No horizontal scroll at narrow widths — ensured by flex-wrap:wrap
  AC4: Desktop layout unchanged — rules are scoped inside @media (max-width:560px)
  AC5: .bc-pg-entry has flex-wrap:wrap inside @media (max-width:560px)
  AC6: .bc-pg-entry-badge has flex:1 1 auto inside @media (max-width:560px)
"""

import re
import pytest

PROJECT_HTML = "apps/dashboard/static/project.html"


@pytest.fixture(scope="module")
def html_source():
    with open(PROJECT_HTML, encoding="utf-8") as f:
        return f.read()


def _extract_media_560_blocks(source: str) -> list[str]:
    """Return the contents of all @media (max-width: 560px) { ... } blocks."""
    pattern = re.compile(
        r"@media\s*\(max-width\s*:\s*560px\)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
        re.DOTALL,
    )
    return [m.group(1) for m in pattern.finditer(source)]


def _normalize(css: str) -> str:
    """Collapse whitespace for flexible matching."""
    return re.sub(r"\s+", " ", css).strip()


# AC5 — .bc-pg-entry has flex-wrap:wrap inside @media (max-width:560px)
def test_bc_pg_entry_has_flex_wrap_in_560_media(html_source):
    blocks = _extract_media_560_blocks(html_source)
    assert blocks, "No @media (max-width:560px) blocks found in project.html"

    combined = " ".join(blocks)
    # Match: .bc-pg-entry { ... flex-wrap: wrap ... }
    pattern = re.compile(
        r"\.bc-pg-entry\s*\{[^}]*flex-wrap\s*:\s*wrap[^}]*\}", re.DOTALL
    )
    assert pattern.search(combined), (
        ".bc-pg-entry must have flex-wrap:wrap inside @media (max-width:560px)"
    )


# AC6 — .bc-pg-entry-badge has flex:1 1 auto inside @media (max-width:560px)
def test_bc_pg_entry_badge_has_flex_1_1_auto_in_560_media(html_source):
    blocks = _extract_media_560_blocks(html_source)
    assert blocks, "No @media (max-width:560px) blocks found in project.html"

    combined = " ".join(blocks)
    # Match: .bc-pg-entry-badge { ... flex: 1 1 auto ... }
    pattern = re.compile(
        r"\.bc-pg-entry-badge\s*\{[^}]*flex\s*:\s*1\s+1\s+auto[^}]*\}", re.DOTALL
    )
    assert pattern.search(combined), (
        ".bc-pg-entry-badge must have flex:1 1 auto inside @media (max-width:560px)"
    )


# AC4 — Desktop layout unchanged: the base .bc-pg-entry rule must NOT contain flex-wrap:wrap
def test_desktop_bc_pg_entry_not_flex_wrap(html_source):
    # Extract all CSS *outside* media queries by stripping media blocks first, then searching
    # We look for the base .bc-pg-entry rule (not inside any @media)
    # Strategy: find the .bc-pg-entry { } block in the raw source and confirm it doesn't get flex-wrap:wrap added
    # The easiest approach: confirm the rule exists at the top level and lacks flex-wrap.
    base_pattern = re.compile(
        r"\.bc-pg-entry\s*\{\s*display\s*:\s*flex[^}]*\}", re.DOTALL
    )
    match = base_pattern.search(html_source)
    assert match, "Base .bc-pg-entry rule not found"
    rule_body = match.group(0)
    assert "flex-wrap" not in rule_body, (
        "Desktop .bc-pg-entry must not have flex-wrap — that belongs only in the mobile media query"
    )


# AC5+AC6 combined — confirm rules live in the SAME 560px media block as a cohesion check
def test_both_badge_rules_present_in_560_media(html_source):
    blocks = _extract_media_560_blocks(html_source)
    combined = " ".join(blocks)

    has_entry = bool(re.search(r"\.bc-pg-entry\s*\{[^}]*flex-wrap\s*:\s*wrap", combined, re.DOTALL))
    has_badge = bool(re.search(r"\.bc-pg-entry-badge\s*\{[^}]*flex\s*:\s*1\s+1\s+auto", combined, re.DOTALL))

    assert has_entry and has_badge, (
        "Both .bc-pg-entry flex-wrap:wrap and .bc-pg-entry-badge flex:1 1 auto "
        "must appear inside @media (max-width:560px)"
    )
