"""Tests for issue #1048 — Polish Home page: hover, motion, a11y, empty/loading states.

AC coverage:
  AC1 — Cards have visible, smooth hover state (scale/shadow) using tokens
  AC2 — Cards and buttons have polished focus rings meeting WCAG 2.1 AA
  AC3 — All transitions/animations respect prefers-reduced-motion
  AC4 — Polished loading state (spinner or skeleton) renders while projects fetch
  AC5 — Friendly, on-brand empty state renders when project list is empty
  AC6 — All interactive elements carry descriptive aria-label/aria-labelledby
  AC7 — Card grid aligns to pixel boundaries; no fractional column fr offsets
  AC8 — Dark theme consistent; no hardcoded hex colours on .pbic; all via tokens
  AC9 — impeccable detect: zero new violations (manual; no automated proxy here)
  AC10 — Vanilla JS/CSS only; no framework additions
  AC11 — Diff scoped to home.html only; tokens.css unchanged
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
    """Extract all inline <script> blocks (not src=... scripts)."""
    parts = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(parts)


def _extract_media_block(style: str, feature: str) -> str:
    """Extract the content of an @media block by counting braces."""
    m = re.search(rf"@media\s*\([^)]*{re.escape(feature)}[^)]*\)\s*\{{", style)
    if not m:
        return ""
    start = m.end()
    depth = 1
    pos = start
    while pos < len(style) and depth > 0:
        if style[pos] == "{":
            depth += 1
        elif style[pos] == "}":
            depth -= 1
        pos += 1
    return style[start : pos - 1]


# =============================================================================
# AC1 — Card hover state: .pbrief:hover with transform/box-shadow + transition
# =============================================================================


def test_ac1_pbrief_hover_rule_exists(inline_style):
    """.pbrief:hover CSS rule must exist with a visual lift effect."""
    assert re.search(r"\.pbrief\s*:\s*hover", inline_style), (
        ".pbrief:hover rule missing — cards must have a visible hover state (AC1)"
    )


def test_ac1_pbrief_hover_has_transform_or_shadow(inline_style):
    """.pbrief:hover must define transform or box-shadow for the lift effect."""
    m = re.search(r"\.pbrief\s*:\s*hover\s*\{([^}]+)\}", inline_style, re.DOTALL)
    assert m, ".pbrief:hover rule body not found"
    rule = m.group(1)
    assert "transform" in rule or "box-shadow" in rule, (
        ".pbrief:hover must include transform (e.g. translateY) or box-shadow "
        "for a visible lift effect (AC1)"
    )


def test_ac1_pbrief_hover_box_shadow_uses_token(inline_style):
    """.pbrief:hover box-shadow must reference a token variable."""
    m = re.search(r"\.pbrief\s*:\s*hover\s*\{([^}]+)\}", inline_style, re.DOTALL)
    if not m:
        pytest.skip(".pbrief:hover not present — AC1 already fails earlier test")
    rule = m.group(1)
    if "box-shadow" not in rule:
        return  # using transform only is acceptable
    assert "var(--" in rule, (
        ".pbrief:hover box-shadow must use var(--shadow-card) or similar token "
        "from tokens.css; hardcoded shadow values not permitted (AC1)"
    )


def test_ac1_pbrief_has_transition(inline_style):
    """.pbrief must define transition so the hover state animates smoothly."""
    m = re.search(r"\.pbrief\s*\{([^}]+)\}", inline_style, re.DOTALL)
    assert m, ".pbrief CSS rule not found"
    rule = m.group(1)
    assert "transition" in rule, (
        ".pbrief must include a transition property for smooth hover animation (AC1)"
    )


# =============================================================================
# AC2 — Focus rings: :focus-visible with outline or box-shadow
# =============================================================================


def test_ac2_focus_visible_rule_exists(inline_style):
    """At least one :focus-visible rule must exist in the inline <style>."""
    assert re.search(r":focus-visible", inline_style), (
        "No :focus-visible CSS rules found — interactive elements must have "
        "polished focus rings meeting WCAG 2.1 AA (AC2)"
    )


def test_ac2_focus_visible_has_outline_or_shadow(inline_style):
    """The :focus-visible rule must define an outline or box-shadow."""
    m = re.search(r":focus-visible\s*\{([^}]+)\}", inline_style, re.DOTALL)
    assert m, ":focus-visible CSS rule body not found"
    rule = m.group(1)
    assert "outline" in rule or "box-shadow" in rule, (
        ":focus-visible must define an outline or box-shadow to produce a "
        "visible focus indicator (WCAG 2.1 AA / AC2)"
    )


def test_ac2_focus_ring_uses_token(inline_style):
    """The :focus-visible outline/shadow must reference a CSS token."""
    m = re.search(r":focus-visible\s*\{([^}]+)\}", inline_style, re.DOTALL)
    if not m:
        pytest.skip(":focus-visible not present — AC2 already fails earlier test")
    rule = m.group(1)
    assert "var(--" in rule, (
        ":focus-visible ring must use var(--blue) or another token for color "
        "(hardcoded hex breaks dark mode / AC2)"
    )


# =============================================================================
# AC3 — prefers-reduced-motion: covers transitions introduced for hover effects
# =============================================================================


def test_ac3_reduced_motion_block_exists(inline_style):
    """@media (prefers-reduced-motion: reduce) block must exist."""
    block = _extract_media_block(inline_style, "prefers-reduced-motion")
    assert block, (
        "@media (prefers-reduced-motion: reduce) block not found in home.html (AC3)"
    )


def test_ac3_reduced_motion_disables_animation(inline_style):
    """Reduced-motion block must still cancel keyframe animations."""
    block = _extract_media_block(inline_style, "prefers-reduced-motion")
    assert "animation" in block, (
        "prefers-reduced-motion block must cancel keyframe animations "
        "(glow/spin) for motion-sensitive users (AC3)"
    )


def test_ac3_reduced_motion_covers_transitions(inline_style):
    """Reduced-motion block must suppress CSS transitions added for card hover."""
    block = _extract_media_block(inline_style, "prefers-reduced-motion")
    assert block, "@media (prefers-reduced-motion: reduce) block not found (AC3)"
    assert "transition" in block or "transform" in block, (
        "prefers-reduced-motion block must include 'transition: none' or "
        "'transform: none' to suppress card hover motion (AC3)"
    )


# =============================================================================
# AC4 — Polished loading state: spinner in boot loading content
# =============================================================================


def test_ac4_loading_state_class_or_spinner_in_style(inline_style):
    """A loading-state CSS class or equivalent spinner styling must exist."""
    has_loading = (
        ".loading-state" in inline_style
        or "loading-spin" in inline_style
        or "ti-loader" in inline_style
    )
    assert has_loading, (
        "A .loading-state CSS class (with spinner styling) must be defined "
        "in home.html inline <style> (AC4)"
    )


def test_ac4_boot_loading_uses_spinner(inline_script):
    """The boot loading state set via root.innerHTML must include a spinner icon."""
    # Find the boot-note assignment — specifically where 'Loading daily brief' is set
    boot_match = re.search(
        r"boot-note['\"][^'\"]*['\"][^)]*?\)?\s*[^)]*Loading daily brief",
        inline_script,
        re.DOTALL,
    )
    if not boot_match:
        # Broader search: find the loading text near boot-note
        boot_match = re.search(
            r"(?:boot-note|loading-state)[^\n]*Loading daily brief",
            inline_script,
            re.DOTALL,
        )
    assert boot_match, (
        "'Loading daily brief' text near 'boot-note' not found in inline script (AC4)"
    )
    context = inline_script[max(0, boot_match.start() - 30):boot_match.end() + 100]
    assert "ti-loader" in context or "loading-state" in context or "spinner" in context, (
        "Boot loading state must include a spinner icon (e.g. ti-loader-2) or "
        "loading-state CSS class — plain text is not sufficient (AC4)"
    )


# =============================================================================
# AC5 — Empty state: on-brand icon + message when project list is empty
# =============================================================================


def test_ac5_empty_state_class_used_for_no_projects(inline_script):
    """renderProjectsShell must use .empty-state class (from tokens.css) for empty list."""
    assert "empty-state" in inline_script, (
        "renderProjectsShell must use the .empty-state CSS class (defined in "
        "tokens.css) when the project list is empty (AC5)"
    )


def test_ac5_empty_state_has_icon(inline_script):
    """The empty state for no projects must include a Tabler icon."""
    # Check that the empty-state content includes a ti- icon class
    # Find the empty-state usage in inline_script and verify it has an icon
    empty_match = re.search(r"empty-state[^\n]{0,300}", inline_script, re.DOTALL)
    assert empty_match, "empty-state usage not found in inline script"
    context = empty_match.group(0)
    assert "ti-" in context, (
        "Empty state must include a Tabler icon (class 'ti-*') to be on-brand; "
        "a bare text message is not sufficient (AC5)"
    )


def test_ac5_add_proj_cta_present(inline_script):
    """The empty state section must still include an 'Add new project' CTA."""
    assert "add-proj" in inline_script, (
        "The .add-proj CTA link must remain in renderProjectsShell so users "
        "have a path to add projects from the empty state (AC5)"
    )


# =============================================================================
# AC6 — aria-label on interactive elements missing accessible names
# =============================================================================


def test_ac6_theme_toggle_has_aria_label(html):
    """The theme toggle button must have aria-label."""
    m = re.search(r'class="theme-toggle"[^>]*>', html)
    assert m, "theme-toggle button not found in HTML"
    tag = m.group(0)
    assert "aria-label" in tag, (
        "Theme toggle button must have aria-label attribute (AC6)"
    )


def test_ac6_open_proj_link_has_aria_label(inline_script):
    """The .open-proj link template must carry an aria-label attribute."""
    m = re.search(r'class="open-proj"[^>]*>', inline_script)
    assert m, ".open-proj link not found in JS templates"
    tag = m.group(0)
    assert "aria-label" in tag, (
        ".open-proj link must have aria-label so screen-reader users understand "
        "which project it opens (AC6)"
    )


def test_ac6_add_proj_link_has_aria_label(inline_script):
    """The .add-proj link template must carry an aria-label attribute."""
    m = re.search(r'class="add-proj"[^>]*>', inline_script)
    assert m, ".add-proj link not found in JS templates"
    tag = m.group(0)
    assert "aria-label" in tag, (
        ".add-proj link must have aria-label attribute (AC6)"
    )


def test_ac6_pb_bottom_toggle_has_aria_label(inline_script):
    """.pb-bottom-toggle must have aria-label (already had it; must not regress)."""
    m = re.search(r'class="pb-bottom-toggle"[^>]*>', inline_script)
    assert m, ".pb-bottom-toggle not found in JS templates"
    tag = m.group(0)
    assert "aria-label" in tag, (
        ".pb-bottom-toggle must retain its aria-label attribute (AC6 regression guard)"
    )


# =============================================================================
# AC7 — Card grid: no fractional fr column offsets
# =============================================================================


def test_ac7_grid_no_fractional_fr(inline_style):
    """.pb-grid grid-template-columns must not use decimal-fraction fr values."""
    m = re.search(r"\.pb-grid\s*\{([^}]+)\}", inline_style, re.DOTALL)
    assert m, ".pb-grid CSS rule not found"
    rule = m.group(1)
    cols_m = re.search(r"grid-template-columns\s*:\s*([^;]+)", rule)
    assert cols_m, ".pb-grid must define grid-template-columns"
    cols = cols_m.group(1)
    fractional = re.findall(r"\d+\.\d+fr", cols)
    assert not fractional, (
        f"Card grid uses fractional fr values {fractional} which cause sub-pixel "
        "rendering artifacts — use integer ratios like '3fr 2fr' instead (AC7)"
    )


def test_ac7_grid_uses_two_columns(inline_style):
    """.pb-grid desktop layout must still use a two-column grid."""
    m = re.search(r"\.pb-grid\s*\{([^}]+)\}", inline_style, re.DOTALL)
    assert m, ".pb-grid CSS rule not found"
    rule = m.group(1)
    cols_m = re.search(r"grid-template-columns\s*:\s*([^;]+)", rule)
    assert cols_m, ".pb-grid must define grid-template-columns"
    cols = cols_m.group(1).strip()
    # Must define at least two column tracks
    column_tracks = re.findall(r"\d+\s*fr|\d+px|\d+%|auto|min-content|max-content", cols)
    assert len(column_tracks) >= 2, (
        ".pb-grid must remain a two-column layout for desktop (AC7)"
    )


# =============================================================================
# AC8 — No hardcoded hex on .pbic; use CSS token-based modifier classes
# =============================================================================


def test_ac8_no_inline_hex_on_pbic(inline_script):
    """JS must not set .pbic background to a hardcoded hex color via inline style."""
    has_inline_hex = re.search(
        r"""pbic[^'"]*['"][^'"]*style\s*=\s*['"]background\s*:\s*#[0-9a-fA-F]{3,8}""",
        inline_script,
        re.DOTALL,
    )
    assert not has_inline_hex, (
        "JS must not set .pbic background via inline style='background:#hex' — "
        "use CSS .pbic--<color> modifier classes with var(--token) instead (AC8)"
    )


def test_ac8_pbic_color_classes_in_style(inline_style):
    """CSS must define at least one .pbic--<color> modifier class."""
    has_classes = bool(re.search(r"\.pbic--\w+", inline_style))
    assert has_classes, (
        "home.html CSS must define .pbic--<color> modifier classes (e.g. "
        ".pbic--blue, .pbic--green) using var(--token) values so icon badge "
        "colors adapt correctly in dark mode (AC8)"
    )


def test_ac8_pbic_classes_use_tokens(inline_style):
    """.pbic--<color> classes must use var(--token) for background, not bare hex."""
    matches = re.findall(r"\.pbic--\w+\s*\{([^}]+)\}", inline_style)
    assert matches, ".pbic--<color> CSS classes not found — AC8 already fails earlier test"
    for rule_body in matches:
        # background must be a var(), not a bare hex literal
        bg_m = re.search(r"\bbackground\s*:\s*([^;]+)", rule_body)
        if bg_m:
            val = bg_m.group(1).strip()
            assert val.startswith("var(--"), (
                f".pbic--<color> background must use var(--token), found '{val}' (AC8)"
            )


def test_ac8_no_color_hex_function(inline_script):
    """The colorHex() helper that returned hardcoded hex values must be removed."""
    assert "function colorHex" not in inline_script, (
        "colorHex() function must be removed — it returned hardcoded hex values "
        "that break dark mode; use CSS .pbic--<color> classes instead (AC8)"
    )


# =============================================================================
# AC10 — Vanilla JS/CSS only; no framework additions
# =============================================================================


def test_ac10_no_framework_scripts(html):
    """No React, Vue, Svelte, or similar framework scripts must be added."""
    for fw in ["react", "vue.js", "svelte", "angular.js", "ember.js"]:
        assert fw not in html.lower(), (
            f"Framework '{fw}' detected in home.html — vanilla JS/CSS only (AC10)"
        )


def test_ac10_no_es_module_imports(html):
    """No ES module import statements must be added to home.html."""
    inline_parts = re.findall(
        r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL
    )
    for part in inline_parts:
        assert not re.search(r"^\s*import\s+", part, re.MULTILINE), (
            "ES module import statement found in inline script — "
            "home.html must remain a no-build-step vanilla file (AC10)"
        )


# =============================================================================
# AC11 — Diff scoped to home.html; tokens.css unchanged
# =============================================================================


def test_ac11_tokens_css_has_shadow_card():
    """tokens.css must still define --shadow-card (unchanged by this PR)."""
    src = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--shadow-card" in src, (
        "tokens.css must be unchanged — --shadow-card token missing (AC11)"
    )


def test_ac11_tokens_css_has_surface_hover():
    """tokens.css must still define --surface-hover (unchanged by this PR)."""
    src = TOKENS_CSS.read_text(encoding="utf-8")
    assert "--surface-hover" in src, (
        "tokens.css must be unchanged — --surface-hover token missing (AC11)"
    )


def test_ac11_tokens_css_has_empty_state_class():
    """tokens.css must still define the .empty-state component class."""
    src = TOKENS_CSS.read_text(encoding="utf-8")
    assert ".empty-state" in src, (
        "tokens.css must be unchanged — .empty-state class missing (AC11)"
    )
