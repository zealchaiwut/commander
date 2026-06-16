"""Sprint finish preview route handlers extracted from server.py (issue #1260).

GET/read-only preview routes owned by this module:
  GET /api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview
  GET /api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview

Write/mutation paths (POST finish, POST bulk-complete) remain in server.py.
Shared server.py helpers are accessed via the deferred ``_server()`` import
to keep the circular-import guard intact.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

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

    return {
        "all_tickets": srv._bulk_complete_ticket_rows(sprint_issues),
        "member_labels": all_labels,
        "base_label": base_label,
        "child_count": len(all_labels) - 1,
        "merge_steps": merge_steps,
    }
