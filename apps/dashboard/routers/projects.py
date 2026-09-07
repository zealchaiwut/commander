"""Project route handlers extracted from server.py (issue #1267).

Routes owned by this module:
  GET    /api/projects
  POST   /api/projects
  DELETE /api/projects/{owner}/{repo_name}
  GET    /api/projects/{project}/running-sprint
  POST   /api/projects/{owner}/{repo_name}/approve-batch
  POST   /api/projects/init
  GET    /api/project-details
  POST   /api/projects/{owner}/{repo_name}/sprint-branch-merge

Pydantic models:
  NewProjectBody, InitProjectBody, RemoveProjectBody, SprintBranchMergeBody

Shared server.py state (broadcast, _BACKUP_AVAILABLE, _backup_module,
github_client, _is_sprint_running) accessed via deferred _server() import.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import projects as projects_module  # noqa: E402

from project_resolver import resolve_project_path as _project_root_path  # noqa: E402

router = APIRouter(tags=["projects"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _gh_error(e: subprocess.CalledProcessError) -> HTTPException:
    return _server()._gh_error(e)


def _gh_branch_exists(repo: str, branch: str) -> bool:
    try:
        from urllib.parse import quote
        ref = quote(branch, safe="")
        res = subprocess.run(
            ["gh", "api", f"repos/{repo}/branches/{ref}", "--jq", ".name"],
            capture_output=True, text=True, timeout=15,
        )
        return res.returncode == 0
    except Exception:
        return False


def _gh_merge_branch_via_pr(
    repo: str,
    head: str,
    base: str,
    title: str,
    delete_branch: bool = True,
) -> tuple[bool, str, int | None]:
    """Create (or reuse) a PR head→base and merge it. Returns (ok, detail, pr_num)."""
    pr_url: Optional[str] = None
    try:
        pr_res = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", head, "--base", base,
             "--state", "open", "--json", "url", "--limit", "1"],
            capture_output=True, text=True, timeout=30,
        )
        if pr_res.returncode == 0 and pr_res.stdout.strip():
            prs = json.loads(pr_res.stdout)
            if prs:
                pr_url = prs[0].get("url")
        if not pr_url:
            create = subprocess.run(
                ["gh", "pr", "create", "--repo", repo, "--base", base, "--head", head,
                 "--title", title, "--body", f"Merge Sprint: `{head}` → `{base}`."],
                capture_output=True, text=True, timeout=60,
            )
            if create.returncode != 0:
                stderr = create.stderr.strip()
                m = re.search(r"https://github\.com/\S+", stderr)
                if m and ("already exists" in stderr or "already have" in stderr.lower()):
                    pr_url = m.group(0)
                else:
                    return False, stderr or "PR create failed", None
            else:
                pr_url = create.stdout.strip()
        merge_args = ["gh", "pr", "merge", pr_url, "--repo", repo, "--merge"]
        if delete_branch:
            merge_args.append("--delete-branch")
        merge_res = subprocess.run(merge_args, capture_output=True, text=True, timeout=120)
        if merge_res.returncode != 0:
            return False, merge_res.stderr.strip() or "PR merge failed", None
        return True, pr_url or f"{head} → {base}", _server()._parse_pr_number_from_url(pr_url)
    except Exception as exc:
        return False, str(exc), None


# ── request models ─────────────────────────────────────────────────────────────

class NewProjectBody(BaseModel):
    repo_url: str
    icon: Optional[str] = "ti-folder"
    color: Optional[str] = "gray"


class InitProjectBody(BaseModel):
    repo_name: str
    projects_dir: str = "~/dev"
    nested: bool = False
    skip_uat: bool = False
    from_existing: bool = False


class RemoveProjectBody(BaseModel):
    delete_local_folders: bool = False
    delete_github_repo: bool = False


class SprintBranchMergeBody(BaseModel):
    confirmed: bool
    head: str
    base: str
    title: str = ""
    delete_branch: bool = True


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/api/projects")
def get_projects():
    try:
        agents = db.get_agents()
        return projects_module.get_all_projects(agents)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/api/projects", status_code=201)
def add_project(body: NewProjectBody):
    srv = _server()
    try:
        new_proj = projects_module.add_project(
            repo=body.repo_url,
            icon=body.icon or "ti-folder",
            color=body.color or "gray",
        )
        # Trigger a background backup after a successful projects.json write
        if srv._BACKUP_AVAILABLE:
            try:
                srv._backup_module.schedule_backup()
            except Exception:
                pass  # backup trigger failures never affect the response
        return new_proj
    except FileExistsError as e:
        raise HTTPException(409, detail=str(e))
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)


@router.delete("/api/projects/{owner}/{repo_name}")
async def remove_project(owner: str, repo_name: str, body: RemoveProjectBody):
    import shutil

    repo = f"{owner}/{repo_name}"
    srv = _server()

    if not any(p["repo"] == repo for p in projects_module.load_projects()):
        raise HTTPException(404, detail="Project not found")

    removed: list[str] = []

    # Remove from all projects.json copies first (not rolled back on subsequent errors)
    removed.extend(projects_module.remove_project(repo))

    if body.delete_local_folders:
        projects_dir = Path.home() / "dev"
        project_root = projects_dir / repo_name
        nested = (project_root / "main").exists() and (project_root / "main" / ".git").exists()
        if nested:
            if project_root.exists():
                shutil.rmtree(project_root)
                removed.append(str(project_root))
        else:
            uat_dir = project_root / "uat"
            if uat_dir.exists():
                shutil.rmtree(uat_dir)
                removed.append(str(uat_dir))
            for suffix in ("", "-coder", "-tester"):
                d = projects_dir / f"{repo_name}{suffix}"
                if d.exists():
                    shutil.rmtree(d)
                    removed.append(str(d))

    if body.delete_github_repo:
        result = subprocess.run(
            ["gh", "repo", "delete", repo, "--yes"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise HTTPException(502, detail=f"Failed to delete GitHub repository: {err}")
        removed.append(f"GitHub repo {repo}")

    # Trigger a background backup after a successful projects.json write
    if srv._BACKUP_AVAILABLE:
        try:
            srv._backup_module.schedule_backup()
        except Exception:
            pass  # backup trigger failures never affect the response

    return {"ok": True, "removed": removed}


@router.get("/api/projects/{project}/running-sprint")
def get_running_sprint(project: str):
    """Return the currently running sprint for the given project slug.

    200 — { label, started_at (ISO 8601 UTC), pid }
    204 — no sprint running (or only stale PID files)
    404 — project not registered
    """
    srv = _server()
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == project or p["repo"] == project),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    project_root = _project_root_path(matched["repo"])
    sprints_dir = _commander_dir(project_root) / "sprints"

    if not sprints_dir.exists():
        return Response(status_code=204)

    seen: set[str] = set()
    for pid_file in list(sprints_dir.glob("*-pid")) + list(sprints_dir.glob("*-pid.pending")):
        label = pid_file.name.removesuffix("-pid.pending").removesuffix("-pid")
        if label in seen:
            continue
        seen.add(label)
        if srv._is_sprint_running(project_root, label):
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pid = 0
            try:
                mtime = pid_file.stat().st_mtime
                started_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except OSError:
                started_at = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
            return {"label": label, "started_at": started_at, "pid": pid}

    return Response(status_code=204)


@router.post("/api/projects/{owner}/{repo_name}/approve-batch")
async def approve_batch(owner: str, repo_name: str, dry_run: bool = False):
    """Close all open UAT issues in the repo.

    Use dry_run=true to preview what would be closed without taking action.
    Prefer the per-sprint endpoint (POST /api/sprints/{label}/uat-signoff) for
    targeted sign-off — this endpoint has no sprint filter and closes everything.
    """
    repo = f"{owner}/{repo_name}"
    srv = _server()
    try:
        issues = srv.github_client.list_open_uat_issues(repo_name=repo)
        if dry_run:
            return {
                "dry_run": True,
                "would_approve": [{"number": i["number"], "title": i.get("title", "")} for i in issues],
                "count": len(issues),
            }
        approved = []
        for issue in issues:
            srv.github_client.approve_issue(issue["number"], repo_name=repo)
            approved.append(issue["number"])
        for issue_id in approved:
            await srv.broadcast({"type": "update", "event": {"event_type": "ticket_approved", "issue": issue_id}})
        return {"approved": approved, "count": len(approved)}
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)


@router.post("/api/projects/init")
async def init_project(body: InitProjectBody):
    """Spawn init_project.py and stream its stdout back as SSE (text/event-stream).

    AC1  — accepts repo_name, projects_dir, nested, skip_uat
    AC2  — spawns init_project.py as subprocess, streams output line by line
    AC3  — resolves ~ in projects_dir via Path.expanduser()
    AC4  — HTTP 400 if repo_name is empty or contains / or \\
    AC5  — HTTP 409 if repo_name already exists in projects.json
    AC6  — streams live log lines as SSE events
    AC7  — sends 'done' SSE event on success (exit code 0)
    AC8  — sends 'error' SSE event on failure (non-zero exit code)
    """
    import sys as _sys

    repo_name = (body.repo_name or "").strip()
    if not repo_name or "/" in repo_name or "\\" in repo_name:
        raise HTTPException(
            status_code=400,
            detail="repo_name must be non-empty and must not contain path separators (/ or \\).",
        )

    # AC5: check projects.json for existing entry
    existing = projects_module.load_projects()
    for p in existing:
        slug = p.get("repo", "").split("/")[-1]
        if slug.lower() == repo_name.lower():
            raise HTTPException(
                status_code=409,
                detail=f"A project named '{repo_name}' already exists in projects.json.",
            )

    # AC3: expand ~ in projects_dir
    projects_dir = Path(body.projects_dir or "~/dev").expanduser()

    # Build subprocess command (scripts/ live at the repo root, not under apps/dashboard)
    script_path = _REPO_ROOT / "scripts" / "init_project.py"
    cmd = [
        _sys.executable,
        str(script_path),
        repo_name,
        "--projects-dir", str(projects_dir),
    ]
    if body.nested:
        cmd.append("--nested")
    if body.skip_uat:
        cmd.append("--skip-uat")
    if body.from_existing:
        cmd.append("--from-existing")

    async def _stream():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        last_line = ""
        try:
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                last_line = line
                yield f"event: log\ndata: {json.dumps(line)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"
            return

        await proc.wait()
        if proc.returncode == 0:
            yield f"event: done\ndata: {json.dumps('ok')}\n\n"
        else:
            yield f"event: error\ndata: {json.dumps(last_line or 'init_project.py failed')}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/project-details")
def get_project_details(repo: str):
    try:
        agents = db.get_agents()
        return projects_module.get_project_details(repo, agents)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/api/projects/{owner}/{repo_name}/sprint-branch-merge")
def sprint_branch_merge(owner: str, repo_name: str, body: SprintBranchMergeBody):
    """Merge one sprint branch into another via PR (used by bulk-complete step progress)."""
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")
    if not body.head or not body.base:
        raise HTTPException(400, detail="head and base are required")

    repo = f"{owner}/{repo_name}"
    title = body.title.strip() or f"Merge `{body.head}` → `{body.base}`"
    ok, detail, _pr_num = _gh_merge_branch_via_pr(
        repo, body.head, body.base, title, body.delete_branch,
    )
    if not ok:
        raise HTTPException(400, detail=detail)
    return {"ok": True, "detail": detail}
