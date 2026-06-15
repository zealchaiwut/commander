"""Background GitHub ↔ DB lifecycle reconcile (lifecycle P3).

Stale-while-revalidate: ``GET /api/sprints/history`` returns cached DB rows
immediately and schedules a background pass that re-checks terminal sprints
against GitHub, updates drift in the DB, and broadcasts deltas to the UI.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable, Awaitable

_log = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({
    "ready_to_merge", "needs_rework", "completed",
    "cancelled", "failed",  # legacy stored values
})

# Repo root: apps/dashboard/routers/ → apps/dashboard/ → apps/ → repo root
_REPO_ROOT = Path(__file__).parent.parent.parent.parent


def _db():
    import db  # noqa: PLC0415
    return db


def _manager_pid_file(label: str) -> Path:
    """Return the per-sprint manager PID file path for the given sprint label."""
    return _REPO_ROOT / ".commander" / "sprints" / f"{label}-pid"


def _is_manager_pid_alive(label: str) -> bool:
    """Return True if the sprint-manager process owning *label* is still running."""
    pid_path = _manager_pid_file(label)
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists but is owned by another user


def transition_sprint_state(
    label: str,
    state: str,
    actor: str,
    end_reason: str | None = None,
) -> bool:
    """Route a reconciler state transition through the DB write layer.

    All reconciler writes must go through this function so that a future
    foundation guard can intercept non-owner running→terminal transitions.
    Returns True when the transition was applied.
    """
    if state == "needs_rework":
        _db().record_sprint_needs_rework(label, end_reason=end_reason)
    elif state == "ready_to_merge":
        _db().record_sprint_ready_to_merge(label, end_reason=end_reason)
    else:
        _db().record_sprint_finish(label, end_reason=end_reason)
    return True


def _github_reconcile_row(label: str, project: str, row: dict) -> dict | None:
    """Return updated fields when GitHub state diverges from the DB row, else None."""
    try:
        import server as srv  # noqa: PLC0415 — lazy to avoid import cycle at load time
    except Exception:
        return None

    stored = row.get("state") or ""
    if stored not in _TERMINAL_STATES and stored != "running":
        return None

    # When the sprint is running, only a *confirmed orphan* may be settled — a
    # manager PID file that exists but whose process is dead (issue #1088):
    #  • live PID             → actively running, no patch (AC1/AC2)
    #  • PID file present+dead → orphaned, settle below (AC3)
    #  • PID file absent       → unknown; a pure rework signal must NOT flip a
    #                            running sprint (issue #1095), so leave it running.
    if stored == "running":
        if not _manager_pid_file(label).exists():
            return None
        if _is_manager_pid_alive(label):
            return None

    try:
        has_rework = srv._has_rework_tickets(label, project)
    except Exception:
        return None

    # AC3: confirmed-orphan running sprint — settle state based on ticket outcomes.
    if stored == "running":
        if has_rework:
            return {"state": "needs_rework", "end_reason": "reconcile-orphan"}
        return {"state": "ready_to_merge", "end_reason": "reconcile-orphan"}

    canonical = _db().canonical_lifecycle(stored)
    if has_rework and canonical in ("ready_to_merge", "completed"):
        # A natural successful run end must not be downgraded because GitHub
        # labels lag (e.g. ticket still OPEN in UAT before Finish sprint).
        end_reason = row.get("end_reason") or ""
        if end_reason == "natural":
            try:
                import json
                issues = json.loads(row.get("issues_json") or "[]")
                if issues and all(
                    (i.get("state") or "").lower() == "merged"
                    or (i.get("agent_status") or "").lower() in ("completed", "done")
                    for i in issues
                ):
                    return None
            except Exception:
                pass
        return {"state": "needs_rework", "end_reason": end_reason or "github-reconcile"}
    if not has_rework and canonical == "needs_rework" and stored in ("needs_rework", "failed", "cancelled"):
        # All tickets settled on GitHub — promote to ready_to_merge when plausible.
        failed_in_db = False
        try:
            import json
            issues = json.loads(row.get("issues_json") or "[]")
            failed_in_db = any(
                (i.get("agent_status") or "").lower() == "failed" or i.get("failure_reason")
                for i in issues
            )
        except Exception:
            pass
        if not failed_in_db:
            return {"state": "ready_to_merge", "end_reason": row.get("end_reason") or "github-reconcile"}
    return None


def reconcile_sprint_label(label: str, project: str) -> bool:
    """Reconcile one sprint row against GitHub. Returns True if DB was updated."""
    row = _db().get_sprint(label)
    if not row:
        return False
    if project and row.get("project") and row.get("project") != project:
        return False
    patch = _github_reconcile_row(label, project or row.get("project") or "", row)
    if not patch:
        return False
    # AC4: all writes go through transition_sprint_state, not direct DB calls.
    result = transition_sprint_state(
        label,
        patch["state"],
        actor="reconcile",
        end_reason=patch.get("end_reason"),
    )
    return result.accepted


def reconcile_project(project: str, limit: int = 40) -> list[str]:
    """Reconcile terminal sprints for *project*. Returns labels that were updated."""
    updated: list[str] = []
    rows = _db().list_sprints_lifecycle()
    checked = 0
    for row in rows:
        if checked >= limit:
            break
        label = row.get("label") or ""
        if not label:
            continue
        if project and row.get("project") != project:
            continue
        state = row.get("state") or ""
        if state == "running" or state in ("draft", "planned", "planning"):
            continue
        checked += 1
        if reconcile_sprint_label(label, project):
            updated.append(label)
    return updated


async def reconcile_project_background(
    project: str,
    broadcast: Callable[[dict], Awaitable[None]] | None = None,
) -> None:
    """Background task: reconcile then notify connected clients."""
    try:
        updated = reconcile_project(project)
    except Exception as exc:
        _log.warning("sprint reconcile failed for %s: %s", project, exc)
        return
    if not updated or broadcast is None:
        return
    try:
        await broadcast({
            "type": "update",
            "event": {
                "event_type": "sprint_reconciled",
                "project": project,
                "labels": updated,
            },
        })
    except Exception as exc:
        _log.warning("sprint reconcile broadcast failed: %s", exc)
