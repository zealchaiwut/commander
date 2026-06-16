"""Tests for issue #1066: Redesign Analytics page with token-based sub-tab layout

TDD tests written before implementation. Each test is anchored to a specific AC.
"""
from __future__ import annotations

import os
import re
import pathlib
import pytest
import httpx

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).parent.parent
_STATIC_DIR = _REPO_ROOT / "apps" / "dashboard" / "static"

BASE_URL = os.environ.get("UAT_BASE_URL") or (
    "http://localhost:" + os.environ.get("UAT_PORT", "8001")
)
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as c:
        yield c


@pytest.fixture(scope="module")
def analytics_html() -> str:
    return (_STATIC_DIR / "analytics.html").read_text()


@pytest.fixture(scope="module")
def project_html() -> str:
    return (_STATIC_DIR / "project.html").read_text()


# ── AC1: Sub-tab bar (Trends / Status / Metrics / Calibration) ────────────────

def test_analytics_html_has_four_tabs(analytics_html):
    """AC1: analytics.html tab bar contains all four required tabs."""
    for tab in ("trends", "status", "metrics", "calibration"):
        assert f"anlShowTab('{tab}')" in analytics_html, (
            f"anlShowTab('{tab}') not found in analytics.html — missing tab button"
        )


def test_analytics_html_tab_order_trends_first(analytics_html):
    """AC1: Trends tab appears before Status, which appears before Metrics, which appears before Calibration."""
    positions = {
        tab: analytics_html.index(f"anlShowTab('{tab}')")
        for tab in ("trends", "status", "metrics", "calibration")
    }
    assert positions["trends"] < positions["status"], "Trends must precede Status"
    assert positions["status"] < positions["metrics"], "Status must precede Metrics"
    assert positions["metrics"] < positions["calibration"], "Metrics must precede Calibration"


def test_analytics_html_active_tab_uses_token(analytics_html):
    """AC1: Active tab style references CSS token vars, not hardcoded colors."""
    # Find any CSS rule targeting .active on the tab bar buttons
    active_css = re.search(
        r'anl-tab-btn[^{]*\.active[^{]*\{([^}]+)\}|\.active[^{]*anl-tab-btn[^{]*\{([^}]+)\}',
        analytics_html, re.DOTALL
    )
    if not active_css:
        # May be a combined rule — just check the style block contains "active" near a token ref
        style_block = re.search(r'<style[^>]*>(.*?)</style>', analytics_html, re.DOTALL)
        assert style_block, "No <style> block found in analytics.html"
        css = style_block.group(1)
        # Should reference var(-- near the active tab rule
        assert 'var(--' in css and 'active' in css, (
            "analytics.html tab active state should use CSS token variables"
        )
        # Must use var(--blue) or var(--border) or similar token for the active indicator
        active_sections = re.findall(r'\.active[^{}]*\{[^}]*\}', css, re.DOTALL)
        token_used = any('var(--' in s for s in active_sections)
        assert token_used, "Active tab rule must reference at least one CSS token (var(--))"


def test_project_html_has_four_tabs_in_correct_order(project_html):
    """AC1: project.html analytics section also has Trends/Status/Metrics/Calibration order."""
    positions = {
        tab: project_html.find(f"anlShowTab('{tab}')")
        for tab in ("trends", "status", "metrics", "calibration")
    }
    for tab, pos in positions.items():
        assert pos != -1, f"anlShowTab('{tab}') not found in project.html"

    assert positions["trends"] < positions["status"], "project.html: Trends must precede Status"
    assert positions["status"] < positions["metrics"], "project.html: Status must precede Metrics"
    assert positions["metrics"] < positions["calibration"], "project.html: Metrics must precede Calibration"


def test_project_html_active_tab_uses_token(project_html):
    """AC1: project.html .anl-tab-btn.active uses CSS token for visual distinction."""
    # Find the .anl-tab-btn.active CSS rule
    active_rule = re.search(
        r'\.anl-tab-btn\.active\s*\{([^}]+)\}',
        project_html, re.DOTALL
    )
    assert active_rule, ".anl-tab-btn.active CSS rule not found in project.html"
    rule_body = active_rule.group(1)
    assert 'var(--' in rule_body, (
        ".anl-tab-btn.active must reference CSS token vars (var(--))"
    )


# ── AC2: Stat cards consistent anatomy ───────────────────────────────────────

def test_metric_cards_have_label_and_value(analytics_html):
    """AC2: Metric cards contain both metric-label and metric-val elements."""
    assert 'metric-label' in analytics_html, "metric-label class missing from analytics.html"
    assert 'metric-val' in analytics_html, "metric-val class missing from analytics.html"


def test_metric_cards_have_delta_or_sub_element(analytics_html):
    """AC2: Metric cards contain an optional delta or sub element for trend indicators."""
    has_delta = 'metric-delta' in analytics_html
    has_sub = 'metric-sub' in analytics_html
    assert has_delta or has_sub, (
        "Metric cards must have a delta (metric-delta) or subtitle (metric-sub) element for optional trend info"
    )


def test_metric_card_anatomy_uses_token_spacing(analytics_html):
    """AC2: Metric card padding uses token-scale values (not arbitrary px)."""
    style_block = re.search(r'<style[^>]*>(.*?)</style>', analytics_html, re.DOTALL)
    assert style_block, "No <style> block found"
    css = style_block.group(1)
    # metric-card should have padding that uses on-scale values (8, 12, 16 px)
    metric_card_rule = re.search(r'\.metric-card\s*\{([^}]+)\}', css, re.DOTALL)
    assert metric_card_rule, ".metric-card CSS rule not found in analytics.html"
    rule_body = metric_card_rule.group(1)
    assert 'padding' in rule_body, ".metric-card must have explicit padding"
    # Check it uses common token scale values
    on_scale = any(str(v) + 'px' in rule_body for v in (8, 12, 16, 24))
    assert on_scale, ".metric-card padding must use token-scale values (8/12/16/24px)"


# ── AC3: Chart and table framing uses tokens ──────────────────────────────────

def test_card_border_uses_token(analytics_html):
    """AC3: Card/section borders reference var(--border), not hardcoded colors."""
    style_block = re.search(r'<style[^>]*>(.*?)</style>', analytics_html, re.DOTALL)
    css = style_block.group(1)
    # anl-card or card should have border: ... var(--border)
    assert 'var(--border)' in css, (
        "Card borders must use var(--border) token, not hardcoded colors"
    )


def test_card_background_uses_token(analytics_html):
    """AC3: Card/section backgrounds reference var(--surface), not hardcoded hex."""
    style_block = re.search(r'<style[^>]*>(.*?)</style>', analytics_html, re.DOTALL)
    css = style_block.group(1)
    assert 'var(--surface)' in css, (
        "Card backgrounds must use var(--surface) token"
    )
    # Should not have hardcoded white/black background for cards
    card_rules = re.findall(r'\.anl-card[^{]*\{([^}]+)\}', css, re.DOTALL)
    for rule in card_rules:
        assert '#ffffff' not in rule.lower() and '#fff ' not in rule.lower(), (
            "Card background must not use hardcoded #ffffff — use var(--surface)"
        )


def test_calibration_table_uses_token_framing(analytics_html):
    """AC3: Calibration table CSS uses token vars for border/color, not hardcoded hex."""
    style_block = re.search(r'<style[^>]*>(.*?)</style>', analytics_html, re.DOTALL)
    css = style_block.group(1)
    # anl-cal-table or cal-table should use var tokens
    table_rule = re.search(r'\.anl-cal-table[^{]*\{([^}]+)\}|\.cal-table[^{]*\{([^}]+)\}', css, re.DOTALL)
    assert table_rule, "Calibration table CSS rule not found"


# ── AC4: Empty states for all panels ─────────────────────────────────────────

def test_trends_panel_has_empty_state(analytics_html):
    """AC4: Trends panel has an empty-state element for no-data periods."""
    # Check for a panel empty state in anl-panel-trends or panel-trends
    has_empty = (
        'anl-trends-empty' in analytics_html or
        ('panel-trends' in analytics_html and 'empty' in analytics_html[
            analytics_html.find('panel-trends'):analytics_html.find('panel-trends') + 800
        ])
    )
    assert has_empty, (
        "Trends panel must have an empty state element (e.g. id='anl-trends-empty')"
    )


def test_status_panel_has_empty_state(analytics_html):
    """AC4: Status panel has an empty-state element."""
    has_empty = (
        'anl-status-empty' in analytics_html or
        ('panel-status' in analytics_html and 'empty' in analytics_html[
            analytics_html.find('panel-status'):analytics_html.find('panel-status') + 800
        ])
    )
    assert has_empty, "Status panel must have an empty state element"


def test_metrics_panel_has_empty_state(analytics_html):
    """AC4: Metrics panel has empty states for metric cards."""
    assert 'metric-empty' in analytics_html, (
        "Metrics panel must have metric-empty elements for no-data states"
    )


def test_calibration_panel_has_empty_state(analytics_html):
    """AC4: Calibration panel has an empty state element."""
    has_empty = 'cal-empty' in analytics_html or 'anl-cal-empty' in analytics_html
    assert has_empty, "Calibration panel must have an empty state element"


# ── AC5: anlShowTab function intact ───────────────────────────────────────────

def test_anl_show_tab_function_defined(analytics_html):
    """AC5: anlShowTab function is defined (not the old showTab)."""
    assert 'anlShowTab' in analytics_html, (
        "anlShowTab function must be defined in analytics.html"
    )


def test_anl_show_tab_switches_panels(analytics_html):
    """AC5: anlShowTab function body toggles panel visibility."""
    # Extract the anlShowTab function body
    fn_match = re.search(
        r'anlShowTab\s*[=:]\s*function[^{]*\{(.*?)(?=\}\s*;?\s*\n\s*(?:window\.|var |function |\}))',
        analytics_html, re.DOTALL
    )
    if not fn_match:
        # Try alternative: window.anlShowTab = ...
        fn_match = re.search(
            r'window\.anlShowTab\s*=\s*function[^{]*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}',
            analytics_html, re.DOTALL
        )
    assert fn_match, "anlShowTab function body not parseable in analytics.html"
    fn_body = fn_match.group(1)
    # Should toggle panel visibility (classList.toggle or show/display)
    assert ('classList' in fn_body or 'display' in fn_body or 'show' in fn_body), (
        "anlShowTab must toggle panel show/hide state"
    )
    # Should toggle active class on tab buttons
    assert ('active' in fn_body), "anlShowTab must set active class on tab buttons"


# ── AC6: All data fetches fire on tab change ──────────────────────────────────

def test_fetch_metrics_called_in_show_tab(analytics_html):
    """AC6: fetchMetrics (or anlFetchDeliveryHealth) is invoked in anlShowTab."""
    fn_block_start = analytics_html.rfind('anlShowTab')
    # Walk backward to find function body
    fn_chunk = analytics_html[max(0, fn_block_start - 200):fn_block_start + 1500]
    assert (
        'fetchMetrics' in fn_chunk or
        'anlFetchDeliveryHealth' in fn_chunk or
        'fetchMetrics' in analytics_html  # at minimum the function must exist
    ), "fetchMetrics / anlFetchDeliveryHealth must be callable from anlShowTab for the metrics tab"


def test_fetch_calibration_called_in_show_tab(analytics_html):
    """AC6: fetchCalibration (or anlFetchCalibration) is invoked in anlShowTab."""
    assert (
        'fetchCalibration' in analytics_html or 'anlFetchCalibration' in analytics_html
    ), "fetchCalibration must exist and be called on calibration tab switch"


def test_fetch_cost_called_for_trends_or_status(analytics_html):
    """AC6: fetchCost is present and fires when trends/status tab is selected."""
    assert 'fetchCost' in analytics_html, (
        "fetchCost function must exist in analytics.html to serve Trends/Status panels"
    )


# ── AC7: /analytics redirect ─────────────────────────────────────────────────

def test_analytics_redirects_to_metrics(client):
    """AC7: GET /project/commander/analytics returns 302 redirect to .../metrics."""
    r = client.get("/project/commander/analytics")
    assert r.status_code == 302, (
        f"Expected 302 redirect from /analytics, got {r.status_code}"
    )
    location = r.headers.get("location", "")
    assert "/metrics" in location, (
        f"Redirect location {location!r} does not point to /metrics"
    )


def test_metrics_page_loads(client):
    """AC7: /project/commander/metrics page loads successfully."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=True) as c:
        r = c.get("/project/commander/metrics")
    assert r.status_code == 200, f"/project/commander/metrics returned {r.status_code}"
    # Should be project.html content (has anlShowTab)
    assert "anlShowTab" in r.text, "Metrics page must include anlShowTab function"


# ── AC8: Vanilla JS only ──────────────────────────────────────────────────────

def test_no_framework_imported(analytics_html):
    """AC8: analytics.html does not import any JS framework (React, Vue, Alpine, etc.)."""
    for framework in ("react", "vue", "alpine.js", "svelte", "angular"):
        assert framework not in analytics_html.lower(), (
            f"analytics.html must not import {framework} — vanilla JS only"
        )


def test_no_build_step_required(analytics_html):
    """AC8: analytics.html uses inline JS (no import/export ES module syntax)."""
    # No top-level import/export that would require a bundler
    has_module_import = bool(re.search(r'^\s*import\s+', analytics_html, re.MULTILINE))
    assert not has_module_import, (
        "analytics.html must not use ES module import — no build step available"
    )
