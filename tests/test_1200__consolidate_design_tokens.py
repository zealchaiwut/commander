"""Tests for issue #1200 — Consolidate design tokens: tokens.css shadowed by inline :root in project.html.

AC coverage:
  AC1 — tokens.css is the single source of truth: every -- variable defined in
         tokens.css is removed from project.html's inline <style> block.
  AC2 — The inline [data-theme="dark"] override block in project.html no longer
         redeclares any token already declared in tokens.css; dark-mode overrides
         live only in tokens.css.
  AC3 — No -- variable that appears in tokens.css appears in any inline <style>
         block in project.html (grep confirms zero overlap).
  AC4 — The <link rel="stylesheet" href="/static/css/tokens.css"> tag remains in
         project.html and loads before any remaining inline styles.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
TOKENS_CSS_PATH = STATIC_DIR / "css" / "tokens.css"
PROJECT_HTML = STATIC_DIR / "project.html"


def _tokens_vars() -> set[str]:
    """Return all CSS variable names declared in tokens.css."""
    src = TOKENS_CSS_PATH.read_text(encoding="utf-8")
    return set(re.findall(r"(--[\w-]+)\s*:", src))


def _inline_style_content(html: str) -> str:
    """Return the concatenated content of all inline <style> blocks."""
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    return "\n".join(blocks)


def _inline_vars(html: str) -> set[str]:
    """Return all CSS variable names declared in inline <style> blocks of an HTML file."""
    inline = _inline_style_content(html)
    return set(re.findall(r"(--[\w-]+)\s*:", inline))


# =============================================================================
# AC1 — tokens.css is the single source of truth; no duplicates in project.html
# =============================================================================


def test_ac1_no_token_vars_in_project_html_root():
    """Every -- var defined in tokens.css must be absent from project.html inline <style> blocks."""
    html = PROJECT_HTML.read_text(encoding="utf-8")
    tokens_vars = _tokens_vars()
    inline_vars = _inline_vars(html)

    overlap = tokens_vars & inline_vars
    assert not overlap, (
        f"project.html inline <style> redeclares {len(overlap)} variable(s) also in tokens.css — "
        f"remove from project.html so tokens.css is the single source of truth. "
        f"Overlapping vars: {sorted(overlap)}"
    )


def test_ac1_sidebar_width_retained():
    """--sidebar-width must still be declared in project.html (it is not in tokens.css)."""
    html = PROJECT_HTML.read_text(encoding="utf-8")
    inline = _inline_style_content(html)
    assert "--sidebar-width" in inline, (
        "--sidebar-width is unique to project.html and must be retained in its inline "
        "<style> block after removing the duplicate tokens"
    )


# =============================================================================
# AC2 — No dark-mode token redeclarations in inline [data-theme="dark"] block
# =============================================================================


def test_ac2_no_dark_mode_token_overrides_inline():
    """The inline [data-theme="dark"] block must not redeclare any token from tokens.css."""
    html = PROJECT_HTML.read_text(encoding="utf-8")
    tokens_vars = _tokens_vars()

    # Extract [data-theme="dark"] blocks from inline styles only
    inline = _inline_style_content(html)
    dark_blocks = re.findall(
        r'\[data-theme=["\']dark["\']\]\s*\{([^}]*)\}', inline, re.DOTALL
    )
    dark_vars: set[str] = set()
    for block in dark_blocks:
        dark_vars.update(re.findall(r"(--[\w-]+)\s*:", block))

    overlap = tokens_vars & dark_vars
    assert not overlap, (
        f"project.html inline [data-theme=\"dark\"] block redeclares {len(overlap)} "
        f"variable(s) already in tokens.css — these must be removed so tokens.css dark "
        f"overrides are authoritative. Overlapping: {sorted(overlap)}"
    )


def test_ac2_no_inline_dark_block_at_all():
    """The [data-theme="dark"] block should be entirely absent from project.html inline <style>."""
    html = PROJECT_HTML.read_text(encoding="utf-8")
    inline = _inline_style_content(html)
    dark_blocks = re.findall(
        r'\[data-theme=["\']dark["\']\]\s*\{([^}]*)\}', inline, re.DOTALL
    )
    assert not dark_blocks, (
        "project.html still has an inline [data-theme=\"dark\"] override block — "
        "all dark-mode token overrides must live exclusively in tokens.css"
    )


# =============================================================================
# AC3 — Zero overlap: tokens.css vars absent from any inline <style> in project.html
# =============================================================================


def test_ac3_zero_overlap_confirmed():
    """Comprehensive: no -- variable from tokens.css appears in any project.html inline <style>."""
    html = PROJECT_HTML.read_text(encoding="utf-8")
    tokens_vars = _tokens_vars()
    inline_vars = _inline_vars(html)

    overlap = tokens_vars & inline_vars
    assert len(overlap) == 0, (
        f"Expected 0 overlapping CSS variables between tokens.css and project.html inline styles, "
        f"found {len(overlap)}: {sorted(overlap)}"
    )


# =============================================================================
# AC4 — tokens.css link tag is present and precedes inline styles
# =============================================================================


def test_ac4_tokens_css_link_present():
    """<link href='/static/css/tokens.css'> must remain in project.html."""
    html = PROJECT_HTML.read_text(encoding="utf-8")
    assert "/static/css/tokens.css" in html, (
        "project.html must keep the <link> to /static/css/tokens.css — "
        "removing it would break all token values on the page"
    )


def test_ac4_link_before_inline_styles():
    """tokens.css <link> must appear before the first inline <style> block."""
    html = PROJECT_HTML.read_text(encoding="utf-8")
    link_pos = html.find("/static/css/tokens.css")
    style_pos = html.find("<style")
    assert link_pos != -1, "tokens.css link tag not found in project.html"
    assert style_pos != -1, "No <style> tag found in project.html"
    assert link_pos < style_pos, (
        "tokens.css <link> must appear before the first inline <style> block so it "
        "loads first and inline styles can override only what they need to"
    )
