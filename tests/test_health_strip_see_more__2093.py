"""Tests for issue #2093: health-strip 'See more →' repoint/relabel/remove

AC1 — health-strip no longer renders a "See more →" link that lands on
       the failures inbox (removed/relabeled).
AC2 — no surviving switchTab('metrics') caller in the health-strip source.

Frontend behavioral tests (node --test) are the primary coverage; see
tests/frontend/health-strip-see-more-2093.test.mjs.  These HTTP tests verify
the rebuilt bundle is served and that the page including the health-strip
element still loads.
"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def test_health_strip_see_more__2093_ac1_page_loads(client):
    # AC1: the sprint-mgmt page must still load with the health-strip element
    r = client.get("/project/commander/sprint-mgmt")
    assert r.status_code == 200
    assert "sprint-health-strip" in r.text, "health-strip element must still exist in DOM"


def test_health_strip_see_more__2093_ac1_bundle_served(client):
    # AC1/AC2: rebuilt bundle.js is served and non-empty
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200
    assert len(r.text) > 1000, "bundle.js must be rebuilt and non-empty"


def test_health_strip_see_more__2093_ac2_no_metrics_caller_in_bundle(client):
    # AC2: bundle must not contain the stale switchTab('metrics') from health-strip
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200
    bundle = r.text
    # The old health-strip link called switchTab('metrics') inside an onclick.
    # Tabs.js coercion guard is allowed, but no onclick caller should remain.
    assert "shs-see-more" not in bundle, (
        "shs-see-more element must be removed — it called switchTab('metrics') "
        "which lands on the failures inbox, not a delivery-health surface"
    )
