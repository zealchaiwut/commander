"""Sprint summary, status, history, and home route handlers (extracted from server.py, issue #1258).

Routes owned by this module:
  POST /api/sprint-status
  GET  /api/sprint-status
  GET  /api/sprint-summary
  GET  /api/home
  GET  /api/sprint-history
  GET  /api/sprint-history-content
  GET  /api/sprints/timeline
  GET  /api/sprints/summaries

Shared server.py helpers (_parse_summary_file, _home_project_data, _home_activity_feed, etc.)
are accessed via the deferred ``_server()`` import to keep the circular-import guard intact.
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
from pydantic import BaseModel, ConfigDict

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402
import github_client  # noqa: E402
import projects as projects_module  # noqa: E402
from sprint_label_re import SPRINT_BASE_LABEL_RE  # noqa: E402

router = APIRouter(tags=["sprint_summaries"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


# ── Pydantic request model (moved from server.py) ─────────────────────────────

class SprintStatusPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    project: str = ""
    sprint_label: str = ""
    sprint_number: Optional[int] = None
    issues: list[dict] = []
    start_timestamp: Optional[str] = None
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    wall_clock_secs: float = 0.0
    token_budget: int = 0
    paused: bool = False


# ── Route handlers ─────────────────────────────────────────────────────────────

@router.post("/api/sprint-status")
def set_sprint_status(payload: SprintStatusPayload):
    srv = _server()
    key = (payload.project, payload.sprint_label)
    data = payload.model_dump()
    srv._sprint_statuses[key] = data

    if payload.project and payload.sprint_label:
        status_path = srv._sprint_status_file_path(payload.project, payload.sprint_label)
        if status_path is not None:
            try:
                status_path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic write: write to a temp file then replace to avoid partial reads.
                tmp_path = status_path.with_suffix(".json.tmp")
                tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(str(tmp_path), str(status_path))
            except OSError as exc:
                print(f"[sprint-status] could not persist status for {payload.sprint_label}: {exc}")

    return {"ok": True}


@router.get("/api/sprint-status")
def get_sprint_status(project: Optional[str] = None):
    srv = _server()
    running = srv._all_sprints_running()
    if project:
        running = [r for r in running if r["project"] == project]
    result = []
    for r in running:
        key = (r["project"], r["sprint_label"])
        status = srv._sprint_statuses.get(key, {})
        issues = status.get("issues", [])
        closed = sum(1 for i in issues if i.get("status") in ("done", "skipped"))
        result.append({
            "project":         r["project"],
            "sprint_label":    r["sprint_label"],
            "pid":             r.get("pid"),
            "issues":          issues,
            "progress":        {"closed": closed, "total": len(issues)},
            "started_at":      status.get("start_timestamp"),
            "wall_clock_secs": status.get("wall_clock_secs", 0.0),
        })
    return {"running_sprints": result}


@router.get("/api/sprint-summary")
def get_sprint_summary():
    """Return the path and markdown content of the most recent summary file."""
    srv = _server()
    sprints_dir = srv.SPRINTS_DIR
    if not sprints_dir.exists():
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    summaries = sorted(
        sprints_dir.glob("*-summary-*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summaries:
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    latest = summaries[0]
    content = latest.read_text(encoding="utf-8")
    return {"path": str(latest), "content": content}


@router.get("/api/home")
def get_home():
    """Aggregated Home page payload: stats, per-project summaries, and activity feed.

    Per-project data is cached 30 s (key home:<slug>).
    Always returns HTTP 200 — failing projects degrade to idle with 0 counts.
    """
    srv = _server()
    projs = projects_module.load_projects()
    running_sprints = srv._all_sprints_running()

    all_open_by_repo: dict[str, list[dict]] = {}
    proj_data_list: list[dict] = []

    from home_service import home_project_data as _home_project_data  # noqa: PLC0415
    for proj in projs:
        repo = proj["repo"]
        data = _home_project_data(proj, running_sprints, srv._sprint_statuses)
        proj_data_list.append(data)
        try:
            all_open_by_repo[repo] = github_client.list_all_open_issues(repo_name=repo)
        except Exception:
            all_open_by_repo[repo] = []

    # stats.sprint_running
    sprint_running_projects: list[dict] = []
    now_utc = datetime.now(timezone.utc)
    for r in running_sprints:
        repo = r["project"]
        slug = repo.split("/")[-1]
        proj_cfg = next((p for p in projs if p["repo"] == repo), {})
        status_data = srv._sprint_statuses.get((repo, r["sprint_label"]), {})
        start_ts = status_data.get("start_timestamp")
        elapsed_sec = 0
        if start_ts:
            try:
                start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                elapsed_sec = int((now_utc - start_dt).total_seconds())
            except Exception:
                pass
        sprint_running_projects.append({
            "name": proj_cfg.get("name", slug),
            "sprint_label": r["sprint_label"],
            "elapsed_sec": elapsed_sec,
        })

    # stats.awaiting_uat
    uat_total = 0
    uat_project_set: set[str] = set()
    oldest_uat_ts: str | None = None
    oldest_age_sec: int | None = None

    for repo, issues in all_open_by_repo.items():
        for issue in issues:
            if any(lbl["name"] == "UAT" for lbl in issue.get("labels", [])):
                uat_total += 1
                uat_project_set.add(repo)
                ts = issue.get("updatedAt") or issue.get("createdAt")
                if ts and (oldest_uat_ts is None or ts < oldest_uat_ts):
                    oldest_uat_ts = ts

    if oldest_uat_ts:
        try:
            oldest_dt = datetime.fromisoformat(oldest_uat_ts.replace("Z", "+00:00"))
            oldest_age_sec = int((now_utc - oldest_dt).total_seconds())
        except Exception:
            pass

    # stats.sprints_planned
    running_labels_by_repo: dict[str, set[str]] = {}
    for r in running_sprints:
        running_labels_by_repo.setdefault(r["project"], set()).add(r["sprint_label"])

    planned_count = 0
    planned_tickets = 0
    for repo, issues in all_open_by_repo.items():
        running_lbls = running_labels_by_repo.get(repo, set())
        label_ticket_counts: dict[str, int] = {}
        for issue in issues:
            for lbl in issue.get("labels", []):
                lname = lbl["name"]
                if SPRINT_BASE_LABEL_RE.match(lname) and lname not in running_lbls:
                    label_ticket_counts[lname] = label_ticket_counts.get(lname, 0) + 1
        planned_count += len(label_ticket_counts)
        planned_tickets += sum(label_ticket_counts.values())

    # stats.backlog
    backlog_per_proj: list[dict] = []
    total_backlog = 0
    for proj in projs:
        repo = proj["repo"]
        issues = all_open_by_repo.get(repo, [])
        bc = sum(1 for i in issues if github_client.classify_issue(i) == "backlog")
        total_backlog += bc
        if bc > 0:
            backlog_per_proj.append({"name": proj.get("name", repo.split("/")[-1]), "count": bc})
    backlog_per_proj.sort(key=lambda x: x["count"], reverse=True)

    activity = srv._home_activity_feed(all_open_by_repo, running_sprints, projs)

    return {
        "stats": {
            "sprint_running": {
                "count": len(sprint_running_projects),
                "projects": sprint_running_projects,
            },
            "awaiting_uat": {
                "count": uat_total,
                "projects": len(uat_project_set),
                "oldest_age_sec": oldest_age_sec,
            },
            "sprints_planned": {
                "count": planned_count,
                "total_tickets": planned_tickets,
            },
            "backlog": {
                "count": total_backlog,
                "per_project": backlog_per_proj[:5],
            },
        },
        "projects": proj_data_list,
        "activity": activity,
    }


@router.get("/api/sprint-history")
def get_sprint_history_legacy():
    """AC-4: Return a JSON array of all past summaries, newest first.

    Each entry:
      sprint_num, date, status, file_path, github_issue_url,
      shipped_count, skipped_count, total_tokens
    """
    srv = _server()
    sprints_dir = srv.SPRINTS_DIR
    if not sprints_dir.exists():
        return []

    summary_files = sorted(
        sprints_dir.glob("*-summary-*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summary_files:
        return []

    results: list[dict] = []
    for path in summary_files:
        try:
            meta = srv._parse_summary_file(path)
        except Exception:
            meta = {"sprint_num": None, "date": "", "status": "unknown",
                    "shipped_count": 0, "skipped_count": 0, "total_tokens": 0}

        # Look for matching state file to get summary_issue_url and reviewer data
        sprint_num           = meta.get("sprint_num")
        issue_url            = None
        reviewer_status      = None
        reviewer_comment_url = None
        reviewer_findings    = None
        if sprint_num is not None:
            state_file = sprints_dir / f"sprint-{sprint_num}-state.json"
            if state_file.exists():
                try:
                    state_data           = json.loads(state_file.read_text())
                    issue_url            = state_data.get("summary_issue_url")
                    reviewer_status      = state_data.get("reviewer_status")
                    reviewer_comment_url = state_data.get("reviewer_comment_url")
                    reviewer_findings    = state_data.get("reviewer_findings")
                except Exception:
                    pass

        results.append({
            "sprint_num":            meta["sprint_num"],
            "date":                  meta["date"],
            "status":                meta["status"],
            "file_path":             str(path),
            "github_issue_url":      issue_url,
            "shipped_count":         meta["shipped_count"],
            "skipped_count":         meta["skipped_count"],
            "total_tokens":          meta["total_tokens"],
            "reviewer_status":       reviewer_status,
            "reviewer_comment_url":  reviewer_comment_url,
            "reviewer_findings":     reviewer_findings,
        })

    return results


@router.get("/api/sprint-history-content")
def get_sprint_history_content(sprint_num: Optional[int] = None, idx: Optional[int] = None):
    """Return markdown content of a specific sprint summary file.

    Looks up by sprint_num first; falls back to position idx in sorted list.
    """
    srv = _server()
    sprints_dir = srv.SPRINTS_DIR
    if not sprints_dir.exists():
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    summary_files = sorted(
        sprints_dir.glob("*-summary-*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summary_files:
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    target = None
    if sprint_num is not None:
        for path in summary_files:
            if re.match(rf"sprint-{sprint_num}-summary-", path.stem):
                target = path
                break
    if target is None and idx is not None and 0 <= idx < len(summary_files):
        target = summary_files[idx]

    if target is None:
        raise HTTPException(status_code=404, detail="Sprint summary not found")

    return {"path": str(target), "content": target.read_text(encoding="utf-8")}


@router.get("/api/sprints/timeline")
def get_sprint_gantt_timeline(project: str):
    """Return Gantt-ready timeline data for all ran sprints in a project (issue #431).

    Reads sprint-N-state.json files from the commander directory.
    Each entry includes: sprint_label, display_name, state, start_date, end_date, ticket_count.

    State values: "running" | "cancelled" | "completed"
    """
    srv = _server()
    project_root = srv._project_root_path(project)
    commander = srv._commander_dir(project_root)
    sprints_dir = commander / "sprints"

    if not sprints_dir.exists():
        return {"sprints": []}

    def _parse_iso_ts(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    def _to_iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = []
    state_label_re = re.compile(r"^sprint-(\d+(?:\.\d+)?)-state\.json$")

    for state_file in sprints_dir.glob("sprint-*-state.json"):
        m = state_label_re.match(state_file.name)
        if not m:
            continue
        sprint_num_str = m.group(1)
        sprint_label = f"sprint-{sprint_num_str}"

        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        start_ts_str = state_data.get("start_timestamp")
        start_ts = _parse_iso_ts(start_ts_str)
        if start_ts is None:
            continue

        wall_clock = state_data.get("wall_clock_secs", 0.0) or 0.0
        issues = state_data.get("issues", [])
        ticket_count = len(issues)

        is_running = srv._is_sprint_running(project_root, sprint_label)

        if is_running:
            state = "running"
            end_ts = datetime.now(timezone.utc).timestamp()
        else:
            # Check cancelled flag from sprint JSON
            json_path = srv._sprint_json_path(project_root, sprint_label)
            sprint_json = srv._sprint_json_read(json_path)
            if sprint_json.get("status") in ("cancelled", "needs_rework"):
                state = "cancelled"
            else:
                state = "completed"
            end_ts = start_ts + wall_clock if wall_clock > 0 else start_ts

        results.append({
            "label": sprint_label,
            "display_name": f"Sprint {sprint_num_str}",
            "state": state,
            "start_date": _to_iso(start_ts),
            "end_date": _to_iso(end_ts),
            "ticket_count": ticket_count,
        })

    # Sort chronologically by start_date
    results.sort(key=lambda s: s["start_date"])

    return {"sprints": results}


@router.get("/api/sprints/summaries")
def get_sprint_summaries(project: str):
    """Return all sprint-summary issues for a project (open + optionally closed).

    Query params:
      project=<owner/repo>
      state=open|all  (default: open)

    Response shape:
      { "summaries": [ { number, title, sprint_number, sprint_sub_label, state,
                          outcome, url, created_at, summary_file_path } ] }
    """
    srv = _server()
    try:
        repo = github_client.get_repo_for_operation(project)
    except Exception as e:
        raise HTTPException(400, detail=str(e))

    sprint_label_re = re.compile(r"^sprint-(\d+)(?:\.(\d+))?$")

    try:
        mirrored = db.get_mirrored_issues(repo, state="open")
        if mirrored:
            open_issues = [
                iss for iss in mirrored
                if any(lbl["name"] == "sprint-summary" for lbl in iss.get("labels", []))
            ]
        else:
            result = subprocess.run(
                [
                    "gh", "issue", "list", "--repo", repo,
                    "--label", "sprint-summary",
                    "--state", "open",
                    "--json", "number,title,labels,state,url,createdAt",
                    "--limit", "200",
                ],
                capture_output=True, text=True, timeout=15,
            )
            open_issues = (
                json.loads(result.stdout or "[]") if result.returncode == 0 else []
            )
    except Exception:
        open_issues = []

    # Title-regex fallback: fetch all open issues and find legacy summaries without the label
    try:
        all_open = github_client.list_open_issues_with_body(repo_name=project, limit=200)
        seen_nums = {i["number"] for i in open_issues}
        for iss in all_open:
            if iss["number"] in seen_nums:
                continue
            if srv._SUMMARY_TITLE_RE.match(iss.get("title", "") or ""):
                open_issues.append(iss)
    except Exception:
        pass

    project_root = srv._project_root_path(project)
    commander = srv._commander_dir(project_root)
    sprints_dir = commander / "sprints"

    def _build_summary(iss: dict, is_closed: bool) -> dict:
        num = iss["number"]
        title = iss.get("title", "")

        # Determine sprint number and sub-label from labels
        sprint_number: Optional[int] = None
        sprint_sub_label: Optional[str] = None
        for lbl in iss.get("labels", []):
            m = sprint_label_re.match(lbl["name"] if isinstance(lbl, dict) else lbl)
            if m:
                sprint_number = int(m.group(1))
                sprint_sub_label = m.group(2)
                break

        # Fallback: parse sprint number from title ("Sprint 21 Executive Summary")
        if sprint_number is None:
            tm = re.match(r"^Sprint (\d+)(?:\.(\d+))?\s+Executive Summary$", title)
            if tm:
                sprint_number = int(tm.group(1))
                sprint_sub_label = tm.group(2)

        # Compute outcome using same logic as finish-card endpoint
        outcome = "completed"
        if sprint_number is not None:
            sprint_n = str(sprint_number)
            if sprint_sub_label:
                sprint_label = f"sprint-{sprint_number}.{sprint_sub_label}"
            else:
                sprint_label = f"sprint-{sprint_number}"

            # Check cancelled state from sprint json
            fc_json_path = srv._sprint_json_path(project_root, sprint_label)
            fc_sprint_json = srv._sprint_json_read(fc_json_path)
            is_cancelled = fc_sprint_json.get("status") in ("cancelled", "needs_rework")

            # Check summary file for status
            if sprints_dir.exists():
                for sf in sorted(
                    sprints_dir.glob(f"sprint-{sprint_n}-summary-*.md"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                ):
                    try:
                        meta = srv._parse_summary_file(sf)
                        raw = (meta.get("status") or "").lower()
                        if raw in ("complete", "completed"):
                            pass  # fc_sprint_status = "completed"
                        elif raw in ("stopped", "failed", "cancelled"):
                            if raw == "cancelled":
                                is_cancelled = True
                    except Exception:
                        pass
                    break

            if is_cancelled:
                outcome = "cancelled"
            elif srv._has_rework_tickets(sprint_label, project):
                outcome = "has_rework"
            else:
                outcome = "completed"

        # Find summary file path
        summary_file_path: Optional[str] = None
        if sprint_number is not None and sprints_dir.exists():
            sprint_n = str(sprint_number)
            cands = sorted(
                sprints_dir.glob(f"sprint-{sprint_n}-summary-*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if cands:
                summary_file_path = f".commander/sprints/{cands[0].name}"

        return {
            "number": num,
            "title": title,
            "sprint_number": sprint_number,
            "sprint_sub_label": sprint_sub_label,
            "state": "closed" if is_closed else "open",
            "outcome": outcome,
            "url": iss.get("url", iss.get("html_url", "")),
            "created_at": iss.get("createdAt", iss.get("created_at", "")),
            "summary_file_path": summary_file_path,
        }

    summaries = [_build_summary(iss, False) for iss in open_issues]

    # Sort: newest sprint number first; for sub-labels, higher sub sorts before base
    def _sort_key(s: dict):
        n = s["sprint_number"] or 0
        sub = int(s["sprint_sub_label"]) if s["sprint_sub_label"] else 0
        return (-n, -sub)

    summaries.sort(key=_sort_key)

    return {"summaries": summaries}
