"""Tests for issue #1846: cleanup: delete retired analytics.html page, route, and dead trend_30d query.

Runs against UAT environment via HTTP. Tests acceptance criteria:
  AC1 — static/analytics.html is deleted
  AC2 — GET /project/{slug}/analytics returns 301 redirect to /project/{slug}/metrics
  AC3 — stab-analytics-page wiring removed from project.html
  AC4 — _query_trend_30d() removed; trend_30d absent from /analytics/cost response
  AC5 — /api/projects/{slug}/analytics/cost still returns by_agent, by_ticket, by_sprint (behavioral)
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
    with httpx.Client(base_url=BASE_URL, timeout=10.0, follow_redirects=False) as c:
        yield c


# --- AC1: static/analytics.html is deleted ---

def test_cleanup_analytics__html_deleted():
    """AC1: analytics.html file is deleted from static/ (verified via HTTP 404 when accessed)."""
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        # If analytics.html existed, GET /static/analytics.html would return 200.
        # After deletion, it should 404. However, Uvicorn may still serve 404s via
        # the error handler, so we check that accessing the nonexistent file fails.
        # The real test is inspection in Step 3, but we confirm via 404 attempt.
        r = c.get("/static/analytics.html")
        # 404 is expected for a deleted static file; other codes indicate it still exists
        assert r.status_code == 404, (
            f"Expected /static/analytics.html to return 404 (file deleted), got {r.status_code}"
        )


# --- AC2: GET /project/{slug}/analytics redirects to metrics ---

def test_cleanup_analytics__redirect_301(client):
    """AC2: GET /project/{slug}/analytics returns 301 redirect to /project/{slug}/metrics."""
    r = client.get("/project/commander/analytics")
    assert r.status_code == 301, (
        f"Expected 301 redirect from /analytics, got {r.status_code}"
    )


def test_cleanup_analytics__redirect_location(client):
    """AC2: Redirect Location header points to /project/{slug}/metrics."""
    r = client.get("/project/commander/analytics")
    location = r.headers.get("location", "")
    assert location.endswith("/project/commander/metrics") or location == "/project/commander/metrics", (
        f"Expected redirect to /project/commander/metrics, got {location!r}"
    )


# --- AC3: stab-analytics-page wiring removed from project.html ---

def test_cleanup_analytics__no_stab_wiring(client):
    """AC3: project.html no longer wires up stab-analytics-page link."""
    r = client.get("/project/commander")
    assert r.status_code == 200, f"Expected 200 from /project/commander, got {r.status_code}"
    content = r.text
    # The removed code referenced 'stab-analytics-page' element by ID
    # If the wiring is removed, this string should not appear in the markup
    assert "stab-analytics-page" not in content, (
        "stab-analytics-page element ID wiring must be removed from project.html (AC3)"
    )


# --- AC4: _query_trend_30d() removed; trend_30d absent from response ---

def test_cleanup_analytics__no_trend_30d_in_cost_response(client):
    """AC4: /api/projects/{slug}/analytics/cost does not include trend_30d field."""
    r = client.get("/api/projects/commander/analytics/cost")
    assert r.status_code == 200, f"Expected 200 from /api/.../analytics/cost, got {r.status_code}"
    data = r.json()
    assert "trend_30d" not in data, (
        "trend_30d must be removed from /api/projects/{slug}/analytics/cost response (AC4)"
    )


# --- AC5: /api/projects/{slug}/analytics/cost still returns required fields ---

def test_cleanup_analytics__cost_returns_by_agent(client):
    """AC5a: /api/.../analytics/cost returns by_agent field (live UI dependency)."""
    r = client.get("/api/projects/commander/analytics/cost")
    assert r.status_code == 200
    data = r.json()
    assert "by_agent" in data, "by_agent field missing from cost response (AC5)"
    assert isinstance(data["by_agent"], list), "by_agent must be a list"


def test_cleanup_analytics__cost_returns_by_ticket(client):
    """AC5b: /api/.../analytics/cost returns by_ticket field (live UI dependency)."""
    r = client.get("/api/projects/commander/analytics/cost")
    assert r.status_code == 200
    data = r.json()
    assert "by_ticket" in data, "by_ticket field missing from cost response (AC5)"
    assert isinstance(data["by_ticket"], list), "by_ticket must be a list"


def test_cleanup_analytics__cost_returns_by_sprint(client):
    """AC5c: /api/.../analytics/cost returns by_sprint field (live UI dependency)."""
    r = client.get("/api/projects/commander/analytics/cost")
    assert r.status_code == 200
    data = r.json()
    assert "by_sprint" in data, "by_sprint field missing from cost response (AC5)"
    assert isinstance(data["by_sprint"], list), "by_sprint must be a list"


def test_cleanup_analytics__cost_returns_per_size_avg(client):
    """AC5d: /api/.../analytics/cost returns per_size_avg_tokens field (live UI dependency)."""
    r = client.get("/api/projects/commander/analytics/cost")
    assert r.status_code == 200
    data = r.json()
    assert "per_size_avg_tokens" in data, "per_size_avg_tokens field missing from cost response (AC5)"
    assert isinstance(data["per_size_avg_tokens"], (list, dict)), "per_size_avg_tokens must be a list or dict"
