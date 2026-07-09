"""In-memory per-project board cache with TTL — issue #1642.

Module-level dict keyed by fully-qualified ``owner/repo`` strings. TTL
defaults to 8 s and is overridable via the ``BOARD_CACHE_TTL_S`` env var
without code changes.

Public API::

    get_board_cache(project: str) -> tuple[dict, float] | None
    store_board_cache(project: str, snapshot: dict) -> None
    invalidate_board(project: str) -> None
    current_ttl() -> int
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

# api_volume lives in apps/dashboard — same dir that's on sys.path at runtime
try:
    import api_volume as _api_volume
except ImportError:
    _api_volume = None  # type: ignore[assignment]

# ── TTL ───────────────────────────────────────────────────────────────────────

def current_ttl() -> int:
    """Return the configured TTL in seconds (read from env on every call).

    Reads ``BOARD_CACHE_TTL_S`` on every call so that tests can override the
    env var without re-importing the module. Minimum value is 1 second.
    """
    try:
        return max(1, int(os.environ.get("BOARD_CACHE_TTL_S", "8")))
    except (ValueError, TypeError):
        return 8


# ── Store ─────────────────────────────────────────────────────────────────────

# Each value: {"snapshot": dict, "expires_at": float (monotonic)}
_cache: dict[str, dict[str, Any]] = {}


def get_board_cache(project: str) -> Optional[tuple[dict, float]]:
    """Return ``(snapshot, remaining_ttl_s)`` for *project*, or ``None`` on miss/expiry.

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
    """Evict *project*'s cache entry and broadcast board_invalidated over SSE (issue #1785)."""
    _cache.pop(project, None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop — sync test context, skip broadcast
    try:
        from .logs_service import broadcast as _bc  # noqa: PLC0415
    except ImportError:
        try:
            from logs_service import broadcast as _bc  # type: ignore[no-redef]  # noqa: PLC0415
        except ImportError:
            return
    loop.create_task(_bc({"type": "board_invalidated", "project": project}))
