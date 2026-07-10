"""Shared per-project aggregate cache — issue #1786.

Per-project, per-namespace in-memory cache with configurable TTL, hit/miss
counters, and cross-namespace invalidation so home+board eviction stays
coordinated from a single call site.

Public API::

    get(project, namespace)                -> tuple[dict, CacheMeta] | None
    store(project, namespace, data, ttl_s) -> None
    invalidate(project, namespace=None)    -> None
    get_stats()                            -> dict

    CacheMeta(hit: bool, ttl_s: int)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# ── Internal store ────────────────────────────────────────────────────────────
# keyed by (project, namespace); value: {"data": dict, "expires_at": float}

_store: dict[tuple[str, str], dict[str, Any]] = {}

# ── Counters (per namespace) ──────────────────────────────────────────────────

_hits: dict[str, int] = {}
_misses: dict[str, int] = {}


# ── Public types ──────────────────────────────────────────────────────────────

@dataclass
class CacheMeta:
    hit: bool
    ttl_s: int


# ── Public API ─────────────────────────────────────────────────────────────────

def get(project: str, namespace: str) -> tuple[dict, CacheMeta] | None:
    """Return ``(data, CacheMeta)`` for *project*/*namespace*, or ``None`` on miss/expiry."""
    key = (project, namespace)
    entry = _store.get(key)
    if entry is None:
        _misses[namespace] = _misses.get(namespace, 0) + 1
        return None
    now = time.monotonic()
    if now >= entry["expires_at"]:
        _store.pop(key, None)
        _misses[namespace] = _misses.get(namespace, 0) + 1
        return None
    _hits[namespace] = _hits.get(namespace, 0) + 1
    remaining = max(0, int(entry["expires_at"] - now))
    return entry["data"], CacheMeta(hit=True, ttl_s=remaining)


def store(project: str, namespace: str, data: dict, ttl_s: float) -> None:
    """Store *data* for *project*/*namespace* with a fresh TTL."""
    _store[(project, namespace)] = {
        "data": data,
        "expires_at": time.monotonic() + ttl_s,
    }


def invalidate(project: str, namespace: str | None = None) -> None:
    """Evict cache entries for *project*.

    If *namespace* is given, only that namespace is evicted.
    If *namespace* is ``None``, all namespaces for *project* are evicted.
    """
    if namespace is not None:
        _store.pop((project, namespace), None)
    else:
        to_del = [k for k in list(_store) if k[0] == project]
        for k in to_del:
            _store.pop(k, None)


def get_stats() -> dict:
    """Return hit/miss counters and current store size (for observability)."""
    return {
        "hits": dict(_hits),
        "misses": dict(_misses),
        "size": len(_store),
    }
