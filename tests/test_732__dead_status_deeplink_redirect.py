"""Tests for issue #732: Dead ?tab=status deep-link after Status moved into Analytics.

Follow-up to #715, which removed the standalone `pane-status` element and relocated
Status into the Analytics tab as the `anl-panel-status` sub-tab. The regression: a
legacy `?tab=status` deep-link (path `/project/<slug>/status`) still mapped to the
`status` tab and toggled a now-nonexistent `pane-status`, activating nothing visible
(blank content) instead of opening Analytics → Status.

The fix lives entirely in the SPA routing logic inside the static file
`apps/dashboard/static/project.html` (parseUrl / switchTab). That logic is plain
client-side JS with no HTTP surface, so these acceptance criteria are verified by
introspecting the shipped source — the same static-source approach used elsewhere in
this suite for frontend-only tickets. Each test is anchored to a specific criterion
and fails against the pre-fix source.
"""
import pathlib
import re

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html():
    assert PROJECT_HTML.exists(), f"missing {PROJECT_HTML}"
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def switch_tab_body(html):
    """Return the body of the switchTab() function."""
    start = html.index("function switchTab(")
    # switchTab is followed by the dropdown-group helpers; bound the slice at the
    # next top-level helper declaration so assertions stay scoped to switchTab.
    end = html.index("function toggleStabDropdown(", start)
    return html[start:end]


@pytest.fixture(scope="module")
def parse_url_body(html):
    """Return the body of the parseUrl() function."""
    start = html.index("function parseUrl(")
    end = html.index("\n}", start) + 2
    return html[start:end]


# --- Acceptance Criteria ---

def test_732__standalone_pane_status_removed(html):
    """AC1: The standalone `pane-status` target no longer exists — it was the dead
    element switchTab toggled, producing blank content. Status now lives only as the
    `anl-panel-status` sub-tab inside the Analytics pane."""
    assert 'id="pane-status"' not in html, (
        "standalone pane-status must not exist; Status is the anl-panel-status sub-tab"
    )
    # The Analytics-resident Status surface must still be present.
    assert 'id="anl-panel-status"' in html, "Analytics Status sub-tab panel must exist"


def test_732__parseurl_still_recognizes_status_deeplink(parse_url_body):
    """AC2: parseUrl() still recognizes the legacy `status` raw tab so the deep-link
    resolves to a real surface instead of falling through to the sprint-mgmt default."""
    assert "rawTab === 'status'" in parse_url_body, (
        "parseUrl must still map the legacy ?tab=status deep-link to a real surface"
    )


def test_732__switchtab_redirects_status_to_analytics(switch_tab_body):
    """AC3a: switchTab redirects the legacy `status` tab onto the Analytics surface
    (`metrics`) rather than toggling the removed pane-status."""
    # A guard that reassigns the status tab to the analytics pane key ('metrics').
    redirect = re.search(
        r"if\s*\(\s*tab\s*===\s*'status'\s*\)\s*\{[^}]*tab\s*=\s*'metrics'",
        switch_tab_body,
        re.DOTALL,
    )
    assert redirect, (
        "switchTab must redirect the legacy status tab to the Analytics pane (metrics)"
    )


def test_732__switchtab_opens_status_subtab(switch_tab_body):
    """AC3b: After redirecting onto Analytics, switchTab opens the Status sub-tab via
    anlShowTab('status') so deep-linking lands on Status, not the default Calibration."""
    assert re.search(r"anlShowTab\(\s*'status'\s*\)", switch_tab_body), (
        "switchTab must call anlShowTab('status') so the deep-link opens Status"
    )


def test_732__switchtab_drops_dead_status_tab_key(switch_tab_body):
    """AC1 (cont.): switchTab iterates tab-key arrays to build `pane-<t>` / `stab-<t>`
    ids. With `status` redirected onto `metrics`, the `status` key can never be the
    active tab, and its `pane-status` / `stab-status` targets were removed by #715.
    The dead `'status'` entry must be dropped from those arrays so switchTab no longer
    tries to toggle a nonexistent pane."""
    assert "'calibration', 'status'" not in switch_tab_body, (
        "the dead 'status' tab key must be removed from switchTab's pane/aria arrays"
    )
