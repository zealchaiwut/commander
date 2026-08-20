"""Tests for issue #1045 — Add design tokens CSS and link to all pages.

AC coverage:
  AC1 — tokens.css exists with a :root block covering color palette, 8pt
         spacing scale, type scale, border-radii, box-shadows, z-index layers,
         role colors ba/coder/tester/dispatch/system, semantic success/warn/fail
  AC2 — Token values are sampled from project.html, home.html, analytics.html;
         existing dark theme is preserved exactly
  AC3 — tokens.css includes reusable component classes: button variants,
         chips/pills, cards, table, badge, empty-state, section header
  AC4 — tokens.css is loaded in <head> of all five pages
  AC5 — No page is restyled (only the token file and <link> tags are added)
  AC6 — (impeccable detect — checked manually; no automated test)
  AC7 — tokens.css returns HTTP 200 when the server is asked for it
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
CSS_DIR = STATIC_DIR / "css"
TOKENS_CSS_PATH = CSS_DIR / "tokens.css"

PAGES = {
    "home.html": STATIC_DIR / "home.html",
    "project.html": STATIC_DIR / "project.html",
    "analytics.html": STATIC_DIR / "analytics.html",
    "diagnostics.html": STATIC_DIR / "diagnostics.html",
    "run_browser.html": STATIC_DIR / "run_browser.html",
}


def _tokens() -> str:
    return TOKENS_CSS_PATH.read_text(encoding="utf-8")


# =============================================================================
# AC1 — tokens.css exists with a documented :root block
# =============================================================================


def test_ac1_tokens_css_file_exists():
    """apps/dashboard/static/css/tokens.css must exist."""
    assert TOKENS_CSS_PATH.exists(), (
        "apps/dashboard/static/css/tokens.css must be created — "
        "it is the canonical single source of truth for design tokens"
    )


def test_ac1_root_block_exists():
    """:root block must be present in tokens.css."""
    src = _tokens()
    assert ":root" in src, "tokens.css must contain a :root block"


def test_ac1_color_palette_bg():
    """tokens.css :root must define --color-bg (background token)."""
    src = _tokens()
    has_bg = "--color-bg" in src or (":root" in src and "--bg" in src)
    assert has_bg, (
        "tokens.css must define --color-bg (or --bg) — "
        "the page background color token"
    )


def test_ac1_color_palette_surface():
    """tokens.css :root must define surface tokens."""
    src = _tokens()
    has = "--color-surface" in src or "--surface" in src
    assert has, "tokens.css must define surface color tokens (--color-surface or --surface)"


def test_ac1_color_palette_text():
    """tokens.css :root must define text color tokens."""
    src = _tokens()
    has = "--color-text" in src or "--text" in src
    assert has, "tokens.css must define text color tokens (--color-text or --text)"


def test_ac1_color_palette_accent_green():
    """tokens.css :root must define an accent green token."""
    src = _tokens()
    has = "--color-green" in src or "--green" in src
    assert has, "tokens.css must define an accent green token (--color-green or --green)"


def test_ac1_semantic_success():
    """tokens.css must define semantic success tokens."""
    src = _tokens()
    has = "--color-success" in src
    assert has, "tokens.css must define --color-success semantic token"


def test_ac1_semantic_warn():
    """tokens.css must define semantic warn/warning tokens."""
    src = _tokens()
    has = "--color-warn" in src or "--color-warning" in src
    assert has, "tokens.css must define --color-warn or --color-warning semantic token"


def test_ac1_semantic_fail():
    """tokens.css must define semantic fail/error tokens."""
    src = _tokens()
    has = "--color-fail" in src or "--color-error" in src
    assert has, "tokens.css must define --color-fail or --color-error semantic token"


def test_ac1_role_color_ba():
    """tokens.css must define a role color for ba (business analyst)."""
    src = _tokens()
    has = "--role-ba" in src or "--color-ba" in src
    assert has, "tokens.css must define --role-ba or --color-ba role color token"


def test_ac1_role_color_coder():
    """tokens.css must define a role color for coder."""
    src = _tokens()
    has = "--role-coder" in src or "--color-coder" in src
    assert has, "tokens.css must define --role-coder or --color-coder role color token"


def test_ac1_role_color_tester():
    """tokens.css must define a role color for tester."""
    src = _tokens()
    has = "--role-tester" in src or "--color-tester" in src
    assert has, "tokens.css must define --role-tester or --color-tester role color token"


def test_ac1_role_color_dispatch():
    """tokens.css must define a role color for dispatch."""
    src = _tokens()
    has = "--role-dispatch" in src or "--color-dispatch" in src
    assert has, "tokens.css must define --role-dispatch or --color-dispatch role color token"


def test_ac1_role_color_system():
    """tokens.css must define a role color for system."""
    src = _tokens()
    has = "--role-system" in src or "--color-system" in src
    assert has, "tokens.css must define --role-system or --color-system role color token"


def test_ac1_spacing_scale():
    """tokens.css must define an 8pt-based spacing scale."""
    src = _tokens()
    # must define at least --space-2 (16px in 8pt scale) and --space-1 (8px)
    has = "--space-1" in src or "--space-2" in src or "--sp-" in src
    assert has, (
        "tokens.css must define an 8pt-based spacing scale "
        "(e.g. --space-1 through --space-8 or --sp-1 through --sp-8)"
    )


def test_ac1_type_scale_font_sizes():
    """tokens.css must define type scale font sizes."""
    src = _tokens()
    has = "--font-size-" in src or "--text-sm" in src or "--font-sm" in src
    assert has, "tokens.css must define type scale font size tokens (--font-size-sm etc.)"


def test_ac1_type_scale_weights():
    """tokens.css must define font weight tokens."""
    src = _tokens()
    has = "--font-weight-" in src or "--fw-" in src
    assert has, "tokens.css must define font weight tokens (--font-weight-normal etc.)"


def test_ac1_border_radii():
    """tokens.css must define border-radius tokens."""
    src = _tokens()
    has = "--radius-" in src or "--border-radius-" in src
    assert has, "tokens.css must define border-radius tokens (--radius-sm, --radius-md etc.)"


def test_ac1_box_shadows():
    """tokens.css must define box-shadow tokens."""
    src = _tokens()
    has = "--shadow-" in src
    assert has, "tokens.css must define box-shadow tokens (--shadow-card etc.)"


def test_ac1_z_index_layers():
    """tokens.css must define z-index layer tokens."""
    src = _tokens()
    has = "--z-" in src or "--z-index-" in src or "--layer-" in src
    assert has, "tokens.css must define z-index layer tokens (--z-nav, --z-modal etc.)"


# =============================================================================
# AC2 — Values sampled from existing pages; dark theme preserved
# =============================================================================


def test_ac2_dark_bg_value_preserved():
    """tokens.css dark theme --bg must be #0d0d0d (from project.html)."""
    src = _tokens()
    # The canonical dark background from project.html and analytics.html is #0d0d0d
    assert "#0d0d0d" in src or "0d0d0d" in src.lower(), (
        "tokens.css must use #0d0d0d as the dark background value — "
        "sampled from project.html and analytics.html, not invented"
    )


def test_ac2_dark_surface_value_preserved():
    """tokens.css dark theme --surface must be #161616."""
    src = _tokens()
    assert "#161616" in src or "161616" in src.lower(), (
        "tokens.css must use #161616 as the dark surface value — "
        "sampled from project.html :root [data-theme=dark]"
    )


def test_ac2_dark_border_value_preserved():
    """tokens.css dark theme --border must be #2a2a2a."""
    src = _tokens()
    assert "#2a2a2a" in src or "2a2a2a" in src.lower(), (
        "tokens.css must use #2a2a2a as the dark border value — "
        "sampled from project.html :root [data-theme=dark]"
    )


def test_ac2_light_bg_value_preserved():
    """tokens.css light theme --bg must be #f9fafb (from project.html)."""
    src = _tokens()
    assert "#f9fafb" in src or "f9fafb" in src.lower(), (
        "tokens.css must use #f9fafb as the light background value — "
        "sampled from project.html :root :root block"
    )


# =============================================================================
# AC3 — Reusable component classes
# =============================================================================


def test_ac3_button_variant_class():
    """tokens.css must define at least one reusable button variant class."""
    src = _tokens()
    has = (
        ".btn" in src
        or ".btn-primary" in src
        or ".tok-btn" in src
    )
    assert has, "tokens.css must define at least one button variant class (e.g. .btn, .btn-primary)"


def test_ac3_chip_pill_class():
    """tokens.css must define a chip or pill component class."""
    src = _tokens()
    has = ".chip" in src or ".pill" in src or ".tok-chip" in src or ".tok-pill" in src
    assert has, "tokens.css must define a chip/pill component class (e.g. .chip or .pill)"


def test_ac3_card_class():
    """tokens.css must define a card component class."""
    src = _tokens()
    has = ".card" in src or ".tok-card" in src
    assert has, "tokens.css must define a card component class (e.g. .card)"


def test_ac3_table_class():
    """tokens.css must define a table component class."""
    src = _tokens()
    has = ".table" in src or ".tok-table" in src
    assert has, "tokens.css must define a table component class (e.g. .table)"


def test_ac3_badge_class():
    """tokens.css must define a badge component class."""
    src = _tokens()
    has = ".badge" in src or ".tok-badge" in src
    assert has, "tokens.css must define a badge component class (e.g. .badge)"


def test_ac3_empty_state_class():
    """tokens.css must define an empty-state component class."""
    src = _tokens()
    has = ".empty-state" in src or ".tok-empty" in src or ".empty" in src
    assert has, "tokens.css must define an empty-state component class (e.g. .empty-state)"


def test_ac3_section_header_class():
    """tokens.css must define a section-header component class."""
    src = _tokens()
    has = (
        ".section-header" in src
        or ".sec-header" in src
        or ".section-title" in src
        or ".tok-section" in src
    )
    assert has, (
        "tokens.css must define a section header component class "
        "(e.g. .section-header or .section-title)"
    )


# =============================================================================
# AC4 — tokens.css loaded in <head> of all five pages
# =============================================================================


@pytest.mark.parametrize("page_name,page_path", list(PAGES.items()))
def test_ac4_link_tag_in_page(page_name: str, page_path: Path):
    """Each of the five pages must load tokens.css in <head>."""
    html = page_path.read_text(encoding="utf-8")
    has_link = (
        "tokens.css" in html
        and ("href" in html[max(0, html.find("tokens.css") - 100):html.find("tokens.css")])
    )
    assert has_link, (
        f"{page_name} must include a <link> tag loading /static/css/tokens.css — "
        "the canonical design token file must be linked from every dashboard page"
    )


@pytest.mark.parametrize("page_name,page_path", list(PAGES.items()))
def test_ac4_link_in_head_section(page_name: str, page_path: Path):
    """The tokens.css link must appear inside <head> of each page."""
    html = page_path.read_text(encoding="utf-8")
    head_end = html.find("</head>")
    if head_end == -1:
        head_end = len(html)
    head_section = html[:head_end]
    assert "tokens.css" in head_section, (
        f"tokens.css <link> must appear inside <head> of {page_name}, "
        "not in the body or after </head>"
    )


# =============================================================================
# AC5 — No page is restyled (only link tags and the new CSS file are added)
# =============================================================================


def test_ac5_existing_style_blocks_preserved():
    """Inline <style> blocks in project.html must still be present after linking tokens.css.

    Updated by issue #1200: duplicate token declarations were removed from the inline
    block so tokens.css is the single source of truth. The <style> block still exists
    (page-specific vars like --sidebar-width remain), but --bg: has moved to tokens.css.
    """
    html = (STATIC_DIR / "project.html").read_text(encoding="utf-8")
    assert "<style>" in html or "<style " in html, (
        "project.html must retain its inline <style> block — "
        "it holds page-specific vars and component rules not in tokens.css"
    )


def test_ac5_analytics_style_blocks_preserved():
    """Existing <style> blocks in analytics.html must still be present."""
    html = (STATIC_DIR / "analytics.html").read_text(encoding="utf-8")
    assert "--bg:" in html or "--color-bg:" in html, (
        "analytics.html must retain its existing inline <style> block"
    )


# =============================================================================
# AC7 — tokens.css returns HTTP 200 via the FastAPI test client
# =============================================================================


def test_ac7_tokens_css_http_200():
    """GET /static/css/tokens.css must return HTTP 200 with CSS content-type."""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from starlette.testclient import TestClient
        from server import app

        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/static/css/tokens.css")
    except Exception as exc:
        pytest.skip(f"Could not start test server: {exc}")

    assert r.status_code == 200, (
        f"/static/css/tokens.css returned HTTP {r.status_code} — "
        "it must return 200; a 404 means the file is missing or not served"
    )
    content_type = r.headers.get("content-type", "")
    assert "css" in content_type or "text" in content_type, (
        f"Content-Type for tokens.css must be CSS or text, got: {content_type}"
    )
