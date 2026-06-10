"""Tests for issue #732: Dead ?tab=status deep-link after Status moved into Analytics.

Follow-up to #715, which removed the standalone `pane-status` element and relocated
Status into the Analytics tab as the `anl-panel-status` sub-tab. The regression: a
legacy `?tab=status` deep-link still mapped to the `status` tab and toggled a
now-nonexistent `pane-status`, activating nothing visible (blank content) instead of
opening Analytics -> Status.

The fix lives entirely in the SPA routing logic inside the static file
`apps/dashboard/static/project.html` (parseUrl / switchTab). That logic is plain
client-side JS with no HTTP surface, so the routing acceptance criteria are verified
by introspecting the shipped source. One HTTP smoke test confirms the UAT server
actually serves the deep-link target page.
"""
import os
import pathlib
import re

import httpx
import pytest


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_HTML = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html():
    assert PROJECT_HTML.exists(), f"missing {PROJECT_HTML}"
    return PROJECT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def switch_tab_body(html):
    start = html.index("function switchTab(")
    end = html.index("function toggleStabDropdown(", start)
    return html[start:end]


@pytest.fixture(scope="module")
def parse_url_body(html):
    start = html.index("function parseUrl(")
    end = html.index("\n}", start) + 2
    return html[start:end]


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_dead_tab_status_deeplink__resolves_to_real_surface(html, parse_url_body):
    # AC: legacy ?tab=status deep-link resolves to a real surface, not a dead pane.
    # parseUrl still recognises the raw `status` tab, and the dead standalone
    # pane-status is gone while the Analytics-resident Status panel remains.
    assert "rawTab === 'status'" in parse_url_body, (
        "parseUrl must still map the legacy ?tab=status deep-link to a real surface"
    )
    assert 'id="pane-status"' not in html, (
        "standalone pane-status must not exist; Status is the anl-panel-status sub-tab"
    )
    assert 'id="anl-panel-status"' in html, "Analytics Status sub-tab panel must exist"


def test_dead_tab_status_deeplink__redirects_into_analytics_status(switch_tab_body):
    # AC: switchTab redirects the legacy `status` tab onto the Analytics pane
    # (`metrics`) and opens the Status sub-tab via anlShowTab('status'), and the dead
    # `status` key is dropped from switchTab's pane/aria tab-key arrays.
    redirect = re.search(
        r"if\s*\(\s*tab\s*===\s*'status'\s*\)\s*\{[^}]*tab\s*=\s*'metrics'",
        switch_tab_body,
        re.DOTALL,
    )
    assert redirect, "switchTab must redirect the legacy status tab to the Analytics pane (metrics)"
    assert re.search(r"anlShowTab\(\s*'status'\s*\)", switch_tab_body), (
        "switchTab must call anlShowTab('status') so the deep-link opens Status"
    )
    assert "'calibration', 'status'" not in switch_tab_body, (
        "the dead 'status' tab key must be removed from switchTab's pane/aria arrays"
    )


def test_dead_tab_status_deeplink__uat_serves_project_page(client):
    # AC (smoke): the UAT server serves the project SPA that hosts the deep-link
    # routing, so the redirected ?tab=status link has a live page to land on.
    r = client.get("/static/project.html")
    assert r.status_code == 200
    assert "function switchTab(" in r.text, "served project.html must contain the SPA router"
