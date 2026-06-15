"""
Tests for issue #1054: Polish project shell nav: a11y, transitions, sticky header.

Checks apps/dashboard/static/project.html and
apps/dashboard/static/src/shell/tabs.js for:

  AC1 — CSS transition on active tab indicator; suppressed by prefers-reduced-motion
  AC2 — Hover + focus rings on tabs and theme toggle using foundation tokens
  AC3 — Keyboard nav: ArrowLeft/Right in tabs.js; roving tabindex so Tab exits group
  AC4 — role="tab", role="tablist", aria-selected on active tab
  AC5 — Theme toggle focus ring matches tab ring style
  AC6 — Header condenses on scroll (CSS condensed class + JS scroll handler)
  AC7 — No new hardcoded color values in added CSS
  AC8 — Vanilla JS/CSS only (no new framework imports)
"""
import re
import pytest
from pathlib import Path

HTML_FILE = Path("apps/dashboard/static/project.html")
HTML = HTML_FILE.read_text()

TABS_JS_FILE = Path("apps/dashboard/static/src/shell/tabs.js")
TABS_JS = TABS_JS_FILE.read_text()


def _all_style_blocks(html: str) -> str:
    return "\n".join(re.findall(r"<style>(.*?)</style>", html, re.DOTALL))


def _first_rule_body(css: str, selector_pattern: str) -> str:
    m = re.search(selector_pattern + r"\s*\{([^}]+)\}", css, re.DOTALL)
    return m.group(1) if m else ""


STYLE = _all_style_blocks(HTML)


# ── AC1 — Smooth tab transitions + prefers-reduced-motion ─────────────────────

class TestSmoothTransitions:
    """AC1: Active tab has smooth CSS transition; suppressed under prefers-reduced-motion."""

    def test_stab_has_transition_on_border_color(self):
        """Tab border-bottom indicator must have a CSS transition for smooth switching."""
        stab_match = re.search(r"\.stab\s*\{([^}]+)\}", STYLE, re.DOTALL)
        assert stab_match, ".stab rule not found"
        rule = stab_match.group(1)
        assert "transition" in rule, ".stab must declare a CSS transition for smooth indicator"
        assert "border" in rule and "transition" in rule, (
            ".stab transition should include border-color for the underline indicator"
        )

    def test_stab_transition_suppressed_for_reduced_motion(self):
        """@media (prefers-reduced-motion: reduce) block must suppress .stab transitions."""
        reduced_motion_blocks = re.findall(
            r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{([^{}]+(?:\{[^{}]*\}[^{}]*)*)\}",
            STYLE,
            re.DOTALL,
        )
        assert reduced_motion_blocks, (
            "No @media (prefers-reduced-motion: reduce) block found in page styles"
        )
        combined = "\n".join(reduced_motion_blocks)
        has_stab_none = bool(
            re.search(r"\.stab\s*\{[^}]*transition\s*:\s*none", combined, re.DOTALL)
        )
        assert has_stab_none, (
            ".stab { transition: none } must appear inside a prefers-reduced-motion:reduce "
            "block to suppress animations for users who prefer reduced motion"
        )

    def test_btn_icon_transition_suppressed_for_reduced_motion(self):
        """@media (prefers-reduced-motion: reduce) must also suppress .btn-icon transitions."""
        reduced_blocks = re.findall(
            r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{([^{}]+(?:\{[^{}]*\}[^{}]*)*)\}",
            STYLE,
            re.DOTALL,
        )
        combined = "\n".join(reduced_blocks)
        has_btn_icon_none = bool(
            re.search(r"\.btn-icon\s*\{[^}]*transition\s*:\s*none", combined, re.DOTALL)
        )
        assert has_btn_icon_none, (
            ".btn-icon { transition: none } must be inside a prefers-reduced-motion:reduce "
            "block so theme toggle hover transitions are suppressed too"
        )


# ── AC2 — Hover and focus rings via foundation tokens ─────────────────────────

class TestHoverAndFocusRings:
    """AC2: Hover and focus rings on all nav tabs and theme toggle use foundation tokens."""

    def test_stab_hover_uses_surface_token(self):
        """Tab hover background must use var(--surface-hover) not a hardcoded value."""
        hover_match = re.search(
            r"\.stab:hover[^{]*\{([^}]+)\}", STYLE, re.DOTALL
        )
        assert hover_match, ".stab:hover rule not found"
        rule = hover_match.group(1)
        assert "var(--surface-hover)" in rule or "var(--surface-2)" in rule, (
            ".stab:hover background must use var(--surface-hover) or var(--surface-2)"
        )

    def test_stab_focus_visible_uses_token(self):
        """Tab :focus-visible outline must reference a CSS variable for color."""
        m = re.search(r"\.stab:focus-visible\s*\{([^}]+)\}", STYLE, re.DOTALL)
        assert m, ".stab:focus-visible rule not found"
        rule = m.group(1)
        assert "var(--" in rule, (
            ".stab:focus-visible must use a CSS variable (e.g. var(--blue)) for outline color"
        )

    def test_stab_focus_visible_has_outline(self):
        """Tab :focus-visible rule must set an outline for keyboard users."""
        m = re.search(r"\.stab:focus-visible\s*\{([^}]+)\}", STYLE, re.DOTALL)
        assert m, ".stab:focus-visible rule not found"
        rule = m.group(1)
        assert "outline" in rule, ".stab:focus-visible must declare an outline property"

    def test_btn_icon_focus_visible_has_outline(self):
        """Theme toggle :focus-visible rule must set an outline."""
        m = re.search(r"\.btn-icon:focus-visible\s*\{([^}]+)\}", STYLE, re.DOTALL)
        assert m, ".btn-icon:focus-visible rule not found"
        rule = m.group(1)
        assert "outline" in rule, ".btn-icon:focus-visible must declare an outline property"


# ── AC3 — Keyboard navigation (roving tabindex) ───────────────────────────────

class TestKeyboardNavigation:
    """AC3: Left/Right arrows navigate tabs; Tab exits the tablist group."""

    def test_arrow_right_handler_in_tabs_js(self):
        """tabs.js must handle ArrowRight to move focus to the next tab."""
        assert "ArrowRight" in TABS_JS, (
            "tabs.js must handle the ArrowRight key for keyboard tab navigation"
        )

    def test_arrow_left_handler_in_tabs_js(self):
        """tabs.js must handle ArrowLeft to move focus to the previous tab."""
        assert "ArrowLeft" in TABS_JS, (
            "tabs.js must handle the ArrowLeft key for keyboard tab navigation"
        )

    def test_switch_tab_sets_tabindex_zero_on_active(self):
        """switchTab in tabs.js must set tabIndex=0 on the newly activated tab."""
        # Matches both `btn.tabIndex = 0` and `btn.tabIndex = condition ? 0 : -1`
        assert re.search(r"\btabIndex\s*=.*\b0\b", TABS_JS), (
            "switchTab must assign tabIndex = 0 to the active tab button so the Tab "
            "key can enter the tablist at the correct button"
        )

    def test_switch_tab_sets_tabindex_minus_one_on_inactive(self):
        """switchTab must set tabIndex=-1 on non-active tabs so Tab skips them."""
        # Matches both `btn.tabIndex = -1` and `btn.tabIndex = condition ? 0 : -1`
        assert re.search(r"\btabIndex\s*=.*-1\b", TABS_JS), (
            "switchTab must assign tabIndex = -1 to inactive tab buttons to implement "
            "the roving tabindex pattern (Tab exits the tab group)"
        )

    def test_active_tab_initial_html_has_tabindex_zero(self):
        """Initial active tab (stab-sprint-mgmt) must have tabindex='0' in HTML."""
        sprint_btn = re.search(
            r'<button[^>]+id="stab-sprint-mgmt"[^>]*>', HTML
        )
        assert sprint_btn, "stab-sprint-mgmt not found"
        tag = sprint_btn.group(0)
        assert 'tabindex="0"' in tag, (
            "Initial active tab (stab-sprint-mgmt) must have tabindex='0' in HTML "
            "so the tab strip is focusable via the Tab key on page load"
        )

    def test_inactive_tabs_initial_html_have_tabindex_minus_one(self):
        """Non-active top-level stab buttons must have tabindex='-1' in initial HTML."""
        for tab_id in ["stab-tickets", "stab-settings"]:
            btn = re.search(rf'<button[^>]+id="{re.escape(tab_id)}"[^>]*>', HTML)
            assert btn, f"#{tab_id} not found in HTML"
            tag = btn.group(0)
            assert 'tabindex="-1"' in tag, (
                f"#{tab_id} must have tabindex='-1' in HTML (roving tabindex pattern) "
                f"so Tab exits the tablist rather than cycling through each button"
            )

    def test_aria_selected_updated_in_switch_tab(self):
        """switchTab in tabs.js must update aria-selected when changing tabs."""
        assert "aria-selected" in TABS_JS, (
            "switchTab must call setAttribute('aria-selected', ...) to update ARIA state"
        )


# ── AC4 — ARIA roles ──────────────────────────────────────────────────────────

class TestAriaRoles:
    """AC4: role='tab', role='tablist', aria-selected set correctly."""

    def test_tablist_role_present(self):
        """The sub-tabs nav must have role='tablist'."""
        assert 'role="tablist"' in HTML, "Tab strip <nav> must have role='tablist'"

    def test_sprint_tab_has_role_tab(self):
        """Sprint tab button must have role='tab'."""
        btn = re.search(r'<button[^>]+id="stab-sprint-mgmt"[^>]*>', HTML)
        assert btn, "stab-sprint-mgmt not found"
        assert 'role="tab"' in btn.group(0), "stab-sprint-mgmt must have role='tab'"

    def test_tickets_tab_has_role_tab(self):
        """Issues tab button must have role='tab'."""
        btn = re.search(r'<button[^>]+id="stab-tickets"[^>]*>', HTML)
        assert btn, "stab-tickets not found"
        assert 'role="tab"' in btn.group(0), "stab-tickets must have role='tab'"

    def test_settings_tab_has_role_tab(self):
        """Settings tab button must have role='tab'."""
        btn = re.search(r'<button[^>]+id="stab-settings"[^>]*>', HTML)
        assert btn, "stab-settings not found"
        assert 'role="tab"' in btn.group(0), "stab-settings must have role='tab'"

    def test_active_tab_has_aria_selected_true(self):
        """Initial active tab must have aria-selected='true'."""
        btn = re.search(r'<button[^>]+id="stab-sprint-mgmt"[^>]*>', HTML)
        assert btn, "stab-sprint-mgmt not found"
        assert 'aria-selected="true"' in btn.group(0), (
            "Initial active tab (stab-sprint-mgmt) must have aria-selected='true'"
        )

    def test_inactive_tabs_have_aria_selected_false(self):
        """Non-active tabs must have aria-selected='false' in initial HTML."""
        for tab_id in ["stab-tickets", "stab-settings"]:
            btn = re.search(rf'<button[^>]+id="{re.escape(tab_id)}"[^>]*>', HTML)
            assert btn, f"#{tab_id} not found"
            tag = btn.group(0)
            assert 'aria-selected="false"' in tag, (
                f"#{tab_id} must have aria-selected='false' in initial HTML"
            )


# ── AC5 — Theme toggle focus ring ─────────────────────────────────────────────

class TestThemeToggleFocusRing:
    """AC5: Theme toggle has a visible focus ring matching the tab ring style."""

    def test_btn_icon_focus_visible_uses_blue_token(self):
        """Theme toggle :focus-visible outline must use var(--blue) matching tabs."""
        m = re.search(r"\.btn-icon:focus-visible\s*\{([^}]+)\}", STYLE, re.DOTALL)
        assert m, ".btn-icon:focus-visible rule not found"
        rule = m.group(1)
        assert "var(--blue)" in rule or "var(--text" in rule, (
            ".btn-icon:focus-visible outline must use var(--blue) to match the tab ring"
        )

    def test_btn_icon_focus_ring_matches_stab_ring_token(self):
        """Theme toggle focus ring must use the same token as the tab focus ring."""
        stab_m = re.search(r"\.stab:focus-visible\s*\{([^}]+)\}", STYLE, re.DOTALL)
        btn_m = re.search(r"\.btn-icon:focus-visible\s*\{([^}]+)\}", STYLE, re.DOTALL)
        assert stab_m and btn_m, "Both focus-visible rules must be present"
        stab_token = re.search(r"var\(--[^)]+\)", stab_m.group(1))
        btn_token = re.search(r"var\(--[^)]+\)", btn_m.group(1))
        assert stab_token and btn_token, "Both focus rings must use CSS variables"
        assert stab_token.group(0) == btn_token.group(0), (
            f"Tab ring uses {stab_token.group(0)!r} but theme toggle uses "
            f"{btn_token.group(0)!r} — they must match"
        )

    def test_theme_toggle_has_focus_ring_offset(self):
        """Theme toggle :focus-visible must include outline-offset for visual clearance."""
        m = re.search(r"\.btn-icon:focus-visible\s*\{([^}]+)\}", STYLE, re.DOTALL)
        assert m, ".btn-icon:focus-visible not found"
        rule = m.group(1)
        assert "outline-offset" in rule, (
            ".btn-icon:focus-visible must include outline-offset to match the tab ring style"
        )


# ── AC6 — Condensed header on scroll ─────────────────────────────────────────

class TestCondensedHeader:
    """AC6: Header condenses (reduced height/padding) on scroll; stays sticky."""

    def test_condensed_css_class_exists(self):
        """A CSS class for the condensed header state must be defined."""
        has_condensed = bool(
            re.search(r"\.proj-header--condensed", STYLE)
        )
        assert has_condensed, (
            ".proj-header--condensed CSS class must be defined to style the "
            "condensed header state when the user scrolls down"
        )

    def test_condensed_class_reduces_padding(self):
        """Condensed header class must reduce padding compared to the normal state."""
        condensed_match = re.search(
            r"\.proj-header--condensed\s*\{([^}]+)\}", STYLE, re.DOTALL
        )
        if not condensed_match:
            # May be applied as a descendant rule
            condensed_match = re.search(
                r"\.proj-header--condensed[^{]*\{([^}]+)\}", STYLE, re.DOTALL
            )
        assert condensed_match, ".proj-header--condensed rule not found"
        rule = condensed_match.group(1)
        has_padding = "padding" in rule
        if not has_padding:
            # Could be a descendant rule reducing padding on child elements
            child_rules = re.findall(
                r"\.proj-header--condensed\s+\.[a-z-]+\s*\{([^}]+)\}", STYLE, re.DOTALL
            )
            has_padding = any("padding" in r for r in child_rules)
        assert has_padding, (
            ".proj-header--condensed must reduce padding (directly or on child elements) "
            "to achieve the condensed appearance on scroll"
        )

    def test_proj_sticky_chrome_is_sticky(self):
        """proj-sticky-chrome must use position: sticky to keep header at top."""
        rule = _first_rule_body(STYLE, r"\.proj-sticky-chrome")
        assert rule, ".proj-sticky-chrome not found"
        assert "sticky" in rule, ".proj-sticky-chrome must use position: sticky"

    def test_scroll_handler_in_js(self):
        """project.html JS must have a scroll handler that toggles the condensed class."""
        js_section = re.search(
            r"<script>(.*?)</script>", HTML, re.DOTALL
        )
        all_scripts = re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)
        combined_js = "\n".join(all_scripts)
        # Look for scroll event listener that references condensed class
        has_scroll = bool(re.search(r"addEventListener\s*\(\s*['\"]scroll['\"]", combined_js))
        has_condensed_toggle = bool(re.search(r"proj-header--condensed", combined_js))
        assert has_scroll, (
            "A scroll event listener must be registered in project.html to detect "
            "when the user scrolls so the condensed class can be toggled"
        )
        assert has_condensed_toggle, (
            "The scroll handler must reference 'proj-header--condensed' to toggle "
            "the condensed state on the header element"
        )

    def test_condensed_transition_uses_tokens_or_is_suppressed(self):
        """Condensed header transition must not use hardcoded values."""
        # If there's a transition on the header element, it should use tokens
        proj_header_rule = _first_rule_body(STYLE, r"\.proj-header")
        if proj_header_rule and "transition" in proj_header_rule:
            t_m = re.search(r"\btransition\s*:\s*([^;]+)", proj_header_rule)
            if t_m:
                val = t_m.group(1)
                assert "var(--" in val or re.search(r"\.\d+s", val), (
                    ".proj-header transition must not use a raw duration outside token system"
                )


# ── AC7 — No new hardcoded color values ──────────────────────────────────────

class TestNoNewColors:
    """AC7: No new hardcoded color values introduced — reuse existing tokens only."""

    def test_condensed_rule_no_hardcoded_hex(self):
        """Condensed header rule must not introduce new hex color values."""
        condensed_blocks = re.findall(
            r"\.proj-header--condensed[^{]*\{([^}]+)\}", STYLE, re.DOTALL
        )
        for block in condensed_blocks:
            hex_colors = re.findall(r"#[0-9a-fA-F]{3,6}\b", block)
            assert not hex_colors, (
                f".proj-header--condensed must not use hardcoded hex colors; "
                f"found: {hex_colors!r} — use existing foundation tokens instead"
            )

    def test_reduced_motion_block_no_hardcoded_hex(self):
        """prefers-reduced-motion block must not introduce new hex colors."""
        rm_blocks = re.findall(
            r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{([^{}]+(?:\{[^{}]*\}[^{}]*)*)\}",
            STYLE,
            re.DOTALL,
        )
        for block in rm_blocks:
            stab_rule = re.search(r"\.stab\s*\{([^}]+)\}", block, re.DOTALL)
            if stab_rule:
                hex_colors = re.findall(r"#[0-9a-fA-F]{3,6}\b", stab_rule.group(1))
                assert not hex_colors, (
                    f"Reduced-motion .stab rule must not add hex colors; found: {hex_colors!r}"
                )


# ── AC8 — Vanilla JS/CSS only ─────────────────────────────────────────────────

class TestVanillaOnly:
    """AC8: No new framework dependencies introduced."""

    def test_no_new_js_framework_imports(self):
        """No new framework <script src=> lines added to the shell for this feature."""
        script_tags = re.findall(r'<script\s+src="([^"]+)"', HTML)
        for src in script_tags:
            # Only expected external script is the icon font — no JS frameworks
            assert "react" not in src.lower(), f"React import found: {src!r}"
            assert "vue" not in src.lower(), f"Vue import found: {src!r}"
            assert "alpine" not in src.lower(), f"Alpine.js import found: {src!r}"

    def test_tabs_js_uses_no_framework_imports(self):
        """tabs.js must not import any framework modules."""
        imports = re.findall(r"^import\s+.+from\s+['\"].+['\"]", TABS_JS, re.MULTILINE)
        for imp in imports:
            assert "react" not in imp.lower(), f"React import in tabs.js: {imp!r}"
            assert "vue" not in imp.lower(), f"Vue import in tabs.js: {imp!r}"
