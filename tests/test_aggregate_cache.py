"""Tests for the aggregate_cache module — issue #1786.

AC coverage:
  AC1  per-project keying; configurable TTL; invalidate(project); cache metadata
  AC2  hit/miss counters incremented and readable
  AC6  cross-project isolation: invalidating project A does not evict project B
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import aggregate_cache as _ac  # noqa: E402


def _reset():
    """Clear all aggregate_cache state between tests."""
    _ac._store.clear()
    _ac._hits.clear()
    _ac._misses.clear()


# ── AC1: per-project keying ────────────────────────────────────────────────────

class TestPerProjectKeying:
    def test_store_and_get_returns_data(self):
        _reset()
        _ac.store("owner/repo-a", "home", {"name": "A"}, ttl_s=30.0)
        result = _ac.get("owner/repo-a", "home")
        assert result is not None
        data, meta = result
        assert data["name"] == "A"

    def test_separate_projects_do_not_share_entries(self):
        _reset()
        _ac.store("owner/repo-a", "home", {"name": "A"}, ttl_s=30.0)
        _ac.store("owner/repo-b", "home", {"name": "B"}, ttl_s=30.0)
        result_a = _ac.get("owner/repo-a", "home")
        result_b = _ac.get("owner/repo-b", "home")
        assert result_a is not None and result_a[0]["name"] == "A"
        assert result_b is not None and result_b[0]["name"] == "B"

    def test_separate_namespaces_do_not_share_entries(self):
        _reset()
        _ac.store("owner/repo", "home", {"kind": "home"}, ttl_s=30.0)
        _ac.store("owner/repo", "board", {"kind": "board"}, ttl_s=30.0)
        result_home = _ac.get("owner/repo", "home")
        result_board = _ac.get("owner/repo", "board")
        assert result_home is not None and result_home[0]["kind"] == "home"
        assert result_board is not None and result_board[0]["kind"] == "board"

    def test_get_returns_none_on_miss(self):
        _reset()
        result = _ac.get("owner/no-such-project", "home")
        assert result is None


# ── AC1: configurable TTL ─────────────────────────────────────────────────────

class TestTTL:
    def test_entry_within_ttl_is_returned(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        assert _ac.get("owner/repo", "home") is not None

    def test_expired_entry_returns_none(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        # Backdating expires_at simulates TTL expiry without sleeping
        _ac._store[("owner/repo", "home")]["expires_at"] = time.monotonic() - 0.001
        assert _ac.get("owner/repo", "home") is None

    def test_expired_entry_evicted_from_store(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        _ac._store[("owner/repo", "home")]["expires_at"] = time.monotonic() - 1.0
        _ac.get("owner/repo", "home")  # triggers eviction
        assert ("owner/repo", "home") not in _ac._store

    def test_fresh_store_after_expiry_is_retrievable(self):
        _reset()
        _ac.store("owner/repo", "home", {"v": 1}, ttl_s=30.0)
        _ac._store[("owner/repo", "home")]["expires_at"] = time.monotonic() - 1.0
        assert _ac.get("owner/repo", "home") is None
        _ac.store("owner/repo", "home", {"v": 2}, ttl_s=30.0)
        result = _ac.get("owner/repo", "home")
        assert result is not None
        assert result[0]["v"] == 2


# ── AC1: invalidate ────────────────────────────────────────────────────────────

class TestInvalidate:
    def test_invalidate_specific_namespace(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        _ac.store("owner/repo", "board", {"y": 2}, ttl_s=30.0)
        _ac.invalidate("owner/repo", "home")
        assert _ac.get("owner/repo", "home") is None
        assert _ac.get("owner/repo", "board") is not None

    def test_invalidate_all_namespaces(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        _ac.store("owner/repo", "board", {"y": 2}, ttl_s=30.0)
        _ac.invalidate("owner/repo")  # no namespace = all
        assert _ac.get("owner/repo", "home") is None
        assert _ac.get("owner/repo", "board") is None

    def test_invalidate_nonexistent_is_noop(self):
        _reset()
        _ac.invalidate("owner/no-such-project", "home")  # must not raise
        _ac.invalidate("owner/no-such-project")


# ── AC1: cache metadata on responses ──────────────────────────────────────────

class TestCacheMeta:
    def test_hit_returns_true_on_cache_hit(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        result = _ac.get("owner/repo", "home")
        assert result is not None
        _data, meta = result
        assert meta.hit is True

    def test_ttl_s_is_positive_int_on_hit(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        result = _ac.get("owner/repo", "home")
        assert result is not None
        _data, meta = result
        assert isinstance(meta.ttl_s, int)
        assert meta.ttl_s > 0

    def test_ttl_s_does_not_exceed_stored_ttl(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        result = _ac.get("owner/repo", "home")
        assert result is not None
        _data, meta = result
        assert meta.ttl_s <= 30


# ── AC2: hit/miss counters ─────────────────────────────────────────────────────

class TestHitMissCounters:
    def test_hit_counter_increments(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        before = _ac.get_stats()["hits"].get("home", 0)
        _ac.get("owner/repo", "home")
        after = _ac.get_stats()["hits"].get("home", 0)
        assert after == before + 1

    def test_miss_counter_increments_on_empty(self):
        _reset()
        before = _ac.get_stats()["misses"].get("home", 0)
        _ac.get("owner/no-such", "home")
        after = _ac.get_stats()["misses"].get("home", 0)
        assert after == before + 1

    def test_miss_counter_increments_on_expiry(self):
        _reset()
        _ac.store("owner/repo", "home", {"x": 1}, ttl_s=30.0)
        _ac._store[("owner/repo", "home")]["expires_at"] = time.monotonic() - 1.0
        before = _ac.get_stats()["misses"].get("home", 0)
        _ac.get("owner/repo", "home")  # expired → miss
        after = _ac.get_stats()["misses"].get("home", 0)
        assert after == before + 1

    def test_get_stats_returns_size(self):
        _reset()
        _ac.store("owner/a", "home", {}, ttl_s=30.0)
        _ac.store("owner/b", "home", {}, ttl_s=30.0)
        stats = _ac.get_stats()
        assert stats["size"] == 2

    def test_get_stats_readable_after_multiple_operations(self):
        _reset()
        _ac.store("owner/repo", "home", {}, ttl_s=30.0)
        _ac.get("owner/repo", "home")   # hit
        _ac.get("owner/repo", "home")   # hit
        _ac.get("owner/missing", "home")  # miss
        stats = _ac.get_stats()
        assert stats["hits"].get("home", 0) >= 2
        assert stats["misses"].get("home", 0) >= 1


# ── AC6: cross-project isolation ──────────────────────────────────────────────

class TestCrossProjectIsolation:
    def test_invalidate_project_a_does_not_evict_project_b(self):
        """Invalidating project A must not evict any of project B's entries."""
        _reset()
        _ac.store("owner/project-a", "home", {"proj": "A"}, ttl_s=30.0)
        _ac.store("owner/project-b", "home", {"proj": "B"}, ttl_s=30.0)
        _ac.store("owner/project-a", "board", {"board": "A"}, ttl_s=8.0)
        _ac.store("owner/project-b", "board", {"board": "B"}, ttl_s=8.0)

        _ac.invalidate("owner/project-a")

        # Project A evicted
        assert _ac.get("owner/project-a", "home") is None
        assert _ac.get("owner/project-a", "board") is None

        # Project B untouched
        assert _ac.get("owner/project-b", "home") is not None
        assert _ac.get("owner/project-b", "board") is not None

    def test_invalidate_one_namespace_does_not_affect_other_project_same_namespace(self):
        _reset()
        _ac.store("owner/project-a", "home", {"a": 1}, ttl_s=30.0)
        _ac.store("owner/project-b", "home", {"b": 2}, ttl_s=30.0)
        _ac.invalidate("owner/project-a", "home")
        assert _ac.get("owner/project-b", "home") is not None
