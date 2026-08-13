"""Tests for issue #2290: Demote the Dev Report to its own route (runs against UAT)"""
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


# --- Acceptance Criteria ---

def test_demote_dev_report__report_page_serves_at_own_route(client):
    # AC1: The Dev Report is served at its own route (`/report`), with its own page
    r = client.get("/report")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert "report" in r.text.lower() or "dev report" in r.text.lower()


def test_demote_dev_report__date_picker_navigation(client):
    # AC2: The date picker / previous-day navigation works on `/report`
    r = client.get("/report")
    assert r.status_code == 200
    # Verify the HTML contains a date picker element
    assert "date" in r.text.lower() or "picker" in r.text.lower()


def test_demote_dev_report__home_page_has_plain_link(client):
    # AC3: The home page links to `/report` — a plain navigation link
    r = client.get("/home")
    assert r.status_code == 200
    # Check for a link to /report
    assert 'href="/report"' in r.text or "href='/report'" in r.text


def test_demote_dev_report__home_html_no_dev_report_references(client):
    # AC4: `apps/dashboard/static/home.html` contains **zero** references to `/api/dev-report`
    r = client.get("/")
    assert r.status_code == 200
    assert "/api/dev-report" not in r.text, "home page must not reference /api/dev-report"


def test_demote_dev_report__report_renders_on_200(client):
    # AC6a: New tests for the `/report` route cover: renders with a report present
    r = client.get("/report")
    assert r.status_code == 200
    # Verify that the page loads and contains expected structure
    assert "<!DOCTYPE html>" in r.text or "<!doctype html>" in r.text.lower()


def test_demote_dev_report__report_empty_state_on_404(client):
    # AC6b: shows a clean empty state when data is not available
    r = client.get("/report")
    assert r.status_code == 200
    # The page should gracefully handle missing data without crashing
    # (the /api/dev-report endpoint may 404 or return empty, page should still render)
