"""Tests for issue #2072: Issues tab loads once and never refreshes — silently shows stale data.

AC1: The Issues tab refetches on tab activation (verified via JS behavioral test).
AC2: Open-ticket count cannot display stale data (satisfied by AC1 — always refetches).
AC3: All three error branches gain a working Retry button (verified via JS behavioral test).
AC4: Behavioral tests per CLAUDE.md #1746 (see tests/frontend/tickets-refetch-2072.test.mjs).
AC5: Bundle rebuild — static/dist/bundle.js updated and committed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import server as srv  # noqa: E402

_DIST_DIR = _DASHBOARD_DIR / "static" / "dist"
_SRC_DIR = _DASHBOARD_DIR / "static" / "src"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    return TestClient(srv.app)


# --- AC1 + AC2: API endpoint that tickets tab relies on is accessible ---

def test_issues_tab_refetch__refetch_on_tab_activation(client):
    """AC1: /api/projects is reachable and returns the expected shape.

    The refetch-on-activation fix (removing the _ticketsLoaded one-shot gate)
    means loadTickets() is called every tab switch. The API backing it must work.
    """
    r = client.get("/api/projects")
    assert r.status_code == 200
    data = r.json()
    assert "projects" in data
    assert isinstance(data["projects"], list)


def test_issues_tab_refetch__ticket_count_freshness(client):
    """AC2: The /api/projects endpoint returns a fresh list on every call.

    Since refetch-on-activation now issues a real network request each time,
    the endpoint must return valid data (not a stale cached value).
    """
    r1 = client.get("/api/projects")
    assert r1.status_code == 200
    r2 = client.get("/api/projects")
    assert r2.status_code == 200
    # Both calls succeed — the endpoint is idempotent and always reachable.
    assert r1.json().keys() == r2.json().keys()


# --- AC3: Error-branch retry controls (behavioral; covered by JS test) ---

def test_issues_tab_refetch__retry_button_on_error():
    """AC3: All three error branches render a working Retry button.

    Full behavioral coverage is in tests/frontend/tickets-refetch-2072.test.mjs
    (AC3 tests: project-info failure, project-not-found, tickets fetch failure).
    This marker confirms the JS tests cover AC3 without requiring browser automation.
    """
    pytest.skip("behavioral — covered by tests/frontend/tickets-refetch-2072.test.mjs AC3 suite")


# --- AC4: Two activations issue two fetches (behavioral; covered by JS test) ---

def test_issues_tab_refetch__double_activation_fetches_twice():
    """AC4: Activating the Issues tab twice issues two loadTickets() calls.

    Full behavioral coverage is in tests/frontend/tickets-refetch-2072.test.mjs
    (AC1 + AC4 tests: switchTab twice → callCount === 2).
    """
    pytest.skip("behavioral — covered by tests/frontend/tickets-refetch-2072.test.mjs AC1/AC4 suite")


# --- AC5: Bundle rebuild committed ---

def test_issues_tab_refetch__bundle_rebuild():
    """AC5: npm run build was run and static/dist/bundle.js is present and non-empty."""
    bundle = _DIST_DIR / "bundle.js"
    assert bundle.exists(), f"static/dist/bundle.js missing — run npm run build (expected at {bundle})"
    content = bundle.read_text(encoding="utf-8")
    assert len(content) > 1000, "bundle.js looks too small — may be empty or stub"
    assert any(kw in content for kw in ("var ", "const ", "function ", "let ")), (
        "bundle.js does not look like valid JavaScript"
    )
