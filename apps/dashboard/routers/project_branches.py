"""Project branch endpoints extracted from server.py (issue #1250).

Handles stale-branch listing and single-branch deletion for a given
owner/repo pair. Previously lived as @app.get / @app.delete decorators
directly on the FastAPI app in server.py (issue #634).
"""

from __future__ import annotations

import json
import re
import subprocess

from fastapi import APIRouter, HTTPException

router = APIRouter(
    prefix="/api/projects/{owner}/{repo}/branches",
    tags=["project_branches"],
)

_PROTECTED_BRANCHES: frozenset[str] = frozenset(
    {"develop", "master", "main", "attachments"}
)
_STALE_BRANCH_RE = re.compile(r"^(feat|feature|sprint)/")


@router.get("/stale")
def get_stale_branches(owner: str, repo: str):
    """Return sorted list of feat/* or sprint/* branch names that are both
    merged (their PR was merged) and still exist on the remote.
    develop, master, main, and attachments are always excluded.
    """
    full_repo = f"{owner}/{repo}"

    merged_heads: set[str] = set()
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                full_repo,
                "--state",
                "merged",
                "--limit",
                "200",
                "--json",
                "headRefName",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for pr in json.loads(result.stdout or "[]"):
            head = pr.get("headRefName", "")
            if (
                _STALE_BRANCH_RE.match(head)
                and head not in _PROTECTED_BRANCHES
            ):
                merged_heads.add(head)
    except Exception:
        pass

    if not merged_heads:
        return []

    existing: set[str] = set()
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{full_repo}/branches",
                "--paginate",
                "--jq",
                ".[].name",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for name in result.stdout.splitlines():
            name = name.strip()
            if (
                name
                and _STALE_BRANCH_RE.match(name)
                and name not in _PROTECTED_BRANCHES
            ):
                existing.add(name)
    except Exception:
        pass

    return sorted(merged_heads & existing)


@router.delete("/{branch:path}", status_code=200)
def delete_project_branch(owner: str, repo: str, branch: str):
    """Delete a feat/* or sprint/* branch from the remote.
    Returns 400 for protected branches (develop/master/main/attachments) or
    branches not matching the allowed patterns.
    """
    if branch in _PROTECTED_BRANCHES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete protected branch: {branch!r}",
        )
    if not _STALE_BRANCH_RE.match(branch):
        raise HTTPException(
            status_code=400,
            detail=(
                "Only feat/*, feature/*, or sprint/* branches"
                f" may be deleted. Got: {branch!r}"
            ),
        )

    full_repo = f"{owner}/{repo}"
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{full_repo}/git/refs/heads/{branch}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete {branch!r}: {result.stderr.strip()}",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "branch": branch}
