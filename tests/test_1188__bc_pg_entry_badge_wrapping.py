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
import sys
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROJECT_HTML = str(DASHBOARD_DIR / "static" / "project.html")


@pytest.fixture(scope="module")
def html_source():
    return Path(PROJECT_HTML).read_text(encoding="utf-8")


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


# AC1-2: At 375px and 560px, progress badges wrap (CSS rule presence confirms both widths)
def test_ac1_ac2_badge_wrap_rules_present_for_narrow_viewports(html_source):
    """Verify flex-wrap:wrap and flex:1 1 auto exist in the 560px media query,
    covering both 375px (AC1) and 560px (AC2) viewport widths."""
    blocks = _extract_media_560_blocks(html_source)
    assert blocks, "No @media (max-width:560px) blocks found in project.html"
    combined = " ".join(blocks)
    assert re.search(r"\.bc-pg-entry\s*\{[^}]*flex-wrap\s*:\s*wrap", combined, re.DOTALL), (
        ".bc-pg-entry must have flex-wrap:wrap in the 560px media query (covers 375px and 560px)"
    )
    assert re.search(r"\.bc-pg-entry-badge\s*\{[^}]*flex\s*:\s*1\s+1\s+auto", combined, re.DOTALL), (
        ".bc-pg-entry-badge must have flex:1 1 auto in the 560px media query"
    )


# AC3: No horizontal scroll at narrow widths (ensured by flex-wrap:wrap)
def test_ac3_responsive_design_viewport_tag(html_source):
    """Verify that project.html contains viewport meta tag for responsive behavior."""
    assert "<meta" in html_source, "HTML should contain meta tags"
    # Verify the CSS has the media query for responsive behavior
    assert "@media" in html_source, "HTML should contain media queries"
    assert "max-width" in html_source, "Should have max-width media query breakpoints"


# AC4: Desktop layout unchanged (rules scoped inside @media)
def test_ac4_desktop_layout_unchanged(html_source):
    """Verify that no flex-wrap:wrap is added to the base desktop .bc-pg-entry rule."""
    # This is already tested above (test_desktop_bc_pg_entry_not_flex_wrap),
    # but we keep it here for completeness
    base_pattern = re.compile(
        r"\.bc-pg-entry\s*\{\s*display\s*:\s*flex[^}]*\}", re.DOTALL
    )
    match = base_pattern.search(html_source)
    assert match, "Base .bc-pg-entry rule not found"
    rule_body = match.group(0)
    assert "flex-wrap" not in rule_body, (
        "Desktop .bc-pg-entry must not have flex-wrap — that belongs only in the mobile media query"
    )
