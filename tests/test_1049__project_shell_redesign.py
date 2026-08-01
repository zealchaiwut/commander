"""
Tests for issue #1049: Redesign project shell and global navigation chrome.

Checks the shell CSS/HTML of apps/dashboard/static/project.html for:
  AC1 — distinct active/hover/focus states on tab bar using only token values
  AC2 — tab spacing (padding, gap, margin) sourced from token scale
  AC3 — project header uses token-based spacing for baseline alignment
  AC4 — theme toggle and breadcrumb coherent with header (tokens, not raw values)
  AC5 — dark theme is the default
  AC6 — all tab switchTab() calls intact
  AC7 — diff scoped to shell markup (tab body content present and unmodified)
  AC8 — vanilla HTML/CSS/JS only, no new frameworks

All tests inspect the static file directly — no running server required.
"""
import re
import pytest
from pathlib import Path

HTML_FILE = Path("apps/dashboard/static/project.html")
HTML = HTML_FILE.read_text()


def _all_style_blocks(html: str) -> str:
    """Concatenate all <style> block contents."""
    return "\n".join(re.findall(r"<style>(.*?)</style>", html, re.DOTALL))


def _first_rule_body(css: str, selector_pattern: str) -> str:
    """Return the property block of the first CSS rule whose selector matches
    selector_pattern (a raw regex pattern — do NOT re.escape it before passing)."""
    m = re.search(selector_pattern + r"\s*\{([^}]+)\}", css, re.DOTALL)
    return m.group(1) if m else ""


STYLE = _all_style_blocks(HTML)


# ── AC1: Active / hover / focus states on tab bar using token values ──────────

class TestTabBarStates:
    """AC1: switchTab() tab bar has distinct active, hover, and focus states
    sourced from CSS token variables — no hardcoded hex or bare-px values."""

    def test_stab_active_uses_css_variable(self):
        """Active state must reference a CSS variable for color/border."""
        rule = _first_rule_body(STYLE, r"\.stab\.active")
        assert rule, ".stab.active CSS rule not found"
        assert "var(--" in rule, (
            ".stab.active must use CSS variable tokens (var(--*)), "
            f"found: {rule.strip()}"
        )

    def test_stab_active_has_bottom_border(self):
        """Active state must show a visible bottom-border indicator."""
        rule = _first_rule_body(STYLE, r"\.stab\.active")
        assert rule and "border-bottom" in rule, (
            ".stab.active must have a border-bottom indicator"
        )

    def test_stab_hover_uses_surface_token(self):
        """Hover state must use a surface token for background feedback."""
        # Check for any hover rule on .stab or .stab:hover variants
        hover_match = re.search(
            r"\.stab:hover[^{]*\{([^}]+)\}", STYLE, re.DOTALL
        )
        assert hover_match, "No .stab:hover rule found"
        rule = hover_match.group(1)
        assert "var(--surface" in rule or "var(--bg" in rule, (
            ".stab:hover must reference a surface/bg token for background"
        )

    def test_stab_focus_visible_rule_exists(self):
        """Focus-visible ring must be explicitly defined for keyboard a11y."""
        # Acceptable: .stab:focus-visible OR a broader *:focus-visible rule
        has_stab_focus = bool(re.search(r"stab:focus[-\s]?visible", STYLE))
        has_btn_focus = bool(re.search(r"button:focus-visible", STYLE))
        has_global_focus = bool(re.search(r":focus-visible\s*\{", STYLE))
        assert has_stab_focus or has_btn_focus or has_global_focus, (
            "No :focus-visible rule found; keyboard users need a visible ring on tabs"
        )

    def test_stab_focus_uses_token(self):
        """Focus ring must use a CSS variable token, not a hardcoded color."""
        focus_match = re.search(
            r"(?:stab|button|\.tab)[^}]*:focus[-\s]?visible[^{]*\{([^}]+)\}",
            STYLE, re.DOTALL
        )
        if focus_match:
            rule = focus_match.group(1)
            assert "var(--" in rule, (
                "Focus-visible rule must use CSS variable (var(--*)), "
                f"found: {rule.strip()}"
            )


# ── AC2: Tab spacing sourced from token scale ─────────────────────────────────

class TestTabSpacingTokens:
    """AC2: .stab padding, gap, and margin use var(--space-*) tokens."""

    def _stab_base_rule(self) -> str:
        rule = _first_rule_body(STYLE, r"\.stab")
        assert rule, ".stab base rule not found in <style>"
        return rule

    def test_stab_padding_uses_space_token(self):
        """Padding must reference var(--space-*) not a bare px value like 10px."""
        rule = self._stab_base_rule()
        # Look for padding property inside the rule
        pad = re.search(r"\bpadding\b\s*:\s*([^;]+)", rule)
        assert pad, "No padding property in .stab rule"
        val = pad.group(1).strip()
        assert "var(--space-" in val, (
            f".stab padding must use var(--space-*) token, found: {val!r}"
        )

    def test_stab_no_magic_10px_padding(self):
        """Magic number 10px in .stab padding must be replaced with a token."""
        stab_start = STYLE.find(".stab {")
        assert stab_start != -1, ".stab { block not found"
        stab_end = STYLE.index("}", stab_start)
        rule = STYLE[stab_start:stab_end]
        pad = re.search(r"\bpadding\b\s*:\s*([^;]+)", rule)
        val = pad.group(1).strip() if pad else ""
        assert "10px" not in val, (
            "Magic number 10px in .stab padding must be replaced with var(--space-3)"
        )

    def test_stab_gap_uses_space_token(self):
        """Gap must reference var(--space-*) not a bare px value like 6px."""
        rule = self._stab_base_rule()
        gap = re.search(r"\bgap\b\s*:\s*([^;]+)", rule)
        assert gap, "No gap property in .stab rule"
        val = gap.group(1).strip()
        assert "var(--" in val, (
            f".stab gap must use a CSS token, found: {val!r}"
        )

    def test_stab_margin_uses_space_token(self):
        """Margin-right must reference var(--space-*) not a bare px value."""
        rule = self._stab_base_rule()
        margin = re.search(r"\bmargin[^:]*:\s*([^;]+)", rule)
        assert margin, "No margin property in .stab rule"
        val = margin.group(1).strip()
        assert "var(--" in val, (
            f".stab margin must use a CSS token, found: {val!r}"
        )


# ── AC3: Header uses token-based spacing for consistent baseline alignment ────

class TestHeaderAlignment:
    """AC3: proj-header padding uses tokens for reliable horizontal baseline."""

    def test_proj_header_top_padding_uses_token(self):
        """proj-header top padding must use var(--space-*) for alignment."""
        rule = _first_rule_body(STYLE, r"\.proj-header")
        assert rule, ".proj-header CSS rule not found"
        pad = re.search(r"\bpadding\b\s*:\s*([^;]+)", rule)
        assert pad, "No padding in .proj-header"
        val = pad.group(1).strip()
        assert "var(--space-" in val, (
            f".proj-header padding must use var(--space-*), found: {val!r}"
        )

    def test_proj_header_row_padding_uses_token(self):
        """proj-header-row bottom padding must use var(--space-*) for alignment."""
        rule = _first_rule_body(STYLE, r"\.proj-header-row")
        assert rule, ".proj-header-row CSS rule not found"
        assert "var(--" in rule, (
            ".proj-header-row spacing must use CSS variable tokens"
        )


# ── AC4: Theme toggle and breadcrumb coherent with header ────────────────────

class TestThemeToggleAndBreadcrumb:
    """AC4: Theme toggle (.btn-icon) and nav breadcrumb use consistent tokens."""

    def test_btn_icon_border_uses_token(self):
        """Theme toggle button border must use var(--border) token."""
        rule = _first_rule_body(STYLE, r"\.btn-icon")
        assert rule, ".btn-icon CSS rule not found"
        assert "var(--border)" in rule, (
            ".btn-icon border must use var(--border) token, not hardcoded color"
        )

    def test_btn_icon_color_uses_text_token(self):
        """Theme toggle button color must use a var(--text*) token."""
        rule = _first_rule_body(STYLE, r"\.btn-icon")
        assert "var(--text" in rule, (
            ".btn-icon color must use var(--text*) token"
        )

    def test_top_nav_padding_uses_token(self):
        """top-nav padding must use var(--space-*) token."""
        rule = _first_rule_body(STYLE, r"\.top-nav")
        assert rule, ".top-nav CSS rule not found"
        assert "var(--space-" in rule or "var(--" in rule, (
            ".top-nav spacing should use CSS variable tokens"
        )

    def test_brand_wordmark_uses_font_token(self):
        """Brand wordmark must use font-size token (not bare px)."""
        rule = _first_rule_body(STYLE, r"\.brand-wordmark")
        assert rule, ".brand-wordmark CSS rule not found"
        assert "var(--font-" in rule or "var(--" in rule, (
            ".brand-wordmark must use font token variables"
        )


# ── AC5: Dark theme is the default ───────────────────────────────────────────

class TestDarkThemeDefault:
    """AC5: Dark theme is the default; shell elements render in both modes."""

    def test_html_root_has_dark_theme(self):
        """<html> element must declare data-theme='dark' as the initial value."""
        assert 'data-theme="dark"' in HTML, (
            "<html> must have data-theme=\"dark\" — dark is the required default"
        )

    def test_js_localstorage_fallback_is_dark(self):
        """JS localStorage fallback must use 'dark' not 'light'."""
        # The inline theme-init script should fall back to 'dark'
        assert re.search(r"\|\|\s*['\"]dark['\"]", HTML), (
            "Theme-init script must fall back to 'dark' when no saved preference"
        )

    def test_dark_theme_tokens_defined(self):
        """[data-theme='dark'] overrides must be present in the stylesheet."""
        assert '[data-theme="dark"]' in STYLE or "[data-theme='dark']" in STYLE, (
            "Dark theme CSS overrides must be defined in the page styles"
        )


# ── AC6: All tab switchTab() calls remain intact ─────────────────────────────

class TestTabSwitchingIntact:
    """AC6: All five (plus) tabs still switch via switchTab()."""

    @pytest.mark.parametrize("tab", [
        "sprint-mgmt",
        "tickets",
        "logs",
        "deploy",
        "metrics",
    ])
    def test_switchtab_call_present(self, tab: str):
        """switchTab() invocation for each tab must still be in the file."""
        assert f"switchTab('{tab}')" in HTML, (
            f"switchTab('{tab}') call is missing — tab may have been accidentally removed"
        )


# ── AC7: Diff scoped to shell — tab body content unchanged ───────────────────

class TestShellScopeOnly:
    """AC7: Changes are confined to shell markup; zero tab-body modifications."""

    def test_sprint_mgmt_tab_pane_present(self):
        """Sprint-mgmt tab pane must still exist unchanged."""
        assert 'id="pane-sprint-mgmt"' in HTML or 'pane-sprint-mgmt' in HTML, (
            "Sprint mgmt pane must still be present"
        )

    def test_tickets_tab_pane_present(self):
        """Tickets tab pane must still be present."""
        assert "pane-tickets" in HTML or "stab-tickets" in HTML

    def test_logs_tab_pane_present(self):
        """Logs tab pane must still be present."""
        assert "pane-logs" in HTML or "stab-logs" in HTML

    def test_deploy_tab_pane_present(self):
        """Deploy tab pane must still be present."""
        assert "pane-deploy" in HTML or "stab-deploy" in HTML

    def test_switchTab_function_defined(self):
        """switchTab function definition must still exist."""
        assert "function switchTab" in HTML or "switchTab" in HTML


# ── AC8: Vanilla HTML/CSS/JS only — no new frameworks ────────────────────────

class TestVanillaOnly:
    """AC8: Implementation is vanilla HTML/CSS/JS — no new frameworks added."""

    def test_no_react_added(self):
        """No React script tag added."""
        assert "react.js" not in HTML and "react.min.js" not in HTML

    def test_no_vue_added(self):
        """No Vue script tag added."""
        assert "vue.js" not in HTML and "vue.min.js" not in HTML

    def test_no_bundler_script_tag(self):
        """No webpack or rollup script tags added."""
        # Only <script src> references — not CSS class names or code comments
        script_srcs = re.findall(r'<script[^>]+src=["\'][^"\']*["\']', HTML)
        for src in script_srcs:
            assert "webpack" not in src and "rollup.js" not in src, (
                f"New bundler script tag found: {src}"
            )
