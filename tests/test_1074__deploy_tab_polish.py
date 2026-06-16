"""Acceptance tests for issue #1074 — Polish Deploy Tab UI: States, Transitions, Accessibility.

AC map:
  AC1   Environment rows show distinct hover and keyboard-focus styles using foundation tokens
  AC2   Start/Stop buttons have clear pressed (:active) and disabled states
  AC3   Status-change transitions are subtle and respect prefers-reduced-motion
  AC4   Live-log panel auto-scrolls; user can scroll up to pause auto-scroll
  AC5   Loading state renders when deploy data is fetching
  AC6   Empty state renders with a helpful message
  AC7   All interactive elements reachable via Tab/Enter/Space (buttons are native <button>)
  AC8   Visible focus indicators meet WCAG 2.1 AA contrast on dark theme
  AC9   ARIA labels on Start/Stop buttons and status indicators
  AC10  detect passes (skipped in CI — npx unavailable)
  AC11  No new foundation tokens introduced
  AC12  Changes scoped to deploy view region only
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _deploy_css(html: str) -> str:
    """Extract the deploy-tab CSS block."""
    start = html.find("/* ── Deploy tab")
    end = html.find("/* ── Scaffold docs card", start)
    assert start != -1 and end != -1, "Could not locate deploy CSS block"
    return html[start:end]


def _deploy_js(html: str) -> str:
    """Extract the deploy-tab JS block."""
    start = html.find("// ── Deploy tab (issue #726)")
    assert start != -1, "Could not locate deploy JS block"
    return html[start:]


# ── AC1: Hover and focus-within styles on environment rows ───────────────────


class TestEnvRowInteractiveStyles:
    def test_row_hover_rule_exists(self, html):
        """AC1 — .deploy-card__row:hover must define a visible background change."""
        css = _deploy_css(html)
        assert re.search(r'\.deploy-card__row:hover\s*\{', css), (
            "AC1: .deploy-card__row:hover CSS rule must exist"
        )

    def test_row_hover_uses_token(self, html):
        """AC1 — hover background must use a foundation token, not a raw color."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-card__row:hover\s*\{([^}]+)\}', css)
        assert block, "AC1: .deploy-card__row:hover rule not found"
        assert re.search(r'var\(--', block.group(1)), (
            "AC1: .deploy-card__row:hover must use a CSS token (var(--...))"
        )

    def test_row_focus_within_rule_exists(self, html):
        """AC1 — .deploy-card__row:focus-within must provide keyboard focus style."""
        css = _deploy_css(html)
        assert re.search(r'\.deploy-card__row:focus-within\s*\{', css), (
            "AC1: .deploy-card__row:focus-within CSS rule must exist for keyboard accessibility"
        )


# ── AC2: Button :active and disabled states ───────────────────────────────────


class TestButtonStates:
    def test_active_state_rule_exists(self, html):
        """AC2 — .deploy-btn:active must define a pressed visual state."""
        css = _deploy_css(html)
        assert re.search(r'\.deploy-btn:active\b', css), (
            "AC2: .deploy-btn:active CSS rule must exist for pressed state"
        )

    def test_active_state_not_disabled(self, html):
        """AC2 — :active rule must exclude disabled buttons (:not(:disabled))."""
        css = _deploy_css(html)
        assert re.search(r'\.deploy-btn:active:not\(:disabled\)', css), (
            "AC2: .deploy-btn:active:not(:disabled) must be the selector (disabled must not look pressed)"
        )

    def test_disabled_state_rule_exists(self, html):
        """AC2 — .deploy-btn:disabled must define a visually distinct disabled state."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-btn:disabled\s*\{([^}]+)\}', css)
        assert block, "AC2: .deploy-btn:disabled rule must exist"
        props = block.group(1)
        # Must have reduced opacity or pointer-events none to signal disabled
        assert re.search(r'opacity|pointer-events|cursor', props), (
            "AC2: .deploy-btn:disabled must visually indicate disabled state (opacity/cursor)"
        )

    def test_active_uses_token(self, html):
        """AC2 — :active pressed state must use foundation tokens only."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-btn:active:not\(:disabled\)\s*\{([^}]+)\}', css)
        assert block, "AC2: .deploy-btn:active:not(:disabled) rule not found"
        props = block.group(1)
        # Must not have a bare hex in the active state
        assert not re.search(r':\s*#[0-9a-fA-F]{3,6}\b', props), (
            "AC2: :active state must not use raw hex — use CSS tokens"
        )


# ── AC3: Status transitions + prefers-reduced-motion ─────────────────────────


class TestStatusTransitions:
    def test_status_pill_has_transition(self, html):
        """AC3 — .status-pill must declare a CSS transition for smooth state changes."""
        css = _deploy_css(html)
        block = re.search(r'\.status-pill\b[^_-][^{]*\{([^}]+)\}', css)
        assert block, "AC3: .status-pill base CSS rule not found"
        assert 'transition' in block.group(1), (
            "AC3: .status-pill must have a transition for smooth color/opacity fade"
        )

    def test_runstate_pill_has_transition(self, html):
        """AC3 — .runstate-pill must declare a CSS transition."""
        css = _deploy_css(html)
        block = re.search(r'\.runstate-pill\b[^-][^{]*\{([^}]+)\}', css)
        assert block, "AC3: .runstate-pill base CSS rule not found"
        assert 'transition' in block.group(1), (
            "AC3: .runstate-pill must have a transition for smooth state changes"
        )

    def test_prefers_reduced_motion_block_exists(self, html):
        """AC3 — a @media (prefers-reduced-motion: reduce) block must exist in deploy CSS."""
        css = _deploy_css(html)
        assert re.search(r'@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)', css), (
            "AC3: deploy CSS must include a prefers-reduced-motion block to respect user motion preferences"
        )

    def test_prefers_reduced_motion_suppresses_deploy_transitions(self, html):
        """AC3 — prefers-reduced-motion block must disable deploy transitions."""
        css = _deploy_css(html)
        # Locate the @media (prefers-reduced-motion...) rule directly
        block_start = css.find("@media (prefers-reduced-motion")
        assert block_start != -1, "AC3: @media (prefers-reduced-motion) rule not found in deploy CSS"
        # Brace-match to find the full block
        depth = 0
        pos = css.find("{", block_start)
        block_end = pos
        while pos < len(css):
            if css[pos] == "{":
                depth += 1
            elif css[pos] == "}":
                depth -= 1
                if depth == 0:
                    block_end = pos
                    break
            pos += 1
        block = css[block_start:block_end + 1]
        assert 'transition' in block or 'animation' in block, (
            "AC3: prefers-reduced-motion block must suppress transitions or animations in deploy section"
        )


# ── AC4: Auto-scroll with user-scroll-up detection ───────────────────────────


class TestAutoScroll:
    def test_log_render_respects_scroll_flag(self, html):
        """AC4 — _deployLogRender must check a user-scroll flag before auto-scrolling."""
        js = _deploy_js(html)
        func = re.search(
            r'function _deployLogRender\s*\(i\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert func, "AC4: _deployLogRender function not found"
        body = func.group(1)
        # Must check either st.userScrolled or st.pinned or similar flag
        assert re.search(r'userScroll|pinned|scrolledUp|_deployLogUserScrolled', body), (
            "AC4: _deployLogRender must check whether the user has scrolled up before auto-scrolling"
        )

    def test_scroll_listener_attached_to_log_body(self, html):
        """AC4 — a scroll event listener must be attached to the log body element."""
        js = _deploy_js(html)
        # Should have addEventListener('scroll', ...) or onscroll on the log body
        assert re.search(
            r'(?:addEventListener\s*\(\s*["\']scroll["\']|\.onscroll\s*=)',
            js
        ), (
            "AC4: a scroll event listener must be registered on the log body for scroll-up detection"
        )

    def test_scroll_to_bottom_affordance_exists(self, html):
        """AC4 — a 'scroll to bottom' or 'resume auto-scroll' affordance must be present."""
        # Check for a CSS class or JS function that provides the affordance
        css = _deploy_css(html)
        js = _deploy_js(html)
        has_css = re.search(r'deploy-log__scroll-btn|deploy-log__pin|deploy-scroll-to-bottom', css)
        has_js = re.search(r'scrollToBottom|scroll.to.bottom|pin.*bottom|resume.*scroll|deploy-log__scroll', js)
        assert has_css or has_js, (
            "AC4: a scroll-to-bottom affordance (button or function) must exist for when the user has scrolled up"
        )


# ── AC5: Loading state ────────────────────────────────────────────────────────


class TestLoadingState:
    def test_loading_state_css_exists(self, html):
        """AC5 — a loading state CSS class must exist for the deploy view."""
        css = _deploy_css(html)
        assert re.search(r'\.deploy-loading|\.deploy-skeleton|\.deploy-spinner', css), (
            "AC5: a deploy loading state CSS class must be defined (spinner or skeleton)"
        )

    def test_deployTabInit_shows_loading(self, html):
        """AC5 — deployTabInit must show a loading indicator before the fetch resolves."""
        js = _deploy_js(html)
        func = re.search(
            r'function deployTabInit\s*\(\s*\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert func, "AC5: deployTabInit function not found"
        body = func.group(1)
        # Must set innerHTML to a loading element before the fetch promise chain
        assert re.search(r'deploy-loading|deploy-skeleton|spinner|Loading', body), (
            "AC5: deployTabInit must inject a loading indicator before fetch resolves"
        )


# ── AC6: Empty state with helpful message ─────────────────────────────────────


class TestEmptyState:
    def test_empty_state_exists(self, html):
        """AC6 — deploy empty state must render when no environments are configured."""
        assert 'deploy-grid-empty' in html, "AC6: deploy-grid-empty element must be used"

    def test_empty_state_has_helpful_message(self, html):
        """AC6 — empty state must provide actionable guidance, not just a generic message."""
        js = _deploy_js(html)
        # Find the empty state innerHTML in _deployRenderGrid
        match = re.search(r'deploy-grid-empty.*?["\']([^"\'<>]{20,})["\']', js, re.DOTALL)
        # Or just check the text content of the empty state is descriptive enough
        empty_block = re.search(
            r'deploy-grid-empty["\']>(.*?)</div>',
            js, re.DOTALL
        )
        assert empty_block or match, "AC6: empty state content not found in _deployRenderGrid"
        # Check if there's some guidance (add/configure/docs link etc.)
        # At minimum the text must be more than a blank filler
        content = (empty_block.group(1) if empty_block else match.group(1) if match else '')
        # The message should not just say "no" — it should be helpful
        assert len(content.strip()) > 10, (
            "AC6: empty state message must have substantive text (guide user to add environments)"
        )

    def test_empty_state_css_rule(self, html):
        """AC6 — .deploy-tab-intro or the empty state must have styling."""
        css = _deploy_css(html)
        assert re.search(r'\.deploy-tab-intro\b', css), (
            "AC6: empty state class must have a CSS rule"
        )


# ── AC7: Keyboard accessibility ───────────────────────────────────────────────


class TestKeyboardAccessibility:
    def test_buttons_are_native_button_elements(self, html):
        """AC7 — deploy action buttons must be <button type="button"> for native tab access."""
        js = _deploy_js(html)
        # _deployActionBtn generates buttons
        func = re.search(r'function _deployActionBtn\s*\([^)]+\)\s*\{(.*?)\n\}', js, re.DOTALL)
        assert func, "AC7: _deployActionBtn function not found"
        body = func.group(1)
        assert '<button' in body, (
            "AC7: _deployActionBtn must generate a <button> element for native keyboard access"
        )
        assert 'type="button"' in body, (
            "AC7: action buttons must have type=\"button\" to prevent form submission"
        )

    def test_no_negative_tabindex_on_interactive_elements(self, html):
        """AC7 — no interactive deploy element should have tabindex=-1."""
        js = _deploy_js(html)
        # tabindex="-1" on a button would remove it from tab order
        deploy_html_fns = re.findall(
            r'function (?:_deployActionBtn|_deployIdleActionsHtml|_deployLogPanelHtml|'
            r'_deployBusyActions)\s*\([^)]+\)\s*\{(.*?)\n\}',
            js, re.DOTALL
        )
        for fn_body in deploy_html_fns:
            assert 'tabindex="-1"' not in fn_body, (
                "AC7: no deploy button or interactive element should have tabindex=-1 "
                "(it removes the element from tab order)"
            )

    def test_log_toggle_button_is_button_element(self, html):
        """AC7 — log collapse/expand toggle must be a <button> element."""
        js = _deploy_js(html)
        panel_func = re.search(
            r'function _deployLogPanelHtml\s*\([^)]+\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert panel_func, "AC7: _deployLogPanelHtml not found"
        body = panel_func.group(1)
        assert 'class="deploy-log__toggle"' in body, "AC7: log toggle button must exist"
        assert '<button' in body, "AC7: log toggle must be a <button> element"


# ── AC8: Focus indicators meeting WCAG 2.1 AA ────────────────────────────────


class TestFocusIndicators:
    def test_deploy_btn_focus_visible_outline(self, html):
        """AC8 — .deploy-btn:focus-visible must use a high-contrast outline."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-btn:focus-visible\s*\{([^}]+)\}', css)
        assert block, "AC8: .deploy-btn:focus-visible CSS rule must exist"
        props = block.group(1)
        # WCAG AA: outline must be at least 2px solid
        assert re.search(r'outline\s*:\s*2px\s+solid', props), (
            "AC8: focus outline must be at least 2px solid (WCAG 2.1 AA)"
        )

    def test_deploy_btn_focus_uses_blue_token(self, html):
        """AC8 — focus outline must use the --blue token for visibility on dark theme."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-btn:focus-visible\s*\{([^}]+)\}', css)
        assert block, "AC8: .deploy-btn:focus-visible CSS rule not found"
        assert 'var(--blue)' in block.group(1), (
            "AC8: focus outline must use var(--blue) for AA contrast on dark theme"
        )

    def test_log_toggle_focus_visible_outline(self, html):
        """AC8 — .deploy-log__toggle:focus-visible must have a visible focus indicator."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log__toggle:focus-visible\s*\{([^}]+)\}', css)
        assert block, "AC8: .deploy-log__toggle:focus-visible rule must exist"
        assert 'outline' in block.group(1), (
            "AC8: log toggle must have a visible focus outline"
        )

    def test_row_focus_within_uses_token(self, html):
        """AC8 — .deploy-card__row:focus-within must use a token for the focus highlight."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-card__row:focus-within\s*\{([^}]+)\}', css)
        assert block, "AC8: .deploy-card__row:focus-within rule not found"
        assert re.search(r'var\(--', block.group(1)), (
            "AC8: focus-within style must use a foundation token"
        )


# ── AC9: ARIA labels ──────────────────────────────────────────────────────────


class TestAriaLabels:
    def test_action_buttons_have_aria_label(self, html):
        """AC9 — Start/Stop/Deploy buttons must have aria-label attributes."""
        js = _deploy_js(html)
        func = re.search(
            r'function _deployActionBtn\s*\([^)]+\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert func, "AC9: _deployActionBtn not found"
        body = func.group(1)
        assert 'aria-label' in body, (
            "AC9: _deployActionBtn must set aria-label on generated buttons"
        )

    def test_runstate_pill_has_role_status(self, html):
        """AC9 — runstate pill must have role=status and aria-live=polite."""
        js = _deploy_js(html)
        func = re.search(
            r'function _deployCardHtml\s*\([^)]+\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert func, "AC9: _deployCardHtml not found"
        body = func.group(1)
        assert 'role="status"' in body, (
            "AC9: runstate pill must have role=\"status\" for screen readers"
        )
        assert 'aria-live="polite"' in body, (
            "AC9: runstate pill must have aria-live=\"polite\" for live updates"
        )

    def test_status_pill_has_aria_label_or_role(self, html):
        """AC9 — deploy status pill must be accessible to screen readers."""
        js = _deploy_js(html)
        func = re.search(
            r'function _deployCardHtml\s*\([^)]+\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert func, "AC9: _deployCardHtml not found"
        body = func.group(1)
        # The status pill must have aria-label or be within an aria-live region
        assert re.search(r'aria-label|aria-live|role="status"', body), (
            "AC9: deploy status indicator must be accessible (aria-label, aria-live, or role=status)"
        )

    def test_log_status_has_aria_live(self, html):
        """AC9 — log status element must update live for screen readers."""
        js = _deploy_js(html)
        panel_func = re.search(
            r'function _deployLogPanelHtml\s*\([^)]+\)\s*\{(.*?)\n\}', js, re.DOTALL
        )
        assert panel_func, "AC9: _deployLogPanelHtml not found"
        body = panel_func.group(1)
        assert 'aria-live' in body or 'role="status"' in body, (
            "AC9: log status indicator must use aria-live or role=status"
        )


# ── AC11: No new foundation tokens ────────────────────────────────────────────


class TestNoNewTokens:
    def test_no_new_custom_properties_in_deploy_css(self, html):
        """AC11 — deploy CSS must not introduce new --deploy-* or other non-standard tokens."""
        css = _deploy_css(html)
        # Check for any new CSS custom property declarations (--foo: value)
        # that are not in the known design system token set
        known_prefixes = (
            '--bg', '--surface', '--border', '--text', '--blue', '--green',
            '--amber', '--red', '--purple', '--mono', '--sans',
        )
        # Find CSS custom property *declarations* (--foo: value), not references (var(--foo))
        # Use lookbehind to require whitespace/{/; before --, so class names like
        # .deploy-btn--primary:hover don't match (the -- is preceded by a word char there).
        declarations = re.findall(r'(?:^|[\s{;])(--[a-z][a-z0-9-]+)\s*:', css, re.MULTILINE)
        new_tokens = [
            t for t in declarations
            if not any(t.startswith(prefix) for prefix in known_prefixes)
        ]
        assert not new_tokens, (
            f"AC11: new foundation tokens declared in deploy CSS: {new_tokens}. "
            "Reuse existing design system tokens only."
        )


# ── AC12: Scoped to deploy view only ─────────────────────────────────────────


class TestScopeDeployOnly:
    def test_other_tab_panes_still_present(self, html):
        """AC12 — other tab panes must remain intact."""
        for pane_id in ('pane-sprint-mgmt', 'pane-logs', 'pane-roadmap'):
            assert f'id="{pane_id}"' in html, (
                f"AC12: #{pane_id} must still exist — deploy polish must not affect other views"
            )

    def test_deploy_tab_pane_exists(self, html):
        """AC12 — deploy tab pane must still exist."""
        assert 'id="pane-deploy"' in html, "AC12: #pane-deploy must still exist"

    def test_deploy_grid_exists(self, html):
        """AC12 — deploy-grid container must still exist."""
        assert 'id="deploy-grid"' in html, "AC12: #deploy-grid container must still exist"
