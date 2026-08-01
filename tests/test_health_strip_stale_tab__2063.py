"""Tests for issue #2063: Health-strip 'See more →' points at removed metrics tab

Per CLAUDE.md #1746, behavioral tests are primary and are written as source-code
functional tests (frontend tests/health-strip*.test.mjs). These HTTP tests verify
the integration layer and API availability only.

Frontend tests (run via `node --test`) validate:
  - AC1: Health-strip link removed (no "See more →", no switchTab('metrics') call)
  - AC2: No other stale switchTab calls survive in source
  - AC3: tabs.js coercion emits console.warn for stale tabs
  - AC4: Bundle rebuild includes the warn behavior

HTTP tests below verify basic integration health.
"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_health_strip_stale_tab__ac1_health_strip_element_exists(client):
    # AC1: Health-strip link removed or retargeted.
    # Verify the sprint-mgmt page serves successfully with the health-strip element.
    r = client.get("/project/commander/sprint-mgmt")
    assert r.status_code == 200
    assert "sprint-health-strip" in r.text, "page must contain the health-strip element"


def test_health_strip_stale_tab__ac4_frontend_bundle_serves(client):
    # AC4: Behavioral test coverage. Frontend tests verify logic; HTTP test
    # verifies the bundle is served and callable.
    r = client.get("/static/dist/bundle.js")
    assert r.status_code == 200
    bundle = r.text
    assert len(bundle) > 1000, "bundle.js must be rebuilt and non-empty"
