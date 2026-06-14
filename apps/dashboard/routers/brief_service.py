"""Service logic for the brief assembly API (issue #839).

Assembles a structured, deterministic brief payload from the durable SQLite
tables — ``sprints`` (lifecycle), ``sprint_ticket_order``, the ``issues``
mirror, ``agent_runs`` and ``project_events``. **No LLM and no network call is
made** (AC5): every field comes from a local DB read. This is the data layer the
brief UI views render from; summarization / AI commentary is explicitly out of
scope.

Two public entry points:

* :func:`build_project_brief` — one project's brief (``shipped``,
  ``in_progress``, ``up_next``, ``blocked``, ``kpis``, ``recent_activity``).
* :func:`build_home_brief` — the home roll-up (``global_kpis``, ``decisions``,
  and every project's full brief under ``projects``).

The ``date`` window scopes all sprint and event queries to a single day
(``[date 00:00:00, date 23:59:59]``); omitting it defaults to today. A project
with no activity in the window returns empty sections, never a 4xx/5xx.

Both endpoints depend on the Logs feature's ``project_events`` table; when it is
missing :func:`ensure_dependencies` raises a clear 503 (AC14).
"""
from __future__ import annotations

import json
import sys as _sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

# apps/dashboard is on sys.path so ``import db`` / ``import projects`` resolve.
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

# services/sprint_manager holds the canonical size→minutes map (issue #766) —
# the single source for the "waiting on you" estimated-hours figure.
_SPRINT_MANAGER_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
if str(_SPRINT_MANAGER_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_SPRINT_MANAGER_ROOT))

# How many recent events to include in recent_activity (no pagination — AC scope).
RECENT_ACTIVITY_LIMIT = 10

# Max lines in the "Suggested Next" brief section (suggestions + look-ahead, issue #884).
SUGGESTED_NEXT_MAX = 5

# Label vocabularies (GitHub labels are the source of truth for ticket state).
_DONE_LABELS = {"done", "uat"}
_SKIPPED_LABELS = {"skipped", "wontfix"}
_REWORK_LABELS = {"rework", "returned-from-qa", "returned", "blocked"}
_FAILED_LABELS = {"failed", "sit-failed"}
_NEEDS_YOU_LABELS = {"uat"}
_SHIPPED_STATES = {"completed"}

# Planner state (issue #864).
# A sprint is "waiting on you" — pending human sign-off — while it sits in the
# ``ready_to_merge`` lifecycle state: finished, awaiting the human merge action.
_SIGNOFF_STATES = {"ready_to_merge"}
# Terminal lifecycle states a "ran overnight" sprint can land in, mapped to a
# human-facing outcome. (``partial_finished`` is derived at read time and never
# stored, so it is unreachable from the sprints table today — kept for forward
# compatibility.) Keyed by the *canonical* lifecycle value.
_OVERNIGHT_OUTCOMES = {
    "completed": "success",
    "ready_to_merge": "success",
    "partial_finished": "partial",
    "needs_rework": "failure",
    "failed": "failure",
    "cancelled": "failure",
}


def _size_to_minutes() -> dict[str, int]:
    """Canonical size→minutes map (issue #766); best-effort with a safe default."""
    try:
        from sizing import SIZE_TO_MINUTES  # noqa: PLC0415
        return SIZE_TO_MINUTES
    except Exception:
        return {"S": 5, "M": 15, "L": 30, "XL": 90}


def _db():
    """Deferred import of the db module (honours a patched DB_PATH at call time)."""
    import db  # noqa: PLC0415
    return db


def _load_projects() -> list[dict]:
    """Return the tracked-project list. Seam — patched in tests."""
    import projects  # noqa: PLC0415
    return projects.load_projects()


def _sprint_goal(project_key: str, slug: str, label: str) -> str:
    """Return the persisted sprint goal text, or "" when none. Seam — patched in tests.

    Reads the on-disk ``.commander/sprints/<label>-goal.txt`` via the server's
    project-root helpers. Best-effort: any failure yields an empty goal (which
    renders the sprint as not-ready) rather than raising.
    """
    try:
        import server  # noqa: PLC0415 — late import; server mounts this package
        project_root = server._project_root_path(slug)
        goal_path = server._sprint_goal_path(project_root, label)
        if goal_path.exists():
            return goal_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


# ── window + time helpers ─────────────────────────────────────────────────────

def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _window(date: Optional[str]) -> tuple[str, str, str]:
    """Return (date, start, end) ISO bounds for a single-day window."""
    d = (date or "").strip() or _today()
    return d, f"{d}T00:00:00", f"{d}T23:59:59"


def _seconds_between(start: Optional[str], end: Optional[str]) -> Optional[int]:
    # Mixed sources: Z/offset timestamps parse aware, bare naive-UTC strings
    # stay naive — normalize both or the subtraction raises TypeError.
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if s.tzinfo is None:
        s = s.replace(tzinfo=timezone.utc)
    if e.tzinfo is None:
        e = e.replace(tzinfo=timezone.utc)
    return round((e - s).total_seconds())


def _elapsed_since(start: Optional[str]) -> Optional[int]:
    if not start:
        return None
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if s.tzinfo is None:
        s = s.replace(tzinfo=timezone.utc)
    return max(0, round((datetime.now(timezone.utc) - s).total_seconds()))


# ── dependency gate (AC14) ────────────────────────────────────────────────────

def ensure_dependencies() -> None:
    """Raise 503 if the activity feed's ``events`` table is missing.

    The brief reads the ``events`` table (the Logs timeline's source);
    ``project_events`` was the originally-assumed table, but nothing in
    production writes it.
    """
    db = _db()
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='events'"
            ).fetchone()
    except Exception:
        row = None
    if row is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "events table unavailable — the Logs feature must be "
                "initialized before the brief API can serve data."
            ),
        )


# ── project resolution ────────────────────────────────────────────────────────

def _resolve_project_key(slug: str) -> str:
    """Map a slug to the project key stored in the tables (full repo path).

    Falls back to the slug itself when the project is not in the tracked list,
    so an unknown slug still yields a valid (empty) brief rather than a 404.
    """
    try:
        for p in _load_projects():
            repo = p.get("repo", "")
            if repo == slug or repo.split("/")[-1] == slug:
                return repo
    except Exception:
        pass
    return slug


def _project_match_values(project_key: str, slug: str) -> set[str]:
    """The set of `project` column values a row may legitimately carry."""
    return {project_key, slug, ""}


# ── ticket classification ─────────────────────────────────────────────────────

def _label_names(issue: dict) -> set[str]:
    names: set[str] = set()
    for lbl in issue.get("labels", []) or []:
        if isinstance(lbl, dict):
            n = lbl.get("name")
        else:
            n = lbl
        if n:
            names.add(str(n).lower())
    return names


def _disposition(issue: dict) -> str:
    """Classify a mirrored issue into done|skipped|rework|failed|other."""
    names = _label_names(issue)
    if names & _SKIPPED_LABELS:
        return "skipped"
    if names & _FAILED_LABELS:
        return "failed"
    if names & _REWORK_LABELS:
        return "rework"
    if names & _DONE_LABELS:
        return "done"
    return "other"


# ── data readers ──────────────────────────────────────────────────────────────

def _sprints_in_state(db, states: set[str], project_key: str, slug: str) -> list[dict]:
    match = _project_match_values(project_key, slug)
    rows = [r for r in db.list_sprints_lifecycle()
            if r.get("state") in states and (r.get("project") or "") in match]
    return rows


def _sprint_ticket_dispositions(db, project_key: str, label: str) -> dict[int, dict]:
    """Map issue_number -> {title, disposition} for a sprint's ordered tickets."""
    out: dict[int, dict] = {}
    for num in db.get_sprint_ticket_order(label):
        issue = db.get_mirrored_issue(project_key, num)
        if issue is None:
            out[num] = {"title": "", "disposition": "other"}
            continue
        out[num] = {
            "title": issue.get("title", ""),
            "disposition": _disposition(issue),
        }
    return out


def _sprint_finish_meta(db, project_key: str, label: str) -> dict:
    """Look up pr_number / summary_issue_number from the activity feed for a sprint.

    Sprint-finish metadata is emitted into the ``events`` table (the same feed
    the Logs timeline reads), keyed by the sprint label in the event ``target``.
    The original implementation read ``project_events``, a table nothing in
    production writes, so these fields were always None.
    """
    out: dict = {"pr_number": None, "summary_issue_number": None}
    try:
        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT detail FROM events WHERE project = ? AND target = ? "
                "ORDER BY timestamp DESC LIMIT 50",
                (project_key, label),
            ).fetchall()]
    except Exception:
        return out
    for ev in rows:
        data = ev.get("detail")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                data = None
        if not isinstance(data, dict):
            continue
        if out["pr_number"] is None and data.get("pr_number") is not None:
            out["pr_number"] = data["pr_number"]
        if out["summary_issue_number"] is None and data.get("summary_issue_number") is not None:
            out["summary_issue_number"] = data["summary_issue_number"]
    return out


# ── section builders ──────────────────────────────────────────────────────────

def _build_shipped(db, project_key: str, slug: str, start: str, end: str) -> list[dict]:
    out: list[dict] = []
    for row in _sprints_in_state(db, _SHIPPED_STATES, project_key, slug):
        ended = row.get("ended_at") or ""
        if not (start <= ended <= end):
            continue
        label = row.get("label")
        disp = _sprint_ticket_dispositions(db, project_key, label)
        features = [d["title"] for d in disp.values() if d["disposition"] == "done"]
        done = sum(1 for d in disp.values() if d["disposition"] == "done")
        skipped = sum(1 for d in disp.values() if d["disposition"] == "skipped")
        meta = _sprint_finish_meta(db, project_key, label)
        out.append({
            "label": label,
            "goal": _sprint_goal(project_key, slug, label),
            "features": features,
            "done": done,
            "skipped": skipped,
            "duration": _seconds_between(row.get("started_at"), row.get("ended_at")),
            "pr_number": meta["pr_number"],
            "summary_issue_number": meta["summary_issue_number"],
        })
    out.sort(key=lambda s: s["label"])
    return out


def _build_in_progress(db, project_key: str, slug: str) -> Optional[dict]:
    running = _sprints_in_state(db, {"running"}, project_key, slug)
    if not running:
        return None
    # Most recently started running sprint wins.
    running.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    row = running[0]
    label = row.get("label")
    disp = _sprint_ticket_dispositions(db, project_key, label)
    total = len(disp)
    done = sum(1 for d in disp.values() if d["disposition"] == "done")
    percent = round(done / total * 100) if total else 0

    current_ticket = None
    active_agent = None
    runs = db.agent_runs_for_sprint(label)
    if runs:
        # Prefer an open run (finished_at is NULL); else the most recent run.
        open_runs = [r for r in runs if not r.get("finished_at")]
        chosen = (open_runs or runs)[-1]
        issue_num = chosen.get("issue_number")
        active_agent = chosen.get("agent")
        title = ""
        issue = db.get_mirrored_issue(project_key, issue_num) if issue_num is not None else None
        if issue:
            title = issue.get("title", "")
        current_ticket = {"issue_number": issue_num, "title": title}

    return {
        "sprint_label": label,
        "current_ticket": current_ticket,
        "active_agent": active_agent,
        "progress": {"done": done, "total": total, "percent": percent},
        "elapsed": _elapsed_since(row.get("started_at")),
    }


def _build_up_next(db, project_key: str, slug: str) -> Optional[dict]:
    planning = _sprints_in_state(db, {"planning"}, project_key, slug)
    if not planning:
        return None
    # Newest planned sprint is the one coming up next.
    planning.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    row = planning[0]
    label = row.get("label")
    ticket_count = len(db.get_sprint_ticket_order(label))
    goal = _sprint_goal(project_key, slug, label)
    return {
        "label": label,
        "ticket_count": ticket_count,
        "ready": bool(goal.strip()),
    }


def _build_blocked(db, project_key: str) -> list[dict]:
    out: list[dict] = []
    for issue in db.get_mirrored_issues(project_key):
        disp = _disposition(issue)
        if disp in ("rework", "failed"):
            out.append({
                "issue_number": issue.get("number"),
                "title": issue.get("title", ""),
                "type": disp,
            })
    out.sort(key=lambda b: b["issue_number"] or 0)
    return out


def _needs_you_count(db, project_key: str) -> int:
    # Open issues only — the mirror holds full history, and shipped tickets
    # keep their `uat` label at close (counting those showed 817 here).
    count = 0
    for issue in db.get_mirrored_issues(project_key, state="open"):
        if _label_names(issue) & _NEEDS_YOU_LABELS:
            count += 1
    return count


def _build_recent_activity(db, project_key: str, start: str, end: str) -> list[dict]:
    """Latest events for the brief's Recent Activity timeline.

    Reads the `events` table — the same source as the Logs activity timeline.
    (The original implementation read `project_events`, a table nothing in the
    dashboard writes, so the brief always showed "No recent activity".)
    Output shape per the mock: HH:MM time, source badge, short message.
    """
    query = (
        "SELECT timestamp, source, type, target, detail FROM events "
        "WHERE project = ? AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT ?"
    )
    try:
        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(
                query, (project_key, start, end, RECENT_ACTIVITY_LIMIT)
            ).fetchall()]
    except Exception:
        return []

    out: list[dict] = []
    for ev in rows:
        detail = ev.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except (ValueError, TypeError):
                detail = None

        message = ""
        if isinstance(detail, dict):
            message = detail.get("message") or ""
        if not message:
            label = str(ev.get("type") or "event").replace("_", " ")
            target = str(ev.get("target") or "")
            # Targets are sometimes opaque ids (UUID action ids) — only show
            # human-shaped ones (#123, sprint-61, short branch-ish names).
            if target and len(target) <= 24 and "-4" not in target:
                message = f"{label} · {target}"
            else:
                message = label

        ts = str(ev.get("timestamp") or "")
        time_short = ts[11:16] if len(ts) >= 16 else ts  # HH:MM per the mock
        out.append({
            "time": time_short,
            "source": ev.get("source"),
            "message": message,
        })
    return out


# ── planner-state section builders (issue #864) ───────────────────────────────

def _ticket_size_minutes(issue: dict) -> int:
    """Minutes for a ticket, read from its ``size-*`` label (canonical map)."""
    sizes = _size_to_minutes()
    for name in _label_names(issue):
        if name.startswith("size-"):
            letter = name.split("-", 1)[1].upper()
            if letter in sizes:
                return sizes[letter]
    return 0


def _build_waiting_on_you(db, project_key: str, slug: str) -> list[dict]:
    """Sprints awaiting human sign-off (``ready_to_merge``), with size totals.

    Each entry carries the sprint's ticket count and an estimated-hours figure
    summed from the per-ticket ``size-*`` labels (issue #864 AC2).
    """
    out: list[dict] = []
    for row in _sprints_in_state(db, _SIGNOFF_STATES, project_key, slug):
        label = row.get("label")
        nums = db.get_sprint_ticket_order(label)
        minutes = 0
        for num in nums:
            issue = db.get_mirrored_issue(project_key, num)
            if issue:
                minutes += _ticket_size_minutes(issue)
        out.append({
            "label": label,
            "ticket_count": len(nums),
            "estimated_hours": round(minutes / 60, 2),
        })
    out.sort(key=lambda s: s["label"] or "")
    return out


def _build_ran_overnight(db, project_key: str, slug: str,
                         start: str, end: str) -> list[dict]:
    """Sprints that finished within the brief window — "ran overnight" (AC1).

    Outcome is derived from the canonical lifecycle state; each entry links to
    its sprint brief via the summary issue and/or the on-disk brief path (AC7).
    """
    match = _project_match_values(project_key, slug)
    out: list[dict] = []
    for row in db.list_sprints_lifecycle():
        if (row.get("project") or "") not in match:
            continue
        ended = row.get("ended_at") or ""
        if not (start <= ended <= end):
            continue
        outcome = _OVERNIGHT_OUTCOMES.get(db.canonical_lifecycle(row.get("state")))
        if outcome is None:
            continue
        label = row.get("label")
        meta = _sprint_finish_meta(db, project_key, label)
        out.append({
            "label": label,
            "outcome": outcome,
            "summary_issue_number": meta["summary_issue_number"],
            # brief_generator (#860) writes docs/sprint-briefs/<label>.md, where
            # the label already carries the "sprint-" prefix.
            "brief_path": f"docs/sprint-briefs/{label}.md" if label else None,
        })
    out.sort(key=lambda s: s["label"] or "")
    return out


# ── advisor suggestions section (issue #884) ─────────────────────────────────

def _build_suggested_next(db, project_key: str, slug: str) -> list[dict]:
    """Read advisor suggestions and first look-ahead entry for the brief.

    Returns at most SUGGESTED_NEXT_MAX items total: suggestions first, then
    the first look-ahead entry as the final item. Returns [] when both sources
    are empty so the "Suggested Next" section is omitted from the rendered brief.
    Gracefully returns [] if the advisor tables are absent (before issues #881/#883
    are merged).
    """
    try:
        with db.get_conn() as conn:
            sug_rows = conn.execute(
                "SELECT pitch FROM advisor_suggestions "
                "WHERE project = ? ORDER BY id ASC",
                (project_key,),
            ).fetchall()
    except Exception:
        sug_rows = []

    try:
        with db.get_conn() as conn:
            la_rows = conn.execute(
                "SELECT entry FROM advisor_look_ahead "
                "WHERE project = ? ORDER BY position ASC LIMIT 1",
                (project_key,),
            ).fetchall()
    except Exception:
        la_rows = []

    look_ahead_entry: Optional[str] = la_rows[0]["entry"] if la_rows else None
    max_sugs = (SUGGESTED_NEXT_MAX - 1) if look_ahead_entry else SUGGESTED_NEXT_MAX
    pitches = [r["pitch"] for r in sug_rows][:max_sugs]

    items: list[dict] = [
        {"text": p, "type": "suggestion", "slug": slug} for p in pitches
    ]
    if look_ahead_entry:
        items.append({"text": look_ahead_entry, "type": "look_ahead", "slug": slug})
    return items


# ── public API ────────────────────────────────────────────────────────────────

def build_project_brief(slug: str, date: Optional[str] = None,
                        window: Optional[tuple[str, str]] = None) -> dict:
    """Assemble one project's brief payload for the given (or today's) window.

    ``date`` names the brief day (and is echoed in the result). By default the
    sprint/event queries are scoped to that calendar day; pass ``window`` —
    ``(start_iso, end_iso)`` — to scope them to an explicit window instead (e.g.
    the 6 AM-anchored 24h window used by the daily-artifact store, issue #841).
    """
    ensure_dependencies()
    db = _db()
    d, start, end = _window(date)
    if window is not None:
        start, end = window
    project_key = _resolve_project_key(slug)

    shipped = _build_shipped(db, project_key, slug, start, end)
    in_progress = _build_in_progress(db, project_key, slug)
    up_next = _build_up_next(db, project_key, slug)
    blocked = _build_blocked(db, project_key)
    recent_activity = _build_recent_activity(db, project_key, start, end)
    ran_overnight = _build_ran_overnight(db, project_key, slug, start, end)
    waiting_on_you = _build_waiting_on_you(db, project_key, slug)
    suggested_next = _build_suggested_next(db, project_key, slug)

    kpis = {
        "sprints_shipped": len(shipped),
        "tickets_done": sum(s["done"] for s in shipped),
        "in_progress": in_progress is not None,
        "in_progress_percent": in_progress["progress"]["percent"] if in_progress else 0,
        "needs_you": _needs_you_count(db, project_key),
    }

    return {
        "project": slug,
        "date": d,
        "shipped": shipped,
        "in_progress": in_progress,
        "up_next": up_next,
        "blocked": blocked,
        "kpis": kpis,
        "recent_activity": recent_activity,
        "ran_overnight": ran_overnight,
        "waiting_on_you": waiting_on_you,
        "suggested_next": suggested_next,
    }


def _decisions_for_project(slug: str, brief: dict) -> list[dict]:
    """Derive the actionable decisions for one project's brief."""
    decisions: list[dict] = []
    up_next = brief.get("up_next")
    if up_next:
        if up_next["ticket_count"] == 0:
            decisions.append({
                "project": slug,
                "type": "empty_sprint",
                "label": up_next["label"],
                "suggested_action": f"Add tickets to {up_next['label']} or delete it",
            })
        elif up_next["ready"]:
            decisions.append({
                "project": slug,
                "type": "run_ready",
                "label": up_next["label"],
                "suggested_action": f"Run sprint {up_next['label']}",
            })
    for b in brief.get("blocked", []):
        if b["type"] == "rework":
            decisions.append({
                "project": slug,
                "type": "rework",
                "label": f"#{b['issue_number']}",
                "suggested_action": f"Review reworked ticket #{b['issue_number']}",
            })
    for s in brief.get("shipped", []):
        if s.get("pr_number") is not None:
            decisions.append({
                "project": slug,
                "type": "branch_cleanup",
                "label": s["label"],
                "suggested_action": f"Clean up merged branches for {s['label']}",
            })
    return decisions


def build_home_brief(date: Optional[str] = None,
                     window: Optional[tuple[str, str]] = None) -> dict:
    """Assemble the home roll-up: global KPIs, decisions, and per-project briefs.

    ``window`` is threaded down to each per-project brief so the whole roll-up
    shares one daily window (issue #841).
    """
    ensure_dependencies()
    d, _start, _end = _window(date)

    projects: list[dict] = []
    decisions: list[dict] = []
    ran_overnight: list[dict] = []
    waiting_on_you: list[dict] = []
    suggested_next_all: list[dict] = []
    try:
        project_list = _load_projects()
    except Exception:
        project_list = []

    for p in project_list:
        repo = p.get("repo", "")
        slug = repo.split("/")[-1] if repo else repo
        brief = build_project_brief(slug, date=d, window=window)
        projects.append(brief)
        decisions.extend(_decisions_for_project(slug, brief))
        # Tag each planner-state entry with its project so the home roll-up
        # stays navigable (issue #864). The lists are the SAME builder output
        # the per-project brief carries, so any flow (morning/evening) sees one
        # source of truth (AC8).
        ran_overnight.extend({**e, "project": slug} for e in brief.get("ran_overnight", []))
        waiting_on_you.extend({**w, "project": slug} for w in brief.get("waiting_on_you", []))
        suggested_next_all.extend(brief.get("suggested_next", []))

    # Cap the home roll-up at SUGGESTED_NEXT_MAX (issue #884 AC2).
    suggested_next = suggested_next_all[:SUGGESTED_NEXT_MAX]

    global_kpis = {
        "sprints_shipped": sum(b["kpis"]["sprints_shipped"] for b in projects),
        "tickets_done": sum(b["kpis"]["tickets_done"] for b in projects),
        "in_progress": sum(1 for b in projects if b["kpis"]["in_progress"]),
        "needs_your_call": sum(b["kpis"]["needs_you"] for b in projects),
        # Drives the nav-pill sign-off indicator (AC5/AC6): >0 → indicator shown.
        "awaiting_signoff": len(waiting_on_you),
    }

    return {
        "date": d,
        "global_kpis": global_kpis,
        "decisions": decisions,
        "projects": projects,
        "ran_overnight": ran_overnight,
        "waiting_on_you": waiting_on_you,
        "suggested_next": suggested_next,
    }
