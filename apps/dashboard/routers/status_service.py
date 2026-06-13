"""Local-only status snapshot for the monitoring/deck endpoint (GET /api/status).

A domain-grouped view of commander's current state, built from LOCAL sources
only — the SQLite mirror, the in-memory sprint-status dict, and on-disk sprint
state/PID files. It makes **zero GitHub API calls** because it is polled every
~10s by a Stream Deck (and, later, a monitoring board).

Design rules (issue: generalized /api/status):
- General resource shape, not a deck payload — no button URLs / deep links here;
  the client builds its own jump targets.
- Never raises: any missing source degrades to null/0 with the rest of the
  payload intact. Fast (<50ms), no network.
- github_* come from cached rate-limit telemetry IF it exists, else null + TODO
  (never call GitHub to fill them).

The route lives in ``status.py``; this is the sibling logic module. New endpoints
belong in ``routers/``, never in ``server.py`` (COMMANDER_GATE_MONOLITH).
"""
from __future__ import annotations

import json
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# apps/dashboard is on sys.path so ``import db`` / ``import server`` resolve
# (mirrors the other router service modules and the server bootstrap).
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

SCHEMA_VERSION = 1

# Event types that count toward errors_today (written to the `events` table by
# the sprint manager / dashboard on failure).
_ERROR_EVENT_TYPES = ("ticket_failed", "sprint_failed")

# Issue statuses that count as completed for a sprint's done/total progress.
_DONE_STATUSES = ("done",)


def _server():
    """Deferred import of the server module (keeps import order / patchability)."""
    import server as srv  # noqa: PLC0415
    return srv


def _db():
    """Deferred import of the db module (honours a patched DB_PATH at call time)."""
    import db  # noqa: PLC0415
    return db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _project_display(repo: str) -> str:
    return repo.split("/")[-1] if repo and "/" in repo else (repo or "")


def _parse_event_ts(raw: str) -> Optional[datetime]:
    """Parse an `events.timestamp` value (naive UTC, '%Y-%m-%dT%H:%M:%S')."""
    if not raw:
        return None
    try:
        s = raw.replace("Z", "")
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _today_start_utc_str() -> str:
    """UTC string (events-table format) for local midnight today.

    errors_today is the operator's day; `events.timestamp` is UTC, so convert the
    local midnight to UTC and compare lexicographically (ISO strings sort
    chronologically when the format matches)."""
    local_now = datetime.now().astimezone()
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


# ── sprints ───────────────────────────────────────────────────────────────────

def _running_by_repo(srv) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        for r in srv._all_sprints_running():
            out[r.get("project")] = r
    except Exception:
        pass
    return out


def _running_sprint_entry(srv, repo: str, running: dict) -> dict:
    label = running.get("sprint_label")
    status = {}
    try:
        status = srv._sprint_statuses.get((repo, label), {}) or {}
    except Exception:
        status = {}
    issues = status.get("issues") or []
    done = sum(1 for i in issues if i.get("status") in _DONE_STATUSES)
    return {"project": _project_display(repo), "state": "running",
            "label": label, "done": done, "total": len(issues)}


def _latest_sprint_entry(srv, repo: str) -> Optional[dict]:
    """Most-recent on-disk sprint outcome for an idle project (no running sprint).

    Mirrors _health_collect_recent_dispatches: failed if any ticket failed/
    skipped, completed if all done, else idle. Returns None when no state file."""
    try:
        root = srv._project_root_path(repo)
        sdir = srv._commander_dir(root) / "sprints"
    except Exception:
        return None
    if not sdir.exists():
        return None
    best = None
    best_mtime = -1.0
    try:
        for f in sdir.glob("*-state.json"):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if mtime > best_mtime:
                best_mtime = mtime
                best = f
    except Exception:
        return None
    if best is None:
        return None
    try:
        data = json.loads(best.read_text(encoding="utf-8"))
    except Exception:
        return None
    label = best.name.removesuffix("-state.json")
    issues = data.get("issues") or []
    done = sum(1 for i in issues if i.get("status") in _DONE_STATUSES)
    has_failed = any(
        i.get("agent_status") == "failed" or i.get("failure_reason") or i.get("status") == "skipped"
        for i in issues
    )
    all_done = bool(issues) and all(i.get("status") == "done" for i in issues)
    state = "failed" if has_failed else ("completed" if all_done else "idle")
    return {"project": _project_display(repo), "state": state,
            "label": label, "done": done, "total": len(issues)}


def sprints() -> list[dict]:
    """One entry per known project: running sprint if any, else most-recent
    outcome, else idle. Local only."""
    srv = _server()
    try:
        projects = srv.projects_module.load_projects()
    except Exception:
        projects = []
    running = _running_by_repo(srv)
    out: list[dict] = []
    for proj in projects:
        repo = proj.get("repo")
        if not repo:
            continue
        try:
            if repo in running:
                out.append(_running_sprint_entry(srv, repo, running[repo]))
            else:
                out.append(_latest_sprint_entry(srv, repo)
                           or {"project": _project_display(repo), "state": "idle",
                               "label": None, "done": 0, "total": 0})
        except Exception:
            out.append({"project": _project_display(repo), "state": "idle",
                        "label": None, "done": 0, "total": 0})
    return out


def _active_project() -> Optional[str]:
    """The project with a running sprint (first), else None."""
    srv = _server()
    try:
        running = srv._all_sprints_running()
        if running:
            return _project_display(running[0].get("project"))
    except Exception:
        pass
    return None


# ── health ────────────────────────────────────────────────────────────────────

def health() -> dict:
    out = {
        "watchdog_healthy": None,
        "heartbeat_age_s": None,
        # TODO: github_* require passive rate-limit telemetry captured from gh
        # calls that already happen elsewhere. There is no cached source yet and
        # this endpoint must never call GitHub, so they stay null until wired.
        "github_remaining_pct": None,
        "github_reset_in_s": None,
    }
    # heartbeat = age of the newest local activity event (zero-network proxy for
    # "is anything alive").
    try:
        db = _db()
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT timestamp FROM events ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        if row and row[0]:
            ts = _parse_event_ts(row[0])
            if ts is not None:
                out["heartbeat_age_s"] = max(0, int((_now() - ts).total_seconds()))
    except Exception:
        pass
    # watchdog_healthy = no orphaned sprint PIDs (local PID-file check).
    try:
        orphans = _server()._health_collect_orphan_pids()
        if isinstance(orphans, dict) and "count" in orphans:
            out["watchdog_healthy"] = orphans["count"] == 0
    except Exception:
        pass
    return out


# ── queue ─────────────────────────────────────────────────────────────────────

def queue() -> dict:
    out = {"uat_pending": 0, "backlog": 0, "errors_today": 0}
    srv = _server()
    db = _db()
    try:
        import github_client as gc  # noqa: PLC0415 — classify_issue is pure (no network)
    except Exception:
        gc = None
    # uat_pending / backlog from the local issues mirror, summed across projects.
    try:
        projects = srv.projects_module.load_projects()
    except Exception:
        projects = []
    if gc is not None:
        for proj in projects:
            repo = proj.get("repo")
            if not repo:
                continue
            try:
                issues = db.get_mirrored_issues(repo, state="open")
            except Exception:
                continue
            for iss in issues:
                try:
                    cls = gc.classify_issue(iss)
                except Exception:
                    continue
                if cls == "uat":
                    out["uat_pending"] += 1
                elif cls == "backlog":
                    out["backlog"] += 1
    # errors_today from today's failure events (local events table).
    try:
        since = _today_start_utc_str()
        placeholders = ",".join("?" for _ in _ERROR_EVENT_TYPES)
        with db.get_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM events "
                f"WHERE type IN ({placeholders}) AND timestamp >= ?",
                (*_ERROR_EVENT_TYPES, since),
            ).fetchone()
        if row and row[0] is not None:
            out["errors_today"] = int(row[0])
    except Exception:
        pass
    return out


# ── cost ──────────────────────────────────────────────────────────────────────

def cost() -> dict:
    # TODO: no local source for Claude subscription-window usage yet. Clients
    # treat null as "hide". Wire a real source (e.g. /api/plan-usage estimator)
    # later without changing this contract.
    return {"claude_window_pct": None}


# ── top-level snapshot ────────────────────────────────────────────────────────

def build_status() -> dict[str, Any]:
    """Full domain-grouped snapshot. Never raises — each domain degrades on its
    own; a catastrophic failure still returns a well-formed, mostly-null body."""
    snap: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "sprints": [],
        "active_project": None,
        "health": {"watchdog_healthy": None, "heartbeat_age_s": None,
                   "github_remaining_pct": None, "github_reset_in_s": None},
        "queue": {"uat_pending": 0, "backlog": 0, "errors_today": 0},
        "cost": {"claude_window_pct": None},
    }
    try:
        snap["sprints"] = sprints()
    except Exception:
        pass
    try:
        snap["active_project"] = _active_project()
    except Exception:
        pass
    try:
        snap["health"] = health()
    except Exception:
        pass
    try:
        snap["queue"] = queue()
    except Exception:
        pass
    try:
        snap["cost"] = cost()
    except Exception:
        pass
    return snap
