"""In-memory API call-volume and cache-hit observability counters (issue #1787).

All counters are module-level in-process state — they reset on process restart.
No locks beyond Python's GIL; uvicorn runs single-process with asyncio, so
simple integer increments are safe without explicit locking.
No file I/O, DB writes, or external store touches.
"""
from __future__ import annotations

import collections
import time

_process_start: float = time.monotonic()

# Per-normalized-path HTTP request counts
path_counts: collections.Counter[str] = collections.Counter()

# github_client._cached hit/miss
gc_cache_hits: int = 0
gc_cache_misses: int = 0

# Times the local DB issues-mirror was absent and code fell back to a live gh call
mirror_fallback: int = 0

# Board aggregate cache hit/miss (routers/board_cache.py)
board_cache_hits: int = 0
board_cache_misses: int = 0

# Total gh CLI subprocess invocations
gh_subprocess_calls: int = 0


def record_request(normalized_path: str) -> None:
    path_counts[normalized_path] += 1


def record_gc_hit() -> None:
    global gc_cache_hits
    gc_cache_hits += 1


def record_gc_miss() -> None:
    global gc_cache_misses
    gc_cache_misses += 1


def record_mirror_fallback() -> None:
    global mirror_fallback
    mirror_fallback += 1


def record_board_hit() -> None:
    global board_cache_hits
    board_cache_hits += 1


def record_board_miss() -> None:
    global board_cache_misses
    board_cache_misses += 1


def record_gh_subprocess() -> None:
    global gh_subprocess_calls
    gh_subprocess_calls += 1


def get_snapshot(top_n: int = 20) -> dict:
    """Return a self-documenting snapshot of all counters."""
    uptime = time.monotonic() - _process_start

    top_paths = [
        {"path": p, "count": c}
        for p, c in path_counts.most_common(top_n)
    ]

    gc_total = gc_cache_hits + gc_cache_misses
    gc_hit_rate = round(gc_cache_hits / gc_total, 4) if gc_total else 0.0

    board_total = board_cache_hits + board_cache_misses
    board_hit_rate = round(board_cache_hits / board_total, 4) if board_total else 0.0

    return {
        "_doc": (
            "In-process API call-volume and cache-hit counters. "
            "All counters reset on process restart — no persistence."
        ),
        "uptime_seconds": round(uptime, 3),
        "_uptime_seconds_doc": "Seconds elapsed since the process started.",
        "top_paths": top_paths,
        "top_paths_n": top_n,
        "_top_paths_doc": (
            "Top N highest-traffic normalized route paths sorted by count descending. "
            "Path parameters are normalized to their template form, "
            "e.g. /api/sprints/{label}/live instead of /api/sprints/sprint-42/live."
        ),
        "cache_stats": {
            "_doc": "Hit/miss counters per cache layer.",
            "github_client": {
                "_doc": (
                    "github_client._cached — TTL-based in-memory cache for gh CLI results. "
                    "hit: result served from cache without a gh subprocess. "
                    "miss: cache was empty or expired; underlying gh call was executed."
                ),
                "hits": gc_cache_hits,
                "misses": gc_cache_misses,
                "hit_rate": gc_hit_rate,
            },
            "mirror_fallback": {
                "_doc": (
                    "Number of times the local DB issues-mirror was absent or empty "
                    "and a live gh CLI request was issued instead."
                ),
                "count": mirror_fallback,
            },
            "board_cache": {
                "_doc": (
                    "In-memory per-project board snapshot cache (routers/board_cache.py). "
                    "hit: snapshot returned from cache. "
                    "miss: cache was empty or expired; full board compute was triggered."
                ),
                "hits": board_cache_hits,
                "misses": board_cache_misses,
                "hit_rate": board_hit_rate,
            },
        },
        "gh_subprocess_total": gh_subprocess_calls,
        "_gh_subprocess_total_doc": (
            "Total gh CLI subprocess invocations since process start. "
            "Counts every call via github_client._json() and github_client._run()."
        ),
    }


def _reset() -> None:
    """Reset all counters to zero. For testing only — do not call in production."""
    global gc_cache_hits, gc_cache_misses, mirror_fallback
    global board_cache_hits, board_cache_misses, gh_subprocess_calls
    path_counts.clear()
    gc_cache_hits = 0
    gc_cache_misses = 0
    mirror_fallback = 0
    board_cache_hits = 0
    board_cache_misses = 0
    gh_subprocess_calls = 0
