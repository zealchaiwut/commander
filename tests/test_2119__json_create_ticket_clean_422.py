"""Tests for issue #2119 — JSON create-ticket 422 leaks raw pydantic ValidationError repr.

AC: When POST /api/tickets/create receives a JSON body that fails Pydantic validation,
    the 422 response detail must be a clean human-readable string
    (e.g. "Invalid ticket body: title: Field required") rather than the raw
    pydantic ValidationError repr (which contains "validation error for CreateTicketBody"
    and stack-like field annotations).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


@pytest.fixture(scope="module")
def client():
    return TestClient(server.app, raise_server_exceptions=False)


# ── AC: clean detail string on missing required field ────────────────────────

def test_missing_title_returns_422(client):
    """Missing required 'title' field triggers a 422 response."""
    resp = client.post(
        "/api/tickets/create",
        json={},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_missing_title_detail_is_clean_string(client):
    """422 detail must be a plain string, not the raw pydantic ValidationError repr."""
    resp = client.post(
        "/api/tickets/create",
        json={},
        headers={"Content-Type": "application/json"},
    )
    detail = resp.json().get("detail", "")
    assert isinstance(detail, str), f"detail should be str, got {type(detail)}"
    assert detail.startswith("Invalid ticket body:"), (
        f"detail should start with 'Invalid ticket body:', got: {detail!r}"
    )


def test_missing_title_detail_does_not_contain_raw_pydantic_repr(client):
    """422 detail must not contain the raw pydantic ValidationError header line."""
    resp = client.post(
        "/api/tickets/create",
        json={},
        headers={"Content-Type": "application/json"},
    )
    detail = resp.json().get("detail", "")
    assert "validation error for" not in detail.lower(), (
        f"detail leaks raw pydantic repr: {detail!r}"
    )


def test_missing_title_detail_includes_field_and_message(client):
    """422 detail must include the field name ('title') and a human message."""
    resp = client.post(
        "/api/tickets/create",
        json={},
        headers={"Content-Type": "application/json"},
    )
    detail = resp.json().get("detail", "")
    assert "title" in detail, (
        f"detail should mention the invalid field 'title', got: {detail!r}"
    )


def test_wrong_type_title_returns_clean_422(client):
    """Wrong type for 'title' also triggers a clean 422 detail."""
    resp = client.post(
        "/api/tickets/create",
        json={"title": {"nested": "object"}},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    detail = resp.json().get("detail", "")
    assert isinstance(detail, str)
    assert detail.startswith("Invalid ticket body:")
    assert "validation error for" not in detail.lower()
