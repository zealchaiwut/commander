"""Tests for issue #2290: Demote the Dev Report to its own route.

AC1: GET /report returns 200 HTML (the report has its own route, reachable from home)
AC1: /report response links back to home (/)
AC4: /report page contains a date input so the date picker works
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
APPS_DASHBOARD = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(APPS_DASHBOARD))
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="module")
def client():
    """In-process TestClient — no live server required (issue #1746)."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    import server as srv  # noqa: PLC0415

    return TestClient(srv.app)


# ── AC1: /report route exists and is reachable ────────────────────────────────

def test_ac1_report_route_returns_200(client):
    resp = client.get("/report")
    assert resp.status_code == 200, f"GET /report must return 200, got {resp.status_code}"


def test_ac1_report_route_returns_html(client):
    resp = client.get("/report")
    ct = resp.headers.get("content-type", "")
    assert "text/html" in ct, f"GET /report must return HTML, got content-type: {ct}"


def test_ac1_report_page_links_back_to_home(client):
    """The report page must contain a nav link back to the home page."""
    resp = client.get("/report")
    body = resp.text
    assert 'href="/"' in body or "href='/'" in body, \
        "report.html must contain a back-link to / (home page)"


# ── AC4: date picker is present on the report route ──────────────────────────

def test_ac4_report_page_has_date_input(client):
    """The /report page must contain a date input for date navigation."""
    resp = client.get("/report")
    assert 'type="date"' in resp.text or "type='date'" in resp.text, \
        "report.html must contain a date <input> for date picker navigation"


def test_ac4_report_page_has_nav_buttons_for_prev_next(client):
    """The /report page must contain prev/next day navigation buttons."""
    resp = client.get("/report")
    body = resp.text
    assert "prev" in body.lower() or "previous" in body.lower() or \
           "date-prev" in body or "prev-day" in body, \
        "report.html must have a previous-day navigation button"
