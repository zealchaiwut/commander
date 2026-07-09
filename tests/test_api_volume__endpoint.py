"""Tests for issue #1787 — AC7 through AC16: /api/debug/api-volume endpoint.

AC7  — GET /api/debug/api-volume returns HTTP 200 with Content-Type application/json.
AC8  — Response includes top_paths array sorted descending, configurable with ?n=.
AC9  — Response includes cache_stats with hit/miss counts and hit_rate per cache.
AC10 — Response includes gh_subprocess_total integer.
AC11 — Response includes uptime_seconds float > 0.
AC12 — Every key or group has an accompanying _doc / description field.
AC15 — No persistence: counters are in-memory only (no DB writes in api_volume.py).
AC16 — No existing /api/debug/* endpoint behavior changes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient for the full dashboard app."""
    import server as srv
    from fastapi.testclient import TestClient
    return TestClient(srv.app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset counters before each test to isolate counter state."""
    import api_volume
    api_volume._reset()
    yield
    api_volume._reset()


# ── AC7: endpoint exists, returns 200 + application/json ─────────────────────

def test_endpoint_returns_200(client):
    """GET /api/debug/api-volume returns HTTP 200. (AC7)"""
    r = client.get("/api/debug/api-volume")
    assert r.status_code == 200


def test_endpoint_returns_json_content_type(client):
    """GET /api/debug/api-volume returns Content-Type application/json. (AC7)"""
    r = client.get("/api/debug/api-volume")
    assert "application/json" in r.headers["content-type"]


# ── AC8: top_paths array, sorted descending, configurable N ──────────────────

def test_top_paths_key_present(client):
    """Response body contains a top_paths key. (AC8)"""
    r = client.get("/api/debug/api-volume")
    body = r.json()
    assert "top_paths" in body, f"Missing 'top_paths' in {list(body.keys())}"


def test_top_paths_is_list(client):
    """top_paths is a list. (AC8)"""
    r = client.get("/api/debug/api-volume")
    assert isinstance(r.json()["top_paths"], list)


def test_top_paths_sorted_descending(client):
    """top_paths entries are sorted by count descending. (AC8)"""
    import api_volume
    api_volume.record_request("/api/a")
    api_volume.record_request("/api/a")
    api_volume.record_request("/api/a")
    api_volume.record_request("/api/b")
    api_volume.record_request("/api/b")
    api_volume.record_request("/api/c")

    r = client.get("/api/debug/api-volume")
    paths = r.json()["top_paths"]
    counts = [entry["count"] for entry in paths if entry["path"] in {"/api/a", "/api/b", "/api/c"}]
    assert counts == sorted(counts, reverse=True)


def test_top_paths_n_param_limits_results(client):
    """?n=2 returns at most 2 top_paths entries. (AC8)"""
    import api_volume
    for i in range(10):
        for _ in range(i + 1):
            api_volume.record_request(f"/api/path/{i}")

    r = client.get("/api/debug/api-volume?n=2")
    body = r.json()
    assert len(body["top_paths"]) <= 2


def test_top_paths_entry_shape(client):
    """Each top_paths entry has 'path' (str) and 'count' (int). (AC8)"""
    import api_volume
    api_volume.record_request("/api/sprints/{label}/live")

    r = client.get("/api/debug/api-volume")
    paths = r.json()["top_paths"]
    assert len(paths) > 0
    entry = paths[0]
    assert "path" in entry
    assert "count" in entry
    assert isinstance(entry["path"], str)
    assert isinstance(entry["count"], int)


# ── AC9: cache_stats with hit/miss/hit_rate ───────────────────────────────────

def test_cache_stats_key_present(client):
    """Response body contains cache_stats. (AC9)"""
    r = client.get("/api/debug/api-volume")
    assert "cache_stats" in r.json()


def test_cache_stats_github_client_shape(client):
    """cache_stats.github_client has hits, misses, hit_rate. (AC9)"""
    r = client.get("/api/debug/api-volume")
    gc = r.json()["cache_stats"]["github_client"]
    assert "hits" in gc
    assert "misses" in gc
    assert "hit_rate" in gc
    assert isinstance(gc["hits"], int)
    assert isinstance(gc["misses"], int)
    assert isinstance(gc["hit_rate"], float)


def test_cache_stats_hit_rate_range(client):
    """hit_rate values are between 0.0 and 1.0 inclusive. (AC9)"""
    r = client.get("/api/debug/api-volume")
    stats = r.json()["cache_stats"]
    for key in ("github_client", "board_cache"):
        rate = stats[key]["hit_rate"]
        assert 0.0 <= rate <= 1.0, f"{key}.hit_rate={rate} out of range"


def test_cache_stats_mirror_fallback_shape(client):
    """cache_stats.mirror_fallback has a count integer. (AC9)"""
    r = client.get("/api/debug/api-volume")
    mf = r.json()["cache_stats"]["mirror_fallback"]
    assert "count" in mf
    assert isinstance(mf["count"], int)


def test_cache_stats_board_cache_shape(client):
    """cache_stats.board_cache has hits, misses, hit_rate. (AC9)"""
    r = client.get("/api/debug/api-volume")
    bc = r.json()["cache_stats"]["board_cache"]
    assert "hits" in bc
    assert "misses" in bc
    assert "hit_rate" in bc


def test_cache_stats_reflect_live_counters(client):
    """After incrementing counters, endpoint response reflects the new values. (AC9)"""
    import api_volume
    api_volume.record_gc_hit()
    api_volume.record_gc_hit()
    api_volume.record_gc_miss()

    r = client.get("/api/debug/api-volume")
    gc = r.json()["cache_stats"]["github_client"]
    assert gc["hits"] == 2
    assert gc["misses"] == 1
    assert abs(gc["hit_rate"] - 2 / 3) < 0.001


# ── AC10: gh_subprocess_total integer ─────────────────────────────────────────

def test_gh_subprocess_total_present(client):
    """Response includes gh_subprocess_total integer. (AC10)"""
    r = client.get("/api/debug/api-volume")
    body = r.json()
    assert "gh_subprocess_total" in body
    assert isinstance(body["gh_subprocess_total"], int)


def test_gh_subprocess_total_reflects_counter(client):
    """gh_subprocess_total matches the live counter value. (AC10)"""
    import api_volume
    api_volume.record_gh_subprocess()
    api_volume.record_gh_subprocess()

    r = client.get("/api/debug/api-volume")
    assert r.json()["gh_subprocess_total"] == 2


# ── AC11: uptime_seconds float > 0 ────────────────────────────────────────────

def test_uptime_seconds_present(client):
    """Response includes uptime_seconds. (AC11)"""
    r = client.get("/api/debug/api-volume")
    assert "uptime_seconds" in r.json()


def test_uptime_seconds_positive_float(client):
    """uptime_seconds is a positive float (process has been running). (AC11)"""
    r = client.get("/api/debug/api-volume")
    uptime = r.json()["uptime_seconds"]
    assert isinstance(uptime, float)
    assert uptime > 0


# ── AC12: self-describing keys ────────────────────────────────────────────────

def test_response_has_top_level_doc(client):
    """Response has a _doc field at the top level. (AC12)"""
    r = client.get("/api/debug/api-volume")
    body = r.json()
    assert "_doc" in body, "Missing top-level _doc field"
    assert isinstance(body["_doc"], str)
    assert len(body["_doc"]) > 0


def test_cache_stats_has_doc(client):
    """cache_stats has a _doc field. (AC12)"""
    r = client.get("/api/debug/api-volume")
    cs = r.json()["cache_stats"]
    assert "_doc" in cs


def test_github_client_cache_stats_has_doc(client):
    """cache_stats.github_client has a _doc field. (AC12)"""
    r = client.get("/api/debug/api-volume")
    gc = r.json()["cache_stats"]["github_client"]
    assert "_doc" in gc


def test_gh_subprocess_total_has_doc_sibling(client):
    """gh_subprocess_total has a sibling doc field. (AC12)"""
    r = client.get("/api/debug/api-volume")
    body = r.json()
    assert "_gh_subprocess_total_doc" in body or "_doc" in body


# ── AC15: No persistence ──────────────────────────────────────────────────────

def test_api_volume_module_has_no_db_import():
    """api_volume.py uses no DB writes or external store — pure in-memory. (AC15)"""
    import ast
    api_volume_path = DASHBOARD_DIR / "api_volume.py"
    tree = ast.parse(api_volume_path.read_text())
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_names.add(node.module.split(".")[0])
    forbidden = {"db", "sqlite3", "sqlalchemy", "psycopg2"}
    found = imported_names & forbidden
    assert not found, f"api_volume.py must not import DB modules; found: {found}"


# ── AC16: Backward compatibility ──────────────────────────────────────────────

def test_existing_debug_sprint_collisions_still_works(client):
    """Existing /api/debug/sprint-collisions endpoint still returns 200. (AC16)"""
    r = client.get("/api/debug/sprint-collisions")
    assert r.status_code == 200


def test_api_health_still_works(client):
    """Adding the new endpoint does not break /api/health. (AC16)"""
    r = client.get("/api/health")
    assert r.status_code == 200
