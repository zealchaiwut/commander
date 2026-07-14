"""Tests for issue #1797: Cap/dedup slug list in batch todos endpoint (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
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

def test_cap_dedup_batch_todos__dedup_repeated_slugs(client):
    # AC: Duplicate slugs in the comma-separated list are collapsed (silent dedup)
    r = client.get("/api/todos?projects=proj-a,proj-b,proj-a,proj-c,proj-b")
    assert r.status_code == 200
    data = r.json()
    # Should have exactly 3 unique slugs
    assert len(data) == 3
    assert set(data.keys()) == {"proj-a", "proj-b", "proj-c"}


def test_cap_dedup_batch_todos__cap_at_max_slugs(client):
    # AC: More than MAX_BATCH_SLUGS unique slugs returns 400
    # MAX_BATCH_SLUGS is 50 in the implementation
    slugs = [f"slug-{i}" for i in range(51)]
    projects_param = ",".join(slugs)
    r = client.get(f"/api/todos?projects={projects_param}")
    assert r.status_code == 400
    assert "Too many slugs" in r.json()["detail"]


def test_cap_dedup_batch_todos__whitespace_trimmed(client):
    # AC: Leading/trailing whitespace in slugs is trimmed and empty strings are skipped
    r = client.get("/api/todos?projects= proj-a , proj-b , , proj-c ")
    assert r.status_code == 200
    data = r.json()
    # Should have exactly 3 unique slugs with trimmed whitespace
    assert len(data) == 3
    assert set(data.keys()) == {"proj-a", "proj-b", "proj-c"}


def test_cap_dedup_batch_todos__empty_projects_param(client):
    # AC: Empty projects parameter returns empty dict
    r = client.get("/api/todos?projects=")
    assert r.status_code == 200
    data = r.json()
    assert data == {}


def test_cap_dedup_batch_todos__no_projects_param(client):
    # AC: Missing projects parameter is treated as empty string
    r = client.get("/api/todos")
    assert r.status_code == 200
    data = r.json()
    assert data == {}


def test_cap_dedup_batch_todos__unknown_slugs_return_empty_list(client):
    # AC: Unknown slugs are included with an empty list
    r = client.get("/api/todos?projects=nonexistent-slug-1,nonexistent-slug-2")
    assert r.status_code == 200
    data = r.json()
    assert "nonexistent-slug-1" in data
    assert "nonexistent-slug-2" in data
    assert data["nonexistent-slug-1"] == []
    assert data["nonexistent-slug-2"] == []
