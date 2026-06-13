"""Service logic for the activity router (extracted from server.py, issue #794).

The activity surfaces read agent/event history straight from SQLite via the
shared ``db`` module and resolve project slugs via the shared ``projects``
module — the same module objects the monolith uses, so test patches against
``server.projects_module`` / ``db`` still apply here.

Issue #902: the Activity API now unions the SQLite ``events`` table with
dispatch lifecycle events from ``events-<date>.jsonl`` files written by
``services/logging.py``. The reader-side union keeps the orchestrator
decoupled from the dashboard DB layer (Option B). Supports a ``category``
query param (agent|error|gate|sprint) that filters only the JSONL-sourced
lifecycle events.

Out of this wave (pinned to server.py by pre-existing tests the AC forbids
modifying): ``/api/logs/runs`` (test_419) and ``/api/logs/sync-github``
(test_630).
"""
from __future__ import annotations

import datetime
import glob
import json
import sys as _sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402
import projects as projects_module  # noqa: E402

# Mirrored from server.py. This endpoint was the only consumer of the constant,
# so it moves with the handler (the server.py copy is removed in the same PR).
_VALID_EVENT_SOURCES = {"agent", "dashboard", "github"}

# Valid category values for the dispatch lifecycle filter (issue #902).
_VALID_LIFECYCLE_CATEGORIES = {"agent", "error", "gate", "sprint"}

# Base directory for projects — mirrors server.py's _PROJECTS_BASE.
_PROJECTS_BASE = Path.home() / "dev"


def list_agents():
    return db.get_agents()


def list_events():
    return db.get_recent_events()


# ---------------------------------------------------------------------------
# JSONL lifecycle event reader (issue #902)
# ---------------------------------------------------------------------------

def _project_logs_dir(slug: str) -> Optional[Path]:
    """Return the .commander/logs/ directory for the given project slug, or None.

    Supports nested layout (~/dev/<slug>/.commander/logs/) and flat layout
    (same path — the .commander dir is always at ~/dev/<slug>/.commander).
    Also checks the repo root's own .commander/logs for the commander project.
    """
    candidates = [
        _PROJECTS_BASE / slug / ".commander" / "logs",
        _REPO_ROOT / ".commander" / "logs",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    # Return first candidate even if it doesn't exist yet — caller decides
    return candidates[0]


def _read_jsonl_lifecycle_events(
    logs_dir: Path,
    project_key: str,
    since: Optional[str] = None,
    until: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Read dispatch lifecycle events from events-<date>.jsonl files.

    Returns a list of dicts normalized to the same shape as SQLite events, with
    additional fields (category, issue_num, agent_role, sprint_label, run_id,
    attempt, exit_code, duration_s, gate, reason, stderr_tail, sidecar, error).
    Events are filtered by date range and category when provided.
    Never raises — returns [] on any IO or parse error.
    """
    results: list[dict] = []
    try:
        pattern = str(logs_dir / "events-*.jsonl")
        jsonl_files = sorted(glob.glob(pattern))
    except Exception:
        return results

    # Pre-compute date bounds for file-level pruning (avoid reading old files)
    since_date = since[:10] if since else None
    until_str = (until if "T" in until else f"{until}T23:59:59") if until else None

    for fpath in jsonl_files:
        # Prune by file date derived from filename: events-YYYY-MM-DD.jsonl
        try:
            fname = Path(fpath).name  # events-2026-06-13.jsonl
            file_date = fname[len("events-"):len("events-") + 10]  # YYYY-MM-DD
            if since_date and file_date < since_date:
                continue
            if until and file_date > until[:10]:
                continue
        except Exception:
            pass

        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Only lifecycle events have a "category" field
                    if "category" not in rec:
                        continue

                    ts = rec.get("timestamp", "")

                    # Date-range filters
                    if since and ts < since:
                        continue
                    if until_str and ts > until_str:
                        continue

                    # Category filter
                    if category is not None and rec.get("category") != category:
                        continue

                    # Project filter: if the event carries a project field, match it.
                    # Events written by sprint_manager carry project via log context.
                    evt_project = rec.get("project")
                    if evt_project is not None and evt_project != project_key:
                        continue

                    # Normalize to a common shape.  The "detail" sub-dict carries
                    # the lifecycle-specific fields so the Activity UI can render
                    # them alongside the existing SQLite-sourced events.
                    detail: dict = {}
                    for fld in (
                        "issue_num", "agent_role", "sprint_label", "run_id",
                        "attempt", "exit_code", "duration_s", "gate", "reason",
                        "stderr_tail", "sidecar", "error",
                    ):
                        val = rec.get(fld)
                        if val is not None:
                            detail[fld] = val

                    results.append({
                        "timestamp": ts,
                        "source": "agent",
                        "actor": rec.get("agent_role") or "sprint_manager",
                        "type": rec.get("name", ""),
                        "target": str(rec.get("issue_num", "")) if rec.get("issue_num") else "",
                        "action_id": rec.get("run_id"),
                        "detail": detail,
                        "category": rec.get("category"),
                        # Preserve top-level lifecycle fields for direct access
                        "issue_num": rec.get("issue_num"),
                        "agent_role": rec.get("agent_role"),
                        "sprint_label": rec.get("sprint_label"),
                        "run_id": rec.get("run_id"),
                        "attempt": rec.get("attempt"),
                        "exit_code": rec.get("exit_code"),
                        "duration_s": rec.get("duration_s"),
                        "gate": rec.get("gate"),
                        "reason": rec.get("reason"),
                        "stderr_tail": rec.get("stderr_tail"),
                        "sidecar": rec.get("sidecar"),
                        "error": rec.get("error"),
                        "_source": "jsonl",  # internal tag for dedup
                    })
        except Exception:
            continue

    return results


def get_project_events(
    slug: str,
    source: Optional[str] = None,
    target: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
    category: Optional[str] = None,
):
    """Return structured events for a project, newest-first.

    Filters: source (agent|dashboard|github), target (exact), since/until (ISO date),
    category (agent|error|gate|sprint — filters dispatch lifecycle events), limit.
    404 — unknown project slug.
    400 — invalid source or category value.

    When category is supplied, only JSONL lifecycle events matching that category
    are returned (the SQLite events table has no category column). When category
    is absent, SQLite events and JSONL lifecycle events are merged, deduped, and
    returned newest-first. Serving the merged feed makes zero GitHub calls.
    """
    # Validate source before any DB work
    if source is not None and source not in _VALID_EVENT_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source {source!r}. Must be one of: {', '.join(sorted(_VALID_EVENT_SOURCES))}",
        )

    if category is not None and category not in _VALID_LIFECYCLE_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category {category!r}. Must be one of: {', '.join(sorted(_VALID_LIFECYCLE_CATEGORIES))}",
        )

    # Resolve slug → project name stored in events table
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")

    # The events table stores project as the full repo path (owner/repo)
    project_key = matched["repo"]
    project_slug = project_key.split("/")[-1]

    # ------------------------------------------------------------------
    # 1. Read JSONL lifecycle events (issue #902)
    # ------------------------------------------------------------------
    logs_dir = _project_logs_dir(project_slug)
    jsonl_events = _read_jsonl_lifecycle_events(
        logs_dir, project_key, since=since, until=until, category=category,
    )

    # When the caller requests a specific category, return only JSONL events —
    # the SQLite events table has no category column so there's nothing to union.
    if category is not None:
        jsonl_events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return jsonl_events[:limit]

    # ------------------------------------------------------------------
    # 2. Read SQLite events (existing path, unchanged)
    # ------------------------------------------------------------------
    query = "SELECT timestamp, source, actor, type, target, action_id, detail FROM events WHERE project = ?"
    params: list = [project_key]

    if source is not None:
        query += " AND source = ?"
        params.append(source)
    if target is not None:
        query += " AND target = ?"
        params.append(target)
    if since is not None:
        query += " AND timestamp >= ?"
        params.append(since)
    if until is not None:
        # include the full day by appending T23:59:59 when only a date is given
        until_bound = until if "T" in until else f"{until}T23:59:59"
        query += " AND timestamp <= ?"
        params.append(until_bound)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    db_result = []
    for row in rows:
        d = dict(row)
        try:
            d["detail"] = json.loads(d["detail"])
        except (TypeError, ValueError):
            pass
        d["_source"] = "db"
        db_result.append(d)

    # issue #764: enrich agent_finished rows with the durable duration_seconds
    # from agent_runs so the Activity tab can show a Duration column. Matched by
    # issue number + agent role; falls back to the duration already carried in
    # the event detail when no agent_runs row is found.
    _agent_finished = [
        d for d in db_result
        if d.get("type") == "agent_finished" and isinstance(d.get("detail"), dict)
    ]
    if _agent_finished:
        _issue_nums = {
            d["detail"].get("issue_num")
            for d in _agent_finished
            if d["detail"].get("issue_num") is not None
        }
        _runs_by_key: dict = {}
        try:
            for _num in _issue_nums:
                for _r in db.agent_runs_for_issue(int(_num)):
                    if _r.get("duration_seconds") is None:
                        continue
                    _runs_by_key[(int(_num), str(_r.get("agent", "")).lower())] = _r["duration_seconds"]
        except Exception:
            _runs_by_key = {}
        for d in _agent_finished:
            det = d["detail"]
            num = det.get("issue_num")
            role = str(det.get("role", "")).lower()
            dur = None
            if num is not None:
                dur = _runs_by_key.get((int(num), role))
            if dur is None:
                dur = det.get("duration")
            if dur is not None:
                d["duration_seconds"] = dur

    # ------------------------------------------------------------------
    # 3. Union, deduplicate, and sort (issue #902)
    # ------------------------------------------------------------------
    # Dedup key: (timestamp, type) — JSONL events have no DB id and may share
    # a timestamp with a SQLite event of a different type. We track which
    # (timestamp, type) pairs we've already added and skip duplicates.
    # DB events take precedence (they're authoritative for dashboard events).
    seen: set = set()
    merged: list[dict] = []

    for ev in db_result:
        key = (ev.get("timestamp", ""), ev.get("type", ""))
        seen.add(key)
        merged.append(ev)

    for ev in jsonl_events:
        key = (ev.get("timestamp", ""), ev.get("type", ""))
        if key not in seen:
            seen.add(key)
            merged.append(ev)

    # Strip internal _source tag before returning
    for ev in merged:
        ev.pop("_source", None)

    merged.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return merged[:limit]
