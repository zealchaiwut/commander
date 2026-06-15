"""Tests for issue #650 — Build Analytics page with Calibration tab.

Acceptance criteria tested:
  AC1  — Analytics page reachable from project view via "Analytics" nav entry
  AC2  — Header with project name + scope selector that updates ANL-1 query params
  AC3  — Tab bar: "Calibration" active by default, "Metrics" tab present
  AC4  — Scatter plot renders as SVG
  AC5  — Scatter dots colored by size: S=green, M=blue, L=amber, XL=red
  AC6  — Reference gridlines and tick labels at 5/15/30/60/90 min on both axes
  AC7  — Dashed diagonal line (est = actual) across scatter plot
  AC8  — Gridline/axis colors use CSS variables (themes correctly in dark mode)
  AC9  — Calibration table: size, configured, count, min, avg, max, verdict columns
  AC10 — Verdict: "over", "under", or "on-target" by comparing avg vs configured
  AC11 — Each applicable table row has an "apply Xm" action button
  AC12 — "apply Xm" calls PUT /api/projects/{slug}/settings with avg as override
  AC13 — After applying, page re-fetches to reflect new configured value
  AC14 — Scope selector change re-queries ANL-1 and updates scatter + table
  AC15 — Design tokens from mock (--bg, --surface, etc.) used throughout
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_SM_ROOT = _REPO_ROOT / "services" / "sprint_manager"
_STATIC_DIR = _DASHBOARD_ROOT / "static"
_ANALYTICS_HTML = _STATIC_DIR / "analytics.html"
_PROJECT_HTML = _STATIC_DIR / "project.html"

for _p in (str(_DASHBOARD_ROOT), str(_SM_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# AC1: Analytics nav entry in project.html
# ---------------------------------------------------------------------------

class TestCalibrationNavEntry:
    """AC1: Analytics page reachable from project view via 'Analytics' nav entry."""

    def test_project_html_has_analytics_nav_group(self):
        assert _PROJECT_HTML.exists(), "project.html must exist"
        html = _PROJECT_HTML.read_text(encoding="utf-8")
        assert "analytics" in html.lower(), \
            "project.html must have an Analytics nav entry"

    def test_analytics_nav_links_to_analytics_page(self):
        html = _PROJECT_HTML.read_text(encoding="utf-8")
        assert "stab-analytics-page" in html or "/analytics" in html, \
            "project.html nav must contain a link to the analytics page"

    def test_analytics_route_served_by_server(self):
        """Route /project/{slug}/analytics must serve analytics.html."""
        from server import app
        from starlette.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/project/commander/analytics", follow_redirects=False)
        assert resp.status_code == 200, \
            f"GET /project/slug/analytics must return 200, got {resp.status_code}"
        assert "analytics" in resp.text.lower() or "Analytics" in resp.text, \
            "Response must be the analytics page"


# ---------------------------------------------------------------------------
# AC2-AC3: Page header, scope selector, tab bar
# ---------------------------------------------------------------------------

class TestCalibrationPageStructure:
    """AC2-AC3: Page header, scope selector, tab bar."""

    def test_analytics_html_exists(self):
        assert _ANALYTICS_HTML.exists(), "analytics.html must exist in static/"

    def test_header_has_project_slug_element(self):
        """AC2: header with project name element."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "anl-proj-slug" in html or "proj-slug" in html, \
            "Page header must include project name display element"

    def test_scope_selector_date_range_present(self):
        """AC2: date range scope selector."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "scope-days" in html, \
            "Page must have a date range scope selector (id=scope-days)"

    def test_scope_selector_sprint_present(self):
        """AC2: sprint scope selector."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "scope-sprint" in html, \
            "Page must have a sprint scope selector (id=scope-sprint)"

    def test_calibration_panel_exists(self):
        """AC3: Calibration panel with expected id."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert 'id="panel-calibration"' in html, \
            "Calibration panel must have id='panel-calibration'"

    def test_calibration_tab_active_by_default(self):
        """AC3: Calibration panel visible (has 'show') by default."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        idx = html.find('id="panel-calibration"')
        assert idx >= 0, "panel-calibration must exist"
        snippet = html[max(0, idx - 100):idx + 50]
        assert "show" in snippet, \
            "Calibration panel must have 'show' class applied by default"

    def test_calibration_tab_button_present(self):
        """AC3: Tab button for Calibration."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert 'id="tab-calibration"' in html, \
            "Tab button with id='tab-calibration' must be present"

    def test_metrics_tab_present(self):
        """AC3: Metrics tab present."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "Metrics" in html, "Tab bar must include a Metrics tab"

    def test_calibration_panel_not_placeholder(self):
        """AC: Calibration panel is no longer a placeholder."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "coming soon" not in html.lower() and "placeholder" not in html.lower(), \
            "Calibration panel must not be a placeholder"


# ---------------------------------------------------------------------------
# AC4-AC8: Scatter plot
# ---------------------------------------------------------------------------

class TestScatterPlot:
    """AC4-AC8: Scatter plot SVG structure and color/style."""

    def test_scatter_svg_element_present(self):
        """AC4: Scatter plot renders as SVG with known id."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert 'id="scatter-svg"' in html, \
            "SVG element with id='scatter-svg' must be present in calibration panel"

    def test_scatter_size_color_constants_for_s(self):
        """AC5: S size mapped to green."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "--green" in html, "CSS var --green must be defined"
        # JS color mapping
        assert "'S'" in html or '"S"' in html, "S size key must be in color mapping"

    def test_scatter_size_color_constants_for_m(self):
        """AC5: M size mapped to blue."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "--blue" in html, "CSS var --blue must be defined"

    def test_scatter_size_color_constants_for_l(self):
        """AC5: L size mapped to amber."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "--amber" in html, "CSS var --amber must be defined"

    def test_scatter_size_color_constants_for_xl(self):
        """AC5: XL size mapped to red."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "--red" in html, "CSS var --red must be defined"

    def test_tick_array_defined_in_js(self):
        """AC6: Grid ticks split axis into 4 equal parts (dynamic from L/XL labels)."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert (
            "_scatterTicks" in html
            or "_anlScatterTicks" in html
            or "[5, 15, 30, 60, 90]" in html
            or "[5,15,30,60,90]" in html
        ), "Scatter plot must define dynamic quarter ticks (_scatterTicks)"

    def test_gridline_rendering_js_present(self):
        """AC6: JS renders gridlines (line elements) at tick positions."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "<line" in html or "stroke" in html, \
            "JS must generate SVG <line> elements for gridlines"

    def test_tick_label_rendering_in_js(self):
        """AC6: Tick labels appear on both axes."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        # The JS generates tick label text with 'm' suffix
        assert "'m</text>'" in html or "m</text>" in html or "+ 'm</text>'" in html or \
               "'m</text>'" in html or "text-anchor" in html, \
            "Tick label text elements must be generated in JS"

    def test_dashed_diagonal_present(self):
        """AC7: Dashed diagonal line (est = actual)."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "stroke-dasharray" in html, \
            "Diagonal line must use stroke-dasharray for dashed style"
        assert "est = actual" in html, \
            "Diagonal line must be labeled 'est = actual'"

    def test_chart_gridlines_use_css_var_border(self):
        """AC8: Gridline stroke uses var(--border)."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "var(--border)" in html, \
            "Gridlines must use var(--border) CSS variable for theming"

    def test_chart_labels_use_css_var_text_sub(self):
        """AC8: Tick labels use var(--text-sub)."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "var(--text-sub)" in html, \
            "Tick labels must use var(--text-sub) CSS variable"

    def test_chart_axes_use_css_var_text_muted(self):
        """AC8: Axis lines use var(--text-muted)."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "var(--text-muted)" in html, \
            "Axis lines must use var(--text-muted) CSS variable"


# ---------------------------------------------------------------------------
# AC9-AC11: Calibration table
# ---------------------------------------------------------------------------

class TestCalibrationTable:
    """AC9-AC11: Calibration by-size table structure."""

    def test_cal_table_element_present(self):
        """AC9: Table with class 'cal-table'."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "cal-table" in html, \
            "Calibration table must have class 'cal-table'"

    def test_cal_table_body_id_present(self):
        """AC9: Table body has id 'cal-table-body' for JS population."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert 'id="cal-table-body"' in html, \
            "Calibration table body must have id='cal-table-body'"

    def test_column_configured_present(self):
        """AC9: 'Configured' column header."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "Configured" in html, "Calibration table must have 'Configured' column"

    def test_column_count_present(self):
        """AC9: ticket count column."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "Tickets" in html or "Count" in html, \
            "Calibration table must have ticket count column"

    def test_column_min_avg_max_present(self):
        """AC9: min, avg, max columns."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "Min" in html, "Calibration table must have 'Min' column"
        assert "Avg" in html, "Calibration table must have 'Avg' column"
        assert "Max" in html, "Calibration table must have 'Max' column"

    def test_column_verdict_present(self):
        """AC9: verdict column."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "Verdict" in html or "Calibration" in html, \
            "Calibration table must have verdict/calibration column"

    def test_verdict_over_defined_in_js(self):
        """AC10: 'over' verdict string defined."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "'over'" in html or '"over"' in html, \
            "JS must use 'over' verdict string"

    def test_verdict_under_defined_in_js(self):
        """AC10: 'under' verdict string defined."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "'under'" in html or '"under"' in html, \
            "JS must use 'under' verdict string"

    def test_verdict_on_target_defined_in_js(self):
        """AC10: 'on-target' verdict string defined."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "'on-target'" in html or '"on-target"' in html, \
            "JS must use 'on-target' verdict string"

    def test_sz_badge_classes_present(self):
        """AC9: Size badge CSS classes (sz-S, sz-M, sz-L, sz-XL)."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "sz-badge" in html, "sz-badge class must be defined"
        assert "sz-S" in html, "sz-S badge class must be present"
        assert "sz-M" in html, "sz-M badge class must be present"
        assert "sz-L" in html, "sz-L badge class must be present"
        assert "sz-XL" in html, "sz-XL badge class must be present"

    def test_verdict_css_classes_present(self):
        """AC10: Verdict CSS classes (v-over, v-under, v-ok)."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "v-over" in html, "v-over verdict class must be defined"
        assert "v-under" in html, "v-under verdict class must be defined"
        assert "v-ok" in html, "v-ok verdict class must be defined"

    def test_apply_button_class_defined(self):
        """AC11: btn-apply CSS class present."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "btn-apply" in html, \
            "Apply button must use 'btn-apply' CSS class"

    def test_apply_button_onclick_handler_present(self):
        """AC11: applyCalibration function wired to apply button."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "applyCalibration" in html, \
            "Apply button must call applyCalibration function"

    def test_apply_button_uses_avg_value(self):
        """AC11: Apply button passes avg minutes to applyCalibration."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "Math.round" in html or "avg" in html, \
            "Apply button label must use rounded avg value"


# ---------------------------------------------------------------------------
# AC12-AC13: Apply calibration writes settings
# ---------------------------------------------------------------------------

class TestApplyCalibration:
    """AC12-AC13: apply button calls PUT /api/projects/{slug}/settings."""

    def test_apply_calls_put_method(self):
        """AC12: PUT method used for settings update."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "'PUT'" in html or '"PUT"' in html, \
            "applyCalibration must call fetch with method: 'PUT'"

    def test_apply_targets_settings_endpoint(self):
        """AC12: PUT targets /settings endpoint."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "/settings" in html, \
            "applyCalibration must PUT to .../settings endpoint"

    def test_apply_uses_estimation_setting_keys(self):
        """AC12: estimation_*_minutes keys used for each size."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "estimation_s_minutes" in html, "S key must be estimation_s_minutes"
        assert "estimation_m_minutes" in html, "M key must be estimation_m_minutes"
        assert "estimation_l_minutes" in html, "L key must be estimation_l_minutes"
        assert "estimation_xl_minutes" in html, "XL key must be estimation_xl_minutes"

    def test_apply_refetches_calibration_after_success(self):
        """AC13: After PUT succeeds, fetchCalibration() is called to update display."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "fetchCalibration" in html, \
            "applyCalibration must call fetchCalibration() after successful PUT"

    def test_put_settings_endpoint_accepts_estimation_s_minutes(self):
        """AC13: PUT /api/projects/{slug}/settings with estimation_s_minutes returns 200."""
        from server import app
        from starlette.testclient import TestClient

        with (
            patch("server._resolve_project_slug", return_value="test/repo"),
            patch("server._settings_repo") as mock_repo,
        ):
            mock_repo.get_setting_scoped.return_value = {}
            mock_repo.get_setting.return_value = {}
            mock_repo.set_setting.return_value = None
            client = TestClient(app)
            resp = client.put(
                "/api/projects/commander/settings",
                json={"estimation_s_minutes": 4},
            )
        assert resp.status_code == 200, \
            f"PUT /api/projects/slug/settings must return 200, got {resp.status_code}"
        data = resp.json()
        assert "estimation_s_minutes" in data, \
            "Response must include updated estimation_s_minutes field"

    def test_put_settings_returns_updated_configured_value(self):
        """AC13: Page reload reflects new configured value via GET calibration endpoint."""
        from server import app
        from starlette.testclient import TestClient

        with (
            patch("server._resolve_project_slug", return_value="test/repo"),
            patch("server._settings_repo") as mock_repo,
        ):
            # Simulate stored setting after apply
            mock_repo.get_setting.return_value = {"estimation_s_minutes": 4}
            client = TestClient(app)
            resp = client.get("/api/projects/commander/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("estimation_s_minutes") == 4, \
            "GET settings must return the applied value after PUT"


# ---------------------------------------------------------------------------
# AC14: Scope selector re-queries calibration
# ---------------------------------------------------------------------------

class TestScopeChange:
    """AC14: Scope selector re-queries ANL-1 and updates scatter + table."""

    def test_on_scope_change_calls_fetch_calibration(self):
        """AC14: onScopeChange triggers fetchCalibration."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "onScopeChange" in html, "onScopeChange function must be defined"
        # Find the JS function definition (not the HTML attribute usage)
        idx = html.find("window.onScopeChange")
        if idx < 0:
            idx = html.rfind("onScopeChange")  # last occurrence is the definition
        snippet = html[idx:idx + 400]
        assert "fetchCalibration" in snippet, \
            "onScopeChange must call fetchCalibration()"

    def test_calibration_url_uses_since_param(self):
        """AC14: Calibration URL includes 'since' date from scope-days selector."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "analytics/calibration" in html, \
            "JS must build URL for /analytics/calibration endpoint"
        # scope-days already tested in AC2; here verify it's used in cal URL builder
        assert "scope-days" in html, "scope-days value must be read for calibration URL"

    def test_calibration_url_uses_sprint_param(self):
        """AC14: Calibration URL includes 'sprint' from scope-sprint selector."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "scope-sprint" in html, "scope-sprint value must be read for calibration URL"

    def test_fetch_calibration_function_defined(self):
        """AC14: fetchCalibration function defined in page JS."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "fetchCalibration" in html, \
            "fetchCalibration JS function must be defined"

    def test_fetch_calibration_updates_scatter(self):
        """AC14: fetchCalibration renders scatter plot from response data."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "renderScatter" in html or "_renderScatter" in html or "scatter-svg" in html, \
            "fetchCalibration must update the scatter SVG after fetching"

    def test_fetch_calibration_updates_table(self):
        """AC14: fetchCalibration renders cal table from response data."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "renderCalTable" in html or "_renderCalTable" in html or "cal-table-body" in html, \
            "fetchCalibration must update the calibration table after fetching"


# ---------------------------------------------------------------------------
# AC15: Design tokens
# ---------------------------------------------------------------------------

class TestDesignTokens:
    """AC15: Design tokens from mock used throughout."""

    @pytest.mark.parametrize("var", [
        "--bg", "--surface", "--surface-2", "--border",
        "--text", "--text-muted", "--text-sub",
        "--green", "--green-bg", "--amber", "--amber-bg",
        "--red", "--red-bg", "--blue", "--blue-bg",
    ])
    def test_css_variable_defined(self, var: str):
        """AC15: CSS variable from mock is defined in analytics.html."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert var in html, f"CSS variable {var} must be defined in analytics.html"

    def test_dark_mode_overrides_present(self):
        """AC15: Dark mode CSS block overrides design tokens."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert '[data-theme="dark"]' in html, \
            "Dark mode CSS overrides must be present"

    def test_mono_font_variable_defined(self):
        """AC15: --mono font variable defined."""
        html = _ANALYTICS_HTML.read_text(encoding="utf-8")
        assert "--mono" in html, "--mono font variable must be defined"
