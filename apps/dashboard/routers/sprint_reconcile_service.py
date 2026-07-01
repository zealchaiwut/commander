"""Background GitHub ↔ DB lifecycle reconcile (lifecycle P3).

Stale-while-revalidate: ``GET /api/sprints/history`` returns cached DB rows
immediately and schedules a background pass that re-checks terminal sprints
against GitHub, updates drift in the DB, and broadcasts deltas to the UI.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, Awaitable

_log = logging.getLogger(__name__)

# B4 (history perf): the project history GET fires reconcile_project_background as
# a BackgroundTask on every page load. For a project with stuck/unmerged sprints
# that means an N+1 `gh` subprocess fan-out per load. Throttle to at most once per
# project per TTL so rapid refreshes coalesce. In-process best-effort; a restart
# clears it (fine — the next load reconciles).
_RECONCILE_TTL_SECONDS = 60.0
_last_reconcile_at: dict[str, float] = {}

_TERMINAL_STATES = frozenset({
    "ready_to_merge", "needs_rework", "completed",
    "cancelled", "failed",  # legacy stored values
})

# Outcome strings in agent_runs that indicate a ticket passed (tester approved).
_AGENT_RUN_DONE = frozenset({
    "merged", "pass", "passed", "success", "done", "complete",
    "completed", "uat", "shipped",
})
# Outcome strings that indicate a ticket failed/was rejected.
_AGENT_RUN_FAILED = frozenset({
    "fail", "failed", "reject", "rejected", "crash", "crashed",
    "skipped", "error",
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
    project: str = "",
) -> bool:
    """Route a reconciler state transition through the DB state machine.

    Delegates to db.transition_sprint_state with the given ``actor`` and returns
    whether the state machine ACCEPTED the edge.

    The previous version called the record_* helpers, which hardcode
    actor="manager" and return None — so reconcile-only edges
    (needs_rework→ready_to_merge / →completed) were silently rejected while this
    wrapper still returned True. That made reconcile-apply report
    ``updated=true`` with the state unchanged (e.g. an orphaned-but-merged sprint
    stuck in needs_rework). Passing the real actor through fixes the promotion,
    and returning ``accepted`` stops the false-success report.
    """
    res = _db().transition_sprint_state(
        label, state, actor=actor, end_reason=end_reason, project=project,
    )
    return bool(getattr(res, "accepted", False))


def _lineage_fully_in_develop(label: str, project: str) -> bool:
    """True when *label* is a lineage member whose work is already in develop.

    Used to auto-complete a superseded ancestor (B2). Two conditions:
      1. The sprint is part of a rerun lineage — it is itself a child
         (``sprint-N.M``) or it has at least one child row. A standalone
         needs_rework sprint with no children is a genuine failed run awaiting
         rerun and must never be auto-completed.
      2. Its sprint branch has no commits missing from develop — i.e. every
         commit it produced has merged up the chain into develop.

    Both checks are conservative: a missing repo, a still-open base→develop PR,
    or any compare error leaves commits "unmerged" and blocks promotion.
    """
    if not project:
        return False
    try:
        import server as srv  # noqa: PLC0415 — lazy, avoids import cycle
    except Exception:
        return False
    is_child = srv._is_child_sprint_label(label)
    has_children = bool(_db().get_sprint_children(label, project=project or None))
    if not (is_child or has_children):
        return False
    branch = srv._sprint_branch_name(label)
    return not srv._branch_has_unmerged_commits(project, branch, "develop")


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
    # needs_rework→completed is never reconciler-driven: parent/ancestor sprints
    # stay open until an explicit Merge Sprint or Bulk complete (manager actor).
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
        # GitHub shows no open rework / unfinished work tickets → the sprint's work
        # has settled, so promote to ready_to_merge.
        #
        # Do NOT gate this on issues_json's `failed`/`failure_reason`: that is a
        # stale snapshot from the ORIGINAL run and survives even after the failed
        # ticket was re-run and passed in a child sprint. Trusting it left
        # vector-search-demo sprint-15 stuck in needs_rework (reconcile returned
        # would_change=false), which disabled Bulk complete. The live GitHub signal
        # (has_rework) is authoritative; a genuinely-unfinished ticket would still
        # be open/not-done and keep has_rework True.
        return {"state": "ready_to_merge", "end_reason": row.get("end_reason") or "github-reconcile"}
    return None


def _issues_from_agent_runs(label: str, project: str = "") -> list[dict]:
    """Derive per-issue states from agent_runs records for a sprint, scoped by project.

    Mirrors the logic in sprint_history_service._issues_from_agent_runs to avoid
    a circular import. Any outcome in _AGENT_RUN_DONE marks the issue done (merged);
    otherwise it is failed or open (unknown). Scoping by project stops a
    same-numbered sprint in another repo from being merged into this sprint's
    issues_json on reconcile.
    """
    try:
        rows = _db().agent_runs_for_sprint(label, project=project or None)
    except Exception:
        return []
    agg: dict[int, dict] = {}
    for row in rows:
        try:
            tid = int(row.get("issue_number") or 0)
        except (TypeError, ValueError):
            continue
        if tid <= 0:
            continue
        outcome = (row.get("outcome") or "").strip().lower()
        rec = agg.setdefault(tid, {"done": False, "failed": False})
        if outcome in _AGENT_RUN_DONE:
            rec["done"] = True
        elif outcome in _AGENT_RUN_FAILED:
            rec["failed"] = True
    result = []
    for tid in sorted(agg):
        rec = agg[tid]
        if rec["done"]:
            state, agent_status = "merged", "completed"
        elif rec["failed"]:
            state, agent_status = "closed", "failed"
        else:
            state, agent_status = "open", None
        result.append({
            "ticket_id": tid,
            "number": tid,
            "state": state,
            "agent_status": agent_status,
        })
    return result


def _reconcile_counts(label: str, row: dict, project: str = "") -> bool:
    """Re-derive issues_json and count columns from agent_runs for a terminal sprint.

    Non-terminal rows are skipped (AC4).  The function:
    1. Fetches agent_runs for *label* and derives per-issue states.
    2. Unions those with the stored issues_json — keeping title/pr_number/time_spent
       from existing entries while overriding state/agent_status from agent_runs.
    3. If any drift is found, persists the merged issues_json plus recomputed counts
       via db.update_sprint_run_counts() and calls db.update_sprint_reconciliation().

    Returns True when the DB row was updated.
    """
    import json as _json

    state = row.get("state") or ""
    if state not in _TERMINAL_STATES:
        return False

    ar_issues = _issues_from_agent_runs(label, project or row.get("project") or "")
    if not ar_issues:
        return False

    # Build index of existing issues_json entries (keeps title, pr_number, time_spent)
    existing: list[dict] = []
    try:
        existing = _json.loads(row.get("issues_json") or "[]")
    except (ValueError, TypeError):
        existing = []

    by_id: dict[int, dict] = {}
    for iss in existing:
        tid_raw = iss.get("ticket_id") or iss.get("number")
        try:
            tid = int(tid_raw or 0)
        except (TypeError, ValueError):
            continue
        if tid > 0:
            by_id[tid] = dict(iss)

    changed = False
    for ar in ar_issues:
        tid = ar["ticket_id"]
        if tid in by_id:
            # Only override when agent_runs has a definitive (non-open) outcome so
            # we never downgrade an issue that already has a positive state.
            if ar["state"] != "open":
                cur = by_id[tid]
                state_diff = cur.get("state") != ar["state"]
                agent_diff = ar["agent_status"] and cur.get("agent_status") != ar["agent_status"]
                if state_diff or agent_diff:
                    cur["state"] = ar["state"]
                    if ar["agent_status"]:
                        cur["agent_status"] = ar["agent_status"]
                    changed = True
        else:
            # New issue from agent_runs with a definitive outcome
            if ar["state"] != "open":
                by_id[tid] = ar
                changed = True

    if not changed:
        return False

    merged = [by_id[k] for k in sorted(by_id)]

    # Recompute denormalized counts from the merged list
    settled_done = sum(
        1 for i in merged
        if (i.get("state") or "").lower() == "merged"
        or (i.get("agent_status") or "").lower() in ("completed", "done")
    )
    failure_count = sum(
        1 for i in merged
        if (i.get("agent_status") or "").lower() == "failed"
        or bool(i.get("failure_reason"))
    )
    uat_count = sum(
        1 for i in merged
        if (i.get("status") or "").lower() == "uat"
    )

    new_json = _json.dumps(merged)
    _proj = (project or row.get("project") or "").strip()
    _db().update_sprint_run_counts(
        label, new_json, settled_done, uat_count, failure_count, project=_proj,
    )
    _db().update_sprint_reconciliation(label, {
        "source": "count-reconcile",
        "fixed": True,
        "settled_done": settled_done,
        "failure_count": failure_count,
    }, _proj)
    return True


def reconcile_sprint_label(label: str, project: str) -> bool:
    """Reconcile one sprint row against GitHub. Returns True if DB was updated."""
    row = _db().get_sprint(label, project=project or None)
    if not row:
        return False
    if project and row.get("project") and row.get("project") != project:
        return False
    _eff_project = project or row.get("project") or ""
    patch = _github_reconcile_row(label, _eff_project, row)
    lifecycle_updated = False
    if patch:
        # AC4 (original): all lifecycle writes go through transition_sprint_state.
        lifecycle_updated = transition_sprint_state(
            label,
            patch["state"],
            actor="reconcile",
            end_reason=patch.get("end_reason"),
            project=_eff_project,
        )
        if lifecycle_updated:
            # Re-fetch so _reconcile_counts sees the updated state.
            row = _db().get_sprint(label, project=_eff_project or None) or row
    # AC1: re-derive counts for terminal sprints alongside lifecycle correction.
    counts_updated = _reconcile_counts(label, row, project=_eff_project)
    return lifecycle_updated or counts_updated


def _parse_sprint_label(label: str) -> tuple[str, tuple[int, ...]]:
    """'sprint-90.3' → ('sprint-90', (90, 3)). Returns ('', ()) if unparseable."""
    core = label[len("sprint-"):] if label.startswith("sprint-") else label
    try:
        parts = tuple(int(p) for p in core.split("."))
    except ValueError:
        return "", ()
    if not parts:
        return "", ()
    return f"sprint-{parts[0]}", parts


def _terminalize_superseded_orphans(project: str) -> list[str]:
    """Terminalize orphan queued rework children that a later sibling has shipped.

    A rework child written at run-end (plan.json state='needs_rework',
    end_reason='queued') but never dispatched has no DB row and lingers on the
    board as a phantom (perf-coach 90.3, 91.1). When a LATER child in the same
    lineage chain has reached 'completed' in the DB, that queued orphan is
    superseded — mark its plan.json completed/superseded so it stops surfacing.
    Writes only the orphan's own plan.json; touches no GitHub and no DB.
    """
    terminalized: list[str] = []
    try:
        import server as srv  # noqa: PLC0415
        sprints_dir = srv._project_root_path(project) / ".commander" / "sprints"
    except Exception:
        return terminalized
    if not sprints_dir.exists():
        return terminalized

    db = _db()
    completed_by_base: dict[str, list[tuple[int, ...]]] = {}
    for row in db.list_sprints_lifecycle():
        if project and row.get("project") != project:
            continue
        if (row.get("state") or "").lower() != "completed":
            continue
        base, parts = _parse_sprint_label(row.get("label") or "")
        if base:
            completed_by_base.setdefault(base, []).append(parts)

    for plan_path in sorted(sprints_dir.glob("sprint-*-plan.json")):
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if (data.get("state") or "").lower() != "needs_rework":
            continue
        if (data.get("end_reason") or "").lower() != "queued":
            continue
        label = plan_path.name[: -len("-plan.json")]
        # Skip if it actually ran (state file) or is tracked in the DB.
        if (sprints_dir / f"{label}-state.json").exists():
            continue
        if db.get_sprint(label, project=project or None):
            continue
        base, parts = _parse_sprint_label(label)
        if not base:
            continue
        # Superseded only if a strictly-later sibling in the same lineage completed.
        if not any(sib > parts for sib in completed_by_base.get(base, [])):
            continue
        data["state"] = "completed"
        data["end_reason"] = "superseded"
        try:
            tmp = plan_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(str(tmp), str(plan_path))
            terminalized.append(label)
        except Exception:
            pass
    return terminalized


def reconcile_project(project: str, limit: int = 40) -> list[str]:
    """Reconcile terminal sprints for *project*. Returns labels that were updated."""
    updated: list[str] = []
    for lbl in _terminalize_superseded_orphans(project):
        updated.append(lbl)
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
        # Skip states the reconciler can't usefully change:
        #  • running / draft / planned / planning — not terminal, nothing to settle.
        #  • completed / deleted — FINAL terminal states (completed only ever goes
        #    to deleted; deleted is the end). Re-checking them every History load
        #    burned ~4s on tangled lineages (mostly completed members) with no
        #    possible state change. Only ready_to_merge / needs_rework / failed can
        #    still move, so reconcile just those.
        if state in ("running", "draft", "planned", "planning", "completed", "deleted"):
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


def _pr_number_from_url(pr_url: str | None) -> int | None:
    """Parse the PR number out of a GitHub pull URL, or None."""
    if not pr_url:
        return None
    import re  # noqa: PLC0415
    m = re.search(r"/pull/(\d+)", pr_url)
    return int(m.group(1)) if m else None


def _gather_reconcile_inputs_mirror(
    label: str,
    repo: str,
    pr_url: str | None,
    ticket_numbers: list[int] | None = None,
) -> dict:
    """Mirror-backed equivalent of ``gather_inputs_via_gh`` (zero GitHub quota).

    Builds the same ``{summary_issues, pr_info, tickets}`` dict that
    ``run_reconciliation`` consumes, sourced from the local ``issues`` mirror
    (kept fresh by the 304-conditional background sync) and the per-repo cached
    merged-branches list — instead of a live ``gh issue list`` + ``gh pr view``
    per sprint. The mirror is a fresh GitHub replica, so GitHub stays the source
    of truth while the read costs ~0 quota.

    PR merge status is inferred from ``list_merged_sprint_branches`` (one cached
    call per repo, shared across all sprints in a sweep). This matches how the
    board already decides a sprint is shipped; it cannot see merged PRs older
    than that list's window, in which case ``sprint_pr`` reports unmerged — a
    report-only check that never affects the sprint's lifecycle state.
    """
    summary_issues: list[dict] = []
    tickets: list[dict] = []
    pr_info: dict | None = None

    def _names(iss: dict) -> list[str]:
        return [l.get("name") for l in (iss.get("labels") or []) if isinstance(l, dict)]

    try:
        import github_client as gh  # noqa: PLC0415
    except Exception:
        return {"summary_issues": [], "pr_info": None, "tickets": []}

    mirror = []
    try:
        mirror = gh._mirror_issues(repo) or []
    except Exception:
        mirror = []

    tnums = {int(n) for n in (ticket_numbers or [])}
    for iss in mirror:
        names = _names(iss)
        if label in names:
            summary_issues.append({
                "number": iss.get("number"),
                "title": iss.get("title") or "",
                "labels": iss.get("labels") or [],
            })
        num = iss.get("number")
        if num is not None and int(num) in tnums:
            tickets.append({"number": num, "labels": iss.get("labels") or []})

    pr_number = _pr_number_from_url(pr_url)
    try:
        merged_branches = gh.list_merged_sprint_branches(repo_name=repo)
    except Exception:
        merged_branches = set()
    if f"sprint/{label}" in (merged_branches or set()):
        pr_info = {"number": pr_number, "merged": True}
    elif pr_number is not None:
        # A PR exists but its branch isn't in the recent merged set — report
        # unmerged. Report-only; does not drive lifecycle state.
        pr_info = {"number": pr_number, "merged": False, "reason": "unmerged"}

    return {"summary_issues": summary_issues, "pr_info": pr_info, "tickets": tickets}


def refresh_post_sprint_reconciliation(label: str, project: str) -> bool:
    """Re-run GitHub post-sprint reconciliation when loose ends may have cleared."""
    row = _db().get_sprint(label, project=project or None)
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
        rec_inputs = _gather_reconcile_inputs_mirror(label, repo, pr_url, ticket_numbers)
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
        _db().update_sprint_reconciliation(label, result, project)
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
        # A completed/deleted sprint that already has a stored reconciliation block
        # is settled — re-deriving its loose-ends every History load is pure churn
        # (the ~4s lag on tangled lineages, mostly completed members). Compute it
        # once (no block yet), then skip. Non-final terminals still refresh.
        if state in ("completed", "deleted") and (row.get("reconciliation_json") or "").strip():
            continue
        checked += 1
        if refresh_post_sprint_reconciliation(label, project):
            updated.append(label)
    return updated


def reconcile_preview(label: str, project: str) -> dict:
    """Dry-run reconcile for ONE sprint — compute GitHub-vs-DB diff, write nothing.

    Drives the per-sprint "Reconcile against GitHub" preview: returns the current
    DB lifecycle state, the state GitHub truth implies, whether applying would
    change it, and the read-only post-sprint checks (summary issue / sprint PR /
    stale labels). All inputs come from the mirror — zero GitHub quota.
    """
    db = _db()
    row = db.get_sprint(label, project=project or None)
    if not row:
        return {"label": label, "exists": False}
    if project and row.get("project") and row.get("project") != project:
        return {"label": label, "exists": False, "wrong_project": True}

    eff_project = project or row.get("project") or ""
    db_state = db.canonical_lifecycle(row.get("state") or "")
    patch = _github_reconcile_row(label, eff_project, row)  # no write
    proposed_state = patch["state"] if patch else db_state

    checks: list[dict] = []
    all_clear: bool | None = None
    try:
        from services.sprint_manager.reconciliation import run_reconciliation  # noqa: PLC0415
        recon = _stored_reconciliation(row, None)
        pr_url = _pr_url_for_reconciliation(eff_project, None, row, recon)
        ticket_numbers = _ticket_numbers_for_reconciliation(row, None)
        rec_inputs = _gather_reconcile_inputs_mirror(label, eff_project, pr_url, ticket_numbers)
        result = run_reconciliation(
            sprint_label=label,
            project=eff_project,
            state_path=None,  # None → run_reconciliation persists nothing
            summary_issues=rec_inputs["summary_issues"],
            pr_info=rec_inputs["pr_info"],
            tickets=rec_inputs["tickets"],
            emit_event=None,
        )
        checks = result.get("checks", [])
        all_clear = result.get("all_clear")
    except Exception as exc:
        _log.warning("reconcile preview checks failed for %s: %s", label, exc)

    return {
        "label": label,
        "exists": True,
        "project": eff_project,
        "db_state": db_state,
        "github_state": proposed_state,
        "would_change": bool(patch),
        "reason": (patch or {}).get("end_reason"),
        "checks": checks,
        "all_clear": all_clear,
    }


def reconcile_apply(label: str, project: str) -> dict:
    """Apply reconcile for ONE sprint: write DB lifecycle + local state, never GitHub."""
    db = _db()
    row = db.get_sprint(label, project=project or None)
    if not row:
        return {"label": label, "exists": False, "updated": False}
    eff_project = project or row.get("project") or ""
    before = db.canonical_lifecycle(row.get("state") or "")
    lifecycle_updated = reconcile_sprint_label(label, eff_project)
    recon_updated = refresh_post_sprint_reconciliation(label, eff_project)
    after_row = db.get_sprint(label, project=eff_project or None) or row
    return {
        "label": label,
        "exists": True,
        "project": eff_project,
        "updated": bool(lifecycle_updated or recon_updated),
        "db_state_before": before,
        "db_state_after": db.canonical_lifecycle(after_row.get("state") or ""),
    }


async def reconcile_project_background(
    project: str,
    broadcast: Callable[[dict], Awaitable[None]] | None = None,
) -> None:
    """Background task: reconcile lifecycle + post-sprint loose ends, then notify."""
    # Opt-out for non-primary clones (e.g. UAT) so only one dashboard self-heals
    # against the shared GitHub token. Backfill/board reads stay unaffected.
    if os.environ.get("COMMANDER_DISABLE_AUTO_RECONCILE") == "1":
        return
    now = time.monotonic()
    last = _last_reconcile_at.get(project)
    if last is not None and (now - last) < _RECONCILE_TTL_SECONDS:
        return  # coalesce rapid history refreshes — within TTL, skip the gh fan-out
    _last_reconcile_at[project] = now
    lifecycle_updated: list[str] = []
    reconciliation_updated: list[str] = []

    def _run_sync() -> tuple[list[str], list[str]]:
        # Both passes shell out to `gh` (blocking). Run them in a worker thread so
        # the spawning never stalls the event loop and starves concurrent
        # requests (history loads were taking ~5s while this ran inline).
        return reconcile_project(project), refresh_post_sprint_reconciliations(project)

    try:
        lifecycle_updated, reconciliation_updated = await asyncio.to_thread(_run_sync)
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
