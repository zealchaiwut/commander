"""Tests for issue #2263: Make diagnostics.html reachable from the current shell.

AC1 — A nav entry to /diagnostics exists in the current shell (home or project header).
AC2 — The page loads and its checks run.

Tests read the static HTML files directly (no running server required for AC1)
and use TestClient for AC2 to exercise the actual route.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

HOME_HTML = Path("apps/dashboard/static/home.html").read_text()
PROJECT_HTML = Path("apps/dashboard/static/project.html").read_text()

_NAV_RE = re.compile(r'href\s*=\s*["\']?/diagnostics["\']?', re.IGNORECASE)


# ── AC1: nav link present in home.html ────────────────────────────────────────

def test_ac1_home_html_has_diagnostics_nav_link():
    """home.html must contain a nav link to /diagnostics."""
    assert _NAV_RE.search(HOME_HTML), (
        "home.html must have an href to /diagnostics in the nav"
    )


def test_ac1_home_html_diagnostics_link_is_in_nav():
    """The diagnostics link must appear inside the <nav> element in home.html."""
    nav_match = re.search(r"<nav\b[^>]*>(.*?)</nav>", HOME_HTML, re.DOTALL)
    assert nav_match, "home.html must have a <nav> element"
    nav_content = nav_match.group(1)
    assert _NAV_RE.search(nav_content), (
        "The /diagnostics link must be inside the <nav> element in home.html"
    )


# ── AC1: nav link present in project.html ────────────────────────────────────

def test_ac1_project_html_has_diagnostics_nav_link():
    """project.html must contain a nav link to /diagnostics."""
    assert _NAV_RE.search(PROJECT_HTML), (
        "project.html must have an href to /diagnostics in the nav"
    )


def test_ac1_project_html_diagnostics_link_is_in_nav():
    """The diagnostics link must appear inside the top-nav element in project.html."""
    nav_match = re.search(r'<nav\b[^>]*class="[^"]*top-nav[^"]*"[^>]*>(.*?)</nav>', PROJECT_HTML, re.DOTALL)
    assert nav_match, "project.html must have a top-nav <nav> element"
    nav_content = nav_match.group(1)
    assert _NAV_RE.search(nav_content), (
        "The /diagnostics link must be inside the top-nav in project.html"
    )


# ── AC2: /diagnostics route returns 200 with check content ───────────────────

@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from server import app
    yield TestClient(app)


def test_ac2_diagnostics_page_returns_200(client):
    """GET /diagnostics must return 200 OK."""
    r = client.get("/diagnostics")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_ac2_diagnostics_page_contains_checks_markup(client):
    """GET /diagnostics response body must contain the health checks container."""
    r = client.get("/diagnostics")
    assert r.status_code == 200
    assert "checks-list" in r.text, (
        "/diagnostics page must include the checks-list element"
    )
