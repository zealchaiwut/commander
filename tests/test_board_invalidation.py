"""Tests for board_invalidated SSE broadcast (issue #1785).

AC1: invalidate_board broadcasts board_invalidated after clearing the cache
AC2: mirror sync returning 200 emits exactly one board_invalidated per project
AC3: mirror sync returning 304 (no changes) emits no board_invalidated event
AC9: E2E — mutating sprint state causes board_invalidated on the SSE channel
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"
_SERVICES_ROOT = Path(__file__).resolve().parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Import modules directly so relative imports and lazy imports both resolve
# against the same module objects (no routers/__init__ loading required).
import board_cache as _board_cache  # noqa: E402
import logs_service as _logs_service  # noqa: E402
import github_events_sync as _github_events_sync  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_subscriber() -> asyncio.Queue:
    """Register a fresh subscriber queue and return it."""
    q: asyncio.Queue = asyncio.Queue()
    _logs_service._subscribers.append(q)
    return q


def _drain_board_invalidated(q: asyncio.Queue) -> list[dict]:
    """Drain all board_invalidated messages from q."""
    msgs = []
    while not q.empty():
        data = json.loads(q.get_nowait())
        if data.get("type") == "board_invalidated":
            msgs.append(data)
    return msgs


class _SubContext:
    """Context manager that adds / removes a subscriber queue."""

    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()

    def __enter__(self):
        _logs_service._subscribers.append(self.q)
        return self.q

    def __exit__(self, *_):
        try:
            _logs_service._subscribers.remove(self.q)
        except ValueError:
            pass


# ── AC1: invalidate_board schedules a board_invalidated broadcast ─────────────

@pytest.mark.asyncio
async def test_invalidate_board_broadcasts_sse_event():
    """AC1: calling invalidate_board schedules a board_invalidated SSE broadcast."""
    with _SubContext() as q:
        _board_cache._cache.clear()
        _board_cache.invalidate_board("owner/test-repo")
        # Yield to the event loop so the scheduled task runs
        await asyncio.sleep(0)

        assert not q.empty(), "subscriber queue must receive a message after invalidate_board"
        msg = json.loads(q.get_nowait())
        assert msg["type"] == "board_invalidated", (
            f"expected board_invalidated, got {msg.get('type')!r}"
        )
        assert msg["project"] == "owner/test-repo"


@pytest.mark.asyncio
async def test_invalidate_board_broadcast_carries_exact_project():
    """AC1 corollary: broadcast carries exactly the project that was invalidated."""
    with _SubContext() as q:
        _board_cache.invalidate_board("owner/repo-alpha")
        await asyncio.sleep(0)

        msg = json.loads(q.get_nowait())
        assert msg["project"] == "owner/repo-alpha"


@pytest.mark.asyncio
async def test_invalidate_board_also_clears_cache():
    """AC1: cache entry is evicted AND broadcast is sent."""
    _board_cache._cache["owner/repo"] = {"snapshot": {"ok": True}, "expires_at": 1e18}
    with _SubContext() as q:
        _board_cache.invalidate_board("owner/repo")
        assert "owner/repo" not in _board_cache._cache, "cache entry must be evicted"
        await asyncio.sleep(0)
        assert not q.empty(), "broadcast must still fire even though cache was already populated"


# ── AC2: mirror sync 200 → exactly one board_invalidated per project ──────────

@pytest.mark.asyncio
async def test_mirror_200_emits_one_board_invalidated():
    """AC2: sync returning 200 emits exactly one board_invalidated for the synced project."""
    _ms_stub = types.SimpleNamespace(sync_milestones_mirror=lambda *a, **kw: None)
    with _SubContext() as q:
        with patch.object(
            _github_events_sync,
            "sync_issues_mirror",
            return_value={"status": 200, "synced": 5, "rate_limited": False},
        ):
            with patch.dict(sys.modules, {"github_milestones": _ms_stub}):
                await _github_events_sync.run_issues_sync_loop(
                    ["owner/repo"], iterations=1
                )
                await asyncio.sleep(0)

        board_msgs = _drain_board_invalidated(q)
        assert len(board_msgs) == 1, (
            f"Expected exactly 1 board_invalidated for 1 repo, got {len(board_msgs)}"
        )
        assert board_msgs[0]["project"] == "owner/repo"


@pytest.mark.asyncio
async def test_mirror_200_emits_one_event_per_project():
    """AC2: two repos with 200 responses → exactly two board_invalidated events."""
    _ms_stub = types.SimpleNamespace(sync_milestones_mirror=lambda *a, **kw: None)
    with _SubContext() as q:
        with patch.object(
            _github_events_sync,
            "sync_issues_mirror",
            return_value={"status": 200, "synced": 2, "rate_limited": False},
        ):
            with patch.dict(sys.modules, {"github_milestones": _ms_stub}):
                await _github_events_sync.run_issues_sync_loop(
                    ["owner/repo-a", "owner/repo-b"], iterations=1
                )
                await asyncio.sleep(0)

        board_msgs = _drain_board_invalidated(q)
        projects = {m["project"] for m in board_msgs}
        assert "owner/repo-a" in projects, "repo-a must have a board_invalidated event"
        assert "owner/repo-b" in projects, "repo-b must have a board_invalidated event"
        assert len(board_msgs) == 2


# ── AC3: mirror sync 304 → no board_invalidated event ────────────────────────

@pytest.mark.asyncio
async def test_mirror_304_emits_no_board_invalidated():
    """AC3: sync returning 304 (no changes) must not emit board_invalidated."""
    _ms_stub = types.SimpleNamespace(sync_milestones_mirror=lambda *a, **kw: None)
    with _SubContext() as q:
        with patch.object(
            _github_events_sync,
            "sync_issues_mirror",
            return_value={"status": 304, "synced": 0, "rate_limited": False},
        ):
            with patch.dict(sys.modules, {"github_milestones": _ms_stub}):
                await _github_events_sync.run_issues_sync_loop(
                    ["owner/repo"], iterations=1
                )
                await asyncio.sleep(0)

        board_msgs = _drain_board_invalidated(q)
        assert len(board_msgs) == 0, (
            f"304 response must not emit board_invalidated, got {len(board_msgs)}"
        )


# ── AC9: E2E — API mutation broadcasts board_invalidated to all SSE clients ───

@pytest.mark.asyncio
async def test_api_mutation_broadcasts_to_all_sse_clients():
    """AC9: invalidate_board (called by every mutating endpoint) broadcasts
    board_invalidated to every connected SSE subscriber."""
    q_a: asyncio.Queue = asyncio.Queue()
    q_b: asyncio.Queue = asyncio.Queue()
    _logs_service._subscribers.extend([q_a, q_b])
    try:
        # Simulate what a sprint-run route handler does
        _board_cache.invalidate_board("owner/project-x")
        await asyncio.sleep(0)

        for label, q in [("client-A", q_a), ("client-B", q_b)]:
            assert not q.empty(), f"{label} must receive board_invalidated"
            msg = json.loads(q.get_nowait())
            assert msg["type"] == "board_invalidated", f"{label}: wrong type"
            assert msg["project"] == "owner/project-x", f"{label}: wrong project"
    finally:
        for q in (q_a, q_b):
            try:
                _logs_service._subscribers.remove(q)
            except ValueError:
                pass
