"""Live-snapshot metric helpers (issue #803), extracted from server.py.

Two pure helpers that the ``/api/sprints/{label}/live`` endpoint composes. They
live here, not in the ``server.py`` monolith, so the endpoint can gain the
Running-view metric strip without growing the guarded file (COMMANDER_GATE_MONOLITH).

* ``compute_levels(issues)`` — per dispatch-level progress for the pipeline
  board, extracted verbatim from the live endpoint.
* ``running_metrics(sprint_label, project)`` — agent_runs-derived metrics for the
  Running view's metric strip: fix-round count, literal token total, per-agent
  elapsed-time split, and (only when a price map is configured) an approximate
  token cost. Returns ``{}`` when no agent_runs rows exist for the sprint, so the
  ``/live`` contract is unchanged and the frontend hides the metric cards.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

# Path setup mirrors the routers/ modules so the sibling ``db`` / ``settings_repo``
# imports resolve whether the dashboard is launched from its dir or the repo root.
_DASHBOARD_ROOT = Path(__file__).resolve().parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _terminal(iss: dict) -> bool:
    """A ticket in a terminal state for level-completion purposes."""
    return iss.get("status") in ("done", "skipped") or iss.get("agent_status") == "failed"


def compute_levels(issues: list[dict]) -> list[dict]:
    """Per dispatch-level progress for the pipeline board (issue #739).

    Only meaningful when issues carry ``dispatch_level > 0``; single-level/serial
    runs report ``[]``. The first non-terminal level is the active one; earlier
    levels are complete and later ones are waiting.
    """
    levels_map: dict[int, list[dict]] = {}
    for iss in issues:
        lvl = iss.get("dispatch_level") or 0
        if lvl > 0:
            levels_map.setdefault(lvl, []).append(iss)

    sorted_levels = sorted(levels_map)
    current_level: Optional[int] = None
    for lvl in sorted_levels:
        if not all(_terminal(i) for i in levels_map[lvl]):
            current_level = lvl
            break

    levels_out: list[dict] = []
    for lvl in sorted_levels:
        group = levels_map[lvl]
        if all(_terminal(i) for i in group):
            level_state = "complete"
        elif current_level is not None and lvl == current_level:
            level_state = "active"
        else:
            level_state = "waiting"
        levels_out.append({
            "level":  lvl,
            "total":  len(group),
            "merged": sum(1 for i in group if i.get("status") == "done"),
            "state":  level_state,
        })
    return levels_out


def _blended_rate(project: Optional[str]) -> Optional[float]:
    """Blended $/token from the settings price_map, or ``None`` when unconfigured.

    Reuses the analytics cost helper so the live strip and the analytics page
    agree on the rate. Any failure (no settings, no price map, no matching model)
    yields ``None`` so the "Tokens ≈ $" card is simply hidden.
    """
    try:
        import settings_repo as _settings_repo  # sibling module (services/sprint_manager)
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


def running_metrics(sprint_label: str, project: Optional[str]) -> dict[str, Any]:
    """agent_runs-derived metrics for the Running-view metric strip (issue #803).

    Returns ``{}`` when no ``agent_runs`` rows exist for ``sprint_label`` (the
    /live contract is then unchanged and the frontend hides the agent_runs-only
    cards). When rows exist, returns:

      - ``fix_rounds``       — count of ``attempt_kind == 'fix_round'`` runs
      - ``token_total``      — literal SUM(total_tokens) (BA Path-A, no estimate)
      - ``agent_time_split`` — {"coder": secs, "tester": secs} from duration_seconds
      - ``token_cost_usd`` / ``usd_per_token`` — only when a price map is set
    """
    import db as _db  # sibling module

    rows: list = []
    try:
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            rows = conn.execute(
                "SELECT agent, total_tokens, duration_seconds, attempt_kind "
                "FROM agent_runs WHERE sprint_label = ?",
                (sprint_label,),
            ).fetchall()
    except Exception:
        rows = []

    if not rows:
        return {}

    fix_rounds = 0
    token_total = 0
    time_by_agent = {"coder": 0, "tester": 0}
    for r in rows:
        if (r["attempt_kind"] or "").lower() == "fix_round":
            fix_rounds += 1
        tok = r["total_tokens"]
        if tok:
            token_total += int(tok)
        dur = r["duration_seconds"]
        agent = (r["agent"] or "").lower()
        if dur and agent in time_by_agent:
            time_by_agent[agent] += int(dur)

    metrics: dict[str, Any] = {
        "agent_runs_present": True,
        "fix_rounds":         fix_rounds,
        "token_total":        token_total,
        "agent_time_split":   time_by_agent,
    }

    # Token cost is approximate and requires a configured price map; omit the
    # field entirely when absent so the frontend hides the "Tokens ≈ $" card.
    rate = _blended_rate(project)
    if rate is not None:
        metrics["usd_per_token"] = rate
        metrics["token_cost_usd"] = round(token_total * rate, 4)

    return metrics
