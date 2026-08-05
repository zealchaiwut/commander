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

# Per-project rotating offset into the eligible-rows list for reconcile_project's
# scan window (issue #1690) — lets a project with more than `limit` eligible
# terminal sprints get full coverage across sweeps instead of only ever
# re-checking the same first N.
_reconcile_cursor: dict[str, int] = {}

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


def _manager_pid_file(label: str, project: str = "") -> Path:
    """Return the per-sprint manager PID file path for the given sprint label.

    AC3 (#1887): resolve under the sprint's PROJECT root, not the dashboard
    repo root.  For non-commander projects (e.g. perf-coach) the PID file lives
    under ~/dev/perf-coach/.commander/sprints/<label>-pid, not under the
    commander checkout.  Falls back to _REPO_ROOT when project is empty/unknown.
    """
    if project:
        try:
            import server as srv  # noqa: PLC0415 — lazy import to avoid cycle
            project_root = srv._project_root_path(project)
        except Exception:
            project_root = _REPO_ROOT
    else:
        project_root = _REPO_ROOT
    return project_root / ".commander" / "sprints" / f"{label}-pid"


def _is_manager_pid_alive(label: str, project: str = "") -> bool:
    """Return True if the sprint-manager process owning *label* is still running."""
    pid_path = _manager_pid_file(label, project)
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
    ended_at: str | None = None,
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
        ended_at=ended_at,
    )
    return bool(getattr(res, "accepted", False))


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
        if not _manager_pid_file(label, project).exists():
            return None
        if _is_manager_pid_alive(label, project):
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
    # Sweep-driven completion is allowed in exactly ONE guarded case — the
    # "real merge-to-develop check in the reconciler" anticipated by db.py's
    # B2-edge comment: the sprint is a superseded ancestor, i.e.
    #   • no open rework tickets (mirror-backed has_rework=False), AND
    #   • a strictly-later member of its lineage is state='completed' (a
    #     verified merge flow blessed the chain tip AFTER this member ended), AND
    #   • the lineage BASE branch (sprint/<base>) is a merged PR head (cached
    #     list_merged_sprint_branches — zero extra GitHub quota).
    # Without this, a superseded ancestor could only ever be promoted to
    # ready_to_merge and sat there forever advertising a Complete CTA for work
    # already in develop (hermes-agent sprint-1/1.1/1.2 zombie lineage). All
    # other completions still come only from Merge Sprint / Bulk complete /
    # Complete-step (issue #1694).
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
    if not has_rework and stored in ("needs_rework", "failed", "cancelled", "ready_to_merge"):
        if _lineage_has_later_completed(label, project) and _base_branch_merged_to_develop(label, project):
            # Superseded ancestor whose chain verifiably shipped → completed.
            # Preserve the original run-end timestamp: the silent ended_at
            # rewrite was part of the hermes zombie-lineage corruption.
            return {
                "state": "completed",
                "end_reason": "superseded",
                "ended_at": row.get("ended_at"),
            }
        if canonical == "needs_rework":
            # GitHub shows no open rework / unfinished work tickets → the sprint's
            # work has settled, so promote to ready_to_merge.
            #
            # Do NOT blindly gate this on issues_json's `failed`/`failure_reason`
            # alone: that is a stale snapshot from the ORIGINAL run and must not
            # block promotion when the failed ticket was re-run and passed in a
            # later child sprint (vector-search-demo sprint-15 — reconcile
            # returned would_change=false forever, disabling Bulk complete).
            # But it must also not be ignored outright: a ticket can be closed
            # without ever being fixed (perf-coach sprint-121 — no rerun/child
            # sprint exists in its lineage), and trusting "no open GitHub
            # ticket" alone there just flip-flops this sprint against
            # _outcome_reconcile_row's downgrade on every reconcile pass
            # (issue #2197). Only promote when either issues_json shows no
            # real failure, or a later lineage member actually completed —
            # proof the failure was addressed somewhere in the chain, not
            # just closed.
            outcome = _derive_terminal_state_from_issues_json(row.get("issues_json") or "[]")
            if outcome == "needs_rework" and not _lineage_has_later_completed(label, project):
                return None
            return {"state": "ready_to_merge", "end_reason": row.get("end_reason") or "github-reconcile"}
    return None


def _issues_from_agent_runs(label: str, project: str = "") -> list[dict]:
    """Derive per-issue states from agent_runs records for a sprint, scoped by project.

    Mirrors the logic in sprint_history_service._issues_from_agent_runs to avoid
    a circular import. The LATEST definitive outcome per ticket wins (issue
    #1882): a first tester pass followed by failed fix-round runs is a failed
    ticket, not a done one — any-done-wins settled perf-coach sprint-105's
    #1361 as merged although its last two tester runs failed. Scoping by
    project stops a same-numbered sprint in another repo from being merged
    into this sprint's issues_json on reconcile.
    """
    try:
        rows = _db().agent_runs_for_sprint(label, project=project or None)
    except Exception:
        return []
    # rows are ordered by (issue_number, id); id order is chronological per
    # ticket, so the last definitive outcome seen is the ticket's final word.
    agg: dict[int, str] = {}
    for row in rows:
        try:
            tid = int(row.get("issue_number") or 0)
        except (TypeError, ValueError):
            continue
        if tid <= 0:
            continue
        outcome = (row.get("outcome") or "").strip().lower()
        agg.setdefault(tid, "open")
        if outcome in _AGENT_RUN_DONE:
            agg[tid] = "done"
        elif outcome in _AGENT_RUN_FAILED:
            agg[tid] = "failed"
    result = []
    for tid in sorted(agg):
        final = agg[tid]
        if final == "done":
            state, agent_status = "merged", "completed"
        elif final == "failed":
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


def _derive_terminal_state_from_issues_json(issues_json_str: str) -> str | None:
    """Apply the _any_failed rule to stored issues_json.

    Mirrors sprint_manager.py's terminal-state classification:
      any_failed = any(
          agent_status == "failed"
          or failure_reason
          or (status == "skipped" and category)
          for iss in issues
      )
    In issues_json, category is not stored, but failure_reason is always set
    alongside category, so checking agent_status and failure_reason is equivalent.

    Returns 'needs_rework' or 'ready_to_merge', or None when issues_json is empty.
    """
    try:
        issues = json.loads(issues_json_str or "[]")
    except (ValueError, TypeError):
        return None
    if not issues:
        return None
    any_failed = any(
        (iss.get("agent_status") or "").lower() == "failed"
        or bool(iss.get("failure_reason"))
        for iss in issues
    )
    return "needs_rework" if any_failed else "ready_to_merge"


def _outcome_reconcile_row(row: dict) -> dict | None:
    """Return a patch when issues_json ticket outcomes contradict the stored terminal state.

    Only downgrades ready_to_merge → needs_rework (the misclassification class
    from issue #2167: dead-lettered tickets that lacked GitHub needs-rework labels
    were invisible to the GitHub-signal check but are visible in issues_json).
    Never upgrades needs_rework → ready_to_merge — that is _github_reconcile_row's
    job, using the live GitHub signal.
    """
    stored = row.get("state") or ""
    canonical = _db().canonical_lifecycle(stored)
    if canonical != "ready_to_merge":
        return None
    derived = _derive_terminal_state_from_issues_json(row.get("issues_json") or "[]")
    if derived == "needs_rework":
        return {
            "state": "needs_rework",
            "end_reason": row.get("end_reason") or "outcome-reclassification",
            "_outcome_reason": "outcome-reclassification",
        }
    return None


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

    # Issue #1882: gate failures that land AFTER the last agent run (e.g. a lint
    # gate exhausting the fix-loop once coder+tester already passed) leave no
    # failed agent_runs outcome, so the union above would settle the ticket
    # done. The live GitHub needs-rework label — served from the zero-quota
    # issues mirror — is authoritative: a ticket carrying it is never settled
    # to merged/done here. (A ticket later fixed in a child sprint has the
    # label removed on GitHub, so this never resurrects stale failures.)
    _proj = (project or row.get("project") or "").strip()
    if _proj:
        for tid, cur in by_id.items():
            if (cur.get("state") or "").lower() != "merged" and \
               (cur.get("agent_status") or "").lower() not in ("completed", "done"):
                continue
            try:
                mirrored = _db().get_mirrored_issue(_proj, tid)
            except Exception:
                mirrored = None
            if not mirrored:
                continue
            names = {
                (lb.get("name") or "").lower()
                for lb in (mirrored.get("labels") or [])
                if isinstance(lb, dict)
            }
            if "needs-rework" in names:
                cur["state"] = "closed"
                cur["agent_status"] = "failed"
                changed = True

    if not changed:
        return False

    merged = [by_id[k] for k in sorted(by_id)]

    # Recompute denormalized counts using the canonical formula from
    # sprint_artifact_service so reconcile and materialize always agree.
    from .sprint_artifact_service import _compute_summary_counts  # noqa: PLC0415
    _counts = _compute_summary_counts(merged)
    settled_done = _counts["summary_settled_done"]
    uat_count = _counts["summary_uat_count"]
    failure_count = _counts["summary_failure_count"]

    new_json = _json.dumps(merged)
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
    # Additive: ticket-outcome check catches dead-lettered failures that the
    # GitHub-label check misses (no needs-rework label on the ticket itself).
    outcome_patch = _outcome_reconcile_row(row)
    effective_patch = patch or outcome_patch
    lifecycle_updated = False
    if effective_patch:
        # Issue #1697: a confirmed-orphan running sprint settles via
        # running->{ready_to_merge,needs_rework}, which db.py's edge guard
        # only allows for actor="manager" (running->terminal is otherwise
        # locked to the live manager process). actor="reconcile" here was a
        # silent no-op for every orphan case — _github_reconcile_row computed
        # the right patch, but transition_sprint_state rejected it every
        # time, for both the sweep AND the per-sprint button, since both call
        # this same function. The reconciler only reaches this branch after
        # confirming the manager process that WOULD have made this
        # transition is dead (_is_manager_pid_alive), so acting with
        # equivalent authority is the point, not a bypass. All other
        # reconcile transitions (terminal<->terminal) keep actor="reconcile"
        # per the original AC4 contract.
        _actor = "manager" if (row.get("state") or "") == "running" else "reconcile"
        _state_before = row.get("state") or ""
        lifecycle_updated = transition_sprint_state(
            label,
            effective_patch["state"],
            actor=_actor,
            end_reason=effective_patch.get("end_reason"),
            project=_eff_project,
            ended_at=effective_patch.get("ended_at"),
        )
        if lifecycle_updated and effective_patch["state"] == "completed":
            # Audit trail for sweep-driven completion of a superseded ancestor —
            # the silent, event-less lifecycle rewrite was half the hermes
            # zombie-lineage bug. Writer-side only: reconcile_preview calls
            # _github_reconcile_row dry-run and must stay side-effect free.
            try:
                import uuid  # noqa: PLC0415
                import server as _srv  # noqa: PLC0415
                _srv._emit_dashboard_event(
                    project=_eff_project,
                    type="sprint_lineage_superseded",
                    target=label,
                    detail={
                        "from_state": _state_before,
                        "end_reason": effective_patch.get("end_reason"),
                        "trigger": "reconcile_sweep",
                    },
                    action_id=str(uuid.uuid4()),
                )
            except Exception:
                pass
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


def _lineage_has_later_completed(label: str, project: str) -> bool:
    """True when a strictly-later member of *label*'s lineage is completed.

    Label-parsing based (parent_label can be NULL on rows terminalized without a
    run). Strictly-later mirrors _terminalize_superseded_orphans: sprint-1.3
    completed counts for sprint-1, 1.1 and 1.2, nothing counts for 1.3 itself.
    Project-scoped — sprints are keyed (label, project).
    """
    base, parts = _parse_sprint_label(label)
    if not base:
        return False
    for row in _db().list_sprints_lifecycle():
        if project and (row.get("project") or "") != project:
            continue
        if (row.get("state") or "").lower() != "completed":
            continue
        sib_base, sib_parts = _parse_sprint_label(row.get("label") or "")
        if sib_base == base and sib_parts > parts:
            return True
    return False


def _base_branch_merged_to_develop(label: str, project: str) -> bool:
    """Zero-quota check: is the lineage BASE branch a merged PR head?

    Always keys on the BASE label — intermediate members (e.g. sprint/sprint-1.2
    in a stacked chain) may never get their own develop PR; the base branch
    merge is what ships the whole chain. Uses the cached merged-PR-head set
    (github_client.list_merged_sprint_branches). Empty set or any error → False:
    never complete on doubt.
    """
    base, _parts = _parse_sprint_label(label)
    if not base:
        return False
    try:
        import github_client as gh  # noqa: PLC0415
        merged = gh.list_merged_sprint_branches(repo_name=project) or set()
    except Exception:
        return False
    return f"sprint/{base}" in merged


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
    eligible: list[dict] = []
    for row in rows:
        label = row.get("label") or ""
        if not label:
            continue
        if project and row.get("project") != project:
            continue
        state = row.get("state") or ""
        # Skip states the reconciler can't usefully change:
        #  • draft / planned / planning — not dispatched yet, nothing to settle.
        #  • completed / deleted — FINAL terminal states (completed only ever goes
        #    to deleted; deleted is the end). Re-checking them every History load
        #    burned ~4s on tangled lineages (mostly completed members) with no
        #    possible state change.
        # `running` IS included (issue #1697): _github_reconcile_row only acts on
        # a CONFIRMED orphan (PID file present AND that process is dead) — a live
        # PID or an absent PID file both return None there and the row is left
        # untouched. Previously orphan settling only ever happened via the
        # per-sprint Reconcile button because this sweep skipped running rows
        # outright.
        if state in ("draft", "planned", "planning", "completed", "deleted"):
            continue
        eligible.append(row)

    n = len(eligible)
    if n == 0:
        return updated

    # Rotate the scan window per project (issue #1690) so a project with more
    # than `limit` eligible rows still gets every row reconciled eventually —
    # a fixed "first N in list order" window let rows past #40 starve forever.
    start = _reconcile_cursor.get(project, 0) % n
    window = [eligible[(start + i) % n] for i in range(min(limit, n))]
    _reconcile_cursor[project] = (start + len(window)) % n

    for row in window:
        label = row["label"]
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
        return [lbl.get("name") for lbl in (iss.get("labels") or []) if isinstance(lbl, dict)]

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
    outcome_patch = _outcome_reconcile_row(row)             # no write
    effective_patch = patch or outcome_patch
    proposed_state = effective_patch["state"] if effective_patch else db_state

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
        "would_change": bool(effective_patch),
        "reason": (effective_patch or {}).get("end_reason"),
        "outcome_mismatch": outcome_patch is not None,
        "outcome_derived_state": _derive_terminal_state_from_issues_json(
            row.get("issues_json") or "[]"
        ),
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
        # Issue #1690: record the timestamp only on success. Stamping it
        # before the pass ran meant a transient failure (e.g. one gh call
        # rate-limited) blocked retries for the full 60s window too — now a
        # failed pass is retried on the very next History load.
        _log.warning("sprint reconcile failed for %s: %s", project, exc)
        return
    _last_reconcile_at[project] = now
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
