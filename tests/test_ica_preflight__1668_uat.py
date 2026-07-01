"""UAT steps for issue #1668 — ICA preflight readiness (manual / live proxy).

Automated coverage lives in tests/test_ica_preflight__validation.py. These tests
document the UAT contract and skip when no live dashboard/proxy is configured.
"""
from __future__ import annotations

import os

import pytest

BASE_URL = os.environ.get("UAT_BASE_URL") or (
    f"http://localhost:{os.environ['UAT_PORT']}" if os.environ.get("UAT_PORT") else ""
)
if not BASE_URL:
    pytest.skip(
        "UAT_BASE_URL / UAT_PORT not set — run manual ICA preflight UAT against live env",
        allow_module_level=True,
    )


@pytest.fixture
def client():
    import httpx

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def test_1668_uat_dashboard_reachable(client):
    """Sanity: dashboard up before manual ICA preflight UAT."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
