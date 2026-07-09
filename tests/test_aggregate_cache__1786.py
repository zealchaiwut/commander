"""Tests for issue #1786: extract shared aggregate-cache helper.

These tests exercise the aggregate_cache module's public API directly and
verify integration with board_cache and home_service — no live server needed.

AC coverage:
  AC1  aggregate_cache.py exposes get/store/invalidate/get_stats with CacheMeta
  AC2  hit/miss counters increment on each get() call
  AC4  board_cache.invalidate_board delegates home eviction to aggregate_cache
  AC7  home_service.home_project_data response contains required fields
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"

for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import aggregate_cache as _ac  # noqa: E402
import board_cache as _bc  # noqa: E402


def _reset():
    _ac._store.clear()
    _ac._hits.clear()
    _ac._misses.clear()
    _bc._cache.clear()


# ── AC1: public API contract ───────────────────────────────────────────────────

def test_aggregate_cache_public_api_symbols_exist():
    assert callable(_ac.get)
    assert callable(_ac.store)
    assert callable(_ac.invalidate)
    assert callable(_ac.get_stats)
    assert _ac.CacheMeta is not None


def test_store_and_get_roundtrip():
    _reset()
    _ac.store("org/repo", "home", {"k": "v"}, ttl_s=30.0)
    result = _ac.get("org/repo", "home")
    assert result is not None
    data, meta = result
    assert data == {"k": "v"}
    assert meta.hit is True
    assert isinstance(meta.ttl_s, int)


def test_cache_miss_returns_none():
    _reset()
    assert _ac.get("org/missing", "home") is None


def test_ttl_expiry_returns_none():
    _reset()
    _ac.store("org/repo", "home", {"x": 1}, ttl_s=30.0)
    _ac._store[("org/repo", "home")]["expires_at"] = time.monotonic() - 0.001
    assert _ac.get("org/repo", "home") is None


def test_invalidate_clears_entry():
    _reset()
    _ac.store("org/repo", "home", {"x": 1}, ttl_s=30.0)
    _ac.invalidate("org/repo", "home")
    assert _ac.get("org/repo", "home") is None


# ── AC2: counters ──────────────────────────────────────────────────────────────

def test_hit_counter_increments():
    _reset()
    _ac.store("org/repo", "home", {}, ttl_s=30.0)
    before = _ac.get_stats()["hits"].get("home", 0)
    _ac.get("org/repo", "home")
    assert _ac.get_stats()["hits"]["home"] == before + 1


def test_miss_counter_increments():
    _reset()
    before = _ac.get_stats()["misses"].get("home", 0)
    _ac.get("org/no-project", "home")
    assert _ac.get_stats()["misses"]["home"] == before + 1


def test_get_stats_size_reflects_stored_entries():
    _reset()
    _ac.store("org/a", "home", {}, ttl_s=30.0)
    _ac.store("org/b", "home", {}, ttl_s=30.0)
    assert _ac.get_stats()["size"] == 2


# ── AC4: board_cache delegates home eviction to aggregate_cache ────────────────

def test_board_invalidate_also_evicts_home_via_aggregate_cache():
    """invalidate_board must call aggregate_cache.invalidate for the home namespace."""
    _reset()
    project = "org/my-project"
    _ac.store(project, "home", {"name": "x"}, ttl_s=30.0)
    assert _ac.get(project, "home") is not None

    _bc.invalidate_board(project)

    assert _ac.get(project, "home") is None


def test_board_invalidate_does_not_affect_other_projects():
    _reset()
    _ac.store("org/proj-a", "home", {"n": "a"}, ttl_s=30.0)
    _ac.store("org/proj-b", "home", {"n": "b"}, ttl_s=30.0)

    _bc.invalidate_board("org/proj-a")

    assert _ac.get("org/proj-b", "home") is not None


# ── AC7: home_service response shape ──────────────────────────────────────────

def test_home_project_data_returns_required_fields():
    import home_service as _hs
    _reset()

    proj = {"repo": "org/test-proj", "name": "test-proj", "icon": "ti-folder", "color": "gray"}

    with (
        patch("github_client.list_all_open_issues", return_value=[]),
        patch("github_client.classify_issue", return_value="backlog"),
    ):
        result = _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})

    for field in ("name", "slug", "repo", "status", "uat_count", "backlog_count"):
        assert field in result, f"Missing field: {field}"
    assert "cache" in result
    assert result["cache"]["hit"] is False
