"""Tests for issue #715 — Add Status and Trends sub-tabs to Analytics.

Acceptance criteria tested:
  AC1  — Analytics sub-tab bar reads exactly: Calibration | Metrics | Status | Trends (in order)
  AC2  — anlShowTab('status') renders pane-status/statusInit content in a new
         panel styled with anl-card / anl-card-head / anl-card-body
  AC3  — Status entry removed from the Analytics dropdown group in the top nav
  AC4  — anlShowTab('trends') renders three Chart.js charts in anl-card wrappers:
         metrics-velocity-chart, metrics-failure-chart, metrics-duration-chart
  AC5  — Trends charts fetch /api/metrics/sprints on first open only (lazy-load)
  AC6  — Status sub-tab calls statusInit() on first open only (lazy-load)
  AC7  — All four sub-tabs visually consistent (same anl-card chrome)
  AC8  — Switching sub-tabs does not reload the page or reset loaded tabs
  AC10 — Existing Calibration and Metrics sub-tabs are unaffected
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_STATIC_DIR = _DASHBOARD_ROOT / "static"
_PROJECT_HTML = _STATIC_DIR / "project.html"

for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


def _subtab_bar(html: str) -> str:
    """Return the inner HTML of the analytics sub-tab bar (anl-tab-bar)."""
    idx = html.find('class="anl-tab-bar"')
    assert idx >= 0, "anl-tab-bar must exist"
    end = html.find("</div>", idx)
    return html[idx:end]


# ---------------------------------------------------------------------------
# AC1: sub-tab bar reads exactly Calibration | Metrics | Status | Trends
# ---------------------------------------------------------------------------

class TestSubTabBar:
    def test_four_subtab_buttons_present(self, html):
        for n in ("calibration", "metrics", "status", "trends"):
            assert f'id="anl-tab-{n}"' in html, \
                f"Sub-tab button anl-tab-{n} must be present"

    def test_subtab_order_is_calibration_metrics_status_trends(self, html):
        bar = _subtab_bar(html)
        order = re.findall(r'id="anl-tab-(\w+)"', bar)
        assert order == ["calibration", "metrics", "status", "trends"], \
            f"Sub-tab order must be Calibration, Metrics, Status, Trends; got {order}"

    def test_subtab_labels_text(self, html):
        bar = _subtab_bar(html)
        for label in ("Calibration", "Metrics", "Status", "Trends"):
            assert f">{label}</button>" in bar, \
                f"Sub-tab bar must contain a '{label}' button"

    def test_status_and_trends_buttons_call_anlshowtab(self, html):
        bar = _subtab_bar(html)
        assert "anlShowTab('status')" in bar, \
            "Status sub-tab button must call anlShowTab('status')"
        assert "anlShowTab('trends')" in bar, \
            "Trends sub-tab button must call anlShowTab('trends')"


# ---------------------------------------------------------------------------
# AC2: Status panel uses anl-card chrome and the existing status IDs
# ---------------------------------------------------------------------------

class TestStatusPanel:
    def test_status_panel_exists(self, html):
        assert 'id="anl-panel-status"' in html, \
            "Status sub-tab panel anl-panel-status must exist"

    def test_status_panel_uses_anl_card_chrome(self, html):
        idx = html.find('id="anl-panel-status"')
        end = html.find('id="anl-panel-trends"', idx)
        assert end > idx, "anl-panel-trends must come after anl-panel-status"
        panel = html[idx:end]
        for cls in ("anl-card", "anl-card-head", "anl-card-body"):
            assert cls in panel, \
                f"Status panel must use {cls} class for visual consistency"

    def test_status_panel_keeps_status_body_ids(self, html):
        """statusInit/statusRefresh target these IDs — they must live in the panel."""
        idx = html.find('id="anl-panel-status"')
        end = html.find('id="anl-panel-trends"', idx)
        panel = html[idx:end]
        for el_id in ("status-sprint-body", "status-labels-body",
                      "status-activity-body", "status-inflight-body"):
            assert f'id="{el_id}"' in panel, \
                f"Status panel must contain #{el_id} (used by statusRefresh)"

    def test_old_standalone_status_pane_removed(self, html):
        assert 'id="pane-status"' not in html, \
            "Standalone pane-status must be removed (content moved into Analytics)"


# ---------------------------------------------------------------------------
# AC3: Status removed from the top-nav Analytics dropdown
# ---------------------------------------------------------------------------

class TestNavDropdown:
    def test_status_nav_entry_removed(self, html):
        assert 'id="stab-status"' not in html, \
            "Status entry must be removed from the top-nav Analytics dropdown"

    def test_nav_no_switchtab_status(self, html):
        assert "switchTab('status')" not in html, \
            "Top nav must not contain a switchTab('status') handler"

    def test_analytics_dropdown_still_has_analytics_entry(self, html):
        idx = html.find('id="stab-dropdown-analytics"')
        assert idx >= 0, "Analytics dropdown group must still exist"
        end = html.find("</div>", idx)
        # walk to the matching closing of the dropdown container
        dropdown = html[idx:idx + 600]
        assert 'id="stab-metrics"' in dropdown, \
            "Analytics dropdown must still contain the Analytics (metrics) entry"


# ---------------------------------------------------------------------------
# AC4: Trends panel has three Chart.js wrappers inside anl-card
# ---------------------------------------------------------------------------

class TestTrendsPanel:
    def test_trends_panel_exists(self, html):
        assert 'id="anl-panel-trends"' in html, \
            "Trends sub-tab panel anl-panel-trends must exist"

    def test_trends_panel_has_three_chart_wraps(self, html):
        idx = html.find('id="anl-panel-trends"')
        panel = html[idx:idx + 4000]
        for key in ("velocity", "failure", "duration"):
            assert f'id="metrics-{key}-wrap"' in panel, \
                f"Trends panel must contain metrics-{key}-wrap (chart mount point)"

    def test_trends_panel_uses_anl_card_chrome(self, html):
        idx = html.find('id="anl-panel-trends"')
        panel = html[idx:idx + 4000]
        for cls in ("anl-card", "anl-card-head", "anl-card-body"):
            assert cls in panel, \
                f"Trends panel must use {cls} class for visual consistency"

    def test_trends_card_titles(self, html):
        idx = html.find('id="anl-panel-trends"')
        panel = html[idx:idx + 4000]
        assert "Sprint Velocity" in panel, "Trends must label the Sprint Velocity chart"
        assert "Failure Rate" in panel, "Trends must label the Failure Rate Trend chart"
        assert "Dispatch Duration" in panel or "Avg Dispatch" in panel, \
            "Trends must label the Avg Dispatch Duration chart"


# ---------------------------------------------------------------------------
# AC5: Trends lazy-loads via a single fetch to /api/metrics/sprints
# ---------------------------------------------------------------------------

class TestTrendsLazyLoad:
    def test_trends_init_fetches_metrics_sprints(self, html):
        assert "/api/metrics/sprints" in html, \
            "Trends loader must fetch /api/metrics/sprints"

    def test_trends_loader_exposed_for_subtab(self, html):
        """The Trends chart loader must be reachable from anlShowTab (not shadowed
        by the metricsInit override that opens Calibration)."""
        assert "window.anlTrendsInit" in html, \
            "Trends chart loader must be exposed as window.anlTrendsInit"

    def test_anlshowtab_trends_calls_loader(self, html):
        idx = html.find("window.anlShowTab = function")
        assert idx >= 0, "anlShowTab must be defined"
        body = html[idx:idx + 1200]
        assert "anlTrendsInit" in body, \
            "anlShowTab('trends') must call the Trends loader"

    def test_anlshowtab_trends_load_guarded(self, html):
        """Trends loader must only be invoked once (first open) — guarded by a flag."""
        idx = html.find("window.anlShowTab = function")
        body = html[idx:idx + 1200]
        assert "_anlTrendsInited" in body, \
            "anlShowTab must guard the Trends loader with a first-open flag"


# ---------------------------------------------------------------------------
# AC6: Status lazy-loads via statusInit on first open
# ---------------------------------------------------------------------------

class TestStatusLazyLoad:
    def test_anlshowtab_status_calls_statusinit(self, html):
        idx = html.find("window.anlShowTab = function")
        body = html[idx:idx + 1200]
        assert "statusInit" in body, \
            "anlShowTab('status') must call statusInit()"

    def test_statusinit_guards_against_refetch(self, html):
        """statusInit already guards re-fetch via _statusLoaded / _statusRefreshId."""
        idx = html.find("async function statusInit")
        assert idx >= 0, "statusInit must be defined"
        body = html[idx:idx + 400]
        assert "_statusLoaded" in body and "_statusRefreshId" in body, \
            "statusInit must guard so subsequent opens do not re-fetch"


# ---------------------------------------------------------------------------
# AC7 / AC8 / AC10: consistency, no reload, existing tabs intact
# ---------------------------------------------------------------------------

class TestConsistencyAndRegression:
    def test_anlshowtab_toggles_all_four_panels(self, html):
        idx = html.find("window.anlShowTab = function")
        body = html[idx:idx + 1200]
        for n in ("calibration", "metrics", "status", "trends"):
            assert f"'{n}'" in body, \
                f"anlShowTab must toggle the '{n}' panel"

    def test_anlshowtab_does_not_reload_page(self, html):
        """AC8: tab switch toggles 'show' class, never reloads (no location assignment)."""
        idx = html.find("window.anlShowTab = function")
        end = html.find("};", idx)
        body = html[idx:end]
        assert "location.reload" not in body and "location.href" not in body, \
            "anlShowTab must not reload the page on tab switch"
        assert "classList.toggle('show'" in body, \
            "anlShowTab must toggle the 'show' class to switch panels"

    def test_calibration_panel_unaffected(self, html):
        assert 'id="anl-panel-calibration"' in html, \
            "Existing Calibration panel must remain"
        assert 'id="scatter-svg"' in html, "Calibration scatter plot must remain"

    def test_metrics_panel_unaffected(self, html):
        assert 'id="anl-panel-metrics"' in html, \
            "Existing Metrics (delivery-health) panel must remain"
        assert 'id="metric-grid"' in html, "Metrics delivery-health grid must remain"
