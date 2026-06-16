"""Issue #1067 — Audit and fix Analytics page against impeccable rules.

Each test is anchored to a specific AC item. Tests that target off-scale
spacing values or contrast violations fail before the fix and pass after.
"""
import os
import re
import shutil
import subprocess

import pytest

ANALYTICS_HTML = os.path.join(
    os.path.dirname(__file__), "..", "apps", "dashboard", "static", "analytics.html"
)
ROOT = os.path.join(os.path.dirname(__file__), "..")

# Foundation spacing scale from DESIGN.md: 4·8·12·16·24·32
# 0, 1, 2 allowed for resets, thin borders, and micro offsets
_SPACING_SCALE = {0, 1, 2, 4, 8, 12, 16, 24, 32, 48, 64}


def _html():
    with open(ANALYTICS_HTML, encoding="utf-8") as f:
        return f.read()


def _style():
    m = re.search(r"<style>(.*?)</style>", _html(), re.DOTALL)
    assert m, "<style> block not found in analytics.html"
    return m.group(1)


def _block(selector, css):
    """Return body of the first CSS rule matching selector string exactly."""
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    m = re.search(pattern, css, re.DOTALL)
    return m.group(1) if m else None


def _spacing_px_vals(block):
    """Extract px values from spacing properties only (padding, margin, gap)."""
    result = []
    for m in re.finditer(r"(?:padding|margin|gap)(?:-\w+)?:\s*[^;]+", block):
        result.extend(int(v) for v in re.findall(r"(\d+)px", m.group(0)))
    return result


# ── AC1: impeccable detect reports zero findings ──────────────────────────────
def test_ac1_impeccable_detect_zero_findings():
    if shutil.which("npx") is None:
        pytest.skip("npx not available; impeccable gate runs in tester/CI environment")
    result = subprocess.run(
        ["npx", "impeccable", "detect", ANALYTICS_HTML],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Impeccable found violations:\n{result.stdout}\n{result.stderr}"
    )


# ── AC2: sub-tab contrast uses foundation tokens only ────────────────────────
def test_ac2_tab_active_uses_foundation_token_not_hex():
    css = _style()
    active_block = _block(".anl-tab-btn.active", css)
    assert active_block, ".anl-tab-btn.active rule not found"
    hex_re = r"#[0-9a-fA-F]{3,8}\b"
    matches = re.findall(hex_re, active_block)
    assert not matches, f"Hardcoded hex in .anl-tab-btn.active — use var(--): {matches}"
    assert "var(--" in active_block, ".anl-tab-btn.active must use at least one CSS token"


def test_ac2_tab_inactive_uses_foundation_token_not_hex():
    css = _style()
    tab_block = _block(".anl-tab-btn", css)
    assert tab_block, ".anl-tab-btn rule not found"
    hex_re = r"#[0-9a-fA-F]{3,8}\b"
    assert not re.search(hex_re, tab_block), (
        f"Hardcoded hex in .anl-tab-btn: {re.findall(hex_re, tab_block)}"
    )
    assert "var(--" in tab_block, ".anl-tab-btn must use foundation CSS tokens for color"


def test_ac2_table_header_uses_sufficient_contrast_token():
    """Table headers must NOT use --text-sub (#9ca3af on #fff = 2.5:1, fails WCAG AA)."""
    css = _style()
    m = re.search(r"\.anl-cal-table\s+th\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".anl-cal-table th rule not found"
    block = m.group(1)
    assert "var(--text-sub)" not in block, (
        ".anl-cal-table th must not use --text-sub — contrast #9ca3af on #fff is 2.5:1, "
        "below WCAG AA 4.5:1. Use --text-muted (#6b7280, 4.84:1 on white)."
    )


def test_ac2_metric_label_uses_sufficient_contrast_token():
    """Metric labels must NOT use --text-sub (#9ca3af on #fff = 2.5:1, fails WCAG AA)."""
    css = _style()
    block = _block(".metric-label", css)
    assert block, ".metric-label rule not found"
    assert "var(--text-sub)" not in block, (
        ".metric-label must not use --text-sub — use --text-muted for WCAG AA contrast"
    )


# ── AC3: card spacing uses foundation spacing tokens ─────────────────────────
def test_ac3_card_head_gap_on_scale():
    css = _style()
    block = _block(".anl-card-head", css)
    assert block, ".anl-card-head rule not found"
    m = re.search(r"gap:\s*(\d+)px", block)
    assert m, ".anl-card-head must define a gap"
    px = int(m.group(1))
    assert px in _SPACING_SCALE, (
        f".anl-card-head gap {px}px is off the foundation spacing scale "
        f"(allowed: {sorted(_SPACING_SCALE)})"
    )


def test_ac3_scatter_wrap_gap_on_scale():
    css = _style()
    block = _block(".anl-scatter-wrap", css)
    assert block, ".anl-scatter-wrap rule not found"
    m = re.search(r"gap:\s*(\d+)px", block)
    assert m, ".anl-scatter-wrap must define a gap"
    px = int(m.group(1))
    assert px in _SPACING_SCALE, (
        f".anl-scatter-wrap gap {px}px is off the foundation spacing scale"
    )


def test_ac3_metric_delta_gap_on_scale():
    css = _style()
    block = _block(".metric-delta", css)
    assert block, ".metric-delta rule not found"
    m = re.search(r"gap:\s*(\d+)px", block)
    assert m, ".metric-delta must define a gap"
    px = int(m.group(1))
    assert px in _SPACING_SCALE, (
        f".metric-delta gap {px}px is off the foundation spacing scale"
    )


def test_ac3_scope_pill_padding_on_scale():
    css = _style()
    block = _block(".scope-pill", css)
    assert block, ".scope-pill rule not found"
    off = [v for v in _spacing_px_vals(block) if v not in _SPACING_SCALE]
    assert not off, (
        f".scope-pill has off-scale spacing values: {off} "
        f"(allowed: {sorted(_SPACING_SCALE)})"
    )


# ── AC4: chart and table alignment follow foundation layout tokens ─────────────
def test_ac4_table_th_padding_on_scale():
    css = _style()
    m = re.search(r"\.anl-cal-table\s+th\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".anl-cal-table th rule not found"
    block = m.group(1)
    off = [v for v in _spacing_px_vals(block) if v not in _SPACING_SCALE]
    assert not off, (
        f".anl-cal-table th has off-scale spacing values: {off} "
        f"(allowed: {sorted(_SPACING_SCALE)})"
    )


def test_ac4_table_td_padding_on_scale():
    css = _style()
    m = re.search(r"\.anl-cal-table\s+td\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".anl-cal-table td rule not found"
    block = m.group(1)
    off = [v for v in _spacing_px_vals(block) if v not in _SPACING_SCALE]
    assert not off, (
        f".anl-cal-table td has off-scale spacing values: {off}"
    )


def test_ac4_velocity_row_gap_on_scale():
    css = _style()
    block = _block(".anl-velocity-row", css)
    assert block, ".anl-velocity-row rule not found"
    m = re.search(r"gap:\s*(\d+)px", block)
    assert m, ".anl-velocity-row must define a gap"
    px = int(m.group(1))
    assert px in _SPACING_SCALE, (
        f".anl-velocity-row gap {px}px is off the foundation spacing scale"
    )


def test_ac4_scatter_legend_spacing_on_scale():
    css = _style()
    block = _block(".anl-scatter-legend", css)
    assert block, ".anl-scatter-legend rule not found"
    off = [v for v in _spacing_px_vals(block) if v not in _SPACING_SCALE]
    assert not off, (
        f".anl-scatter-legend has off-scale spacing values: {off}"
    )


def test_ac4_legend_item_gap_on_scale():
    css = _style()
    block = _block(".anl-legend-item", css)
    assert block, ".anl-legend-item rule not found"
    m = re.search(r"gap:\s*(\d+)px", block)
    assert m, ".anl-legend-item must define a gap"
    px = int(m.group(1))
    assert px in _SPACING_SCALE, (
        f".anl-legend-item gap {px}px is off the foundation spacing scale"
    )


# ── AC5: typography hierarchy uses foundation type tokens ─────────────────────
def test_ac5_sans_token_defined_and_used():
    """--sans foundation token must be defined with a named non-generic font first.

    impeccable's GENERIC_FONTS list excludes -apple-system, BlinkMacSystemFont,
    Segoe UI, and system-ui — all of which are in the typical system sans stack.
    Starting with 'Helvetica Neue' gives impeccable a recognizable named font so
    it sees two distinct families (Helvetica Neue + SF Mono) instead of one.
    """
    css = _style()
    assert "--sans:" in css, (
        "--sans token must be defined in :root; body uses var(--sans) to pair "
        "with var(--mono) for clear typographic hierarchy"
    )
    # The named font that leads --sans must NOT be in impeccable's GENERIC_FONTS
    # (which excludes -apple-system, blinkmacsystemfont, segoe ui, system-ui).
    # 'Helvetica Neue' is the correct choice — it's a macOS system font that
    # impeccable recognizes as a distinct named font from SF Mono.
    sans_m = re.search(r"--sans:\s*([^;]+);", css)
    assert sans_m, "--sans definition not found in :root"
    sans_val = sans_m.group(1).strip()
    first_font = re.split(r",", sans_val)[0].strip().strip("'\"").lower()
    generic_fonts = {
        "serif", "sans-serif", "monospace", "cursive", "fantasy",
        "system-ui", "ui-serif", "ui-sans-serif", "ui-monospace", "ui-rounded",
        "-apple-system", "blinkmacsystemfont", "segoe ui",
        "inherit", "initial", "unset", "revert",
    }
    assert first_font not in generic_fonts, (
        f"--sans must start with a named non-generic font (got: '{first_font}'). "
        "Use 'Helvetica Neue' so impeccable recognizes two distinct font families."
    )
    body_block = _block("body", css)
    assert body_block, "body CSS rule not found"
    assert "var(--sans)" in body_block, (
        "body must use font-family: var(--sans) — needed so impeccable detects "
        "two font families (Helvetica Neue + SF Mono), not just SF Mono"
    )


def test_ac5_mono_token_used_not_hardcoded_in_components():
    css = _style()
    assert "var(--mono)" in css, "Foundation mono token var(--mono) must be used"
    # After removing the :root definition, 'SF Mono' must not appear in component rules
    root_stripped = re.sub(r":root\s*\{[^}]*\}", "", css, flags=re.DOTALL)
    assert "SF Mono" not in root_stripped, (
        "Hardcoded 'SF Mono' in component rules — use var(--mono) instead"
    )


def test_ac5_no_hardcoded_hex_in_component_rules():
    css = _style()
    rules = re.sub(r":root\s*\{[^}]*\}", "", css, flags=re.DOTALL)
    rules = re.sub(r'\[data-theme="dark"\]\s*\{[^}]*\}', "", rules, flags=re.DOTALL)
    hex_matches = re.findall(r"(?<![&\w])#[0-9a-fA-F]{3,8}\b", rules)
    assert not hex_matches, (
        f"Hardcoded hex colors found in analytics component CSS: {hex_matches}"
    )


# ── AC6: all fixes scoped to analytics surface ───────────────────────────────
def test_ac6_analytics_classes_present():
    css = _style()
    assert ".anl-" in css, "Analytics .anl- classes must be present"


def test_ac6_shared_nav_unchanged():
    html = _html()
    assert 'class="top-nav"' in html, "Shared top-nav must remain in place"
    assert 'class="btn-icon"' in html, "Shared btn-icon class must remain"


# ── AC7: dark theme integrity maintained ─────────────────────────────────────
def test_ac7_dark_theme_tokens_complete():
    css = _style()
    assert '[data-theme="dark"]' in css, "Dark theme override block must be present"
    dark_m = re.search(r'\[data-theme="dark"\]\s*\{([^}]*)\}', css, re.DOTALL)
    assert dark_m, "Dark theme block not parseable"
    dark_block = dark_m.group(1)
    for token in ("--bg", "--surface", "--text", "--border", "--blue"):
        assert token in dark_block, f"Dark theme block missing token: {token}"


# ── AC8: vanilla CSS/JS only — no new external dependencies ──────────────────
def test_ac8_no_new_external_dependencies():
    html = _html()
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    allowed = ["cdn.jsdelivr.net/npm/@tabler/icons-webfont"]
    for url in external:
        assert any(a in url for a in allowed), (
            f"Unexpected external dependency introduced: {url}"
        )


# ── AC9: all sub-tabs remain functional ──────────────────────────────────────
def test_ac9_all_four_tabs_present():
    html = _html()
    for tab in ("trends", "status", "metrics", "calibration"):
        assert f'id="anl-tab-{tab}"' in html, f"Tab button missing: anl-tab-{tab}"
        assert f'id="anl-panel-{tab}"' in html, f"Tab panel missing: anl-panel-{tab}"


def test_ac9_tab_onclick_handlers_wired():
    html = _html()
    for tab in ("trends", "status", "metrics", "calibration"):
        assert f"anlShowTab('{tab}')" in html, (
            f"Tab onclick missing for: {tab}"
        )
    assert "window.anlShowTab" in html, "anlShowTab function not defined on window"


def test_ac9_fetch_functions_present():
    html = _html()
    for fn in ("fetchCost", "fetchMetrics", "fetchCalibration"):
        assert f"window.{fn}" in html, f"Data-fetch function not exposed: {fn}"


# ── AC10: data rendering in metrics tab unchanged and correct ─────────────────
def test_ac10_metric_value_elements_intact():
    html = _html()
    for elem_id in (
        "fpr-val", "fpr-sub", "fpr-bar", "fpr-delta",
        "rwr-val", "rwr-sub", "rwr-bar", "rwr-delta",
        "dur-val", "dur-breakdown",
        "thr-val", "thr-sub",
        "cps-val", "cps-breakdown",
        "cpt-val", "cpt-sub",
    ):
        assert f'id="{elem_id}"' in html, f"Metrics element missing: #{elem_id}"


def test_ac10_metric_card_classes_intact():
    html = _html()
    assert 'class="metric-grid"' in html, "Metric grid container missing"
    assert "metric-card" in html, "metric-card class missing"
    assert "metric-label" in html, "metric-label class missing"
    assert "metric-val" in html, "metric-val class missing"
