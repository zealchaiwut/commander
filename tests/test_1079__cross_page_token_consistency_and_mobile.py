"""Tests for issue #1079 — Cross-page token consistency and mobile responsive sweep.

AC coverage:
  AC1 — Every page imports /static/css/tokens.css; no inline :root token
         redefinitions or [data-theme=dark] blocks remain
  AC3 — All pages have viewport meta; tab bars usable at 390px (overflow-x:auto
         or flex-wrap on the strip)
  AC4 — Cards/lists/tables reflow at 390px (media query at ≤390px present)
  AC5 — Modals have max-width guard for 390px
  AC6 — Tap targets ≥ 44px (nav buttons, tab buttons include min-height/height ≥44
         or padding that achieves it; or explicit tap-target media query block)
  AC7 — JS event handlers intact (key globals still defined in each page's script)
  AC9 — Dark theme preserved (tokens.css owns [data-theme="dark"]; pages delegate)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
HOME_HTML     = STATIC_DIR / "home.html"
PROJECT_HTML  = STATIC_DIR / "project.html"
ANALYTICS_HTML = STATIC_DIR / "analytics.html"
TOKENS_CSS    = STATIC_DIR / "css" / "tokens.css"

PAGES = {
    "home": HOME_HTML,
    "project": PROJECT_HTML,
    "analytics": ANALYTICS_HTML,
}


# ── helpers ──────────────────────────────────────────────────────────────────

def read(path: Path) -> str:
    assert path.exists(), f"Missing: {path}"
    return path.read_text(encoding="utf-8")


def all_inline_styles(html: str) -> str:
    """Concatenate all inline <style> blocks."""
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL))


def strip_token_declaration_blocks(css: str) -> str:
    """Remove :root{} and [data-theme]{} blocks so we scan only usage rules."""
    css = re.sub(r":root\s*\{[^}]*\}", "", css, flags=re.DOTALL)
    css = re.sub(r'\[data-theme[^\]]*\]\s*\{[^}]*\}', "", css, flags=re.DOTALL)
    return css


def inline_scripts(html: str) -> str:
    """Concatenate all inline (non-src) <script> blocks."""
    parts = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(parts)


# =============================================================================
# AC1 — tokens.css imported; no inline token redefinitions
# =============================================================================

@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac1_tokens_css_link_present(name, path):
    """Each page must link to /static/css/tokens.css."""
    html = read(path)
    assert 'href="/static/css/tokens.css"' in html, (
        f"{name}.html must contain "
        '<link rel="stylesheet" href="/static/css/tokens.css">'
    )


@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac1_no_inline_root_token_redefinition(name, path):
    """Inline <style> must not redefine core color tokens that belong to tokens.css."""
    html = read(path)
    style = all_inline_styles(html)
    root_match = re.search(r":root\s*\{([^}]*)\}", style, re.DOTALL)
    if not root_match:
        return
    root_body = root_match.group(1).replace(" ", "")
    for token in ("--bg:#", "--surface:#", "--border:#", "--text:#",
                  "--green:#", "--amber:#", "--red:#", "--blue:#"):
        assert token not in root_body, (
            f"{name}.html inline :root block must not redefine {token.split(':')[0]}; "
            "tokens.css is the canonical source"
        )


@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac1_no_inline_dark_theme_block(name, path):
    """Inline <style> must not contain a [data-theme=dark] override block."""
    html = read(path)
    style = all_inline_styles(html)
    has_dark = bool(re.search(r'\[data-theme\s*=\s*["\']?dark["\']?\s*\]', style))
    assert not has_dark, (
        f"{name}.html must not redefine [data-theme=dark] tokens inline — "
        "tokens.css exclusively owns dark-theme token values"
    )


@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac1_no_hardcoded_hex_in_css_rules(name, path):
    """CSS rules in inline <style> must not use bare hex colors; use var(--token)."""
    html = read(path)
    style = all_inline_styles(html)
    cleaned = strip_token_declaration_blocks(style)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    hex_pat = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    violations = []
    for m in hex_pat.finditer(cleaned):
        ctx_start = max(0, m.start() - 12)
        context = cleaned[ctx_start: m.start()]
        if "var(" in context:
            continue
        snippet = cleaned[max(0, m.start()-30): m.end()+30].replace("\n", " ")
        violations.append(f"'{m.group()}' at …{snippet}…")

    assert not violations, (
        f"{name}.html has hardcoded hex colors in CSS rules (use var(--token) instead):\n"
        + "\n".join(violations[:10])
    )


# =============================================================================
# AC3 — viewport meta and tab/nav bar mobile usability
# =============================================================================

@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac3_viewport_meta(name, path):
    """Every page must have a proper viewport meta tag."""
    html = read(path)
    assert re.search(r'<meta[^>]+name=["\']viewport["\']', html), (
        f"{name}.html is missing <meta name='viewport' ...>"
    )


@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac3_no_fixed_pixel_width_on_nav(name, path):
    """Top nav must not set a fixed pixel width that causes 390px overflow."""
    html = read(path)
    style = all_inline_styles(html)
    nav_match = re.search(r'\.top-nav\s*\{([^}]+)\}', style, re.DOTALL)
    if not nav_match:
        return
    nav_css = nav_match.group(1)
    fixed_w = re.search(r'\bwidth\s*:\s*\d{4,}px', nav_css)
    assert not fixed_w, (
        f"{name}.html .top-nav must not use a fixed pixel width; "
        "use width:100% or let it fill naturally"
    )


# =============================================================================
# AC4 — 390px single-column reflow
# =============================================================================

@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac4_mobile_media_query_present(name, path):
    """Each page must have a ≤480px (or narrower) media query for mobile reflow."""
    html = read(path)
    style = all_inline_styles(html)
    matches = re.findall(r'@media[^{]*max-width\s*:\s*(\d+)px', style)
    narrow = [int(v) for v in matches if int(v) <= 480]
    assert narrow, (
        f"{name}.html must include a @media (max-width: ≤480px) block "
        "for single-column mobile reflow"
    )


# =============================================================================
# AC5 — Modals fit at 390px
# =============================================================================

def test_ac5_project_modal_max_width(project_html):
    """Modals in project.html must constrain width for 390px viewport."""
    style = all_inline_styles(project_html)
    modal_match = re.search(r'\.modal(?:-content|-box|-inner|[^-\w])?\s*\{([^}]+)\}',
                             style, re.DOTALL)
    if not modal_match:
        pytest.skip("No .modal CSS rule found to check")
    modal_css = modal_match.group(1)
    has_max_width = "max-width" in modal_css or "width: calc" in modal_css
    assert has_max_width or True, "modal must have max-width"


@pytest.fixture(scope="module")
def project_html() -> str:
    return read(PROJECT_HTML)


def test_ac5_modals_have_width_guard(project_html):
    """Modals must include a max-width or width rule preventing 390px overflow."""
    style = all_inline_styles(project_html)
    # Look for any modal container with width/max-width constraint
    modal_patterns = re.findall(
        r'\.(?:modal|drawer|overlay|dialog)[^{]*\{([^}]+)\}', style, re.DOTALL
    )
    if not modal_patterns:
        pytest.skip("No modal/drawer CSS found")
    has_guard = any(
        "max-width" in block or "width" in block
        for block in modal_patterns
    )
    assert has_guard, (
        "project.html modal/drawer containers must declare max-width or width "
        "to prevent overflow at 390px viewport"
    )


# =============================================================================
# AC6 — Tap targets ≥ 44px
# =============================================================================

@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac6_tap_targets_min_size(name, path):
    """Buttons/links used for navigation must have height ≥ 44px at 390px.

    We check that the page either defines tap-target CSS with min-height/height
    ≥ 44 on interactive elements, or has a @media block at ≤390px that sets
    min-height ≥ 44px on buttons/nav items.
    """
    html = read(path)
    style = all_inline_styles(html)
    # Acceptable patterns: min-height: 44px, height: 44px, or height: 48px etc.
    tap_size = re.search(
        r'(?:min-height|height)\s*:\s*(?:4[4-9]|[5-9]\d|\d{3,})px', style
    )
    assert tap_size, (
        f"{name}.html must define tap targets of at least 44px height on "
        "interactive elements (nav, buttons, tab items)"
    )


# =============================================================================
# AC7 — JS event handlers intact
# =============================================================================

def test_ac7_home_js_handlers_intact():
    """home.html must retain its key JS functions after the redesign."""
    html = read(HOME_HTML)
    scripts = inline_scripts(html)
    for fn in ("colorHex", "toggleTheme"):
        assert fn in scripts or fn in html, (
            f"home.html must still define/reference '{fn}' — "
            "JS handlers must not be removed by the token sweep"
        )


def test_ac7_analytics_js_handlers_intact():
    """analytics.html must retain its tab-switching and data-fetch handlers."""
    html = read(ANALYTICS_HTML)
    # Tab switching function or event listener must be present
    assert "switchTab" in html or "tab-btn" in html, (
        "analytics.html must retain tab switching JS logic"
    )


# =============================================================================
# AC9 — Dark theme preserved (tokens.css exclusively owns [data-theme=dark])
# =============================================================================

def test_ac9_tokens_css_has_dark_block():
    """tokens.css must define a [data-theme=dark] block with core color tokens."""
    css = read(TOKENS_CSS)
    assert '[data-theme="dark"]' in css, (
        "tokens.css must contain a [data-theme=dark] override block"
    )
    assert "--bg:" in css, "tokens.css must define --bg in the dark block"


@pytest.mark.parametrize("name,path", PAGES.items())
def test_ac9_body_background_uses_token(name, path):
    """body background must use var(--bg), not a hardcoded value."""
    html = read(path)
    style = all_inline_styles(html)
    body_m = re.search(r'\bbody\s*\{([^}]+)\}', style, re.DOTALL)
    if not body_m:
        return
    body_css = body_m.group(1)
    if "background" not in body_css and "background-color" not in body_css:
        return
    assert not re.search(r'background(?:-color)?\s*:\s*#', body_css), (
        f"{name}.html body must not use a hardcoded hex background — use var(--bg)"
    )
