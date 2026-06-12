"""Service logic for the History run-stats block (issue #810).

Aggregates the durable ``agent_runs`` rows for a single finished sprint into the
shape the expanded History card renders: stat chips, a split-bar, and a
per-ticket gantt timeline. Read-only and local — no GitHub call — so the block
renders instantly when a card is expanded.

Everything is computed directly from the raw rows so the rendered numbers
reconcile with them (AC8/AC9). When a sprint has no ``agent_runs`` rows the
service returns ``{"has_runs": False, ...}`` and the frontend hides the
split-bar and gantt while still showing wall-time / token chips (AC7).

New endpoints belong in ``routers/``, never in ``server.py`` (COMMANDER_GATE_MONOLITH);
this is the sibling logic module for the route mounted on the History router.
"""
from __future__ import annotations

import sys as _sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# apps/dashboard is on sys.path so ``import db`` resolves (mirrors the other
# router service modules and the server bootstrap).
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

# The four agents the split-bar accounts for (issue #810 AC2). Estimator runs are
# deliberately excluded — they are sizing overhead, not sprint execution time.
SPLIT_AGENTS = ("coder", "tester", "documenter", "reviewer")

# Only these agents produce per-ticket gantt segments. documenter / reviewer run
# once per sprint, not per ticket, so they belong in the split-bar but not the
# gantt (AC3: purple coder segments, amber tester segments).
GANTT_AGENTS = ("coder", "tester")


def _db():
    """Deferred import of the db module (honours a patched DB_PATH at call time)."""
    import db  # noqa: PLC0415
    return db


def _parse(ts: str | None) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``), or None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _run_duration(row: dict) -> int:
    """Whole-second duration of a run: the stored value, else start→finish."""
    dur = row.get("duration_seconds")
    if dur is not None:
        try:
            return max(0, int(dur))
        except (ValueError, TypeError):
            pass
    s, f = _parse(row.get("started_at")), _parse(row.get("finished_at"))
    if s and f:
        return max(0, round((f - s).total_seconds()))
    return 0


def _largest_remainder(weights: dict[str, int]) -> dict[str, int]:
    """Distribute 100 across positive weights so the integer parts sum to 100."""
    total = sum(weights.values())
    if total <= 0:
        return {k: 0 for k in weights}
    raw = {k: (v * 100) / total for k, v in weights.items()}
    floors = {k: int(v) for k, v in raw.items()}
    remainder = 100 - sum(floors.values())
    # Hand the leftover points to the largest fractional parts, ties by name.
    order = sorted(raw, key=lambda k: (raw[k] - floors[k], weights[k]), reverse=True)
    for k in order[:remainder]:
        floors[k] += 1
    return floors


def _blended_rate(project: Optional[str]) -> Optional[float]:
    """Blended $/token from the settings price_map, or None when unconfigured.

    Mirrors ``live_metrics._blended_rate`` so the History block and the Running
    strip agree on the rate; any failure yields None and the cost chip is hidden.
    """
    try:
        import settings_repo as _settings_repo  # sibling (services/sprint_manager)
        stored = _settings_repo.get_setting("app_config", project=project)
    except Exception:
        stored = None
    price_map = stored.get("price_map") if isinstance(stored, dict) else None
    if not isinstance(price_map, dict):
        return None
    try:
        from routers.analytics import _blended_usd_per_token
        return _blended_usd_per_token(price_map)
    except Exception:
        return None


def sprint_run_stats(label: str, project: Optional[str] = None) -> dict[str, Any]:
    """Aggregate one sprint's ``agent_runs`` rows for the History run-stats block.

    Returns ``{"has_runs": False, ...}`` (with empty collections) when the sprint
    has no rows, so the caller can render wall-time / token chips from the
    history feed and hide the agent-derived split-bar and gantt (AC7).
    """
    try:
        rows = _db().agent_runs_for_sprint(label)
    except Exception:
        rows = []

    if not rows:
        return {"label": label, "has_runs": False, "split": [], "tickets": []}

    # Sprint start = earliest run start; all offsets are relative to it so the
    # gantt rows share one timeline and parallelism is visible across tickets.
    starts = [s for s in (_parse(r.get("started_at")) for r in rows) if s]
    sprint_start = min(starts) if starts else None

    agent_seconds = {a: 0 for a in SPLIT_AGENTS}
    total_tokens = 0
    fix_round_count = 0
    fix_round_tickets: list[int] = []
    latest_end_dt: Optional[datetime] = None
    # ticket_number -> list of gantt segments (coder/tester only)
    seg_by_ticket: dict[int, list[dict]] = {}

    for r in rows:
        agent = (r.get("agent") or "").lower()
        dur = _run_duration(r)
        tok = r.get("total_tokens")
        if tok:
            total_tokens += int(tok)
        if agent in agent_seconds:
            agent_seconds[agent] += dur

        started = _parse(r.get("started_at"))
        finished = _parse(r.get("finished_at"))
        end_dt = finished or (started + _dt_seconds(dur) if started else None)
        if end_dt and (latest_end_dt is None or end_dt > latest_end_dt):
            latest_end_dt = end_dt

        is_fix = (r.get("attempt_kind") or "").lower() == "fix_round"
        ticket = int(r.get("issue_number") or 0)
        if is_fix:
            fix_round_count += 1
            if ticket and ticket not in fix_round_tickets:
                fix_round_tickets.append(ticket)

        if agent in GANTT_AGENTS and started and sprint_start is not None:
            seg_by_ticket.setdefault(ticket, []).append({
                "agent": agent,
                "start": max(0, round((started - sprint_start).total_seconds())),
                "duration": dur,
                "fix_round": is_fix,
                "outcome": r.get("outcome"),
            })

    # Wall = full span of every run (covers sprint-level documenter/reviewer too).
    wall_seconds = 0
    if sprint_start is not None and latest_end_dt is not None:
        wall_seconds = max(0, round((latest_end_dt - sprint_start).total_seconds()))

    agent_total_seconds = sum(agent_seconds.values())
    parallel_saved_seconds = max(0, agent_total_seconds - wall_seconds)

    # Split-bar: integer percentages over the agents that actually ran, summing
    # to exactly 100 via largest-remainder rounding (AC2).
    nonzero = {a: agent_seconds[a] for a in SPLIT_AGENTS if agent_seconds[a] > 0}
    pcts = _largest_remainder(nonzero)
    split = [
        {"agent": a, "seconds": agent_seconds[a], "pct": pcts[a]}
        for a in SPLIT_AGENTS if a in nonzero
    ]

    # Per-ticket gantt rows, ordered along the timeline (earliest start first).
    tickets = []
    for ticket, segs in seg_by_ticket.items():
        segs.sort(key=lambda s: (s["start"], s["agent"]))
        row_start = min(s["start"] for s in segs)
        row_end = max(s["start"] + s["duration"] for s in segs)
        tickets.append({
            "ticket": ticket,
            "start": row_start,
            "end": row_end,
            "segments": segs,
        })
    tickets.sort(key=lambda t: (t["start"], t["ticket"]))

    # Slowest ticket = the longest wall span across its own segments.
    slowest_ticket = None
    if tickets:
        slow = max(tickets, key=lambda t: t["end"] - t["start"])
        slowest_ticket = {"ticket": slow["ticket"], "seconds": slow["end"] - slow["start"]}

    # Crash marker = the end offset of the last-reached ticket (the row that ends
    # latest). Emitted only for needs_rework sprints (AC4 / sprint-lifecycle P4);
    # clean runs omit the field so the frontend never paints a spurious ✕.
    lifecycle = None
    try:
        row = _db().get_sprint(label)
        if row:
            lifecycle = _db().canonical_lifecycle(row.get("state"))
    except Exception:
        pass
    crash = None
    if tickets and lifecycle == "needs_rework":
        last = max(tickets, key=lambda t: t["end"])
        crash = {"ticket": last["ticket"], "offset": last["end"]}

    result: dict[str, Any] = {
        "label": label,
        "has_runs": True,
        "wall_seconds": wall_seconds,
        "total_tokens": total_tokens,
        "agent_seconds": agent_seconds,
        "agent_total_seconds": agent_total_seconds,
        "parallel_saved_seconds": parallel_saved_seconds,
        "split": split,
        "fix_round_count": fix_round_count,
        "fix_round_tickets": fix_round_tickets,
        "slowest_ticket": slowest_ticket,
        "crash": crash,
        "tickets": tickets,
    }

    # Approximate $ only when a price map is configured; otherwise the cost chip
    # is hidden (the field is simply absent).
    rate = _blended_rate(project)
    if rate is not None:
        result["usd_per_token"] = rate
        result["token_cost_usd"] = round(total_tokens * rate, 4)

    return result


def _dt_seconds(seconds: int):
    """A timedelta of ``seconds`` (kept tiny so the import stays local)."""
    from datetime import timedelta
    return timedelta(seconds=seconds)
