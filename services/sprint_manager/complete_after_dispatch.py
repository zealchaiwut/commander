"""Complete-after-dispatch helper (issue #2357).

After a green API dispatch that opened a sprint→develop PR, merge that PR
(and optionally run the Finish close/UAT path) in one HTTP call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from services.sprint_manager.dispatch_runner import list_dispatch_runs


def find_successful_sprint_pr(
    project_root: Path,
    sprint_label: str,
    repo: str,
) -> Optional[dict]:
    """Return the newest done dispatch run that recorded a sprint_pr_number."""
    done = list_dispatch_runs(
        project_root,
        statuses={"done"},
        sprint_label=sprint_label,
        repo=repo,
    )
    # Prefer newest finished_at
    done.sort(key=lambda r: r.get("finished_at") or r.get("started_at") or "", reverse=True)
    for run in done:
        pr = run.get("sprint_pr_number")
        if pr:
            return {
                "run_id": run.get("run_id"),
                "sprint_pr_number": int(pr),
                "sprint_pr_url": f"https://github.com/{repo}/pull/{int(pr)}",
                "sprint_label": sprint_label,
                "tickets": list(run.get("tickets") or []),
            }
    return None


def build_complete_preview(
    *,
    project_root: Path,
    sprint_label: str,
    repo: str,
    uat_issues: list[dict] | None = None,
) -> dict:
    """Dry-run payload: PR + UAT issue numbers that would be closed."""
    pr_info = find_successful_sprint_pr(project_root, sprint_label, repo)
    if pr_info is None:
        return {"ok": False, "reason": "no_sprint_pr", "sprint_pr": None, "uat_tickets": []}
    uat = uat_issues or []
    return {
        "ok": True,
        "reason": None,
        "sprint_pr": {
            "number": pr_info["sprint_pr_number"],
            "url": pr_info["sprint_pr_url"],
            "run_id": pr_info["run_id"],
        },
        "uat_tickets": [
            {"number": i.get("number"), "title": i.get("title", "")}
            for i in uat
        ],
        "tickets": pr_info.get("tickets") or [],
    }


def complete_after_dispatch(
    *,
    project_root: Path,
    sprint_label: str,
    repo: str,
    preview: bool = False,
    uat_signoff: bool = False,
    finish_fn: Optional[Callable[..., Any]] = None,
    merge_pr_fn: Optional[Callable[[str, str], tuple[bool, str]]] = None,
    list_uat_fn: Optional[Callable[..., list[dict]]] = None,
) -> dict:
    """Merge the dispatch-opened sprint PR; optionally Finish/UAT-close.

    ``preview=True`` never mutates. Without a successful sprint PR → caller
    should map ``ok: False`` / ``reason: no_sprint_pr`` to HTTP 409.
    """
    uat_issues: list[dict] = []
    if list_uat_fn is not None:
        try:
            uat_issues = list(list_uat_fn(repo_name=repo) or [])
        except Exception:
            uat_issues = []

    preview_payload = build_complete_preview(
        project_root=project_root,
        sprint_label=sprint_label,
        repo=repo,
        uat_issues=uat_issues,
    )
    if preview:
        return {"preview": True, **preview_payload}

    if not preview_payload["ok"]:
        return {"preview": False, **preview_payload}

    pr = preview_payload["sprint_pr"]
    result: dict = {
        "preview": False,
        "ok": True,
        "sprint_pr": pr,
        "merged": False,
        "uat_signoff": uat_signoff,
        "finish_result": None,
        "errors": [],
    }

    if uat_signoff:
        if finish_fn is None:
            result["ok"] = False
            result["errors"].append("finish_fn not provided")
            return result
        finish_result = finish_fn(
            sprint_label=sprint_label,
            repo=repo,
            sprint_pr_url=pr["url"],
            merge_pr=True,
        )
        result["finish_result"] = finish_result
        result["merged"] = True
        return result

    # Merge-only path
    if merge_pr_fn is None:
        result["ok"] = False
        result["errors"].append("merge_pr_fn not provided")
        return result
    ok, detail = merge_pr_fn(pr["url"], repo)
    result["merged"] = ok
    if not ok:
        result["ok"] = False
        result["errors"].append(detail)
    else:
        result["detail"] = detail
    return result
