"""Sprint finish route handlers extracted from server.py (issues #1260, #1261).

Canonical flat routes (issue #2065):
  GET  /api/sprints/{sprint_label}/finish-preview?project=
  GET  /api/sprints/{sprint_label}/bulk-complete-preview?project=
  POST /api/sprints/{sprint_label}/finish?project=
  POST /api/sprints/{sprint_label}/bulk-complete?project=
  POST /api/sprints/{sprint_label}/complete-step?project=
  GET  /api/sprints/{sprint_label}/conflict-status?project=

Deprecated nested aliases (kept for backward compatibility):
  GET  /api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview
  GET  /api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview
  POST /api/projects/{owner}/{repo_name}/sprints/{label}/finish
  POST /api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete
  POST /api/projects/{owner}/{repo_name}/sprints/{label}/complete-step
  GET  /api/projects/{owner}/{repo_name}/sprints/{label}/conflict-status

Shared server.py helpers are accessed via the deferred ``_server()`` import
to keep the circular-import guard intact.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

router = APIRouter(tags=["sprint_finish"])

from project_resolver import split_project  # noqa: E402

from .board_cache import invalidate_board  # noqa: E402

_NON_WORK_LABELS_FINISH = {"sprint-summary", "docs", "documentation"}


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


def _finish_rework_tickets(srv, sprint_issues: list[dict]) -> list[dict]:
    """Work tickets that have NOT reached UAT — shared by finish-preview and the
    finish POST guard (issue #1696) so "what would be closed unfinished" is
    computed identically in both places.
    """
    out: list[dict] = []
    for iss in sprint_issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        if _NON_WORK_LABELS_FINISH & label_names:
            continue
        if "UAT" in label_names:
            continue
        status = next(
            (lbl for lbl in sorted(label_names) if lbl in srv._FINISH_SPRINT_STATUS_LABELS and lbl != "UAT"),
            "queued",
        )
        out.append({"number": iss["number"], "title": iss.get("title", ""), "status": status})
    return out


def _finish_sprint_issues(srv, repo: str, label: str) -> list[dict]:
    """Fetch the sprint issue set for *label* (own issues, or base + all children
    when *label* is a base sprint) — shared by finish-preview, the finish POST
    guard, and finish-bg's guard (issue #1696) so all three agree on scope.
    """
    base_label = srv._sprint_label_base(label)
    is_child = srv._is_child_sprint_label(label)
    if is_child:
        return srv._get_sprint_issues(repo, label)
    project_root = srv._project_root_path(repo)
    sprint_issues = srv._get_sprint_issues(repo, base_label)
    seen_nums: set[int] = {iss["number"] for iss in sprint_issues}
    for child_label in srv.children_of(base_label, project_root, project=repo):
        try:
            for iss in srv._get_sprint_issues(repo, child_label):
                if iss["number"] not in seen_nums:
                    sprint_issues.append(iss)
                    seen_nums.add(iss["number"])
        except subprocess.CalledProcessError:
            pass
    return sprint_issues


@router.get(
    "/api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview",
    deprecated=True,
    description="Deprecated: use GET /api/sprints/{sprint_label}/finish-preview?project= instead (issue #2065).",
)
def get_sprint_finish_preview(owner: str, repo_name: str, label: str):
    """Return preview data for the Merge Sprint dialog.

    Returns: {
      all_tickets: [{number, title, category}],
      uat_tickets: [{number, title}],
      non_uat_tickets: [{number, title, status}],
      sprint_pr: {url, number, title} | null,
      merge_branches: [{head, base}],
      base_label: str,
      next_sprint_label: str,
      next_sprint_exists: bool,
      conflict_error: str | null,
    }
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    base_label = srv._sprint_label_base(label)
    project_root = srv._project_root_path(repo)

    next_num = srv._next_sprint_number(base_label)
    next_sprint_label = f"sprint-{next_num}"

    try:
        existing_sprints = srv.github_client.list_sprints(repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    next_sprint_exists = next_num in existing_sprints
    conflict_error: Optional[str] = None

    is_child = srv._is_child_sprint_label(label)
    try:
        if is_child:
            sprint_issues = srv._get_sprint_issues(repo, label)
        else:
            sprint_issues = srv._get_sprint_issues(repo, base_label)
            seen_nums: set[int] = {iss["number"] for iss in sprint_issues}
            for child_label in srv.children_of(base_label, project_root, project=repo):
                try:
                    for iss in srv._get_sprint_issues(repo, child_label):
                        if iss["number"] not in seen_nums:
                            sprint_issues.append(iss)
                            seen_nums.add(iss["number"])
                except subprocess.CalledProcessError:
                    pass
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    # Merge branches for this label only (child → base, or full chain on base)
    sprint_pr: Optional[dict] = None
    merge_branches: list[dict] = []
    for step in srv._finish_merge_steps(project_root, repo, label):
        merge_branches.append({
            "head": step["head"],
            "base": step["base"],
            "label": step["label"],
        })
    try:
        branch_name = srv._sprint_branch_name(label)
        pr_res = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", branch_name,
             "--state", "open", "--json", "number,url,title,baseRefName", "--limit", "1"],
            capture_output=True, text=True, timeout=4,
        )
        if pr_res.returncode == 0 and pr_res.stdout.strip():
            prs = json.loads(pr_res.stdout)
            if prs:
                sprint_pr = {
                    "url": prs[0].get("url"),
                    "number": prs[0].get("number"),
                    "title": prs[0].get("title"),
                    "base": prs[0].get("baseRefName"),
                }
    except Exception:
        pass

    all_tickets = []
    uat_tickets = []
    for iss in sprint_issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        number = iss["number"]
        title = iss.get("title", "")
        if _NON_WORK_LABELS_FINISH & label_names:
            all_tickets.append({"number": number, "title": title, "category": "sprint-summary"})
            continue
        if "UAT" in label_names:
            all_tickets.append({"number": number, "title": title, "category": "UAT"})
            uat_tickets.append({"number": number, "title": title})
        else:
            status = next(
                (lbl for lbl in sorted(label_names) if lbl in srv._FINISH_SPRINT_STATUS_LABELS and lbl != "UAT"),
                "queued",
            )
            all_tickets.append({"number": number, "title": title, "category": status})
    non_uat_tickets = _finish_rework_tickets(srv, sprint_issues)

    return {
        "all_tickets": all_tickets,
        "uat_tickets": uat_tickets,
        "non_uat_tickets": non_uat_tickets,
        # issue #1696: same list as non_uat_tickets, named for what the Merge
        # Sprint confirmation modal actually warns about — tickets that would
        # be closed without having reached UAT.
        "rework_tickets": non_uat_tickets,
        "sprint_pr": sprint_pr,
        "merge_branches": merge_branches,
        "base_label": base_label,
        "next_sprint_label": next_sprint_label,
        "next_sprint_exists": next_sprint_exists,
        "conflict_error": conflict_error,
    }


@router.get(
    "/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview",
    deprecated=True,
    description="Deprecated: use GET /api/sprints/{sprint_label}/bulk-complete-preview?project= instead (issue #2065).",
)
def get_sprint_bulk_complete_preview(owner: str, repo_name: str, label: str):
    """Preview tickets to close and member sprints to mark completed."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    base_label = srv._sprint_label_base(label)
    project_root = srv._project_root_path(repo)

    all_labels, sprint_issues = srv._bulk_complete_collect_issues(repo, project_root, base_label)

    # A base sprint with zero DB children is a clean, single-attempt sprint
    # (no rework ever needed) — not an error state (issue #1758). Do not
    # reject here; all_labels == [base_label] is handled fine below.

    unsettled_children = srv._bulk_complete_unsettled_children(project_root, base_label, project=repo)
    children_all_completed = not unsettled_children
    if not children_all_completed:
        raise HTTPException(
            409,
            detail=(
                "Bulk complete requires every child sprint run to finish — "
                f"still open: {', '.join(unsettled_children)}"
            ),
        )

    merge_steps = srv._bulk_complete_merge_steps(project_root, repo, base_label)

    # Per-step Complete order: deepest child first, base last. The modal loops
    # complete-step over this (idempotent), so already-merged-but-not-completed
    # members still get finalised — not just the ones with unmerged commits.
    complete_order = sorted(
        (lbl for lbl in all_labels if lbl != base_label),
        key=srv._sprint_label_sub_index,
        reverse=True,
    ) + [base_label]

    ticket_rows = srv._bulk_complete_ticket_rows(sprint_issues)

    # Per-member status for the modal's chain view: which steps are already done
    # (merged ✓ / completed ✓) vs pending ○.
    # AC3 (issue #2086): "branch not in pending_heads" alone is not sufficient —
    # a deleted-without-merging branch also has no unmerged commits and would
    # be reported as merged=True.  Distinguish the cases explicitly.
    pending_heads = {s.get("head") for s in merge_steps}
    members = []
    for lbl in complete_order:
        is_base = lbl == base_label
        if is_base:
            parent = "develop"
            parent_branch = "develop"
        else:
            parent = srv._sprint_merge_parent_label(project_root, lbl, project=repo)
            parent_branch = srv._sprint_branch_name(parent)
        branch = srv._sprint_branch_name(lbl)
        completed = False
        try:
            row = srv.db.get_sprint(lbl, project=repo)
            completed = bool(row) and srv.db.canonical_lifecycle(row.get("state") or "") == "completed"
        except Exception:
            pass

        if branch in pending_heads:
            merged = False
        elif srv._gh_branch_exists(repo, branch):
            # Branch still exists and has no unmerged commits → properly merged
            merged = True
        else:
            # Branch absent: check for a merged PR so we distinguish
            # "properly merged and pruned" from "deleted without merging".
            merged = srv._has_merged_pr(repo, branch, parent_branch)

        members.append({
            "label": lbl,
            "parent": parent,
            "is_base": is_base,
            "merged": merged,
            "completed": completed,
        })

    # Tickets grouped by the sprint that ran them (deepest child wins on reruns;
    # leftovers fall to the base). Lets the modal break the close-list down by
    # child sprint instead of one flat "close all N".
    by_num = {t["number"]: t for t in ticket_rows}
    assigned: set[int] = set()
    tickets_by_sprint: list[dict] = []
    for lbl in complete_order:
        nums: list[int] = []
        try:
            for r in srv.db.agent_runs_for_sprint(lbl, project=repo):
                n = r.get("issue_number")
                if n is None:
                    continue
                n = int(n)
                if n in by_num and n not in assigned:
                    nums.append(n)
                    assigned.add(n)
        except Exception:
            pass
        if nums:
            tickets_by_sprint.append({"label": lbl, "tickets": [by_num[n] for n in nums]})
    leftover = [t for t in ticket_rows if t["number"] not in assigned]
    if leftover:
        tickets_by_sprint.append({"label": base_label, "tickets": leftover})

    return {
        "all_tickets": ticket_rows,
        "member_labels": all_labels,
        "base_label": base_label,
        "child_count": len(all_labels) - 1,
        "merge_steps": merge_steps,
        "complete_order": complete_order,
        "members": members,
        "tickets_by_sprint": tickets_by_sprint,
    }


# ── Pydantic request models ───────────────────────────────────────────────────

class FinishSprintBody(BaseModel):
    confirmed: bool
    move_non_uat_to: str = ""
    selected_ticket_numbers: list[int] = []
    merge_pr: bool = False
    sprint_pr_url: Optional[str] = None
    # issue #1696: soft rework guard — set true only after the confirmation
    # modal shows the rework ticket list and the human explicitly overrides.
    confirm_rework: bool = False


class BulkCompleteSprintBody(BaseModel):
    confirmed: bool
    selected_ticket_numbers: list[int] = []
    skip_issue_close: bool = False


# ── POST /finish ──────────────────────────────────────────────────────────────

@router.post(
    "/api/projects/{owner}/{repo_name}/sprints/{label}/finish",
    deprecated=True,
    description="Deprecated: use POST /api/sprints/{sprint_label}/finish?project= instead (issue #2065).",
)
async def finish_sprint(owner: str, repo_name: str, label: str, body: FinishSprintBody):
    """Merge Sprint: merge sprint branch(es), close issues, keep labels.

    Child sprint (58.2): merges only that child branch into the base sprint branch.
    Base sprint (58): merges all children into base, then base into develop.
    Single human UAT sign-off — no per-ticket gate, no label stripping.
    """
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")

    srv = _server()

    if not srv._SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    project_root = srv._project_root_path(repo)
    base_label = srv._sprint_label_base(label)
    child_labels = srv.children_of(base_label, project_root, project=repo)
    is_child = srv._is_child_sprint_label(label)
    all_labels = [base_label, *child_labels]
    finish_labels = [label] if is_child else all_labels

    for lbl in finish_labels:
        if srv._is_sprint_running(project_root, lbl):
            raise HTTPException(
                409,
                detail=f"Sprint {lbl} is currently running — merge after the run completes",
            )

    try:
        sprint_issues = _finish_sprint_issues(srv, repo, label)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    # Soft rework guard (issue #1696): finish previously closed ALL sprint
    # issues including ones that never reached UAT, with no warning — unlike
    # bulk-complete/complete-step, which hard-refuse. Never close unfinished
    # work silently; require an explicit override once the modal has shown
    # the list. Nothing is merged or closed before this check.
    rework_tickets = _finish_rework_tickets(srv, sprint_issues)
    if rework_tickets and not body.confirm_rework:
        raise HTTPException(
            409,
            detail={
                "code": "rework_tickets_present",
                "message": (
                    f"{len(rework_tickets)} ticket(s) have not reached UAT and "
                    f"will be closed unfinished. Confirm to proceed anyway."
                ),
                "rework_tickets": rework_tickets,
            },
        )

    closed = 0
    moved = 0
    errors: list[str] = []

    # 1. Merge sprint branches for this label only
    merge_errors, merge_pr_number = srv._merge_sprint_branches_for_label(repo, label)

    # AC1/AC2 (issue #2086): abort before closing issues or marking completed when
    # the merge step failed or was a silent no-op.  Never close unfinished work.
    if merge_errors:
        raise HTTPException(
            409,
            detail={
                "code": "merge_failed",
                "message": (
                    "Sprint merge step failed — issues NOT closed and sprint NOT marked "
                    "completed. Resolve the merge failure and retry."
                ),
                "merge_errors": merge_errors,
            },
        )

    errors.extend(merge_errors)

    # Legacy: merge a specific open PR if the client still sends one (child end-of-run PR)
    if body.merge_pr and body.sprint_pr_url:
        try:
            merge_res = subprocess.run(
                ["gh", "pr", "merge", body.sprint_pr_url, "--repo", repo, "--merge", "--delete-branch"],
                capture_output=True, text=True, check=False,
            )
            if merge_res.returncode != 0:
                errors.append(f"PR merge failed: {merge_res.stderr.strip()}")
        except Exception as exc:
            errors.append(f"PR merge error: {exc}")

    _NON_WORK_LABELS_FS = {"sprint-summary", "docs", "documentation"}

    # 2. Close sprint issues — labels stay on for GitHub history queries
    for iss in sprint_issues:
        issue_num = iss["number"]
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        if _NON_WORK_LABELS_FS & label_names:
            continue
        try:
            srv.github_client.close_issue(issue_num, repo_name=repo, reason="completed")
            closed += 1
        except subprocess.CalledProcessError as exc:
            err_msg = exc.stderr.strip() if exc.stderr else str(exc)
            errors.append(f"#{issue_num}: {err_msg}")

    # 3. Mark finished sprint member(s) completed in plan.json + sprints DB
    _ended_at = datetime.now(timezone.utc).isoformat()
    for lbl in finish_labels:
        try:
            srv._plan_json_set_state(
                project_root, lbl, "completed",
                ended_at=_ended_at,
                end_reason="merge_sprint",
            )
        except Exception as exc:
            errors.append(f"plan.json update failed for {lbl}: {exc}")
        srv._sprint_db_mark_merged_completed(
            lbl, repo,
            ended_at=_ended_at,
            end_reason="merge_sprint",
        )

    if merge_pr_number:
        try:
            srv.db.update_sprint_pr_number(base_label, merge_pr_number, repo)
        except Exception:
            pass

    # Invalidate caches so board refreshes
    srv.github_client.invalidate("open_issues_body:")
    srv.github_client.invalidate("open_issues:")
    srv.github_client.invalidate("issues:")
    srv.github_client.invalidate("recent_closed:")
    invalidate_board(repo)

    await srv.broadcast({
        "type": "update",
        "event": {
            "event_type": "sprint_finished",
            "sprint_label": base_label,
        },
    })

    srv._emit_dashboard_event(
        project=repo,
        type="sprint_finished",
        target=base_label,
        detail={
            "sprint_id": base_label,
            "summary_issue_url": srv._read_sprint_summary_url(project_root, base_label, repo),
        },
        action_id=str(uuid.uuid4()),
    )

    result: dict = {
        "closed": closed,
        "moved": moved,
        "errors": errors,
        "next_sprint_label": f"sprint-{srv._next_sprint_number(base_label)}",
        "base_label": base_label,
    }
    status_code = 207 if errors else 200
    return JSONResponse(content=result, status_code=status_code)


# ── POST /bulk-complete ───────────────────────────────────────────────────────

@router.post(
    "/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete",
    deprecated=True,
    description="Deprecated: use POST /api/sprints/{sprint_label}/bulk-complete?project= instead (issue #2065).",
)
async def bulk_complete_sprint(owner: str, repo_name: str, label: str, body: BulkCompleteSprintBody):
    """Close UAT + summary issues across a lineage and mark every member completed.

    Requires the sprint branch merge chain to be complete (child → base → develop)
    before closing issues — run merge_steps from bulk-complete-preview first.
    """
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")

    srv = _server()

    if not srv._SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    project_root = srv._project_root_path(repo)
    base_label = srv._sprint_label_base(label)
    # Cheap local child-state gate first — a 409 here avoids N+1 GitHub calls
    # in _bulk_complete_collect_issues. Silently passes when no children exist,
    # so collect_issues still raises its own 400 for the "no child sprints" case.
    srv._bulk_complete_assert_children_completed(project_root, base_label, project=repo)

    all_labels, sprint_issues = srv._bulk_complete_collect_issues(repo, project_root, base_label)

    for lbl in all_labels:
        if srv._is_sprint_running(project_root, lbl):
            raise HTTPException(
                409,
                detail=f"Sprint {lbl} is currently running — bulk complete after the run finishes",
            )

    pending_merges = srv._bulk_complete_merge_pending(project_root, repo, base_label)
    if pending_merges:
        raise HTTPException(
            409,
            detail=(
                "Sprint branches are not fully merged to develop — complete merge steps first: "
                + "; ".join(pending_merges)
            ),
        )

    # Honesty guard: refuse to complete a lineage whose member sprints still have
    # open rework / SIT / unfinished work tickets — those need Re-run, not Complete.
    # Mirror-backed (_has_rework_tickets reads the local issues mirror), no GH calls.
    #
    # issue #2206: _has_rework_tickets alone reads False for a member whose
    # tickets are all closed WITHOUT ever being fixed (end_reason=
    # "ticket-failures" — perf-coach sprint-121's shape). Left unguarded,
    # Complete/Bulk Complete would sail through and overwrite that member's
    # DB state to "completed", destroying the classification #2197/#2199/
    # #2200/#2202/#2204/#2205 all depend on being preserved. Same fix shape
    # as those issues: also block on the DB row's own end_reason -- UNLESS a
    # later lineage member already completed (a genuine rerun fixed it, the
    # hermes-agent cascade-completion shape this function itself supports),
    # matching #2197's exact carve-out. Also allow through when every failed
    # ticket was independently fixed under a DIFFERENT, unrelated later
    # sprint and this sprint's own branch already merged (issue #2208 —
    # perf-coach sprint-121's #1420/#1525, fixed via sprint-122/122.1, not a
    # sprint-121 rerun child).
    try:
        from routers.sprint_reconcile_service import (
            _lineage_has_later_completed,
            _failed_tickets_resolved_by_later_run,
            _base_branch_merged_to_develop,
        )
    except Exception:
        _lineage_has_later_completed = None
        _failed_tickets_resolved_by_later_run = None
        _base_branch_merged_to_develop = None
    _has_rework = getattr(srv, "_has_rework_tickets", None)
    if _has_rework is not None:
        rework_members: list[str] = []
        for lbl in all_labels:
            try:
                _row = srv.db.get_sprint(lbl, project=repo)
                _resolved_elsewhere = bool(
                    _failed_tickets_resolved_by_later_run
                    and _base_branch_merged_to_develop
                    and _row
                    and _failed_tickets_resolved_by_later_run(_row)
                    and _base_branch_merged_to_develop(lbl, repo)
                )
                _end_reason_blocks = (
                    (_row or {}).get("end_reason") == "ticket-failures"
                    and not (_lineage_has_later_completed and _lineage_has_later_completed(lbl, repo))
                    and not _resolved_elsewhere
                )
                if _has_rework(lbl, repo) or _end_reason_blocks:
                    rework_members.append(lbl)
            except Exception:
                pass
        if rework_members:
            raise HTTPException(
                409,
                detail=(
                    "Cannot complete — needs-rework/SIT tickets remain in: "
                    + ", ".join(sorted(rework_members))
                    + ". Re-run those sprints first."
                ),
            )

    selected = set(body.selected_ticket_numbers) if body.selected_ticket_numbers else None
    closed = 0
    errors: list[str] = []

    if not body.skip_issue_close:
        for iss in sprint_issues:
            issue_num = iss["number"]
            if selected is not None and issue_num not in selected:
                continue
            try:
                srv.github_client.close_issue(issue_num, repo_name=repo, reason="completed")
                closed += 1
            except subprocess.CalledProcessError as exc:
                err_msg = exc.stderr.strip() if exc.stderr else str(exc)
                errors.append(f"#{issue_num}: {err_msg}")

    completed = 0
    now = datetime.now(timezone.utc).isoformat()
    for lbl in all_labels:
        plan = srv._read_plan_json(project_root, lbl)
        if plan and (plan.get("state") or "").lower() == "deleted":
            continue
        try:
            srv._plan_json_set_state(
                project_root, lbl, "completed",
                ended_at=now,
                end_reason="bulk_complete",
            )
            ok = srv._sprint_db_mark_merged_completed(
                lbl, repo,
                ended_at=now,
                end_reason="bulk_complete",
            )
            if ok is False:
                errors.append(
                    f"{lbl}: DB transition to completed rejected by state machine"
                )
                continue
            completed += 1
        except Exception as exc:
            errors.append(f"plan.json update failed for {lbl}: {exc}")

    srv.github_client.invalidate("open_issues_body:")
    srv.github_client.invalidate("open_issues:")
    srv.github_client.invalidate("issues:")
    srv.github_client.invalidate("recent_closed:")
    srv.github_client.invalidate("summary_issues:")

    await srv.broadcast({
        "type": "update",
        "event": {
            "event_type": "sprint_bulk_completed",
            "sprint_label": base_label,
        },
    })

    srv._emit_dashboard_event(
        project=repo,
        type="sprint_bulk_completed",
        target=base_label,
        detail={
            "sprint_id": base_label,
            "member_labels": all_labels,
            "closed": closed,
            "completed": completed,
        },
        action_id=str(uuid.uuid4()),
    )

    invalidate_board(repo)
    result: dict = {
        "closed": closed,
        "completed": completed,
        "errors": errors,
        "base_label": base_label,
        "member_labels": all_labels,
    }
    status_code = 207 if errors else 200
    return JSONResponse(content=result, status_code=status_code)


class CompleteStepBody(BaseModel):
    confirmed: bool = True


# Lineage members in these canonical states are superseded once the base branch
# merges to develop: their own run didn't ship, but their work reached develop
# via a descendant. Stored failed/cancelled canonicalize to needs_rework.
_CASCADE_SOURCE_CANONICAL = frozenset({"needs_rework", "ready_to_merge", "partial_finished"})


def _cascade_complete_lineage(
    srv, repo: str, project_root: Path, base_label: str, exclude: str, ended_at: str,
) -> tuple[list[str], list[str]]:
    """After the BASE branch merged to develop, complete leftover lineage members.

    Without this, superseded ancestors (e.g. sprint-1/1.1/1.2 after 1.3 shipped)
    stay needs_rework forever and the reconcile sweep later rebrands them
    ready_to_merge — the hermes-agent zombie-lineage bug.
    """
    cascaded: list[str] = []
    errors: list[str] = []
    for lbl in srv.children_of(base_label, project_root, project=repo):
        if lbl == exclude:
            continue
        row = srv.db.get_sprint(lbl, project=repo)
        if not row:
            # plan.json-only orphans are handled by _terminalize_superseded_orphans
            continue
        canonical = srv.db.canonical_lifecycle(row.get("state") or "")
        if canonical not in _CASCADE_SOURCE_CANONICAL:
            continue
        if srv._is_sprint_running(project_root, lbl):
            continue
        try:
            srv._plan_json_set_state(
                project_root, lbl, "completed", ended_at=ended_at, end_reason="superseded",
            )
        except Exception as exc:
            errors.append(f"{lbl}: plan.json update failed: {exc}")
        ok = srv._sprint_db_mark_merged_completed(
            lbl, repo, ended_at=ended_at, end_reason="superseded",
        )
        if ok is False:
            errors.append(f"{lbl}: DB transition to completed rejected by state machine")
        else:
            cascaded.append(lbl)
    return cascaded, errors


@router.post(
    "/api/projects/{owner}/{repo_name}/sprints/{label}/complete-step",
    deprecated=True,
    description="Deprecated: use POST /api/sprints/{sprint_label}/complete-step?project= instead (issue #2065).",
)
def complete_sprint_step(owner: str, repo_name: str, label: str, body: CompleteStepBody):
    """Complete ONE lineage step and finalise that sprint.

    Merges this sprint into its IMMEDIATE parent (or develop for the base),
    closes this sprint's summary issue, and marks it completed. Single "Complete"
    calls this once (e.g. 94.4 → 94.3); Bulk complete loops it deepest-first, then
    runs the base (94 → develop). The base step also closes the lineage's work
    tickets (they ship to develop here).

    Idempotent — an already-merged step skips the merge; an already-completed
    sprint is a no-op. On a merge conflict returns 409 so the caller STOPS;
    resolve the conflict and re-run (merged/closed steps are skipped on resume).
    """
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    project_root = srv._project_root_path(repo)

    if srv._is_sprint_running(project_root, label):
        raise HTTPException(409, detail=f"Sprint {label} is currently running — complete after it finishes")

    # Honesty guard: a sprint with open rework / SIT / unfinished work tickets is
    # not done — Re-run it, don't Complete. Mirror-backed, no GitHub calls.
    #
    # issue #2206: also block when the DB row's own end_reason is
    # "ticket-failures" — _has_rework_tickets alone reads False once a
    # failed sprint's tickets are all closed without being fixed, which
    # would otherwise let Complete overwrite the DB state and destroy that
    # classification. Same fix shape as #2197/#2199/#2200/#2202/#2204/#2205.
    # Carve-out for a genuine rerun fix (a later lineage member already
    # completed — the hermes-agent cascade shape below), matching #2197.
    # Also allow through when every failed ticket was independently fixed
    # under a DIFFERENT, unrelated later sprint and this sprint's own branch
    # already merged (issue #2208).
    try:
        from routers.sprint_reconcile_service import (
            _lineage_has_later_completed,
            _failed_tickets_resolved_by_later_run,
            _base_branch_merged_to_develop,
        )
    except Exception:
        _lineage_has_later_completed = None
        _failed_tickets_resolved_by_later_run = None
        _base_branch_merged_to_develop = None
    _has_rework = getattr(srv, "_has_rework_tickets", None)
    if _has_rework is not None:
        try:
            _rework = _has_rework(label, repo)
        except Exception:
            _rework = False
        try:
            _row = srv.db.get_sprint(label, project=repo)
            _resolved_elsewhere = bool(
                _failed_tickets_resolved_by_later_run
                and _base_branch_merged_to_develop
                and _row
                and _failed_tickets_resolved_by_later_run(_row)
                and _base_branch_merged_to_develop(label, repo)
            )
            if (_row or {}).get("end_reason") == "ticket-failures" and not (
                (_lineage_has_later_completed and _lineage_has_later_completed(label, repo))
                or _resolved_elsewhere
            ):
                _rework = True
        except Exception:
            pass
        if _rework:
            raise HTTPException(
                409,
                detail=(
                    f"Cannot complete {label} — it still has needs-rework/SIT tickets. "
                    "Re-run it first."
                ),
            )

    is_base = not srv._is_child_sprint_label(label)
    head = srv._sprint_branch_name(label)
    if is_base:
        base = "develop"
        target_name = "develop"
    else:
        parent_label = srv._sprint_merge_parent_label(project_root, label, project=repo)
        base = srv._sprint_branch_name(parent_label)
        target_name = parent_label

    # AC1 (issue #1934): when the immediate parent branch has been deleted (merged
    # and pruned), _branch_has_unmerged_commits returns False for the missing base,
    # producing a silent no-op while the child's commits remain stranded.  Walk up
    # the lineage to find the next surviving ancestor branch and retarget there.
    if not is_base and srv._gh_branch_exists(repo, head) and not srv._gh_branch_exists(repo, base):
        _current = parent_label
        _visited: set[str] = set()
        _MAX_LINEAGE_DEPTH = 50
        while True:
            if _current in _visited or len(_visited) >= _MAX_LINEAGE_DEPTH:
                raise HTTPException(
                    409,
                    detail=(
                        f"Sprint lineage is self-referential or too deep (depth {len(_visited)}, "
                        f"stopped at {_current!r}). The ancestry chain from {parent_label!r} "
                        "did not converge to a base sprint within the allowed depth. "
                        "Inspect and repair the sprint lineage manually."
                    ),
                )
            _visited.add(_current)
            if not srv._is_child_sprint_label(_current):
                # _current is the base sprint label — check its branch
                _base_sprint_br = srv._sprint_branch_name(_current)
                if srv._gh_branch_exists(repo, _base_sprint_br):
                    base = _base_sprint_br
                    target_name = _current
                else:
                    base = "develop"
                    target_name = "develop"
                break
            _grandparent = srv._sprint_merge_parent_label(project_root, _current, project=repo)
            _gp_branch = srv._sprint_branch_name(_grandparent)
            if srv._gh_branch_exists(repo, _gp_branch):
                base = _gp_branch
                target_name = _grandparent
                break
            _current = _grandparent

    # AC2 (issue #1934): refuse the base complete-step while any child sprint
    # still has a live branch (running or with unmerged commits).  A deleted child
    # branch means its work already merged up; a live branch means it has not.
    if is_base:
        for _child_lbl in srv.children_of(label, project_root, project=repo):
            _child_br = srv._sprint_branch_name(_child_lbl)
            if srv._gh_branch_exists(repo, _child_br):
                if srv._is_sprint_running(project_root, _child_lbl):
                    raise HTTPException(
                        409,
                        detail=(
                            f"Child sprint {_child_lbl} is still running — "
                            f"complete {_child_lbl} before completing base {label}"
                        ),
                    )
                _cp_lbl = srv._sprint_merge_parent_label(project_root, _child_lbl, project=repo)
                _cp_br = srv._sprint_branch_name(_cp_lbl)
                _eff_base = _cp_br if srv._gh_branch_exists(repo, _cp_br) else "develop"
                if srv._branch_has_unmerged_commits(repo, _child_br, _eff_base):
                    raise HTTPException(
                        409,
                        detail=(
                            f"Child sprint {_child_lbl} has unmerged commits on "
                            f"{_child_br} — run complete-step on {_child_lbl} first"
                        ),
                    )

    # 1) Merge this step (skip if already merged). Conflict → 409, stop & resume.
    merged = False
    if srv._branch_has_unmerged_commits(repo, head, base):
        conflict_info: dict = {}
        ok, detail, _pr = srv._gh_merge_branch_via_pr(
            repo, head, base,
            f"Merge Sprint: {label} → {target_name}",
            delete_branch=not is_base,
            conflict_detail_out=conflict_info,
        )
        if not ok:
            if conflict_info.get("code") == "merge_conflict_needs_human":
                try:
                    srv._sprint_set_conflict_blocked(
                        project_root, label, conflict_info.get("files", []),
                    )
                except Exception:
                    pass
                raise HTTPException(
                    409,
                    detail={
                        "code": "merge_conflict_needs_human",
                        "files": conflict_info.get("files", []),
                        "label": label,
                        "message": (
                            f"Merge {label} → {target_name} requires human conflict "
                            f"resolution — auto-resolve is not safe for these files"
                        ),
                    },
                )
            raise HTTPException(
                409,
                detail=f"Merge {label} → {target_name} failed — resolve the conflict and re-run: {detail}",
            )
        merged = True
        # Clear any stale conflict-blocked flag from a previous failed attempt
        try:
            srv._sprint_clear_conflict_blocked(project_root, label)
        except Exception:
            pass

    # 2) Close this sprint's summary issue(s).
    closed_summary: list[int] = []
    try:
        for iss in srv._open_summary_issues_for_labels(repo, [label]):
            try:
                srv.github_client.close_issue(iss["number"], repo_name=repo, reason="completed")
                closed_summary.append(iss["number"])
            except Exception:
                pass
    except Exception:
        pass

    # 3) Base step ships to develop → close the lineage's work tickets too.
    closed_tickets: list[int] = []
    if is_base:
        try:
            _labels, sprint_issues = srv._bulk_complete_collect_issues(repo, project_root, label)
            # AC3 (issue #1934): skip tickets whose sprint label belongs to a child
            # with a live branch (commits not yet in develop).  Defense-in-depth:
            # AC2 normally prevents reaching here when children are unmerged.
            _unmerged_child_labels: set[str] = {
                cl for cl in srv.children_of(label, project_root, project=repo)
                if srv._gh_branch_exists(repo, srv._sprint_branch_name(cl))
            }
            for iss in sprint_issues:
                _iss_label_names = {lbl["name"] for lbl in iss.get("labels", [])}
                if _unmerged_child_labels & _iss_label_names:
                    continue
                try:
                    srv.github_client.close_issue(iss["number"], repo_name=repo, reason="completed")
                    closed_tickets.append(iss["number"])
                except Exception:
                    pass
        except Exception:
            pass

    # 4) Mark this sprint completed. actor="reconcile" so a superseded ancestor
    # still in needs_rework (whose lineage merged) can complete via the B2 edge.
    # _sprint_db_set_state returns False on a silent state-machine rejection —
    # surface it instead of reporting a false success.
    now = datetime.now(timezone.utc).isoformat()
    try:
        srv._plan_json_set_state(project_root, label, "completed", ended_at=now, end_reason="merge_sprint")
        db_ok = srv._sprint_db_mark_merged_completed(
            label, repo, ended_at=now, end_reason="merge_sprint",
        )
    except Exception as exc:
        raise HTTPException(500, detail=f"merged {label} but failed to mark completed: {exc}")
    if db_ok is False:
        raise HTTPException(
            500,
            detail=f"merged {label} but the DB transition to completed was rejected by the "
                   f"state machine (illegal edge) — lifecycle left unchanged",
        )

    # 5) Base step: the whole lineage's work is now in develop — complete the
    # superseded ancestors/siblings too (never fail the request here; the merge
    # already landed, so surface errors in the payload instead).
    cascaded: list[str] = []
    cascade_errors: list[str] = []
    if is_base:
        cascaded, cascade_errors = _cascade_complete_lineage(
            srv, repo, project_root, label, exclude=label, ended_at=now,
        )
        if cascaded:
            try:
                srv._emit_dashboard_event(
                    project=repo,
                    type="sprint_lineage_superseded",
                    target=label,
                    detail={
                        "base_label": label,
                        "cascaded": cascaded,
                        "end_reason": "superseded",
                        "trigger": "complete_step",
                    },
                    action_id=str(uuid.uuid4()),
                )
            except Exception:
                pass

    for _k in ("open_issues_body:", "open_issues:", "issues:", "recent_closed:", "summary_issues:"):
        try:
            srv.github_client.invalidate(_k)
        except Exception:
            pass

    invalidate_board(repo)

    return {
        "ok": True,
        "label": label,
        "merged_into": target_name,
        "merged": merged,
        "closed_summary": closed_summary,
        "closed_tickets": closed_tickets,
        "cascaded_completed": cascaded,
        "cascade_errors": cascade_errors,
    }


@router.get(
    "/api/projects/{owner}/{repo_name}/sprints/{label}/conflict-status",
    deprecated=True,
    description="Deprecated: use GET /api/sprints/{sprint_label}/conflict-status?project= instead (issue #2065).",
)
def get_sprint_conflict_status(owner: str, repo_name: str, label: str):
    """Return the merge-conflict-blocked state for a sprint (issue #1898).

    Autonomous callers (Hermes loop) poll this to discover which sprints are
    parked on a human-needed merge conflict so they can skip and continue with
    other sprints instead of hanging.

    Response:
      200 {label, blocked: false}                         — no conflict block
      200 {label, blocked: true, code, files, at}        — blocked on conflict
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    project_root = srv._project_root_path(repo)

    blocked_info = srv._sprint_get_conflict_blocked(project_root, label)
    if blocked_info:
        return {
            "label": label,
            "blocked": True,
            "code": "merge_conflict_needs_human",
            "files": blocked_info.get("files", []),
            "at": blocked_info.get("at"),
        }
    return {"label": label, "blocked": False}


# ── Canonical flat routes (issue #2065) ──────────────────────────────────────
# These are the primary addresses. The nested /api/projects/…/sprints/… paths
# above are kept as deprecated aliases so existing callers continue to work.
# project= must be in 'owner/repo' format (e.g. zealchaiwut/commander).

@router.get("/api/sprints/{sprint_label}/finish-preview")
def get_sprint_finish_preview_flat(sprint_label: str, project: str):
    """Canonical: GET /api/sprints/{sprint_label}/finish-preview?project=owner/repo"""
    owner, repo_name = split_project(project)
    return get_sprint_finish_preview(owner, repo_name, sprint_label)


@router.get("/api/sprints/{sprint_label}/bulk-complete-preview")
def get_sprint_bulk_complete_preview_flat(sprint_label: str, project: str):
    """Canonical: GET /api/sprints/{sprint_label}/bulk-complete-preview?project=owner/repo"""
    owner, repo_name = split_project(project)
    return get_sprint_bulk_complete_preview(owner, repo_name, sprint_label)


@router.post("/api/sprints/{sprint_label}/finish")
async def finish_sprint_flat(sprint_label: str, project: str, body: FinishSprintBody):
    """Canonical: POST /api/sprints/{sprint_label}/finish?project=owner/repo"""
    owner, repo_name = split_project(project)
    return await finish_sprint(owner, repo_name, sprint_label, body)


@router.post("/api/sprints/{sprint_label}/bulk-complete")
async def bulk_complete_sprint_flat(sprint_label: str, project: str, body: BulkCompleteSprintBody):
    """Canonical: POST /api/sprints/{sprint_label}/bulk-complete?project=owner/repo"""
    owner, repo_name = split_project(project)
    return await bulk_complete_sprint(owner, repo_name, sprint_label, body)


@router.post("/api/sprints/{sprint_label}/complete-step")
def complete_sprint_step_flat(sprint_label: str, project: str, body: CompleteStepBody):
    """Canonical: POST /api/sprints/{sprint_label}/complete-step?project=owner/repo"""
    owner, repo_name = split_project(project)
    return complete_sprint_step(owner, repo_name, sprint_label, body)


@router.get("/api/sprints/{sprint_label}/conflict-status")
def get_sprint_conflict_status_flat(sprint_label: str, project: str):
    """Canonical: GET /api/sprints/{sprint_label}/conflict-status?project=owner/repo"""
    owner, repo_name = split_project(project)
    return get_sprint_conflict_status(owner, repo_name, sprint_label)
