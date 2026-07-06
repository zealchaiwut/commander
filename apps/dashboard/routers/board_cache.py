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

import os
import time
from typing import Any, Optional

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
        return None
    now = time.monotonic()
    if now >= entry["expires_at"]:
        del _cache[project]
        return None
    return entry["snapshot"], entry["expires_at"] - now


def store_board_cache(project: str, snapshot: dict) -> None:
    """Store *snapshot* for *project* with a fresh TTL read from env."""
    _cache[project] = {
        "snapshot": snapshot,
        "expires_at": time.monotonic() + current_ttl(),
    }


def invalidate_board(project: str) -> None:
    """Evict only *project*'s entry; all other projects are untouched."""
    _cache.pop(project, None)
