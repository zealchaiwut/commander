"""Background GitHub ↔ DB lifecycle reconcile (lifecycle P3).

Stale-while-revalidate: ``GET /api/sprints/history`` returns cached DB rows
immediately and schedules a background pass that re-checks terminal sprints
against GitHub, updates drift in the DB, and broadcasts deltas to the UI.
"""
from __future__ import annotations

import json
import logging
import os
import re
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
    # The local transition_sprint_state wrapper (above) returns a bool — True when
    # the transition was applied — so return it directly. (It previously did
    # `result.accepted`, which raised AttributeError on every settle: a bool has
    # no `.accepted`. The DB-layer TransitionResult is a separate type.)
    result = transition_sprint_state(
        label,
        patch["state"],
        actor="reconcile",
        end_reason=patch.get("end_reason"),
    )
    return result


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


def _stored_reconciliation(row: dict, state: dict | None) -> dict:
    recon = (state or {}).get("reconciliation") or {}
    raw = row.get("reconciliation_json")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                recon = parsed
        except (TypeError, ValueError):
            pass
    return recon if isinstance(recon, dict) else {}


def _reconciliation_needs_refresh(recon: dict) -> bool:
    """True when persisted post-sprint checks may be stale (e.g. PR merged since finish)."""
    if not recon:
        return False
    checks = recon.get("checks") or []
    return any(c.get("name") == "sprint_pr" and not c.get("ok") for c in checks)


def _ticket_numbers_for_reconciliation(row: dict, state: dict | None) -> list[int]:
    nums: list[int] = []
    seen: set[int] = set()
    for source in (
        (state or {}).get("issues") or [],
        json.loads(row.get("issues_json") or "[]") if row.get("issues_json") else [],
    ):
        for iss in source:
            n = iss.get("number", iss.get("ticket_id", iss.get("issue_number")))
            if n is None:
                continue
            try:
                num = int(n)
            except (TypeError, ValueError):
                continue
            if num not in seen:
                seen.add(num)
                nums.append(num)
    return nums


def _pr_url_for_reconciliation(repo: str, state: dict | None, row: dict, recon: dict) -> str | None:
    state = state or {}
    pr_url = state.get("sprint_pr_url") or state.get("pr_url")
    if pr_url:
        return str(pr_url)
    pr_number = row.get("pr_number")
    if pr_number is None:
        for chk in recon.get("checks") or []:
            if chk.get("name") == "sprint_pr" and chk.get("pr_number") is not None:
                pr_number = chk.get("pr_number")
                break
    if pr_number is None:
        pr_number = state.get("pr_number")
    if pr_number is not None and repo:
        return f"https://github.com/{repo}/pull/{int(pr_number)}"
    return None


def refresh_post_sprint_reconciliation(label: str, project: str) -> bool:
    """Re-run GitHub post-sprint reconciliation when loose ends may have cleared."""
    row = _db().get_sprint(label)
    if not row:
        return False
    if project and row.get("project") and row.get("project") != project:
        return False
    state = row.get("state") or ""
    if state == "running" or state in ("draft", "planned", "planning"):
        return False

    try:
        import server as srv  # noqa: PLC0415
        from . import sprint_artifact_service  # noqa: PLC0415
        from services.sprint_manager.reconciliation import (  # noqa: PLC0415
            gather_inputs_via_gh,
            run_reconciliation,
        )
    except Exception:
        return False

    repo = project or row.get("project") or ""
    if not repo:
        return False

    try:
        project_root = srv._project_root_path(repo)
        sprints_dir = project_root / ".commander" / "sprints"
        state_path = sprint_artifact_service.resolve_state_path(sprints_dir, label)
        state_data = sprint_artifact_service.load_state_file(sprints_dir, label) or {}
    except Exception:
        state_data = {}
        state_path = None

    recon = _stored_reconciliation(row, state_data)
    if not _reconciliation_needs_refresh(recon):
        return False

    pr_url = _pr_url_for_reconciliation(repo, state_data, row, recon)
    ticket_numbers = _ticket_numbers_for_reconciliation(row, state_data)
    try:
        rec_inputs = gather_inputs_via_gh(label, repo, pr_url, ticket_numbers)
        result = run_reconciliation(
            sprint_label=label,
            project=repo,
            state_path=state_path,
            summary_issues=rec_inputs["summary_issues"],
            pr_info=rec_inputs["pr_info"],
            tickets=rec_inputs["tickets"],
            emit_event=None,
        )
    except Exception as exc:
        _log.warning("post-sprint reconciliation refresh failed for %s: %s", label, exc)
        return False

    prior_fp = recon.get("fingerprint")
    if prior_fp and prior_fp == result.get("fingerprint"):
        return False

    if state_path is not None:
        try:
            merged = dict(state_data)
            merged["reconciliation"] = result
            state_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        except Exception:
            pass
    try:
        _db().update_sprint_reconciliation(label, result)
    except Exception:
        pass
    return True


def refresh_post_sprint_reconciliations(project: str, limit: int = 40) -> list[str]:
    """Refresh stale post-sprint reconciliation blocks for terminal sprints."""
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
        if refresh_post_sprint_reconciliation(label, project):
            updated.append(label)
    return updated


async def reconcile_project_background(
    project: str,
    broadcast: Callable[[dict], Awaitable[None]] | None = None,
) -> None:
    """Background task: reconcile lifecycle + post-sprint loose ends, then notify."""
    lifecycle_updated: list[str] = []
    reconciliation_updated: list[str] = []
    try:
        lifecycle_updated = reconcile_project(project)
        reconciliation_updated = refresh_post_sprint_reconciliations(project)
    except Exception as exc:
        _log.warning("sprint reconcile failed for %s: %s", project, exc)
        return
    updated = sorted(set(lifecycle_updated) | set(reconciliation_updated))
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
