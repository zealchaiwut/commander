"""Sprint nav route handlers extracted from server.py (issue #1267).

Routes owned by this module:
  GET /api/sprint-nav-status   — GitHub-backed sprint status for nav-bar pill
  GET /api/sprint-progress     — Unified sprint progress (live + persisted + GitHub)
  GET /api/sprint-nav-summary  — Sprint summary issue body (on-demand)

Helper functions moved from server.py:
  _gh_graphql_reset_seconds()
  _gh_error()
  _settled_done_from_columns()
  _sprint_progress_file_path()
  _persist_sprint_progress()

Functions that remain in server.py and are accessed via deferred _server() import:
  _finished_sprint_summaries()
  _all_sprints_running()
  _sprint_statuses (dict)
  _project_root_path()
  _commander_dir()
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import github_client  # noqa: E402

router = APIRouter(tags=["sprint_nav"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


# ── helpers ───────────────────────────────────────────────────────────────────

def _gh_graphql_reset_seconds() -> Optional[int]:
    """Seconds until the GitHub GraphQL budget resets, or None.

    Queries the rate_limit endpoint, which is REST (core) and does not itself
    count against any limit, so it is safe to call on an error path.
    """
    try:
        import time as _t
        r = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".resources.graphql.reset"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return max(0, int(r.stdout.strip()) - int(_t.time()))
    except Exception:
        pass
    return None


def _gh_error(e: subprocess.CalledProcessError) -> HTTPException:
    detail = e.stderr.strip() if e.stderr else str(e)
    # Map a GitHub rate-limit failure to a clean 429 with a reset countdown, so
    # callers (e.g. the Sprint Mgmt board) can say "rate limit, retry in Ns"
    # instead of a generic failure. Refills hourly.
    if "rate limit" in detail.lower():
        reset_in = _gh_graphql_reset_seconds()
        msg = "GitHub API rate limit reached."
        if reset_in:
            msg += f" Retry in ~{reset_in // 60}m {reset_in % 60}s."
        else:
            msg += " It refills hourly; retry shortly."
        return HTTPException(status_code=429, detail=msg)
    return HTTPException(status_code=502, detail=detail)


def _settled_done_from_columns(total: int, columns: dict) -> int:
    """Canonical GitHub-derived "done" = settled work past SIT
    (uat + done + needs-rework) = total minus the not-yet-settled columns
    (backlog + in-progress + sit).

    Single source of the GitHub-side count: mirrors the frontend
    ``_snavSettledDone()`` and the live tier's ``done+skipped+failed`` so the nav
    pill, sidebar badge, and board running badge can never disagree. The old
    ``done + uat`` formula undercounted needs-rework tickets; ``total - backlog``
    (frontend) overcounted by treating in-progress + SIT as done.
    """
    columns = columns or {}
    return max(0, (total or 0) - (columns.get("backlog") or 0)
               - (columns.get("in-progress") or 0) - (columns.get("sit") or 0))


def _sprint_progress_file_path(project: str) -> Optional[Path]:
    """Return the path to the persisted sprint-progress JSON file for a project."""
    if not project:
        return None
    srv = _server()
    project_root = srv._project_root_path(project)
    return srv._commander_dir(project_root) / "runtime" / "sprint-progress.json"


def _persist_sprint_progress(project: str, data: dict) -> None:
    """Write sprint progress data atomically to .commander/runtime/sprint-progress.json."""
    path = _sprint_progress_file_path(project)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        pass


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/api/sprint-nav-status")
def get_sprint_nav_status(repo: str = ""):
    """GitHub-backed sprint status for the nav-bar pill.

    Read-only and derived entirely from GitHub labels/issues, so it works on a
    machine that is NOT running the sprint (the runner's local state.json/PID
    files are never shared cross-machine). Cached 30s via github_client.

    A sprint is "finished" when its "Sprint N Executive Summary" issue exists
    (label ``sprint-summary``); otherwise it is "running" and progress is
    inferred from each ticket's workflow column.
    """
    repo_name = repo or None
    try:
        sprint_nums = github_client.list_sprints(repo_name=repo_name)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    if not sprint_nums:
        return {"has_sprint": False}

    # Current sprint = sprint with active work (in-progress/SIT/UAT) if any,
    # otherwise the highest-numbered sprint that has issues.
    # This ensures a running sprint takes precedence over a higher-numbered
    # planned sprint that only has backlog tickets.
    all_sprint_issues: dict[int, list[dict]] = {}
    for n in reversed(sprint_nums):
        issues = github_client.list_issues(n, repo_name=repo_name)
        if issues:
            all_sprint_issues[n] = issues

    if not all_sprint_issues:
        return {"has_sprint": False}

    # Fetch finished summaries once — used both to skip finished sprints when
    # picking "current" and to populate the panel summary link below.
    finished_summaries = _server()._finished_sprint_summaries(repo_name)

    _ACTIVE_COLS = {"in-progress", "sit", "uat", "needs-rework"}
    current = None
    raw_issues: list[dict] = []
    # Prefer the lowest-numbered UNFINISHED sprint that has active (non-backlog) work.
    # Skipping finished sprints prevents a higher-numbered running sprint from being
    # eclipsed by a lower-numbered sprint whose only remaining work is UAT sign-off.
    for n in sorted(all_sprint_issues.keys()):
        if f"sprint-{n}" in finished_summaries:
            continue  # sprint already finished — skip when looking for running sprint
        issues = all_sprint_issues[n]
        if any(i.get("column") in _ACTIVE_COLS for i in issues):
            current = n
            raw_issues = issues
            break
    # Fall back to highest-numbered sprint with any issues (covers finished-only state)
    if current is None:
        current = max(all_sprint_issues.keys())
        raw_issues = all_sprint_issues[current]

    # The "Sprint N Executive Summary" issue may carry sprint-N label in addition
    # to sprint-summary. Strip any sprint-summary / docs issues so they don't
    # inflate ticket counts.
    _NON_WORK_LABELS = {"sprint-summary", "docs", "documentation"}
    work_issues = [
        i for i in raw_issues
        if not _NON_WORK_LABELS & {lbl.get("name", "") for lbl in i.get("labels", [])}
    ]
    summary_issue = finished_summaries.get(f"sprint-{current}")

    columns = {"backlog": 0, "in-progress": 0, "sit": 0, "uat": 0, "done": 0, "needs-rework": 0}
    for i in work_issues:
        col = i.get("column", "backlog")
        columns[col] = columns.get(col, 0) + 1

    return {
        "has_sprint": True,
        "repo": github_client.get_repo_for_operation(repo_name),
        "sprint": current,
        "state": "finished" if summary_issue else "running",
        "total": len(work_issues),
        "done": columns["done"],
        "uat": columns["uat"],
        "columns": columns,
        "summary_issue": summary_issue,
    }


@router.get("/api/sprint-progress")
def get_sprint_progress(project: str = "", repo: str = ""):
    """Unified sprint progress endpoint — single source of truth for all three pill components.

    Priority:
    1. In-memory live status from sprint_manager (most recent, pushed via POST /api/sprint-status)
    2. Persisted JSON file (survives server restart)
    3. GitHub-backed fallback via sprint-nav-status logic

    Persists the result to .commander/runtime/sprint-progress.json so the UI
    can re-hydrate from disk when the server restarts or live data is not yet
    available.
    """

    key_project = project or repo or ""

    srv = _server()

    # ── 1. Try live in-memory status ─────────────────────────────────────────
    running = srv._all_sprints_running()
    if key_project:
        running = [r for r in running if r["project"] == key_project]

    for r in running:
        key = (r["project"], r["sprint_label"])
        status = srv._sprint_statuses.get(key, {})
        issues = status.get("issues", [])
        if not issues:
            # Disk fallback: sprint_manager writes <label>-state.json regardless
            # of which port it posts to. Read the FULL sub-label state (e.g.
            # sprint-67.1-state.json) so the pill reflects the running sub-sprint
            # and its live progress instead of a GitHub-derived base guess (the
            # pill showed "S71 0/11" while 67.1 ran — same gap #950 fixed for /live).
            try:
                _sp = (srv._commander_dir(srv._project_root_path(r["project"]))
                       / "sprints" / f"{r['sprint_label']}-state.json")
                if _sp.exists():
                    _disk = json.loads(_sp.read_text(encoding="utf-8"))
                    issues = _disk.get("issues", [])
                    if issues and status.get("sprint_number") is None:
                        status = {**status, "sprint_number": _disk.get("sprint_number")}
            except Exception:
                pass
        if not issues:
            continue
        sprint_number = status.get("sprint_number")
        if sprint_number is None:
            label = r["sprint_label"]
            m = re.search(r"\d+", label)
            sprint_number = int(m.group()) if m else 0

        # Numerator follows the done+uat convention (CLAUDE.md): a ticket counts
        # once it is merged/skipped OR its tester has passed (reached UAT/gate).
        # agent_status "tester_done" = tester finished and handed to gates,
        # "completed" = merged. Without these a tester-passed-but-unmerged ticket
        # stayed `pending` and the live pill undercounted (showed 2/4 not 3/4).
        done = sum(
            1 for i in issues
            if i.get("status") in ("done", "skipped", "failed")
            or i.get("agent_status") in ("tester_done", "completed")
        )
        total = len(issues)

        result = {
            "has_sprint": True,
            "sprint_label": r["sprint_label"],
            "sprint": sprint_number,
            "done": done,
            "total": total,
            "run_state": "running",
            "source": "live",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        _persist_sprint_progress(key_project, result)
        return result

    # ── 2. Try persisted file ────────────────────────────────────────────────
    progress_path = _sprint_progress_file_path(key_project)
    if progress_path and progress_path.exists():
        try:
            cached = json.loads(progress_path.read_text(encoding="utf-8"))
            if cached.get("has_sprint"):
                return cached
        except (OSError, json.JSONDecodeError):
            pass

    # ── 3. GitHub fallback ───────────────────────────────────────────────────
    try:
        gh_data = get_sprint_nav_status(repo=repo or (project or ""))
    except Exception:
        return {"has_sprint": False}

    if not gh_data.get("has_sprint"):
        return {"has_sprint": False}

    sprint_num = gh_data.get("sprint", 0)
    gh_total = gh_data.get("total") or 0
    gh_done = _settled_done_from_columns(gh_total, gh_data.get("columns", {}))
    gh_state = gh_data.get("state", "running")

    result = {
        "has_sprint": True,
        "sprint_label": f"sprint-{sprint_num}",
        "sprint": sprint_num,
        "done": gh_done,
        "total": gh_total,
        "run_state": gh_state,
        "columns": gh_data.get("columns", {}),
        "source": "github",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _persist_sprint_progress(key_project, result)
    return result


@router.get("/api/sprint-nav-summary")
def get_sprint_nav_summary(number: int, repo: str = ""):
    """Return a sprint-summary issue's markdown body (GitHub-backed), fetched on
    demand when the user opens the nav panel."""
    repo_name = repo or None
    try:
        issue = github_client.get_issue(number, repo_name=repo_name)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "body": issue.get("body") or "",
    }
