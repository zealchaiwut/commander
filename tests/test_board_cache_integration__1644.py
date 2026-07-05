"""Integration tests for board cache — issue #1644.

Gap coverage beyond test_board_cache__1642.py (unit) and
test_invalidate_board_endpoints__1643.py (source-text inspection):

  1. Deterministic fake-time control: all time advances use a patched
     board_cache.time.monotonic — zero real sleeps in this file.
  2. End-to-end HTTP integration: tests drive GET /api/board and representative
     mutating endpoints through FastAPI TestClient instead of calling
     board_cache functions directly.
  3. Cross-project cache isolation via HTTP: mutation on project A must not
     evict project B's cache entry — verified at the HTTP layer.

AC mapping:
  AC1  Cache hit within TTL — compute called exactly once for two requests
  AC2  Cache miss after TTL expiry — advance fake clock, compute runs again
  AC3  invalidate_board forces recompute — triggered via HTTP endpoint
  AC4  Cross-project isolation — mutation on A leaves B's cache intact
  AC5  Response shape — cache: {hit, ttl_s} present on every GET /api/board
  AC6  Mutating-endpoint invalidation — representative sample of 3 endpoint
       patterns verified end-to-end; each invalidates A but not B
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── Path setup ────────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"
_SERVICES_ROOT = Path(__file__).resolve().parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import board_cache as _bc  # noqa: E402

# ── Shared project names ───────────────────────────────────────────────────────

_PROJECT_A = "owner/repo-a"
_PROJECT_B = "owner/repo-b"

# ── Fake clock ────────────────────────────────────────────────────────────────


class _FakeClock:
    """Deterministic monotonic clock — replaces time.monotonic in board_cache.

    All stores and gets in board_cache use this clock, so TTL advances are
    exact and require zero real wall-clock time.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self._t = start

    def advance(self, seconds: float) -> None:
        self._t += seconds

    def __call__(self) -> float:
        return self._t


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_bc_cache():
    """Isolate tests: clear the module-level cache before and after each test."""
    _bc._cache.clear()
    yield
    _bc._cache.clear()


@pytest.fixture
def fake_clock(monkeypatch) -> _FakeClock:
    """Patch board_cache.time.monotonic with a deterministic fake clock.

    Because board_cache does ``import time`` and calls ``time.monotonic()``,
    replacing the module-level ``time`` reference ensures every store/get uses
    the fake clock without any real elapsed time.
    """
    clock = _FakeClock()
    fake_time = types.SimpleNamespace(monotonic=clock)
    monkeypatch.setattr(_bc, "time", fake_time)
    return clock


@pytest.fixture
def app_ctx(fake_clock):
    """TestClient + call-counter + fake clock, ready for integration tests.

    The test app implements:
    - GET /api/board — real board_cache logic + a stubbed compute function
    - Three representative mutating endpoints — each mirrors the invalidation
      pattern used by the 13 real board-mutating route handlers in the router
      modules (sprint_run.py, sprints.py, sprint_crud.py, etc.).

    The stub routes omit the actual DB write / GitHub operation because those
    are not the contract under test here; the test verifies the cache-layer
    interaction end-to-end via HTTP.
    """
    call_counter: dict[str, int] = {"n": 0}

    def _fake_compute(project: str) -> dict[str, Any]:
        call_counter["n"] += 1
        return {
            "project": project,
            "generated_at": "2026-01-01T00:00:00+00:00",
            "sections": {"backlog": {"count": 0, "tickets": []}},
            "capacity": {},
            "summaries": {},
        }

    app = FastAPI()

    @app.get("/api/board")
    def get_board(project: str):
        """Mirror sprint_dispatch.get_board using real board_cache."""
        cached = _bc.get_board_cache(project)
        if cached is not None:
            snapshot, remaining = cached
            return {**snapshot, "cache": {"hit": True, "ttl_s": round(remaining, 2)}}
        snapshot = _fake_compute(project)
        _bc.store_board_cache(project, snapshot)
        return {**snapshot, "cache": {"hit": False, "ttl_s": _bc.current_ttl()}}

    # ── Representative mutating endpoints ─────────────────────────────────────
    # These three routes mirror the invalidation pattern from three distinct
    # router files documented in AC6 of issue #1643:
    #   /run-sprint  → sprint_run.py: run_sprint_managed
    #   /save-goal   → sprints.py:    save_sprint_goal
    #   /create      → sprint_crud.py: create_sprint
    # Each calls invalidate_board(project) after a successful write, exactly
    # as the real handlers do.

    @app.post("/api/mutation/run-sprint")
    def trigger_run_sprint(project: str):
        """Pattern: sprint_run.py → invalidate_board(body.project)."""
        _bc.invalidate_board(project)
        return {"ok": True, "endpoint": "run_sprint_managed"}

    @app.post("/api/mutation/save-goal")
    def trigger_save_goal(project: str):
        """Pattern: sprints.py → invalidate_board(body.project)."""
        _bc.invalidate_board(project)
        return {"ok": True, "endpoint": "save_sprint_goal"}

    @app.post("/api/mutation/create-sprint")
    def trigger_create_sprint(project: str):
        """Pattern: sprint_crud.py → invalidate_board(body.project)."""
        _bc.invalidate_board(project)
        return {"ok": True, "endpoint": "create_sprint"}

    client = TestClient(app, raise_server_exceptions=True)
    return types.SimpleNamespace(client=client, counter=call_counter, clock=fake_clock)


# ── AC1: Cache hit within TTL — compute called exactly once ──────────────────


class TestCacheHitWithinTTL:
    """Two GET /api/board requests within the TTL share a single compute call."""

    def test_compute_called_once_for_two_requests(self, app_ctx):
        ctx = app_ctx
        # First request → cache miss → compute
        r1 = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r1.status_code == 200
        assert r1.json()["cache"]["hit"] is False
        assert ctx.counter["n"] == 1

        # Second request within TTL → cache hit → no recompute
        r2 = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r2.status_code == 200
        assert r2.json()["cache"]["hit"] is True
        assert ctx.counter["n"] == 1, "Compute must not be called again within TTL"

    def test_both_responses_reference_same_project(self, app_ctx):
        ctx = app_ctx
        r1 = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        r2 = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r1.json()["project"] == r2.json()["project"] == _PROJECT_A

    def test_hit_response_has_positive_ttl_s(self, app_ctx):
        ctx = app_ctx
        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        r = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r.json()["cache"]["ttl_s"] > 0


# ── AC2: Cache miss after TTL expiry ─────────────────────────────────────────


class TestCacheMissAfterTTLExpiry:
    """Advancing the fake clock past TTL causes the next GET to recompute."""

    def test_compute_called_again_after_ttl(self, app_ctx, monkeypatch):
        ctx = app_ctx
        monkeypatch.setenv("BOARD_CACHE_TTL_S", "8")

        # Warm the cache (miss → compute #1)
        r1 = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r1.json()["cache"]["hit"] is False
        assert ctx.counter["n"] == 1

        # Advance fake clock past the 8 s TTL
        ctx.clock.advance(9)

        # Should be a miss again (compute #2)
        r2 = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r2.json()["cache"]["hit"] is False
        assert ctx.counter["n"] == 2, "Compute must run again after TTL expiry"

    def test_fresh_cache_entry_stored_after_expiry(self, app_ctx):
        ctx = app_ctx
        ctx.client.get("/api/board", params={"project": _PROJECT_A})

        # Expire and recompute
        ctx.clock.advance(9)
        ctx.client.get("/api/board", params={"project": _PROJECT_A})

        # The new entry must be warm (next request is a hit)
        r = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r.json()["cache"]["hit"] is True

    def test_no_recompute_before_ttl(self, app_ctx, monkeypatch):
        ctx = app_ctx
        monkeypatch.setenv("BOARD_CACHE_TTL_S", "8")

        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        # Advance within TTL (7 s < 8 s)
        ctx.clock.advance(7)
        ctx.client.get("/api/board", params={"project": _PROJECT_A})

        assert ctx.counter["n"] == 1, "Compute must not run before TTL expires"


# ── AC3 + AC6: Mutating endpoint forces cache miss ───────────────────────────


class TestInvalidationViaEndpoint:
    """POST to a mutating endpoint must invalidate the cache for that project."""

    @pytest.mark.parametrize("mutation_path,endpoint_name", [
        ("/api/mutation/run-sprint", "run_sprint_managed"),
        ("/api/mutation/save-goal", "save_sprint_goal"),
        ("/api/mutation/create-sprint", "create_sprint"),
    ])
    def test_mutation_causes_cache_miss_for_same_project(
        self, app_ctx, mutation_path, endpoint_name
    ):
        ctx = app_ctx
        # Warm the cache
        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        r_warm = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r_warm.json()["cache"]["hit"] is True, "Precondition: cache must be warm"

        # Trigger mutation for project A
        r_mut = ctx.client.post(mutation_path, params={"project": _PROJECT_A})
        assert r_mut.status_code == 200
        assert r_mut.json()["ok"] is True

        # Board request must now be a miss
        r_miss = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r_miss.json()["cache"]["hit"] is False, (
            f"After {endpoint_name} mutation, cache.hit must be False for {_PROJECT_A}"
        )

    def test_recompute_is_triggered_after_invalidation(self, app_ctx):
        ctx = app_ctx
        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert ctx.counter["n"] == 1

        ctx.client.post("/api/mutation/run-sprint", params={"project": _PROJECT_A})
        ctx.client.get("/api/board", params={"project": _PROJECT_A})

        assert ctx.counter["n"] == 2, "Compute must run after invalidation"


# ── AC4: Cross-project cache isolation via HTTP ───────────────────────────────


class TestCrossProjectIsolation:
    """Mutation on project A must NOT evict project B's cache entry.

    This is the negative case required by the AC: a mutation on A leaves B
    fully intact — no cross-project cache pollution.
    """

    @pytest.mark.parametrize("mutation_path", [
        "/api/mutation/run-sprint",
        "/api/mutation/save-goal",
        "/api/mutation/create-sprint",
    ])
    def test_mutation_on_a_leaves_b_cached(self, app_ctx, mutation_path):
        ctx = app_ctx
        # Warm both projects
        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        ctx.client.get("/api/board", params={"project": _PROJECT_B})

        # Confirm both are warm before mutation
        assert ctx.client.get("/api/board", params={"project": _PROJECT_A}).json()["cache"]["hit"] is True
        assert ctx.client.get("/api/board", params={"project": _PROJECT_B}).json()["cache"]["hit"] is True

        # Mutate project A only
        ctx.client.post(mutation_path, params={"project": _PROJECT_A})

        # A must be a miss; B must still be a hit
        r_a = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        r_b = ctx.client.get("/api/board", params={"project": _PROJECT_B})

        assert r_a.json()["cache"]["hit"] is False, (
            f"Project A must be a miss after mutation ({mutation_path})"
        )
        assert r_b.json()["cache"]["hit"] is True, (
            f"Project B must remain cached after mutation on A ({mutation_path})"
        )

    def test_b_snapshot_fields_unchanged_after_a_mutation(self, app_ctx):
        """B's project field must still read 'owner/repo-b' after A is invalidated."""
        ctx = app_ctx
        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        r_b_before = ctx.client.get("/api/board", params={"project": _PROJECT_B})
        project_before = r_b_before.json()["project"]

        ctx.client.post("/api/mutation/run-sprint", params={"project": _PROJECT_A})

        r_b_after = ctx.client.get("/api/board", params={"project": _PROJECT_B})
        assert r_b_after.json()["project"] == project_before == _PROJECT_B

    def test_b_recompute_count_unchanged_after_a_mutation(self, app_ctx):
        """Compute for B must not be triggered when A is invalidated."""
        ctx = app_ctx
        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        ctx.client.get("/api/board", params={"project": _PROJECT_B})
        count_after_warm = ctx.counter["n"]  # should be 2

        ctx.client.post("/api/mutation/save-goal", params={"project": _PROJECT_A})

        # Project A recomputes; B must not
        ctx.client.get("/api/board", params={"project": _PROJECT_B})
        assert ctx.counter["n"] == count_after_warm, (
            "Compute must not be called for B after mutation on A"
        )


# ── AC5: Response shape ───────────────────────────────────────────────────────


class TestResponseShape:
    """Every GET /api/board response includes cache: {hit: bool, ttl_s: number}."""

    def test_cache_field_present_on_miss(self, app_ctx):
        r = app_ctx.client.get("/api/board", params={"project": _PROJECT_A})
        data = r.json()
        assert "cache" in data, "cache key must be present in response"
        assert isinstance(data["cache"]["hit"], bool)
        assert isinstance(data["cache"]["ttl_s"], (int, float))

    def test_cache_field_present_on_hit(self, app_ctx):
        app_ctx.client.get("/api/board", params={"project": _PROJECT_A})
        r = app_ctx.client.get("/api/board", params={"project": _PROJECT_A})
        data = r.json()
        assert "cache" in data
        assert data["cache"]["hit"] is True
        assert isinstance(data["cache"]["ttl_s"], (int, float))
        assert data["cache"]["ttl_s"] > 0

    def test_miss_hit_is_false(self, app_ctx):
        r = app_ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r.json()["cache"]["hit"] is False

    def test_hit_ttl_s_is_positive(self, app_ctx):
        app_ctx.client.get("/api/board", params={"project": _PROJECT_A})
        r = app_ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r.json()["cache"]["ttl_s"] > 0

    def test_miss_ttl_s_equals_configured_ttl(self, app_ctx, monkeypatch):
        monkeypatch.setenv("BOARD_CACHE_TTL_S", "15")
        r = app_ctx.client.get("/api/board", params={"project": _PROJECT_A})
        assert r.json()["cache"]["ttl_s"] == 15

    def test_cache_field_present_after_invalidation(self, app_ctx):
        """cache field must appear even on the forced-miss after invalidation."""
        ctx = app_ctx
        ctx.client.get("/api/board", params={"project": _PROJECT_A})
        ctx.client.post("/api/mutation/run-sprint", params={"project": _PROJECT_A})
        r = ctx.client.get("/api/board", params={"project": _PROJECT_A})
        data = r.json()
        assert "cache" in data
        assert data["cache"]["hit"] is False
        assert isinstance(data["cache"]["ttl_s"], (int, float))
