"""In-memory per-project board cache with TTL — issue #1642.

Module-level dict keyed by fully-qualified ``owner/repo`` strings. TTL
defaults to 8 s and is overridable via the ``BOARD_CACHE_TTL_S`` env var
without code changes.

Uses aggregate_cache (issue #1786) to co-invalidate the home cache on every
board eviction so mutation hooks need only call ``invalidate_board``.

Public API::

    get_board_cache(project: str) -> tuple[dict, float] | None
    store_board_cache(project: str, snapshot: dict) -> None
    invalidate_board(project: str) -> None
    current_ttl() -> int
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

# aggregate_cache lives in apps/dashboard/ (parent of this routers/ package).
# Force it to position 0 so `import api_volume` resolves to
# apps/dashboard/api_volume.py, not apps/dashboard/routers/api_volume.py,
# even when tests insert the routers dir first.
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_dashboard_s = str(_DASHBOARD_ROOT)
try:
    sys.path.remove(_dashboard_s)
except ValueError:
    pass
sys.path.insert(0, _dashboard_s)

# api_volume lives in apps/dashboard — same dir that's on sys.path at runtime
try:
    import api_volume as _api_volume
except ImportError:
    _api_volume = None  # type: ignore[assignment]

# ── Guarded broadcast helper (issue #1826) ───────────────────────────────────


async def _guarded_broadcast(
    broadcast_fn: Callable[[dict], Coroutine[Any, Any, None]],
    payload: dict,
) -> None:
    """Await *broadcast_fn(payload)* and log any exception instead of
    surfacing it as an unhandled asyncio task warning at GC time."""
    try:
        await broadcast_fn(payload)
    except Exception as exc:
        logger.warning(
            "board_invalidated broadcast failed for %s: %s",
            payload.get("project"),
            exc,
        )


# ── Main-loop reference for threadpool broadcast (issue #1897) ───────────────

# Captured at server startup so invalidate_board can schedule SSE broadcasts
# via run_coroutine_threadsafe when called from threadpool (sync) route
# handlers where asyncio.get_running_loop() raises RuntimeError.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the main event loop.

    Call once from the lifespan startup context before any sync route handlers
    can fire. invalidate_board then uses run_coroutine_threadsafe to schedule
    board_invalidated broadcasts without a running loop in the calling thread.
    """
    global _main_loop
    _main_loop = loop


# ── TTL ──────────────────────────────────────────────────────────────────────


def current_ttl() -> int:
    """Return the configured TTL in seconds (read from env on every call).

    Reads ``BOARD_CACHE_TTL_S`` on every call so that tests can override the
    env var without re-importing the module. Minimum value is 1 second.
    """
    try:
        return max(1, int(os.environ.get("BOARD_CACHE_TTL_S", "8")))
    except (ValueError, TypeError):
        return 8


# ── Store ────────────────────────────────────────────────────────────────────

# Each value: {"snapshot": dict, "expires_at": float (monotonic)}
_cache: dict[str, dict[str, Any]] = {}


def get_board_cache(project: str) -> Optional[tuple[dict, float]]:
    """Return ``(snapshot, remaining_ttl_s)`` for *project*, or ``None`` on
    miss/expiry.

    Keys must be fully-qualified ``owner/repo`` strings. A separate cached
    entry exists for each project — no state is shared across projects.
    """
    entry = _cache.get(project)
    if entry is None:
        if _api_volume:
            _api_volume.record_board_miss()
        return None
    now = time.monotonic()
    if now >= entry["expires_at"]:
        # pop, not del: GET /api/board runs sync in the threadpool, so two
        # requests can race past the expiry check for the same project.
        _cache.pop(project, None)
        if _api_volume:
            _api_volume.record_board_miss()
        return None
    if _api_volume:
        _api_volume.record_board_hit()
    return entry["snapshot"], entry["expires_at"] - now


def store_board_cache(project: str, snapshot: dict) -> None:
    """Store *snapshot* for *project* with a fresh TTL read from env."""
    _cache[project] = {
        "snapshot": snapshot,
        "expires_at": time.monotonic() + current_ttl(),
    }


def invalidate_board(project: str) -> None:
    """Evict *project*'s cache entry and broadcast board_invalidated over SSE
    (issue #1785).

    Also evicts the home-cache entry for the same project so sprint mutations
    invalidate both caches from a single call site (issue #1786).

    Broadcast path (issue #1897):
    - Async context (async route): asyncio.get_running_loop() succeeds →
      create_task.
    - Threadpool context (sync ``def`` route): get_running_loop() raises
      RuntimeError → fall back to run_coroutine_threadsafe on the main loop
      captured at startup.
    - No main loop captured (test / script context): skip broadcast silently.
    """
    _cache.pop(project, None)
    try:
        import aggregate_cache as _agg  # noqa: PLC0415
        _agg.invalidate(project, "home")
    except Exception:
        pass
    try:
        from .logs_service import broadcast as _bc  # noqa: PLC0415
    except ImportError:
        try:
            from logs_service import broadcast as _bc  # noqa: PLC0415
        except ImportError:
            return
    _payload = {"type": "board_invalidated", "project": project}
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_guarded_broadcast(_bc, _payload))
        return
    except RuntimeError:
        pass
    # Called from a threadpool (sync route handler) — schedule on main loop.
    if _main_loop is not None and not _main_loop.is_closed():
        asyncio.run_coroutine_threadsafe(
            _guarded_broadcast(_bc, _payload),
            _main_loop,
        )
