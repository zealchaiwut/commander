"""Tests for issue #1787 — AC2 through AC6: counter accuracy and instrumentation.

AC2  — path counter produces exactly N for N requests to the same normalized path.
AC3  — github_client._cached increments hits on cache hit, misses on cache miss.
AC4  — mirror fallback counter increments when list_issues falls back from mirror to gh.
AC5  — board_cache hit/miss counters increment correctly.
AC6  — gh subprocess counter increments on every _json / _run call.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def reset_counters():
    """Reset all api_volume counters before and after each test."""
    import api_volume
    api_volume._reset()
    yield
    api_volume._reset()


# ── AC2: Path counter accuracy ─────────────────────────────────────────────────

def test_path_counter_exact_n(reset_counters):
    """N calls to record_request with the same path → counter == N exactly. (AC2)"""
    import api_volume
    N = 7
    for _ in range(N):
        api_volume.record_request("/api/sprints/{label}/live")
    assert api_volume.path_counts["/api/sprints/{label}/live"] == N


def test_path_counter_zero_for_unknown(reset_counters):
    """Counter returns 0 for a path that was never requested. (AC2)"""
    import api_volume
    assert api_volume.path_counts["/api/never/called"] == 0


def test_path_counter_independent_paths(reset_counters):
    """Different normalized paths increment their own counters independently. (AC2)"""
    import api_volume
    api_volume.record_request("/api/a")
    api_volume.record_request("/api/a")
    api_volume.record_request("/api/b")
    assert api_volume.path_counts["/api/a"] == 2
    assert api_volume.path_counts["/api/b"] == 1


# ── AC3: github_client._cached hit/miss counters ──────────────────────────────

def test_gc_cache_miss_increments(reset_counters):
    """Cold cache call → gc_cache_misses +1, gc_cache_hits unchanged. (AC3)"""
    import api_volume
    import github_client

    unique_key = "test_ac3_miss_unique_key_9a3f"
    github_client._cache.pop(unique_key, None)

    api_volume._reset()
    github_client._cached(unique_key, lambda: "fresh_value")

    assert api_volume.gc_cache_misses == 1
    assert api_volume.gc_cache_hits == 0

    # cleanup
    github_client._cache.pop(unique_key, None)


def test_gc_cache_hit_increments(reset_counters):
    """Warm cache call → gc_cache_hits +1, gc_cache_misses unchanged. (AC3)"""
    import api_volume
    import github_client

    unique_key = "test_ac3_hit_unique_key_7b2e"
    github_client._cache.pop(unique_key, None)

    # Prime the cache (this is a miss — we'll reset counters after)
    github_client._cached(unique_key, lambda: "value")
    api_volume._reset()

    # Second call should be a cache hit
    github_client._cached(unique_key, lambda: "value")

    assert api_volume.gc_cache_hits == 1
    assert api_volume.gc_cache_misses == 0

    # cleanup
    github_client._cache.pop(unique_key, None)


# ── AC4: Mirror fallback counter ──────────────────────────────────────────────

def test_mirror_fallback_increments_when_mirror_absent(reset_counters):
    """list_issues with no mirror data → mirror_fallback counter +1. (AC4)"""
    import api_volume
    import github_client

    # Patch mirror to return None (simulates empty/unavailable mirror)
    # Patch _cached to avoid spawning a real gh subprocess
    with (
        patch.object(github_client, "_mirror_issues", return_value=None),
        patch.object(github_client, "_cached", return_value=[]),
    ):
        github_client.list_issues(sprint=9999, repo_name="test/repo-ac4")

    assert api_volume.mirror_fallback == 1


def test_mirror_fallback_not_incremented_when_mirror_has_data(reset_counters):
    """list_issues with mirror data → mirror_fallback counter unchanged. (AC4)"""
    import api_volume
    import github_client

    fake_mirror = [{"number": 1, "title": "x", "labels": [{"name": "sprint-1"}],
                    "state": "open", "url": "", "createdAt": "", "updatedAt": ""}]

    with patch.object(github_client, "_mirror_issues", return_value=fake_mirror):
        github_client.list_issues(sprint=1, repo_name="test/repo-ac4")

    assert api_volume.mirror_fallback == 0


# ── AC5: Board (aggregate) cache hit/miss counters ────────────────────────────

def test_board_cache_miss_increments(reset_counters):
    """get_board_cache on a cold entry → board_cache_misses +1, hits unchanged. (AC5)"""
    import api_volume
    from routers import board_cache

    result = board_cache.get_board_cache("test/repo-ac5-miss-unique")

    assert result is None
    assert api_volume.board_cache_misses == 1
    assert api_volume.board_cache_hits == 0


def test_board_cache_hit_increments(reset_counters):
    """get_board_cache on a warm entry → board_cache_hits +1, misses unchanged. (AC5)"""
    import api_volume
    from routers import board_cache

    project = "test/repo-ac5-hit-unique"
    board_cache.store_board_cache(project, {"data": "snapshot"})
    api_volume._reset()  # reset after store (store itself may trigger a miss check)

    result = board_cache.get_board_cache(project)

    assert result is not None
    assert api_volume.board_cache_hits == 1
    assert api_volume.board_cache_misses == 0

    # cleanup
    board_cache._cache.pop(project, None)


# ── AC6: gh subprocess counter ────────────────────────────────────────────────

def test_gh_subprocess_counter_increments_on_json(reset_counters):
    """github_client._json() → gh_subprocess_calls +1. (AC6)"""
    import api_volume
    import github_client

    mock_result = MagicMock()
    mock_result.stdout = "[]"
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        github_client._json("issue", "list")
        assert mock_run.called

    assert api_volume.gh_subprocess_calls == 1


def test_gh_subprocess_counter_increments_on_run(reset_counters):
    """github_client._run() → gh_subprocess_calls +1. (AC6)"""
    import api_volume
    import github_client

    mock_result = MagicMock()
    mock_result.stdout = "output"
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result):
        github_client._run("label", "list")

    assert api_volume.gh_subprocess_calls == 1


def test_gh_subprocess_counter_accumulates(reset_counters):
    """Multiple gh calls accumulate in gh_subprocess_calls. (AC6)"""
    import api_volume
    import github_client

    mock_result = MagicMock()
    mock_result.stdout = "[]"
    mock_result.returncode = 0

    with patch("subprocess.run", return_value=mock_result):
        github_client._json("issue", "list")
        github_client._json("label", "list")
        github_client._run("issue", "close", "1")

    assert api_volume.gh_subprocess_calls == 3
