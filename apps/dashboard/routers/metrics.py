from __future__ import annotations
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import github_client  # noqa: E402
import projects as projects_module  # noqa: E402

_PROJECTS_BASE = Path.home() / "dev"

router = APIRouter()


def _server():
    import server
    return server


def _project_root_path(repo):
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root):
    return project_root / ".commander"


def _sprint_json_path(project_root, sprint_label):
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}.json"


def _sprint_json_read(path):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _count_rework_tickets(sprint_label: str, project: str) -> int:
    """Return number of open issues in the sprint carrying needs-rework label."""
    try:
        r = github_client.get_repo_for_operation(project)
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", r,
             "--label", sprint_label,
             "--label", "needs-rework",
             "--json", "number",
             "--limit", "100"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return len(json.loads(result.stdout or "[]"))
    except Exception:
        pass
    return 0


@router.get("/api/metrics/sprints")
def get_sprint_metrics(request: Request):
    """Return per-sprint aggregate metrics across all registered projects.

    Query params:
      from=YYYY-MM-DD  (default: 30 days ago)
      to=YYYY-MM-DD    (default: today)

    Response: JSON array of sprint metric objects.
    """
    today = datetime.now(tz=timezone.utc).date()

    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")

    if raw_from is None and raw_to is None:
        from_date = today - timedelta(days=30)
        to_date = today
    else:
        if raw_from is not None:
            try:
                from_date = datetime.strptime(raw_from, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    400,
                    detail=f"Invalid 'from' date {raw_from!r} — expected YYYY-MM-DD",
                )
        else:
            from_date = today - timedelta(days=30)

        if raw_to is not None:
            try:
                to_date = datetime.strptime(raw_to, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    400,
                    detail=f"Invalid 'to' date {raw_to!r} — expected YYYY-MM-DD",
                )
        else:
            to_date = today

    if from_date > to_date:
        raise HTTPException(
            400,
            detail=f"'from' date ({from_date}) must not be after 'to' date ({to_date})",
        )

    from_dt = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
    to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)

    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    results = []
    seen_paths: set[Path] = set()

    for proj in all_projects:
        repo = proj.get("repo", "")
        if not repo:
            continue

        project_root = _project_root_path(repo)
        sprints_dir = _commander_dir(project_root) / "sprints"

        if not sprints_dir.exists():
            continue

        for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
            if state_file in seen_paths:
                continue
            seen_paths.add(state_file)

            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            start_ts_str = state_data.get("start_timestamp")
            if not start_ts_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_ts_str.rstrip("Z")).replace(
                    tzinfo=timezone.utc
                )
            except Exception:
                continue

            if not (from_dt <= start_dt <= to_dt):
                continue

            sprint_label_val = state_data.get(
                "sprint_label",
                state_file.stem.replace("-state", ""),
            )
            project_val = state_data.get("project") or repo

            wall_clock_secs = float(state_data.get("wall_clock_secs") or 0.0)
            issues = state_data.get("issues", [])

            done_count = sum(1 for i in issues if i.get("status") == "done")
            failed_count = sum(1 for i in issues if i.get("status") == "failed")
            skipped_count = sum(1 for i in issues if i.get("status") == "skipped")

            coder_count = sum(1 for i in issues if i.get("coder_started_at"))
            tester_count = sum(1 for i in issues if i.get("tester_started_at"))
            reviewer_count = 1 if state_data.get("reviewer_status") not in (None, "") else 0
            documenter_count = 1 if state_data.get("documenter_status") not in (None, "") else 0

            tokens_in = int(state_data.get("total_tokens_in") or 0)
            tokens_out = int(state_data.get("total_tokens_out") or 0)
            total_tokens = tokens_in + tokens_out
            token_estimate = total_tokens if total_tokens > 0 else None

            results.append({
                "sprint_label": sprint_label_val,
                "project": project_val,
                "duration_minutes": round(wall_clock_secs / 60, 2),
                "ticket_count": len(issues),
                "ticket_outcomes_breakdown": {
                    "done": done_count,
                    "failed": failed_count,
                    "skipped": skipped_count,
                    "needs_rework": 0,
                },
                "agent_dispatch_counts": {
                    "coder": coder_count,
                    "tester": tester_count,
                    "reviewer": reviewer_count,
                    "documenter": documenter_count,
                },
                "total_token_estimate": token_estimate,
            })

    return results


@router.get("/api/projects/{slug}/analytics/metrics")
def get_project_analytics_metrics(slug: str, request: Request):
    """GET /api/projects/{slug}/analytics/metrics — delivery-health metrics (issue #1267)."""
    srv = _server()
    repo = srv._resolve_project_slug(slug)
    project_root = _project_root_path(repo)
    since = request.query_params.get("since")
    until = request.query_params.get("until")
    sprint_filter = request.query_params.get("sprint")
    return srv._compute_analytics_metrics(project_root, since=since, until=until,
                                          sprint_filter=sprint_filter)
