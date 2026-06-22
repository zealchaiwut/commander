"""Sprint finish route handlers extracted from server.py (issues #1260, #1261).

GET/read-only preview routes:
  GET /api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview
  GET /api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview

POST/write mutation routes (extracted in issue #1261):
  POST /api/projects/{owner}/{repo_name}/sprints/{label}/finish
  POST /api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete

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


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


@router.get("/api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview")
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
            for child_label in srv.children_of(base_label, project_root):
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

    _NON_WORK_LABELS_FP = {"sprint-summary", "docs", "documentation"}
    all_tickets = []
    uat_tickets = []
    non_uat_tickets = []
    for iss in sprint_issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        number = iss["number"]
        title = iss.get("title", "")
        if _NON_WORK_LABELS_FP & label_names:
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
            non_uat_tickets.append({"number": number, "title": title, "status": status})

    return {
        "all_tickets": all_tickets,
        "uat_tickets": uat_tickets,
        "non_uat_tickets": non_uat_tickets,
        "sprint_pr": sprint_pr,
        "merge_branches": merge_branches,
        "base_label": base_label,
        "next_sprint_label": next_sprint_label,
        "next_sprint_exists": next_sprint_exists,
        "conflict_error": conflict_error,
    }


@router.get("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview")
def get_sprint_bulk_complete_preview(owner: str, repo_name: str, label: str):
    """Preview tickets to close and member sprints to mark completed."""
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    base_label = srv._sprint_label_base(label)
    project_root = srv._project_root_path(repo)

    all_labels, sprint_issues = srv._bulk_complete_collect_issues(repo, project_root, base_label)

    unsettled_children = srv._bulk_complete_unsettled_children(project_root, base_label)
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
    # (merged ✓ / completed ✓) vs pending ○. merged = branch is NOT a pending
    # merge step; completed = lifecycle DB says so.
    pending_heads = {s.get("head") for s in merge_steps}
    members = []
    for lbl in complete_order:
        is_base = lbl == base_label
        parent = "develop" if is_base else srv._sprint_merge_parent_label(project_root, lbl)
        branch = srv._sprint_branch_name(lbl)
        completed = False
        try:
            row = srv.db.get_sprint(lbl, project=repo)
            completed = bool(row) and srv.db.canonical_lifecycle(row.get("state") or "") == "completed"
        except Exception:
            pass
        members.append({
            "label": lbl,
            "parent": parent,
            "is_base": is_base,
            "merged": branch not in pending_heads,
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


class BulkCompleteSprintBody(BaseModel):
    confirmed: bool
    selected_ticket_numbers: list[int] = []
    skip_issue_close: bool = False


# ── POST /finish ──────────────────────────────────────────────────────────────

@router.post("/api/projects/{owner}/{repo_name}/sprints/{label}/finish")
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
    child_labels = srv.children_of(base_label, project_root)
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
        if is_child:
            sprint_issues = srv._get_sprint_issues(repo, label)
        else:
            sprint_issues = srv._get_sprint_issues(repo, base_label)
            seen_nums: set[int] = {iss["number"] for iss in sprint_issues}
            for child_label in child_labels:
                try:
                    for iss in srv._get_sprint_issues(repo, child_label):
                        if iss["number"] not in seen_nums:
                            sprint_issues.append(iss)
                            seen_nums.add(iss["number"])
                except subprocess.CalledProcessError:
                    pass
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    closed = 0
    moved = 0
    errors: list[str] = []

    # 1. Merge sprint branches for this label only
    merge_errors, merge_pr_number = srv._merge_sprint_branches_for_label(repo, label)
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
        srv._sprint_db_set_state(
            lbl, repo, "completed",
            ended_at=_ended_at,
            end_reason="merge_sprint",
        )

    if merge_pr_number:
        try:
            srv.db.update_sprint_pr_number(base_label, merge_pr_number)
        except Exception:
            pass

    # Invalidate caches so board refreshes
    srv.github_client.invalidate("open_issues_body:")
    srv.github_client.invalidate("open_issues:")
    srv.github_client.invalidate("issues:")
    srv.github_client.invalidate("recent_closed:")

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
            "summary_issue_url": srv._read_sprint_summary_url(project_root, base_label),
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

@router.post("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete")
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
    srv._bulk_complete_assert_children_completed(project_root, base_label)

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
            srv._sprint_db_set_state(
                lbl, repo, "completed",
                ended_at=now,
                end_reason="bulk_complete",
            )
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


@router.post("/api/projects/{owner}/{repo_name}/sprints/{label}/complete-step")
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

    is_base = not srv._is_child_sprint_label(label)
    head = srv._sprint_branch_name(label)
    if is_base:
        base = "develop"
        target_name = "develop"
    else:
        parent_label = srv._sprint_merge_parent_label(project_root, label)
        base = srv._sprint_branch_name(parent_label)
        target_name = parent_label

    # 1) Merge this step (skip if already merged). Conflict → 409, stop & resume.
    merged = False
    if srv._branch_has_unmerged_commits(repo, head, base):
        ok, detail, _pr = srv._gh_merge_branch_via_pr(
            repo, head, base,
            f"Merge Sprint: {label} → {target_name}",
            delete_branch=not is_base,
        )
        if not ok:
            raise HTTPException(
                409,
                detail=f"Merge {label} → {target_name} failed — resolve the conflict and re-run: {detail}",
            )
        merged = True

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
            for iss in sprint_issues:
                try:
                    srv.github_client.close_issue(iss["number"], repo_name=repo, reason="completed")
                    closed_tickets.append(iss["number"])
                except Exception:
                    pass
        except Exception:
            pass

    # 4) Mark this sprint completed.
    now = datetime.now(timezone.utc).isoformat()
    try:
        srv._plan_json_set_state(project_root, label, "completed", ended_at=now, end_reason="merge_sprint")
        srv._sprint_db_set_state(label, repo, "completed", ended_at=now, end_reason="merge_sprint")
    except Exception as exc:
        raise HTTPException(500, detail=f"merged {label} but failed to mark completed: {exc}")

    for _k in ("open_issues_body:", "open_issues:", "issues:", "recent_closed:", "summary_issues:"):
        try:
            srv.github_client.invalidate(_k)
        except Exception:
            pass

    return {
        "ok": True,
        "label": label,
        "merged_into": target_name,
        "merged": merged,
        "closed_summary": closed_summary,
        "closed_tickets": closed_tickets,
    }
