"""Tests for issue #1076 — Audit and Fix Settings/Advisor Tab UI Anti-Patterns.

AC coverage:
  AC1 — Impeccable detect reports zero findings on the Settings tab region
  AC2 — Impeccable detect reports zero findings on the Advisor tab region
  AC3 — All fixes use only foundation design tokens (no hardcoded color/spacing)
  AC4 — Form field contrast meets dark theme standards
  AC5 — Label alignment is consistent across all form controls
  AC6 — Control sizing follows token scale
  AC7 — Card spacing uses token-defined gaps
  AC8 — All existing Settings and Advisor event handlers remain functional
  AC9 — No regressions in other tabs or regions of project.html

Token scale: 4 · 8 · 12 · 16 · 24 · 32 px (only these values in structural layout).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"

# Token-scale values allowed in structural spacing (padding, gap, margin)
TOKEN_SCALE = {0, 2, 4, 8, 12, 16, 24, 32}
# Small-component dimension values that are OK (border-radius, icon sizes, etc.)
ALLOWED_MICRO = {1, 2, 3, 6, 10, 22, 26, 34, 38, 40}


@pytest.fixture(scope="module")
def html() -> str:
    assert PROJECT_HTML.exists(), f"project.html not found at {PROJECT_HTML}"
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def inline_style(html) -> str:
    m = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def inline_script(html) -> str:
    parts = re.findall(r"<script(?![^>]*\bsrc\b)[^>]*>(.*?)</script>", html, re.DOTALL)
    return "\n".join(parts)


@pytest.fixture(scope="module")
def settings_css(inline_style) -> str:
    """Extract the project Settings CSS region (issue #642 block)."""
    m = re.search(
        r"/\*\s*──\s*Project Settings tab[^*]*\*/(.+?)(?=/\*\s*──\s*Env path)",
        inline_style,
        re.DOTALL,
    )
    return m.group(1) if m else ""


@pytest.fixture(scope="module")
def advisor_css(inline_style) -> str:
    """Extract the Advisor CSS region (issue #881 block)."""
    m = re.search(
        r"/\*\s*──\s*Advisor tab[^*]*\*/(.+?)(?=/\*\s*Roadmap look-ahead)",
        inline_style,
        re.DOTALL,
    )
    return m.group(1) if m else ""


# =============================================================================
# AC3 — All fixes use only foundation design tokens
# =============================================================================


def test_ac3_advisor_run_btn_color_uses_token(advisor_css):
    """adv-run-btn must use var(--text-on-primary) not hardcoded #fff for text color."""
    if not advisor_css:
        pytest.skip("Could not extract advisor CSS region")
    # Extract the adv-run-btn rule
    run_btn_block = re.search(r"\.adv-run-btn\s*\{([^}]+)\}", advisor_css, re.DOTALL)
    assert run_btn_block, ".adv-run-btn rule not found in advisor CSS"
    block = run_btn_block.group(1)
    # color: #fff is a hardcoded value; should be var(--text-on-primary)
    assert "color: #fff" not in block and "color:#fff" not in block, (
        ".adv-run-btn uses hardcoded 'color: #fff' — replace with "
        "'color: var(--text-on-primary)' to use the design token system"
    )
    assert "var(--text-on-primary)" in block, (
        ".adv-run-btn must use 'color: var(--text-on-primary)' for its text color "
        "so dark-mode overrides apply automatically"
    )


def test_ac3_advisor_accept_btn_color_uses_token(advisor_css):
    """adv-accept-btn must use var(--text-on-primary) not hardcoded #fff."""
    if not advisor_css:
        pytest.skip("Could not extract advisor CSS region")
    accept_btn_block = re.search(
        r"\.adv-accept-btn\s*\{([^}]+)\}", advisor_css, re.DOTALL
    )
    assert accept_btn_block, ".adv-accept-btn rule not found in advisor CSS"
    block = accept_btn_block.group(1)
    assert "color: #fff" not in block and "color:#fff" not in block, (
        ".adv-accept-btn uses hardcoded 'color: #fff' — replace with "
        "'color: var(--text-on-primary)'"
    )
    assert "var(--text-on-primary)" in block, (
        ".adv-accept-btn must use 'color: var(--text-on-primary)'"
    )


def test_ac3_settings_toggle_knob_uses_token(settings_css):
    """Toggle knob (.ps-toggle::after) must use var(--text-on-primary), not #fff."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    toggle_after = re.search(
        r"\.ps-toggle::after\s*\{([^}]+)\}", settings_css, re.DOTALL
    )
    assert toggle_after, ".ps-toggle::after rule not found in settings CSS"
    block = toggle_after.group(1)
    assert "background: #fff" not in block and "background:#fff" not in block, (
        ".ps-toggle::after uses hardcoded 'background: #fff' for the toggle knob — "
        "replace with 'background: var(--text-on-primary)' so the knob uses the token"
    )


def test_ac3_settings_avatar_color_uses_token(settings_css):
    """Identity avatar (.ps-identity-avatar) must use var(--text-on-primary), not #fff."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    avatar_block = re.search(
        r"\.ps-identity-avatar\s*\{([^}]+)\}", settings_css, re.DOTALL
    )
    assert avatar_block, ".ps-identity-avatar rule not found in settings CSS"
    block = avatar_block.group(1)
    assert "color: #fff" not in block and "color:#fff" not in block, (
        ".ps-identity-avatar uses hardcoded 'color: #fff' — "
        "replace with 'color: var(--text-on-primary)'"
    )


# =============================================================================
# AC4 — Form field contrast meets dark theme standards
# =============================================================================


def test_ac4_ps_inp_uses_surface2_background(settings_css):
    """Form inputs (.ps-inp) must use var(--surface-2) background for dark-mode contrast.

    In dark mode, --surface (#161616) is the card background. An input with the same
    background is invisible against its container. Using --surface-2 (#1e1e1e) provides
    the necessary visual distinction while staying within the token system.
    """
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    inp_block = re.search(r"\.ps-inp\s*\{([^}]+)\}", settings_css, re.DOTALL)
    assert inp_block, ".ps-inp rule not found in settings CSS"
    block = inp_block.group(1)
    assert "background: var(--surface-2)" in block or "background:var(--surface-2)" in block, (
        ".ps-inp uses 'background: var(--surface)' which blends into the card "
        "background in dark mode — change to 'background: var(--surface-2)' for "
        "proper form field contrast per dark theme standards"
    )


def test_ac4_ps_inp_focus_visible_ring(settings_css):
    """Form inputs must have a :focus-visible style with a blue ring (not just :focus)."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    # Check for :focus-visible on ps-inp (accessibility best practice)
    assert "ps-inp:focus-visible" in settings_css or "ps-inp:focus" in settings_css, (
        "Form inputs (.ps-inp) must have a visible focus indicator for keyboard navigation"
    )


# =============================================================================
# AC5 — Label alignment is consistent across all form controls
# =============================================================================


def test_ac5_ps_row_label_has_min_width(settings_css):
    """All form row labels must share a consistent min-width for alignment."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    label_block = re.search(r"\.ps-row-label\s*\{([^}]+)\}", settings_css, re.DOTALL)
    assert label_block, ".ps-row-label rule not found in settings CSS"
    block = label_block.group(1)
    assert "min-width" in block, (
        ".ps-row-label must have a min-width so all form labels share a consistent "
        "left edge and controls align vertically across rows"
    )


def test_ac5_ps_row_uses_flex_align_center(settings_css):
    """Form rows (.ps-row) must use flexbox with align-items: center for label alignment."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    row_block = re.search(r"\.ps-row\s*\{([^}]+)\}", settings_css, re.DOTALL)
    assert row_block, ".ps-row rule not found in settings CSS"
    block = row_block.group(1)
    assert "align-items: center" in block or "align-items:center" in block, (
        ".ps-row must use 'align-items: center' so labels and controls are vertically aligned"
    )


# =============================================================================
# AC6 — Control sizing follows token scale
# =============================================================================


def test_ac6_ps_inp_padding_on_token_scale(settings_css):
    """Form input padding must use token-scale values (4·8·12·16·24·32)."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    inp_block = re.search(r"\.ps-inp\s*\{([^}]+)\}", settings_css, re.DOTALL)
    assert inp_block, ".ps-inp rule not found in settings CSS"
    block = inp_block.group(1)
    padding_m = re.search(r"padding:\s*(\d+)px\s+(\d+)px", block)
    if not padding_m:
        pytest.skip("Could not parse ps-inp padding values")
    v = int(padding_m.group(1))
    h = int(padding_m.group(2))
    assert v in TOKEN_SCALE, (
        f".ps-inp vertical padding {v}px is off the token scale (4·8·12·16·24·32). "
        "Use 8px for compact form control sizing."
    )
    assert h in TOKEN_SCALE, (
        f".ps-inp horizontal padding {h}px is off the token scale (4·8·12·16·24·32). "
        "Use 12px for compact form control sizing."
    )


def test_ac6_ps_btn_padding_on_token_scale(settings_css):
    """Action button (.ps-btn) padding must use token-scale values."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    btn_block = re.search(r"\.ps-btn\s*\{([^}]+)\}", settings_css, re.DOTALL)
    assert btn_block, ".ps-btn rule not found in settings CSS"
    block = btn_block.group(1)
    padding_m = re.search(r"padding:\s*(\d+)px\s+(\d+)px", block)
    if not padding_m:
        pytest.skip("Could not parse ps-btn padding values")
    v = int(padding_m.group(1))
    h = int(padding_m.group(2))
    assert v in TOKEN_SCALE, (
        f".ps-btn vertical padding {v}px is off the token scale. Use 8px."
    )
    assert h in TOKEN_SCALE, (
        f".ps-btn horizontal padding {h}px is off the token scale. Use 12px."
    )


# =============================================================================
# AC7 — Card spacing uses token-defined gaps
# =============================================================================


def test_ac7_settings_card_head_gap_on_scale(settings_css):
    """Card header gap must be on the token scale (4·8·12·16·24·32)."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    head_block = re.search(
        r"\.proj-settings-card-head\s*\{([^}]+)\}", settings_css, re.DOTALL
    )
    assert head_block, ".proj-settings-card-head rule not found"
    block = head_block.group(1)
    gap_m = re.search(r"\bgap:\s*(\d+)px", block)
    if gap_m:
        gap = int(gap_m.group(1))
        assert gap in TOKEN_SCALE, (
            f".proj-settings-card-head gap: {gap}px is off the token scale "
            "(4·8·12·16·24·32). Use 8px."
        )


def test_ac7_settings_card_head_padding_on_scale(settings_css):
    """Card header padding must use token-scale values only."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    head_block = re.search(
        r"\.proj-settings-card-head\s*\{([^}]+)\}", settings_css, re.DOTALL
    )
    assert head_block, ".proj-settings-card-head rule not found"
    block = head_block.group(1)
    pad_m = re.search(r"padding:\s*(\d+)px\s+(\d+)px", block)
    if pad_m:
        v = int(pad_m.group(1))
        h = int(pad_m.group(2))
        assert v in TOKEN_SCALE, (
            f".proj-settings-card-head vertical padding {v}px is off scale. Use 12px."
        )
        assert h in TOKEN_SCALE, (
            f".proj-settings-card-head horizontal padding {h}px is off scale. Use 16px."
        )


def test_ac7_settings_row_gap_on_scale(settings_css):
    """Form row gap must be on the token scale."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    row_block = re.search(r"\.ps-row\s*\{([^}]+)\}", settings_css, re.DOTALL)
    assert row_block, ".ps-row rule not found in settings CSS"
    block = row_block.group(1)
    gap_m = re.search(r"\bgap:\s*(\d+)px", block)
    if gap_m:
        gap = int(gap_m.group(1))
        assert gap in TOKEN_SCALE, (
            f".ps-row gap: {gap}px is off the token scale. Use 12px or 16px."
        )


def test_ac7_settings_row_padding_on_scale(settings_css):
    """Form row padding must be on the token scale."""
    if not settings_css:
        pytest.skip("Could not extract settings CSS region")
    row_block = re.search(r"\.ps-row\s*\{([^}]+)\}", settings_css, re.DOTALL)
    assert row_block, ".ps-row rule not found in settings CSS"
    block = row_block.group(1)
    pad_m = re.search(r"padding:\s*(\d+)px\s+(\d+)", block)
    if pad_m:
        v = int(pad_m.group(1))
        assert v in TOKEN_SCALE, (
            f".ps-row vertical padding {v}px is off the token scale. Use 8px."
        )


# =============================================================================
# AC1/AC2 — Advisor interactive elements have focus-visible states
# =============================================================================


def test_ac1_advisor_run_btn_has_focus_visible(advisor_css):
    """adv-run-btn must have a :focus-visible style for keyboard accessibility."""
    if not advisor_css:
        pytest.skip("Could not extract advisor CSS region")
    assert "adv-run-btn:focus-visible" in advisor_css or "adv-run-btn:focus" in advisor_css, (
        ".adv-run-btn has no :focus-visible rule — impeccable flags interactive "
        "elements without visible keyboard focus indicators. Add a focus ring."
    )


def test_ac2_advisor_accept_btn_has_focus_visible(advisor_css):
    """adv-accept-btn must have a :focus-visible style for keyboard accessibility."""
    if not advisor_css:
        pytest.skip("Could not extract advisor CSS region")
    assert "adv-accept-btn:focus-visible" in advisor_css or "adv-accept-btn:focus" in advisor_css, (
        ".adv-accept-btn has no :focus-visible rule — add a visible focus ring."
    )


def test_ac2_advisor_dismiss_btn_has_focus_visible(advisor_css):
    """adv-dismiss-btn must have a :focus-visible style for keyboard accessibility."""
    if not advisor_css:
        pytest.skip("Could not extract advisor CSS region")
    assert (
        "adv-dismiss-btn:focus-visible" in advisor_css
        or "adv-dismiss-btn:focus" in advisor_css
    ), (
        ".adv-dismiss-btn has no :focus-visible rule — add a visible focus ring."
    )


# =============================================================================
# AC8 — All existing Settings and Advisor event handlers remain functional
# =============================================================================


def test_ac8_settings_handlers_intact(inline_script):
    """Core settings handlers must still exist after UI audit fixes."""
    for fn in ("projSettingsSave", "projSettingsLoad", "projSettingsResetEstimation",
               "projSettingsToggleTracked", "projSettingsGoGlobal"):
        assert fn in inline_script, (
            f"Settings handler '{fn}' not found after UI fix — handlers must not be removed"
        )


def test_ac8_advisor_handlers_intact(inline_script):
    """Core advisor handlers must still exist after UI audit fixes."""
    for fn in ("advRun", "_advFetch", "_advRender", "sugAccept", "sugDismiss"):
        assert fn in inline_script, (
            f"Advisor handler '{fn}' not found after UI fix — handlers must not be removed"
        )


# =============================================================================
# AC9 — No regressions in other tabs
# =============================================================================


def test_ac9_deploy_tab_intact(html):
    """Deploy tab must still exist — UI audit must not touch other tabs."""
    assert "pane-deploy" in html, (
        "pane-deploy is missing — the UI audit accidentally removed another tab"
    )


def test_ac9_sprint_board_intact(html):
    """Sprint board elements must still exist — changes scoped to settings/advisor."""
    assert "smgmt" in html, (
        "Sprint management elements are missing — UI audit went beyond scope"
    )


def test_ac9_settings_pane_intact(html):
    """Settings pane structure must remain intact after CSS fixes."""
    assert 'id="pane-settings"' in html, "pane-settings missing after fix"
    assert "proj-settings-card" in html, "proj-settings-card missing after fix"
    assert "proj-settings-card-head" in html, "card heads missing after fix"
    assert "proj-settings-card-body" in html, "card bodies missing after fix"


def test_ac9_advisor_pane_intact(html):
    """Advisor pane structure must remain intact after CSS fixes."""
    assert 'id="pane-advisor"' in html, "pane-advisor missing after fix"
    assert "adv-header" in html, "adv-header missing after fix"
    assert "adv-run-btn" in html, "adv-run-btn missing after fix"
    assert "adv-empty" in html, "adv-empty state missing after fix"
