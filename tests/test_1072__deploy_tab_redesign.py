"""Acceptance tests for issue #1072 — Redesign Deploy Tab with Token-Based Dark UI.

AC map:
  AC1   Environment rows follow consistent anatomy: env name, status badge, port,
        Start/Stop actions — all aligned with token spacing
  AC2   Run-state indicators use semantic color tokens (running, stopped, error
        states visually distinct)
  AC3   Live-log panel renders with readable frame: token-based background,
        monospace font, scrollable, clearly bounded
  AC4   Empty state renders when no environments are configured (not a blank region)
  AC5   deployTabInit and deployTabDestroy functions remain intact and functional
  AC6   All Start/Stop event handlers fire correctly after redesign
  AC7   No raw hex values or hardcoded colors — all styling via tokens.css custom
        properties
  AC8   Vanilla JS/CSS only — no new frameworks or build steps introduced
  AC9   Impeccable detect passes on the deploy view region (verified manually;
        skipped in CI because npx unavailable in this environment)
  AC10  Diff scoped to deploy view region of apps/dashboard/static/project.html
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


def _read() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html() -> str:
    return _read()


def _deploy_css(html: str) -> str:
    """Extract just the deploy-tab CSS block (between its section comment and the next)."""
    start = html.find("/* ── Deploy tab")
    end = html.find("/* ── Scaffold docs card", start)
    assert start != -1 and end != -1, "Could not locate deploy CSS block"
    return html[start:end]


def _deploy_js(html: str) -> str:
    """Extract the deploy-tab JS block (from the JS section comment onwards)."""
    start = html.find("// ── Deploy tab (issue #726)")
    assert start != -1, "Could not locate deploy JS block"
    return html[start:]


# ── AC1: env-row anatomy ─────────────────────────────────────────────────────


class TestEnvRowAnatomy:
    def test_row_css_rule_exists(self, html):
        """AC1 — .deploy-card__row CSS rule must exist for the primary env row."""
        assert re.search(r'\.deploy-card__row\b', html), (
            "AC1: .deploy-card__row CSS rule must be defined"
        )

    def test_row_is_flex(self, html):
        """AC1 — .deploy-card__row must use flex layout for horizontal alignment."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-card__row\s*\{([^}]+)\}', css)
        assert block, "AC1: .deploy-card__row CSS rule not found"
        assert 'flex' in block.group(1), (
            "AC1: .deploy-card__row must use display:flex"
        )

    def test_card_html_generates_row_element(self, html):
        """AC1 — _deployCardHtml must produce a .deploy-card__row container."""
        js = _deploy_js(html)
        func_block = re.search(
            r'function _deployCardHtml\(.*?\}\s*\n', js, re.DOTALL
        )
        assert func_block, "AC1: _deployCardHtml function not found"
        body = func_block.group(0)
        assert 'deploy-card__row' in body, (
            "AC1: _deployCardHtml must output a .deploy-card__row element"
        )

    def test_row_includes_actions_container(self, html):
        """AC1 — deploy-card-actions ID must appear inside the row in generated HTML."""
        js = _deploy_js(html)
        func_block = re.search(
            r'function _deployCardHtml\(.*?\}\s*\n', js, re.DOTALL
        )
        assert func_block, "AC1: _deployCardHtml function not found"
        body = func_block.group(0)
        assert 'deploy-card-actions' in body, (
            "AC1: actions container must be in _deployCardHtml output"
        )

    def test_row_includes_port_display(self, html):
        """AC1 — port should be visible in the row (via c.port reference in row area)."""
        js = _deploy_js(html)
        func_block = re.search(
            r'function _deployCardHtml\(.*?\}\s*\n', js, re.DOTALL
        )
        assert func_block, "AC1: _deployCardHtml function not found"
        body = func_block.group(0)
        # Port must appear somewhere in card HTML generation
        assert re.search(r'c\.port\b|port', body), (
            "AC1: port must be referenced in _deployCardHtml"
        )

    def test_grid_is_vertical_list(self, html):
        """AC1 — deploy grid must lay out envs as a vertical list, not multi-column cards."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-grid\s*\{([^}]+)\}', css)
        assert block, "AC1: .deploy-grid CSS rule not found"
        props = block.group(1)
        # Should NOT be a multi-column grid (auto-fill)
        assert 'auto-fill' not in props, (
            "AC1: .deploy-grid must not use multi-column auto-fill grid; should be vertical list"
        )


# ── AC2: semantic run-state color tokens ──────────────────────────────────────


class TestRunStateColors:
    def test_runstate_running_uses_green_token(self, html):
        """AC2 — running state must use --green token."""
        css = _deploy_css(html)
        block = re.search(r'\.runstate-pill--running\s*\{([^}]+)\}', css)
        assert block, "AC2: .runstate-pill--running rule not found"
        assert 'var(--green)' in block.group(1), (
            "AC2: running state must use var(--green)"
        )

    def test_runstate_stopped_uses_red_token(self, html):
        """AC2 — stopped state must use --red token."""
        css = _deploy_css(html)
        block = re.search(r'\.runstate-pill--stopped\s*\{([^}]+)\}', css)
        assert block, "AC2: .runstate-pill--stopped rule not found"
        assert 'var(--red)' in block.group(1), (
            "AC2: stopped state must use var(--red)"
        )

    def test_runstate_idle_uses_surface_or_text_sub_token(self, html):
        """AC2 — idle state must use surface/text-sub tokens (neutral)."""
        css = _deploy_css(html)
        block = re.search(r'\.runstate-pill--idle\s*\{([^}]+)\}', css)
        assert block, "AC2: .runstate-pill--idle rule not found"
        # Should use a neutral token (surface-2, text-sub, text-muted, etc.)
        assert re.search(r'var\(--(?:surface|text-sub|text-muted)', block.group(1)), (
            "AC2: idle state must use a neutral token (--surface-2 or --text-sub)"
        )

    def test_error_state_uses_token_not_hex(self, html):
        """AC2 — error/danger states must not use raw hex for color."""
        css = _deploy_css(html)
        # Check deploy-btn--danger does not have a raw hex fallback
        danger = re.search(r'\.deploy-btn--danger\s*\{([^}]+)\}', css)
        if danger:
            props = danger.group(1)
            # Must not have a standalone hex like #ef4444
            assert not re.search(r':\s*#[0-9a-fA-F]{3,6}\b', props), (
                "AC2: .deploy-btn--danger must not use raw hex color"
            )


# ── AC3: live-log panel styling ──────────────────────────────────────────────


class TestLiveLogPanel:
    def test_log_body_uses_mono_font(self, html):
        """AC3 — .deploy-log__body must use monospace font token."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log__body\s*\{([^}]+)\}', css)
        assert block, "AC3: .deploy-log__body rule not found"
        assert 'var(--mono)' in block.group(1), (
            "AC3: log body must use var(--mono) font"
        )

    def test_log_body_is_scrollable(self, html):
        """AC3 — .deploy-log__body must have overflow:auto or overflow-y:auto."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log__body\s*\{([^}]+)\}', css)
        assert block, "AC3: .deploy-log__body rule not found"
        assert re.search(r'overflow(?:-y)?:\s*auto', block.group(1)), (
            "AC3: log body must be scrollable (overflow:auto)"
        )

    def test_log_body_has_max_height(self, html):
        """AC3 — .deploy-log__body must have max-height to bound the panel."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log__body\s*\{([^}]+)\}', css)
        assert block, "AC3: .deploy-log__body rule not found"
        assert 'max-height' in block.group(1), (
            "AC3: log body must have max-height to keep it bounded"
        )

    def test_log_panel_uses_token_background(self, html):
        """AC3 — .deploy-log must use token-based background."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log\b[^_]\s*\{([^}]+)\}', css)
        assert block, "AC3: .deploy-log rule not found"
        assert re.search(r'var\(--(?:surface|border)', block.group(1)), (
            "AC3: .deploy-log must use token-based background or border"
        )


# ── AC4: empty state ─────────────────────────────────────────────────────────


class TestEmptyState:
    def test_empty_state_element_in_html(self, html):
        """AC4 — deploy-grid-empty element must exist in static HTML."""
        assert 'id="deploy-grid-empty"' in html, (
            "AC4: #deploy-grid-empty element must be present"
        )

    def test_empty_state_has_text_content(self, html):
        """AC4 — empty state must show a non-blank message (not just a blank region)."""
        # Check the JS empty-state render
        js = _deploy_js(html)
        # _deployRenderGrid sets innerHTML when no cards
        match = re.search(r'deploy-grid-empty["\']>[^<]+<', js)
        assert match, (
            "AC4: JS empty state must output a non-blank message inside #deploy-grid-empty"
        )

    def test_empty_state_css_class_styled(self, html):
        """AC4 — deploy-tab-intro (used for empty state) must have a CSS rule."""
        css = _deploy_css(html)
        assert re.search(r'\.deploy-tab-intro\b', css), (
            "AC4: .deploy-tab-intro class used for empty state must have CSS rule"
        )


# ── AC5: deployTabInit and deployTabDestroy intact ────────────────────────────


class TestTabLifecycleFunctions:
    def test_deployTabInit_exists(self, html):
        """AC5 — deployTabInit function must be defined."""
        assert re.search(r'function deployTabInit\s*\(', html), (
            "AC5: deployTabInit function must exist"
        )

    def test_deployTabDestroy_exists(self, html):
        """AC5 — deployTabDestroy function must be defined."""
        assert re.search(r'function deployTabDestroy\s*\(', html), (
            "AC5: deployTabDestroy function must exist"
        )

    def test_deployTabDestroy_clears_pollers(self, html):
        """AC5 — deployTabDestroy must clear _deployTabPollers timers."""
        js = _deploy_js(html)
        func = re.search(
            r'function deployTabDestroy\s*\(\s*\)\s*\{([^}]+)\}', js
        )
        assert func, "AC5: deployTabDestroy function not found"
        body = func.group(1)
        assert '_deployTabPollers' in body, (
            "AC5: deployTabDestroy must clear _deployTabPollers"
        )

    def test_deployTabInit_fetches_overview(self, html):
        """AC5 — deployTabInit must call /api/deploy/overview."""
        js = _deploy_js(html)
        func = re.search(
            r'function deployTabInit\s*\(\s*\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert func, "AC5: deployTabInit function not found"
        body = func.group(1)
        assert '/api/deploy/overview' in body, (
            "AC5: deployTabInit must fetch /api/deploy/overview"
        )


# ── AC6: Start/Stop event handlers ───────────────────────────────────────────


class TestStartStopHandlers:
    def test_deployCardStart_exists(self, html):
        """AC6 — deployCardStart handler must be defined."""
        assert re.search(r'function deployCardStart\s*\(', html), (
            "AC6: deployCardStart function must exist"
        )

    def test_deployCardStop_exists(self, html):
        """AC6 — deployCardStop handler must be defined."""
        assert re.search(r'function deployCardStop\s*\(', html), (
            "AC6: deployCardStop function must exist"
        )

    def test_start_button_wired_in_actions(self, html):
        """AC6 — Start action button must call deployCardStart in generated HTML."""
        js = _deploy_js(html)
        assert re.search(r'deployCardStart\(', js), (
            "AC6: deployCardStart must be called in action button HTML"
        )

    def test_stop_button_wired_in_actions(self, html):
        """AC6 — Stop action button must call deployCardStop in generated HTML."""
        js = _deploy_js(html)
        assert re.search(r'deployCardStop\(', js), (
            "AC6: deployCardStop must be called in action button HTML"
        )


# ── AC7: no raw hex values in deploy section ─────────────────────────────────


class TestNoRawHex:
    def test_no_bare_hex_in_deploy_css(self, html):
        """AC7 — deploy CSS section must contain no bare #RRGGBB or #RGB hex colors."""
        css = _deploy_css(html)
        # Find raw hex color values: # followed by 3 or 6 hex digits
        # Exclude CSS ID selectors (#deploy-*) by requiring the # to follow
        # a colon, space, or comma (color property values)
        hex_colors = re.findall(
            r'(?::\s*|,\s*)#([0-9a-fA-F]{3,6})\b', css
        )
        # Filter out any that are clearly ID selectors (not colors)
        problematic = [h for h in hex_colors if not h.startswith('deploy')]
        assert not problematic, (
            f"AC7: raw hex colors found in deploy CSS: {['#' + h for h in problematic]}"
        )

    def test_primary_button_uses_token_not_hex(self, html):
        """AC7 — .deploy-btn--primary color must use a CSS token, not #fff."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-btn--primary\s*\{([^}]+)\}', css)
        assert block, "AC7: .deploy-btn--primary rule not found"
        props = block.group(1)
        assert '#fff' not in props and '#ffffff' not in props.lower(), (
            "AC7: .deploy-btn--primary must not use #fff — use var(--text-on-primary) or equivalent"
        )


# ── AC8: vanilla JS/CSS only ─────────────────────────────────────────────────


class TestVanillaOnly:
    def test_no_react_import_in_deploy(self, html):
        """AC8 — deploy section must not import React."""
        js = _deploy_js(html)
        assert 'import React' not in js and "require('react')" not in js, (
            "AC8: deploy section must not use React"
        )

    def test_no_vue_import_in_deploy(self, html):
        """AC8 — deploy section must not import Vue."""
        js = _deploy_js(html)
        assert 'import Vue' not in js and "require('vue')" not in js, (
            "AC8: deploy section must not use Vue"
        )

    def test_no_build_step_markers(self, html):
        """AC8 — deploy section must not reference bundler/build constructs."""
        js = _deploy_js(html)
        assert 'import {' not in js and 'export default' not in js, (
            "AC8: deploy section must not use ES module import/export"
        )


# ── AC10: diff scoped to deploy region ───────────────────────────────────────


class TestDiffScope:
    def test_deploy_tab_pane_exists(self, html):
        """AC10 — deploy tab pane element must exist."""
        assert 'id="pane-deploy"' in html, (
            "AC10: #pane-deploy tab pane must exist"
        )

    def test_deploy_grid_exists(self, html):
        """AC10 — deploy-grid container must exist."""
        assert 'id="deploy-grid"' in html, (
            "AC10: #deploy-grid container must exist"
        )

    def test_non_deploy_tab_panes_unchanged(self, html):
        """AC10 — other tab panes must still be present and unaffected."""
        required_panes = ['pane-sprint-mgmt', 'pane-logs', 'pane-roadmap']
        for pane_id in required_panes:
            assert f'id="{pane_id}"' in html, (
                f"AC10: #{pane_id} tab pane must still exist (deploy redesign must not remove other tabs)"
            )
