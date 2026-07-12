"""Tests for issue #1856: Analytics promoted to top-level tab, lands on Metrics sub-tab.

TDD tests written before implementation.
Each test is anchored to a specific acceptance criterion.

Run with:
    pytest tests/test_1856__analytics_tab_promotion.py -v
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import httpx
import pytest

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).parent.parent
_STATIC_DIR = _REPO_ROOT / "apps" / "dashboard" / "static"
_PROJECT_HTML = _STATIC_DIR / "project.html"
_TABS_JS = _STATIC_DIR / "src" / "shell" / "tabs.js"

BASE_URL = os.environ.get("UAT_BASE_URL") or (
    "http://localhost:" + os.environ.get("UAT_PORT", "8001")
)
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=True) as c:
        yield c


@pytest.fixture(scope="module")
def project_html() -> str:
    return _PROJECT_HTML.read_text()


@pytest.fixture(scope="module")
def tabs_js() -> str:
    return _TABS_JS.read_text()


# ── AC1: Opening Analytics lands on the Metrics sub-tab ──────────────────────

def test_ac1_metrics_init_opens_metrics_subtab(project_html):
    """AC1: metricsInit must call anlShowTab('metrics'), not anlShowTab('calibration').

    Behavioral: executes metricsInit with a stub anlShowTab and asserts the
    argument passed, rather than just checking source text.
    """
    node_script = r"""
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(
    path.join(__dirname, '../apps/dashboard/static/project.html'),
    'utf-8'
);

// Extract the metricsInit override line
const match = html.match(/window\.metricsInit\s*=\s*function\s*\(\)\s*\{([^}]*)\}/);
if (!match) {
    console.error('FAIL: metricsInit override not found in project.html');
    process.exit(1);
}

const calls = [];
const win = {
    anlShowTab: (name) => { calls.push(name); },
    metricsInit: null,
};

// Execute the override so win.metricsInit is set
const body = match[0];
const fn = new Function('window', body.replace(/window\.metricsInit/, 'window.metricsInit'));
fn(win);

if (!win.metricsInit) {
    console.error('FAIL: metricsInit not set on window after executing the override');
    process.exit(1);
}

win.metricsInit();

if (calls.length !== 1) {
    console.error('FAIL: expected anlShowTab to be called once, got ' + calls.length);
    process.exit(1);
}
if (calls[0] !== 'metrics') {
    console.error('FAIL: expected anlShowTab("metrics"), got anlShowTab("' + calls[0] + '")');
    process.exit(1);
}
console.log('PASS: metricsInit calls anlShowTab("metrics")');
process.exit(0);
"""
    script_path = _REPO_ROOT / "tests" / "_tmp_metrics_init_check.js"
    script_path.write_text(node_script)
    try:
        result = subprocess.run(
            ["node", str(script_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"metricsInit behavioral check failed:\n{result.stdout}\n{result.stderr}"
        )
    finally:
        script_path.unlink(missing_ok=True)


def test_ac1_anl_panel_metrics_is_the_default_shown_panel(project_html):
    """AC1: The anl-panel-metrics element has 'show' class in the initial HTML,
    ensuring Metrics is the visible panel on first paint before JS runs.
    """
    # Find the Analytics pane HTML block (it spans many KB — calibration panel is large)
    metrics_pane_start = project_html.find('id="pane-metrics"')
    assert metrics_pane_start != -1, "pane-metrics not found in project.html"
    # Search a large window — calibration scatter SVG alone can be >4 KB
    pane_block = project_html[metrics_pane_start: metrics_pane_start + 30000]

    # The initial active panel should be anl-panel-metrics (not calibration)
    metrics_panel_pos = pane_block.find('id="anl-panel-metrics"')
    calibration_panel_pos = pane_block.find('id="anl-panel-calibration"')

    assert metrics_panel_pos != -1, "anl-panel-metrics not found within pane-metrics block"
    assert calibration_panel_pos != -1, "anl-panel-calibration not found within pane-metrics block"

    # Recover full opening <div tag for each panel by looking back from the id= position
    def _get_div_tag(block, id_pos):
        tag_start = block.rfind("<div", 0, id_pos)
        tag_end = block.index(">", id_pos)
        return block[tag_start:tag_end]

    metrics_tag = _get_div_tag(pane_block, metrics_panel_pos)
    assert "show" in metrics_tag, (
        f"anl-panel-metrics must have 'show' class as the default visible panel. Tag: {metrics_tag[:200]}"
    )

    cal_tag = _get_div_tag(pane_block, calibration_panel_pos)
    assert "show" not in cal_tag, (
        f"anl-panel-calibration must NOT have 'show' class — Metrics is the default panel. Tag: {cal_tag[:200]}"
    )


def test_ac1_anl_tab_metrics_is_active_by_default(project_html):
    """AC1: The anl-tab-metrics button has 'active' class in initial HTML."""
    metrics_pane_start = project_html.find('id="pane-metrics"')
    assert metrics_pane_start != -1
    pane_block = project_html[metrics_pane_start: metrics_pane_start + 3000]

    def _get_button_tag(block, id_pos):
        """Return the full opening <button tag for the button containing id_pos."""
        tag_start = block.rfind("<button", 0, id_pos)
        tag_end = block.index(">", id_pos)
        return block[tag_start:tag_end]

    # anl-tab-metrics should have 'active' class
    metrics_btn_pos = pane_block.find('id="anl-tab-metrics"')
    assert metrics_btn_pos != -1, "anl-tab-metrics button not found"
    btn_tag = _get_button_tag(pane_block, metrics_btn_pos)
    assert "active" in btn_tag, (
        f"anl-tab-metrics must have 'active' class as the default sub-tab. Tag: {btn_tag}"
    )

    # anl-tab-calibration must NOT have 'active' class
    cal_btn_pos = pane_block.find('id="anl-tab-calibration"')
    assert cal_btn_pos != -1, "anl-tab-calibration button not found"
    cal_tag = _get_button_tag(pane_block, cal_btn_pos)
    assert "active" not in cal_tag, (
        f"anl-tab-calibration must NOT have 'active' class — Metrics is the default. Tag: {cal_tag}"
    )


# ── AC2: Analytics is a top-level tab ────────────────────────────────────────

def test_ac2_stab_metrics_has_role_tab(project_html):
    """AC2: stab-metrics button has role='tab' (top-level), not role='menuitem' (dropdown)."""
    pos = project_html.find('id="stab-metrics"')
    assert pos != -1, "stab-metrics button not found"
    # Find the opening tag
    tag_start = project_html.rfind("<button", 0, pos)
    tag_end = project_html.index(">", pos)
    tag = project_html[tag_start:tag_end]

    assert 'role="tab"' in tag, (
        f"stab-metrics must have role='tab' (top-level tab), not role='menuitem'. Tag: {tag}"
    )
    assert 'role="menuitem"' not in tag, (
        "stab-metrics must NOT have role='menuitem' — it is a top-level tab now"
    )


def test_ac2_stab_metrics_not_inside_manage_dropdown(project_html):
    """AC2: stab-metrics is NOT a descendant of the Manage dropdown (#stab-group-manage)."""
    manage_group_start = project_html.find('id="stab-group-manage"')
    assert manage_group_start != -1, "stab-group-manage not found"

    # Find the end of the manage group div
    # Count nested divs to find the matching closing tag
    depth = 0
    i = manage_group_start
    while i < len(project_html):
        if project_html[i:i+4] == "<div":
            depth += 1
        elif project_html[i:i+6] == "</div>":
            depth -= 1
            if depth == 0:
                manage_group_end = i + 6
                break
        i += 1
    else:
        pytest.fail("Could not find end of stab-group-manage div")

    manage_group_html = project_html[manage_group_start:manage_group_end]
    assert 'id="stab-metrics"' not in manage_group_html, (
        "stab-metrics must NOT be inside #stab-group-manage — "
        "Analytics is promoted to a top-level tab (issue #1856)"
    )


def test_ac2_analytics_top_level_tab_one_click_reachable(client):
    """AC2: GET /project/<slug>/metrics serves project.html in one request (no redirect to wrong tab)."""
    # Use 'commander' as the test slug — pages.py serves project.html for all valid tabs
    r = client.get("/project/commander/metrics")
    assert r.status_code == 200, (
        f"Expected 200 for /project/commander/metrics, got {r.status_code}. "
        "The tab must be in _VALID_PROJECT_TABS and served directly."
    )
    assert "pane-metrics" in r.text, (
        "Response for /project/commander/metrics must include the metrics pane HTML"
    )


# ── AC3: Calibration still reachable as a sub-tab ────────────────────────────

def test_ac3_calibration_sub_tab_exists_in_analytics_pane(project_html):
    """AC3: anl-tab-calibration button and anl-panel-calibration div still exist in pane-metrics."""
    metrics_pane_start = project_html.find('id="pane-metrics"')
    assert metrics_pane_start != -1
    pane_block = project_html[metrics_pane_start: metrics_pane_start + 5000]

    assert 'id="anl-tab-calibration"' in pane_block, (
        "anl-tab-calibration sub-tab button must still exist inside pane-metrics"
    )
    assert 'id="anl-panel-calibration"' in pane_block, (
        "anl-panel-calibration panel must still exist inside pane-metrics"
    )
    assert "anlShowTab('calibration')" in pane_block, (
        "Calibration sub-tab must still be reachable via anlShowTab('calibration')"
    )


# ── AC4: Deep links that referenced the old path still resolve ────────────────

def test_ac4_metrics_url_in_parseurl_table(project_html):
    """AC4: parseUrl() maps rawTab 'metrics' to 'metrics' (not fall-through to sprint-mgmt).

    Behavioral: checks that the URL parsing table includes the 'metrics' entry,
    which is what controls whether /project/<slug>/metrics deep link lands correctly.
    """
    # Find the parseUrl function body
    parseurl_start = project_html.find("function parseUrl()")
    assert parseurl_start != -1, "parseUrl function not found in project.html"
    parseurl_block = project_html[parseurl_start: parseurl_start + 2000]

    assert "'metrics'" in parseurl_block, (
        "parseUrl() must include 'metrics' in its rawTab → tab mapping so that "
        "deep links to /project/<slug>/metrics land on the Analytics tab"
    )


def test_ac4_analytics_redirect_still_works(client):
    """AC4: /project/<slug>/analytics (old retired analytics page) redirects to /project/<slug>/metrics."""
    # pages.py has a 301 redirect from /analytics to /metrics — must still work
    r = client.get("/project/commander/analytics", follow_redirects=False)
    # The redirect itself gives 301; with follow_redirects=False we see it directly
    assert r.status_code == 301, (
        f"Expected 301 redirect from /analytics → /metrics, got {r.status_code}"
    )
    location = r.headers.get("location", "")
    assert "/metrics" in location, (
        f"Redirect from /analytics should point to /metrics, got Location: {location}"
    )
