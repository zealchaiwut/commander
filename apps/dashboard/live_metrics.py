"""Live-snapshot metric helpers (issue #803, #927), extracted from server.py.

Pure helpers that the ``/api/sprints/{label}/live`` endpoint composes. They
live here, not in the ``server.py`` monolith, so the endpoint can grow its
response without growing the guarded file (COMMANDER_GATE_MONOLITH).

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _inflight_seconds(started_at: Optional[str]) -> int:
    """Live elapsed seconds since ``started_at`` for an unfinished agent_runs row.

    agent_runs timestamps are naive UTC (``record_sprint_start`` writes
    ``utcnow().isoformat()``); compare against a naive UTC now. Returns 0 on any
    parse failure so a malformed timestamp never breaks the metric strip.
    """
    if not started_at:
        return 0
    try:
        ts = datetime.fromisoformat(started_at)
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
        delta = (datetime.utcnow() - ts).total_seconds()
        return int(delta) if delta > 0 else 0
    except (ValueError, TypeError):
        return 0

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


def _fetch_sprint_agent_run_rows(
    sprint_label: str, project: Optional[str] = None
) -> list[dict]:
    """All agent_runs rows for a sprint label, scoped by project (issue #1897).

    When *project* is given, rows are filtered to that project first. If no
    project-scoped rows exist, falls back to legacy blank/NULL-project rows so
    pre-migration sprints still render. Rows owned by a *different* project are
    never returned — same contract as db.agent_runs_for_sprint (#1881).
    """
    import db as _db  # sibling module

    _COLS = (
        "SELECT issue_number, agent, total_tokens, duration_seconds, "
        "attempt_kind, started_at, finished_at "
        "FROM agent_runs "
    )
    try:
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            if project:
                raw = conn.execute(
                    _COLS + "WHERE sprint_label = ? AND project = ?",
                    (sprint_label, project),
                ).fetchall()
                if not raw:
                    raw = conn.execute(
                        _COLS + "WHERE sprint_label = ? "
                        "AND (project = '' OR project IS NULL)",
                        (sprint_label,),
                    ).fetchall()
            else:
                raw = conn.execute(
                    _COLS + "WHERE sprint_label = ?",
                    (sprint_label,),
                ).fetchall()
            return [dict(r) for r in raw]
    except Exception:
        return []


def runs_by_issue(rows: list[dict]) -> dict[int, list[dict]]:
    """Group agent_runs rows by issue_number."""
    out: dict[int, list[dict]] = {}
    for r in rows:
        num = r.get("issue_number")
        if num is None:
            continue
        out.setdefault(int(num), []).append(r)
    return out


def _parse_ts_utc(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).rstrip("Z"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def issue_elapsed_secs(
    iss: dict,
    now_utc: datetime,
    runs_by_issue_map: dict[int, list[dict]],
) -> Optional[int]:
    """Cumulative wall time for one ticket across all coder/tester attempts.

    Prefers ``agent_runs`` (sums every dispatch including fix rounds). Falls
  back to coder_started_at → tester_finished_at when no runs are recorded.
    """
    num = iss.get("number")
    if num is None:
        return None

    runs = runs_by_issue_map.get(int(num)) or []
    if runs:
        total = 0
        counted = False
        for r in runs:
            dur = r.get("duration_seconds")
            if dur:
                total += int(dur)
                counted = True
            elif not r.get("finished_at"):
                inflight = _inflight_seconds(r.get("started_at"))
                if inflight > 0:
                    total += inflight
                counted = True
        if counted:
            return max(0, total)

    coder_start_dt = _parse_ts_utc(iss.get("coder_started_at"))
    if coder_start_dt is None:
        return None
    end_dt = _parse_ts_utc(iss.get("tester_finished_at")) or now_utc
    return max(0, int((end_dt - coder_start_dt).total_seconds()))


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
    rows = _fetch_sprint_agent_run_rows(sprint_label, project)

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
        agent = (r["agent"] or "").lower()
        if agent not in time_by_agent:
            continue
        dur = r["duration_seconds"]
        if dur:
            time_by_agent[agent] += int(dur)
        elif not r["finished_at"]:
            # In-flight run: duration_seconds is written only at finish, so an
            # unfinished row reads 0 and the Agent Time card sat at 0m0s for the
            # whole run. Count the live elapsed (now − started_at) so the split
            # ticks while the agent works.
            elapsed = _inflight_seconds(r["started_at"])
            if elapsed > 0:
                time_by_agent[agent] += elapsed

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


def pipeline_stage_from_status(raw_agent_status: str, derived_status: str, tac: int) -> str:
    """Two-queue lane assignment for the running pane (issue #927)."""
    if raw_agent_status in ("coder_dispatched", "coder_running"):
        return "coding"
    if raw_agent_status == "coder_done":
        return "awaiting_tester"
    if raw_agent_status in ("tester_dispatched", "tester_running"):
        return "testing"
    if derived_status in ("done", "skipped") or raw_agent_status in ("tester_done", "completed", "failed"):
        return "done"
    if tac > 0:
        return "rework"
    return "pending"


def lane_capacity(status_data: dict) -> dict:
    """Max concurrent slots per lane for the two-queue view (issue #927)."""
    return {
        "max_coder_slots":  int(status_data.get("max_coder_slots", 1)),
        "max_tester_slots": int(status_data.get("max_tester_slots", 1)),
    }
