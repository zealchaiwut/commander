"""Sprint run read/preview route handlers extracted from server.py (issue #1262).

GET read-only routes moved here:
  GET /api/sprints/{sprint_label}/branch-status
  GET /api/sprints/{sprint_label}/rerun/preview
  GET /api/sprints/{sprint_label}/rerun-preview

Shared server.py helpers are accessed via the deferred ``_server()`` import
to keep the circular-import guard intact.
"""
from __future__ import annotations

import json
import subprocess
from typing import Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["sprint_run"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


def _ticket_rerun_category(labels: set[str]) -> str:
    """Map a ticket's current labels to a rerun category string."""
    if labels & {"UAT", "UAT-approved"}:
        return "UAT"
    if "SIT" in labels:
        return "SIT"
    if "needs-rework" in labels or "need-rework" in labels:
        return "needs-rework"
    return "queued"


@router.get("/api/sprints/{sprint_label}/branch-status")
def get_sprint_branch_status(sprint_label: str, project: str):
    """Check if the sprint branch exists on GitHub.

    Uses gh CLI with a 2-second hard timeout; returns {exists, branch}.
    If the CLI times out or fails, returns exists=False so the UI shows
    the amber fallback without blocking page load.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        repo = srv.github_client.get_repo_for_operation(project)
    except Exception:
        return {"exists": False, "branch": f"sprint/{sprint_label}"}

    branch_name = f"sprint/{sprint_label}"
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/git/ref/heads/{branch_name}"],
            capture_output=True, text=True, timeout=2,
        )
        exists = result.returncode == 0
    except Exception:
        exists = False

    # Check for open PR from sprint branch → develop or master
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    pr_title: Optional[str] = None
    try:
        pr_result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", branch_name,
             "--state", "open", "--json", "number,url,title", "--limit", "1"],
            capture_output=True, text=True, timeout=3,
        )
        if pr_result.returncode == 0 and pr_result.stdout.strip():
            prs = json.loads(pr_result.stdout)
            if prs:
                pr_url = prs[0].get("url")
                pr_number = prs[0].get("number")
                pr_title = prs[0].get("title")
    except Exception as pr_err:
        srv._slog.warn(
            "sprint_pr_lookup_failed",
            f"PR lookup for {branch_name!r} failed: {pr_err}",
            branch=branch_name,
            error=str(pr_err),
        )

    return {"exists": exists, "branch": branch_name,
            "pr_url": pr_url, "pr_number": pr_number, "pr_title": pr_title}


@router.get("/api/sprints/{sprint_label}/rerun/preview")
def rerun_sprint_preview(sprint_label: str, project: str):
    """Return per-ticket rerun preview counts without executing anything (legacy).

    Response schema:
      { new_label, redispatch_count, tester_count, skip_count, by_ticket: [
          { issue_num, issue_title, action }  # action: dispatch_coder|dispatch_tester|skip
        ]
      }
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        sprint_issues = srv._get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    redispatch_count = 0
    tester_count = 0
    skip_count = 0
    by_ticket: list[dict] = []

    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        action, _ = srv._rerun_policy(current_labels)
        if action == "dispatch_coder":
            redispatch_count += 1
        elif action == "dispatch_tester":
            tester_count += 1
        else:
            skip_count += 1
        by_ticket.append({
            "issue_num": iss["number"],
            "issue_title": iss["title"],
            "action": action,
        })

    project_root = srv._project_root_path(project)
    existing_label_names = {lbl["name"] for lbl in srv.github_client.list_labels(repo_name=project)}
    new_label = srv._next_sprint_sublabel(sprint_label, existing_label_names, project_root)

    return {
        "new_label": new_label,
        "redispatch_count": redispatch_count,
        "tester_count": tester_count,
        "skip_count": skip_count,
        "by_ticket": by_ticket,
    }


@router.get("/api/sprints/{sprint_label}/rerun-preview")
def rerun_sprint_preview_v2(sprint_label: str, project: str):
    """Return per-ticket rerun preview with checkbox-ready ticket list.

    Response schema:
      {
        suggested_versioned_label: str,
        tickets: [{ number, title, category, checked }]
          # category: UAT | SIT | needs-rework | queued
          # checked: true for non-UAT tickets (default selection)
      }
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        sprint_issues = srv._get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    project_root = srv._project_root_path(project)
    existing_label_names = {lbl["name"] for lbl in srv.github_client.list_labels(repo_name=project)}
    suggested_versioned_label = srv._next_sprint_sublabel(
        sprint_label, existing_label_names, project_root,
    )

    _NON_WORK_LABELS_RR = {"sprint-summary", "docs", "documentation"}
    tickets = []
    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if current_labels & _NON_WORK_LABELS_RR:
            continue  # skip sprint-summary / docs tickets
        category = _ticket_rerun_category(current_labels)
        tickets.append({
            "number": iss["number"],
            "title": iss["title"],
            "category": category,
            "checked": category != "UAT",
        })

    return {
        "suggested_versioned_label": suggested_versioned_label,
        "tickets": tickets,
    }
