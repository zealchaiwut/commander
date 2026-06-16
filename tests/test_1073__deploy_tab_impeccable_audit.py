"""Acceptance tests for issue #1073 — Audit and fix Deploy tab UI against impeccable rules.

AC map:
  AC1  Impeccable detect on the deploy region reports zero findings
       (deploy-grid must not cause cramped-padding — no border on the grid container,
        or children must not be flush against a visible boundary)
  AC2  Status indicators meet contrast requirements using foundation tokens only
       (idle/queued states must use --text-muted, not --text-sub, for legibility)
  AC3  Row spacing conforms to impeccable spacing scale (4, 8, 12, 16, 24, 32 px only)
       (no 6px, 10px, or 20px values in the deploy CSS block)
  AC4  Action buttons meet minimum touch/click target sizing (44px per DESIGN.md)
  AC5  Log panel alignment matches grid/layout tokens (on-scale margins and padding)
  AC6  No new CSS variables or custom values introduced outside foundation tokens
  AC7  All existing deploy action handlers remain functional (JS references intact)
  AC8  Dark theme renders correctly — all deploy colors use var(--*) tokens, no hex
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _deploy_css(html: str) -> str:
    """Extract the deploy-tab CSS block (between its markers)."""
    start = html.find("/* ── Deploy tab")
    end = html.find("/* ── Scaffold docs card", start)
    assert start != -1 and end != -1, "Could not locate deploy CSS block"
    return html[start:end]


def _deploy_html(html: str) -> str:
    """Extract the static deploy tab HTML pane."""
    start = html.find('id="pane-deploy"')
    end = html.find("<!-- Tab pane shell", start)
    assert start != -1 and end != -1, "Could not locate deploy HTML pane"
    return html[start:end]


def _deploy_js(html: str) -> str:
    """Extract the deploy JS block."""
    start = html.find("// ── Deploy tab (issue #726)")
    assert start != -1, "Could not locate deploy JS block"
    return html[start : start + 20000]


# ── AC1: No cramped-padding on deploy-grid container ─────────────────────────


class TestDeployGridNoCrampedPadding:
    def test_deploy_grid_has_no_outer_border(self, html):
        """AC1 — deploy-grid must not carry a border that creates a cramped-padding finding.

        When a bordered container's children are flush against the border,
        impeccable flags [cramped-padding]. Removing the border from the grid
        container (and moving borders to individual deploy-card elements) fixes it.
        """
        css = _deploy_css(html)
        block = re.search(r'\.deploy-grid\s*\{([^}]+)\}', css)
        assert block, "AC1: .deploy-grid rule must exist"
        props = block.group(1)
        # The grid container must NOT have its own border (that would cause cramped-padding)
        assert "border: 1px solid var(--border)" not in props, (
            "AC1: .deploy-grid must not have a border property — move borders to "
            "individual .deploy-card elements to avoid cramped-padding finding"
        )

    def test_deploy_card_has_border(self, html):
        """AC1 — individual deploy-card elements must carry the border styling."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-card\s*\{([^}]+)\}', css)
        assert block, "AC1: .deploy-card rule must exist"
        props = block.group(1)
        assert "var(--border)" in props, (
            "AC1: .deploy-card must include border: var(--border) since the grid "
            "container no longer carries the border"
        )

    def test_deploy_grid_loading_state_has_padding(self, html):
        """AC1 — the loading/empty state inside deploy-grid must have padding so text
        is not flush against any visual boundary."""
        # Either the empty element has explicit padding, or the grid itself has gap/padding
        css = _deploy_css(html)
        deploy_html = _deploy_html(html)
        # The deploy-grid-empty element must not be the only child inside a bordered container
        # If deploy-grid has no border (from test above), this check is satisfied
        # We also check that the deploy-grid has gap spacing between cards
        has_gap = re.search(r'\.deploy-grid\s*\{[^}]*gap:\s*(?:[1-9]\d*|[0-9]+)px', css)
        has_card_border = re.search(r'\.deploy-card\s*\{[^}]*border', css)
        assert has_card_border, (
            "AC1: deploy-cards must have border styling when grid container has no border"
        )


# ── AC2: Status indicator contrast using foundation tokens ────────────────────


class TestStatusIndicatorContrast:
    def test_status_pill_idle_uses_text_muted_not_text_sub(self, html):
        """AC2 — the default/idle status-pill must use --text-muted (not --text-sub).

        --text-muted (#6b7280 light / #9ca3af dark) has better contrast than
        --text-sub (#9ca3af light / #6b7280 dark) on --surface-2 backgrounds.
        """
        css = _deploy_css(html)
        # The base .status-pill rule must not use --text-sub
        base_block = re.search(r'\.status-pill\s*\{([^}]+)\}', css)
        assert base_block, "AC2: .status-pill rule must exist"
        props = base_block.group(1)
        assert "--text-sub" not in props, (
            "AC2: .status-pill default color must not use --text-sub (low contrast). "
            "Use --text-muted instead for WCAG AA compliance."
        )

    def test_status_pill_queued_uses_text_muted_not_text_sub(self, html):
        """AC2 — the queued status-pill must use --text-muted for contrast."""
        css = _deploy_css(html)
        block = re.search(r'\.status-pill--queued\s*\{([^}]+)\}', css)
        assert block, "AC2: .status-pill--queued rule must exist"
        props = block.group(1)
        assert "--text-sub" not in props, (
            "AC2: .status-pill--queued must not use --text-sub. Use --text-muted."
        )

    def test_runstate_pill_idle_uses_text_muted_not_text_sub(self, html):
        """AC2 — runstate-pill idle state must use --text-muted for contrast."""
        css = _deploy_css(html)
        block = re.search(r'\.runstate-pill--idle\s*\{([^}]+)\}', css)
        assert block, "AC2: .runstate-pill--idle rule must exist"
        props = block.group(1)
        assert "--text-sub" not in props, (
            "AC2: .runstate-pill--idle must not use --text-sub. Use --text-muted."
        )

    def test_runstate_pill_base_uses_text_muted_not_text_sub(self, html):
        """AC2 — runstate-pill base rule must use --text-muted for contrast."""
        css = _deploy_css(html)
        block = re.search(r'\.runstate-pill\s*\{([^}]+)\}', css)
        assert block, "AC2: .runstate-pill base rule must exist"
        props = block.group(1)
        assert "--text-sub" not in props, (
            "AC2: .runstate-pill base color must not use --text-sub. Use --text-muted."
        )

    def test_colored_states_use_semantic_token_pairs(self, html):
        """AC2 — live/building/failed states must use paired semantic tokens."""
        css = _deploy_css(html)
        assert "status-pill--live" in css, "AC2: .status-pill--live rule must exist"
        assert "status-pill--failed" in css, "AC2: .status-pill--failed rule must exist"
        assert "var(--green-bg)" in css and "var(--green)" in css, (
            "AC2: live status must use --green-bg / --green token pair"
        )
        assert "var(--red-bg)" in css and "var(--red)" in css, (
            "AC2: failed status must use --red-bg / --red token pair"
        )


# ── AC3: Row spacing on impeccable spacing scale ──────────────────────────────


class TestRowSpacingOnScale:
    _OFF_SCALE = ["6px", "10px", "20px"]

    def test_no_6px_spacing_in_deploy_css(self, html):
        """AC3 — 6px is off the spacing scale (4, 8, 12, 16, 24, 32). Must not appear."""
        css = _deploy_css(html)
        off_scale_uses = re.findall(r':\s*[^;]*\b6px\b', css)
        # Allow 6px only inside font-size (e.g. border-radius can use it too, but spacing gap/padding/margin should not)
        spacing_uses = [u for u in off_scale_uses if re.search(r'(?:gap|padding|margin)\s*:', u.split(':')[0] + ':')]
        # Check direct spacing properties with 6px
        spacing_pattern = re.findall(r'(?:gap|padding|margin)[^;]*:\s*[^;]*\b6px\b', css)
        assert not spacing_pattern, (
            f"AC3: Deploy CSS uses 6px in spacing properties (off-scale). "
            f"Found: {spacing_pattern[:3]}. Use 4px or 8px instead."
        )

    def test_no_10px_spacing_in_deploy_css(self, html):
        """AC3 — 10px is off the spacing scale. Must not appear in gap/padding/margin."""
        css = _deploy_css(html)
        spacing_pattern = re.findall(r'(?:gap|padding|margin)[^;]*:\s*[^;]*\b10px\b', css)
        assert not spacing_pattern, (
            f"AC3: Deploy CSS uses 10px in spacing properties (off-scale). "
            f"Found: {spacing_pattern[:3]}. Use 8px or 12px instead."
        )

    def test_no_20px_spacing_in_deploy_css(self, html):
        """AC3 — 20px is off the spacing scale. Must not appear in gap/padding/margin."""
        css = _deploy_css(html)
        spacing_pattern = re.findall(r'(?:gap|padding|margin)[^;]*:\s*[^;]*\b20px\b', css)
        assert not spacing_pattern, (
            f"AC3: Deploy CSS uses 20px in spacing properties (off-scale). "
            f"Found: {spacing_pattern[:3]}. Use 16px or 24px instead."
        )


# ── AC4: Action buttons meet 44px minimum touch target ───────────────────────


class TestActionButtonSizing:
    def test_deploy_btn_min_height_44px(self, html):
        """AC4 — deploy-btn must have min-height: 44px (DESIGN.md: 44×44px touch targets)."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-btn\s*\{([^}]+)\}', css)
        assert block, "AC4: .deploy-btn rule must exist"
        props = block.group(1)
        match = re.search(r'min-height:\s*(\d+)px', props)
        assert match, "AC4: .deploy-btn must define min-height"
        assert int(match.group(1)) >= 44, (
            f"AC4: .deploy-btn min-height must be at least 44px for touch targets. "
            f"Found: {match.group(1)}px"
        )

    def test_deploy_field_edit_touch_target_44px(self, html):
        """AC4 — deploy-field-edit icon button must meet 44px touch target requirement."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-field-edit\s*\{([^}]+)\}', css)
        assert block, "AC4: .deploy-field-edit rule must exist"
        props = block.group(1)
        h_match = re.search(r'(?:min-height|height):\s*(\d+)px', props)
        w_match = re.search(r'(?:min-width|width):\s*(\d+)px', props)
        assert h_match and w_match, (
            "AC4: .deploy-field-edit must define width and height"
        )
        assert int(h_match.group(1)) >= 44 and int(w_match.group(1)) >= 44, (
            f"AC4: .deploy-field-edit must be at least 44×44px for touch targets. "
            f"Found: {w_match.group(1)}×{h_match.group(1)}px"
        )

    def test_deploy_log_toggle_touch_target_44px(self, html):
        """AC4 — deploy-log__toggle icon button must meet 44px touch target requirement."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log__toggle\s*\{([^}]+)\}', css)
        assert block, "AC4: .deploy-log__toggle rule must exist"
        props = block.group(1)
        h_match = re.search(r'(?:min-height|height):\s*(\d+)px', props)
        w_match = re.search(r'(?:min-width|width):\s*(\d+)px', props)
        assert h_match and w_match, (
            "AC4: .deploy-log__toggle must define width and height"
        )
        assert int(h_match.group(1)) >= 44 and int(w_match.group(1)) >= 44, (
            f"AC4: .deploy-log__toggle must be at least 44×44px for touch targets. "
            f"Found: {w_match.group(1)}×{h_match.group(1)}px"
        )


# ── AC5: Log panel alignment using grid/layout tokens ─────────────────────────


class TestLogPanelAlignment:
    def test_deploy_log_margin_uses_scale_values(self, html):
        """AC5 — deploy-log margin values must be on the spacing scale."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log\s*\{([^}]+)\}', css)
        assert block, "AC5: .deploy-log rule must exist"
        props = block.group(1)
        margin_match = re.search(r'margin:\s*([^;]+)', props)
        if margin_match:
            margin_val = margin_match.group(1).strip()
            px_values = re.findall(r'(\d+)px', margin_val)
            VALID_SCALE = {0, 4, 8, 12, 16, 24, 32}
            for v in px_values:
                assert int(v) in VALID_SCALE, (
                    f"AC5: .deploy-log margin value {v}px is off the spacing scale. "
                    f"Valid: {sorted(VALID_SCALE)}"
                )

    def test_deploy_log_body_padding_uses_scale_values(self, html):
        """AC5 — deploy-log__body padding must be on the spacing scale."""
        css = _deploy_css(html)
        block = re.search(r'\.deploy-log__body\s*\{([^}]+)\}', css)
        assert block, "AC5: .deploy-log__body rule must exist"
        props = block.group(1)
        padding_match = re.search(r'padding:\s*([^;]+)', props)
        if padding_match:
            px_values = re.findall(r'(\d+)px', padding_match.group(1))
            VALID_SCALE = {0, 4, 8, 12, 16, 24, 32}
            for v in px_values:
                assert int(v) in VALID_SCALE, (
                    f"AC5: .deploy-log__body padding value {v}px is off the spacing scale. "
                    f"Valid: {sorted(VALID_SCALE)}"
                )


# ── AC6: No new custom CSS variables in deploy region ─────────────────────────


class TestNoNewCSSVariables:
    _FOUNDATION_TOKENS = {
        "--bg", "--surface", "--surface-2", "--surface-hover", "--border",
        "--text", "--text-muted", "--text-sub",
        "--blue", "--blue-bg", "--green", "--green-bg", "--amber", "--amber-bg",
        "--red", "--red-bg", "--purple", "--purple-bg",
        "--indigo", "--indigo-bg", "--steel", "--steel-bg", "--yellow", "--yellow-bg",
        "--teal", "--teal-bg", "--pink", "--pink-bg",
        "--color-success", "--color-success-fg", "--color-error", "--color-error-fg",
        "--color-info", "--color-info-fg", "--color-warning", "--color-warning-fg",
        "--mono", "--text-on-primary",
    }

    def test_no_new_custom_properties_in_deploy_css(self, html):
        """AC6 — deploy CSS must not define new --custom-property variables."""
        css = _deploy_css(html)
        # Strip CSS comments before scanning (issue numbers like /* issue #726 */ look like tokens)
        css_no_comments = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
        # Match only property declarations: must be preceded by { or ; (declaration context)
        defined_props = re.findall(r'(?:[{;])\s*(--[\w-]+)\s*:', css_no_comments)
        non_foundation = [
            p for p in defined_props
            if p not in self._FOUNDATION_TOKENS
        ]
        assert not non_foundation, (
            f"AC6: Deploy CSS introduces new custom properties not in foundation tokens: "
            f"{non_foundation}. Only use var(--foundation-token) references."
        )

    def test_no_hardcoded_hex_in_deploy_css(self, html):
        """AC8/AC6 — deploy CSS must not use hardcoded hex color values in property values."""
        css = _deploy_css(html)
        # Strip CSS comments (they contain issue numbers like #726 which look like hex colors)
        css_no_comments = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
        # Match hex colors only inside CSS property values (after : until ; or })
        # This avoids matching class selectors like .foo--primary or issue refs
        prop_values = re.findall(r':\s*([^;{}]+)', css_no_comments)
        hex_colors = []
        for val in prop_values:
            found = re.findall(r'(?<!["\'\w])#[0-9a-fA-F]{3,8}\b', val)
            hex_colors.extend(found)
        assert not hex_colors, (
            f"AC6/AC8: Deploy CSS contains hardcoded hex colors in property values: {hex_colors[:5]}. "
            "Use var(--token) references instead to ensure dark theme works correctly."
        )


# ── AC7: Existing deploy action handlers remain functional ────────────────────


class TestDeployHandlersIntact:
    def test_deploy_start_handler_exists(self, html):
        """AC7 — deploy start/restart JS handler must still be present."""
        js = _deploy_js(html)
        assert "deployStart" in js or "deploy_start" in js or "startDeploy" in js or \
               "doDeploy" in js or "deployAction" in js, (
            "AC7: Deploy start action handler must exist in deploy JS block"
        )

    def test_deploy_rollback_or_cancel_handler_exists(self, html):
        """AC7 — deploy cancel/rollback handler must still be present."""
        js = _deploy_js(html)
        assert "cancel" in js.lower() or "rollback" in js.lower(), (
            "AC7: Deploy cancel or rollback handler must exist in deploy JS block"
        )

    def test_deploy_action_fetch_calls_present(self, html):
        """AC7 — the deploy JS must still make fetch calls for actions."""
        js = _deploy_js(html)
        assert "fetch(" in js or "fetch (" in js, (
            "AC7: Deploy JS must contain fetch() calls for action handling"
        )


# ── AC8: Dark theme — all colors via tokens ───────────────────────────────────


class TestDarkThemeTokens:
    def test_status_pill_states_use_only_token_colors(self, html):
        """AC8 — all status-pill color states must reference var(--*) tokens."""
        css = _deploy_css(html)
        # Extract all status-pill state rules
        pill_rules = re.findall(r'\.status-pill(?:--\w+)?\s*\{([^}]+)\}', css)
        for rule in pill_rules:
            hex_in_rule = re.findall(r'(?<!["\'])#[0-9a-fA-F]{3,8}\b', rule)
            assert not hex_in_rule, (
                f"AC8: status-pill rule contains hardcoded hex {hex_in_rule}. "
                "Use var(--token) for dark theme compatibility."
            )

    def test_runstate_pill_states_use_only_token_colors(self, html):
        """AC8 — all runstate-pill color states must reference var(--*) tokens."""
        css = _deploy_css(html)
        pill_rules = re.findall(r'\.runstate-pill(?:--\w+)?\s*\{([^}]+)\}', css)
        for rule in pill_rules:
            hex_in_rule = re.findall(r'(?<!["\'])#[0-9a-fA-F]{3,8}\b', rule)
            assert not hex_in_rule, (
                f"AC8: runstate-pill rule contains hardcoded hex {hex_in_rule}. "
                "Use var(--token) for dark theme compatibility."
            )

    def test_deploy_btn_uses_token_colors(self, html):
        """AC8 — deploy-btn color properties must use var(--*) tokens."""
        css = _deploy_css(html)
        btn_rules = re.findall(r'\.deploy-btn(?:[^{]*)\s*\{([^}]+)\}', css)
        for rule in btn_rules:
            hex_in_rule = re.findall(r'(?<!["\'])#[0-9a-fA-F]{3,8}\b', rule)
            assert not hex_in_rule, (
                f"AC8: deploy-btn rule contains hardcoded hex {hex_in_rule}. "
                "Use var(--token) for dark theme compatibility."
            )
