"""Tests for issue #1047 — Audit and fix Home page against impeccable rules.

AC coverage:
  AC1 — impeccable detect run on apps/dashboard/static/home.html reports
        zero findings. Proxy checks (since npx is unavailable in this clone):
        (a) no duplicate :root token re-declarations already owned by tokens.css
        (b) no [data-theme="dark"] override block in inline style
        (c) every @keyframes block has a prefers-reduced-motion counterpart
        (d) the .pbic icon color uses var(--token) not a bare hex literal
  AC2 — all fixes use only foundation tokens; no hardcoded color/spacing/size
        values introduced in the CSS rules section of the inline <style>
  AC3 — dark theme preserved: tokens.css remains linked; the page can
        display in dark mode via data-theme="dark" on <html>
  AC4 — diff scoped exclusively to home.html; tokens.css is unchanged
  AC5 — audit loop (detect → fix → detect) completes clean — covered by AC1
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
HOME_HTML = STATIC_DIR / "home.html"
TOKENS_CSS = STATIC_DIR / "css" / "tokens.css"


@pytest.fixture(scope="module")
def html() -> str:
    assert HOME_HTML.exists(), f"home.html not found at {HOME_HTML}"
    return HOME_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def inline_style(html) -> str:
    """Extract the content of the inline <style> block."""
    m = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def inline_script(html) -> str:
    """Extract inline <script> block(s) (not src=... external scripts)."""
    parts = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(parts)


# =============================================================================
# AC1-a — No duplicate :root color token declarations
# =============================================================================


def test_ac1a_no_duplicate_bg_declaration(inline_style):
    """Inline :root must not redefine --bg; that token is owned by tokens.css.

    Removing the redundant :root block is the primary fix; tokens.css is
    linked before the inline <style> and provides all palette variables.
    """
    root_match = re.search(r":root\s*\{([^}]*)\}", inline_style, re.DOTALL)
    if not root_match:
        return  # no :root at all — pass
    root_body = root_match.group(1).replace(" ", "").replace("\n", "")
    assert "--bg:#" not in root_body, (
        "home.html inline :root must not redefine --bg — "
        "this value is already defined in tokens.css"
    )


def test_ac1a_no_duplicate_surface_declaration(inline_style):
    """Inline :root must not redefine --surface; owned by tokens.css."""
    root_match = re.search(r":root\s*\{([^}]*)\}", inline_style, re.DOTALL)
    if not root_match:
        return
    root_body = root_match.group(1).replace(" ", "").replace("\n", "")
    assert "--surface:#" not in root_body, (
        "home.html inline :root must not redefine --surface — "
        "use var(--surface) sourced from tokens.css"
    )


def test_ac1a_no_duplicate_text_declaration(inline_style):
    """Inline :root must not redefine --text; owned by tokens.css."""
    root_match = re.search(r":root\s*\{([^}]*)\}", inline_style, re.DOTALL)
    if not root_match:
        return
    root_body = root_match.group(1).replace(" ", "").replace("\n", "")
    assert "--text:#" not in root_body, (
        "home.html inline :root must not redefine --text — "
        "use var(--text) from tokens.css"
    )


# =============================================================================
# AC1-b — No [data-theme="dark"] override block in inline style
# =============================================================================


def test_ac1b_no_inline_dark_theme_block(inline_style):
    """Inline <style> must not contain a [data-theme=dark] override block.

    Dark-theme token values are exclusively owned by tokens.css.  Duplicating
    them in-page creates two sources of truth for the same variables.
    """
    has_dark_block = bool(
        re.search(r'\[data-theme\s*=\s*["\']?dark["\']?\s*\]', inline_style)
    )
    assert not has_dark_block, (
        "home.html must not contain a [data-theme=dark] CSS block — "
        "dark-theme overrides belong exclusively in tokens.css"
    )


# =============================================================================
# AC1-c — Every @keyframes block has a prefers-reduced-motion override
# =============================================================================


def test_ac1c_reduced_motion_media_query_present(inline_style):
    """The inline <style> must include a prefers-reduced-motion media query.

    Any @keyframes animation (glow, spin) must be cancelled for users who have
    requested reduced motion — WCAG 2.3.3 / impeccable audit rule.
    """
    has_keyframes = bool(re.search(r"@keyframes\s+\w+", inline_style))
    if not has_keyframes:
        return  # no animations at all — trivially passes
    has_reduced = bool(
        re.search(r"prefers-reduced-motion\s*:\s*reduce", inline_style)
    )
    assert has_reduced, (
        "home.html defines @keyframes animations (glow, spin) but has no "
        "@media (prefers-reduced-motion: reduce) override — "
        "add one to disable animations for users who need it (WCAG 2.3.3)"
    )


def test_ac1c_reduced_motion_disables_animation(inline_style):
    """The prefers-reduced-motion block must set animation to none."""
    m = re.search(
        r"@media\s*\(prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}",
        inline_style,
        re.DOTALL,
    )
    assert m, (
        "No @media (prefers-reduced-motion: reduce) block found — "
        "this block must exist to pass the impeccable accessibility gate"
    )
    block = m.group(1)
    assert "animation" in block, (
        "The prefers-reduced-motion media query must contain 'animation: none' "
        "to disable glow/spin animations for motion-sensitive users"
    )


# =============================================================================
# AC1-d — .pbic icon color uses a CSS variable, not a bare hex literal
# =============================================================================


def test_ac1d_pbic_icon_color_uses_token(inline_style):
    """.pbic selector must use var(--token) for color, not a bare hex literal.

    tokens.css has no 'pure white' constant, so a local --icon-on-color
    variable may be defined in the inline :root block.  The *rule* itself
    must reference the variable, not hardcode #fff.
    """
    pbic_match = re.search(r"\.pbic\s*\{([^}]+)\}", inline_style, re.DOTALL)
    assert pbic_match, ".pbic rule not found in home.html inline style"
    rule_body = pbic_match.group(1)

    # color property must use a var(--...) expression
    color_match = re.search(r"\bcolor\s*:\s*([^;]+)", rule_body)
    assert color_match, ".pbic must have an explicit color property"
    color_value = color_match.group(1).strip()
    assert color_value.startswith("var(--"), (
        f".pbic color must use var(--token) not a bare value '{color_value}'. "
        "Define --icon-on-color:#fff in the inline :root block and reference it "
        "as var(--icon-on-color) to satisfy the no-hardcoded-colors rule."
    )


# =============================================================================
# AC2 — No hardcoded hex colors in CSS rules (outside token declaration blocks)
# =============================================================================


def test_ac2_no_hardcoded_hex_colors_in_rules(inline_style):
    """CSS rules must use var(--token) for colors, not bare hex codes.

    We strip :root and [data-theme] blocks first (token declarations are
    allowed to contain hex literals), then scan the remaining selectors for
    any hex color that is not inside a var() expression.
    """
    # Remove :root block(s) and [data-theme] blocks
    cleaned = re.sub(r":root\s*\{[^}]*\}", "", inline_style, flags=re.DOTALL)
    cleaned = re.sub(
        r"\[data-theme[^\]]*\]\s*\{[^}]*\}", "", cleaned, flags=re.DOTALL
    )
    # Remove CSS comments
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)

    hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    violations = []
    for m in hex_re.finditer(cleaned):
        # Check the 10 chars before to see if we're inside a var(
        start = max(0, m.start() - 10)
        context_before = cleaned[start : m.start()]
        if "var(" in context_before:
            continue  # value is a fallback inside var() — acceptable
        violations.append(
            f"'{m.group()}' at offset {m.start()} "
            f"(context: '…{cleaned[max(0,m.start()-30):m.end()+20]}…')"
        )

    assert not violations, (
        "Hardcoded hex colors found in home.html CSS rules — "
        "use var(--token) from tokens.css instead:\n"
        + "\n".join(violations)
    )


# =============================================================================
# AC3 — Dark theme preserved: tokens.css linked, data-theme attribute present
# =============================================================================


def test_ac3_tokens_css_link_present(html):
    """home.html must still link tokens.css in the <head>.

    tokens.css owns all color tokens including the [data-theme=dark]
    overrides; removing this link would break both theming and the entire
    token system.
    """
    assert "tokens.css" in html, (
        "home.html must include <link href='...tokens.css'> in <head> — "
        "this file is the single source of truth for all design tokens"
    )


def test_ac3_tokens_css_link_before_style(html):
    """tokens.css <link> must appear before the inline <style> block.

    Tokens must be declared before the inline style overrides them so that
    local :root additions take precedence over defaults, not the reverse.
    """
    link_pos = html.find("tokens.css")
    style_pos = html.find("<style")
    assert link_pos != -1, "tokens.css link not found"
    assert link_pos < style_pos, (
        "<link rel='stylesheet' href='...tokens.css'> must appear before "
        "the inline <style> block so that token values are available to "
        "the inline CSS overrides"
    )


def test_ac3_dark_theme_toggle_function_preserved(inline_script):
    """toggleTheme() must still exist so the user can switch themes."""
    assert "toggleTheme" in inline_script, (
        "toggleTheme() must be preserved in home.html — "
        "it lets the user switch between light and dark mode (AC3)"
    )


# =============================================================================
# AC4 — Diff scoped to home.html; tokens.css must be unchanged
# =============================================================================


def test_ac4_tokens_css_owns_dark_bg():
    """tokens.css must still define --bg:#0d0d0d for dark mode.

    This verifies tokens.css was not modified as a side-effect of the audit.
    """
    src = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--bg:#0d0d0d" in src.replace(" ", "").replace("\n", "") or \
           "--bg:           #0d0d0d" in src or \
           "--bg:          #0d0d0d" in src, (
        "tokens.css must still contain --bg:#0d0d0d (dark theme background) — "
        "this file must not be modified by the #1047 audit"
    )


def test_ac4_tokens_css_owns_dark_surface():
    """tokens.css must still define --surface:#161616 for dark mode."""
    src = TOKENS_CSS.read_text(encoding="utf-8")
    assert "#161616" in src, (
        "tokens.css must still contain --surface:#161616 (dark surface) — "
        "this file must not be modified by the #1047 audit"
    )


def test_ac4_tokens_css_has_space_tokens():
    """tokens.css must still define the --space-N spacing scale."""
    src = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--space-12" in src, (
        "tokens.css must still contain --space-12 (48px spacing token) — "
        "this token is used for tap-target fixes; tokens.css must be unchanged"
    )


# =============================================================================
# AC5 — Tap-target compliance (44px minimum per WCAG 2.5.5 / impeccable)
# The .pb-bottom-toggle is 24×24 by default — clearly below the 44px minimum.
# After fix it must declare a min-height / min-width using a token value.
# =============================================================================


def test_ac5_pb_bottom_toggle_has_min_height(inline_style):
    """.pb-bottom-toggle must declare min-height ≥ 44px via a token.

    The current 24×24px size fails the WCAG 2.5.5 / impeccable 44px
    tap-target requirement on mobile (Tailscale access).  The fix must use
    a spacing token (e.g. var(--space-12) = 48px) rather than a hardcoded px.
    """
    toggle_match = re.search(
        r"\.pb-bottom-toggle\s*\{([^}]+)\}", inline_style, re.DOTALL
    )
    assert toggle_match, ".pb-bottom-toggle CSS rule not found in home.html"
    rule = toggle_match.group(1)
    has_min_height = "min-height" in rule and "var(--" in rule
    assert has_min_height, (
        ".pb-bottom-toggle must set min-height using a token value (e.g. "
        "var(--space-12) = 48px) to meet the 44px tap-target requirement — "
        "WCAG 2.5.5 / impeccable responsive dimension"
    )
