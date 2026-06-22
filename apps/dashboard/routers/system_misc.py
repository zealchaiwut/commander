"""System/misc route handlers extracted from server.py (issue #1259).

Routes owned by this module:
  GET    /api/alerts
  POST   /api/alerts
  DELETE /api/alerts/{idx}
  POST   /api/docs-freshness/check
  GET    /api/docs-freshness/warnings
  DELETE /api/docs-freshness/warnings/{warning_id}
  GET    /api/deploy/overview
  POST   /api/maintenance/sprints/cleanup
  GET    /api/plan-usage
  GET    /api/estimator/health
  POST   /api/issues/{issue_id}/estimate

Shared server.py helpers are accessed via the deferred ``_server()`` import
to keep the circular-import guard intact.  ``_alerts`` / ``_test_pat`` are
imported directly from ``routers.logs_service`` (their canonical home).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402

# _alerts and _test_pat live in logs_service (their canonical home)
from routers.logs_service import _alerts, _test_pat  # noqa: E402

router = APIRouter(tags=["system_misc"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


# ── Pydantic models (moved from server.py) ────────────────────────────────────

class AlertPayload(BaseModel):
    title: str = ""
    body: str = ""
    issue_num: Optional[int] = None
    category: Optional[str] = None
    repo: Optional[str] = None


class _SprintCleanupBody(BaseModel):
    project: str
    dry_run: bool = False


class DocsFreshnessWarning(BaseModel):
    repo: str
    doc_path: str
    trigger_ref: str
    trigger_type: str = "push"
    trigger_url: Optional[str] = None


class DocsFreshnessCheckPayload(BaseModel):
    repo: str
    trigger_ref: str
    trigger_type: str = "push"
    trigger_url: Optional[str] = None
    stale_docs: list[str] = []
    cleared_docs: list[str] = []


# ── Alert routes ──────────────────────────────────────────────────────────────

@router.post("/api/alerts", status_code=201)
def receive_alert(payload: AlertPayload):
    _alerts.append(payload.model_dump())
    return {"ok": True, "count": len(_alerts)}


@router.get("/api/alerts")
def get_alerts():
    return [
        a for a in _alerts
        if not (_test_pat.search(a.get("title", "")) or _test_pat.search(a.get("body", "")))
    ]


@router.delete("/api/alerts/{idx}")
def dismiss_alert(idx: int):
    if 0 <= idx < len(_alerts):
        _alerts.pop(idx)
    return {"ok": True, "count": len(_alerts)}


# ── Docs freshness routes ─────────────────────────────────────────────────────

@router.post("/api/docs-freshness/check", status_code=200)
def docs_freshness_check(payload: DocsFreshnessCheckPayload):
    """Receive a freshness check result and update the warnings table."""
    upserted, cleared = [], []
    for doc in payload.stale_docs:
        db.upsert_docs_warning(
            repo=payload.repo,
            doc_path=doc,
            trigger_ref=payload.trigger_ref,
            trigger_type=payload.trigger_type,
            trigger_url=payload.trigger_url,
        )
        upserted.append(doc)
    for doc in payload.cleared_docs:
        db.clear_docs_warning(repo=payload.repo, doc_path=doc)
        cleared.append(doc)
    return {"ok": True, "upserted": upserted, "cleared": cleared}


@router.get("/api/docs-freshness/warnings")
def get_docs_freshness_warnings(repo: Optional[str] = None):
    return db.get_active_docs_warnings(repo=repo)


@router.delete("/api/docs-freshness/warnings/{warning_id}")
def clear_docs_freshness_warning(warning_id: int):
    found = db.clear_docs_warning_by_id(warning_id)
    return {"ok": True, "cleared": found}


# ── Deploy overview route ─────────────────────────────────────────────────────

@router.get("/api/deploy/overview")
def get_deploy_overview():
    """Aggregate deployable environments across all known projects (issue #726)."""
    srv = _server()
    import projects as projects_module  # noqa: PLC0415
    slugs: list[str] = list(srv._deploy_known_slugs())
    try:
        for proj in projects_module.load_projects():
            s = proj["repo"].split("/")[-1]
            if s not in slugs:
                slugs.append(s)
    except Exception:
        pass

    environments: list[dict] = []
    for slug in slugs:
        try:
            repo = srv._resolve_project_slug(slug)
        except HTTPException:
            repo = f"zealchaiwut/{slug}"
        merged = srv._merged_deploy_config(slug, repo)
        srv._enrich_local_working_dirs(repo, merged)
        srv._enrich_deploy_readiness(merged)
        for card in srv._deploy_overview_entries_for(slug, merged):
            cfg = merged.get(card["env"], {})
            card["deploy_ready"] = cfg.get("deploy_ready", card["host"] == "render")
            card["deploy_errors"] = cfg.get("deploy_errors", [])
            card["restart_ready"] = cfg.get("restart_ready", card["host"] == "render")
            card["restart_errors"] = cfg.get("restart_errors", [])
            card["stop_ready"] = cfg.get("stop_ready", card["host"] == "render")
            card["start_ready"] = cfg.get("start_ready", card["host"] == "render")
            card["stop_errors"] = cfg.get("stop_errors", [])
            card["start_errors"] = cfg.get("start_errors", [])
            if card["host"] == "local":
                card["git_sha"] = srv._GIT_SHA
                card["git_commit_msg"] = srv._GIT_COMMIT_MSG
                card["server_started_at"] = srv._STARTED_AT
                card["last_deployed_at"] = srv._deploy_times.get(f"{slug}/{card['env']}")
            environments.append(card)

    return {"environments": environments}


# ── Sprint file archive maintenance route ─────────────────────────────────────

@router.post("/api/maintenance/sprints/cleanup")
def post_sprint_cleanup(body: _SprintCleanupBody):
    """Archive stale per-sprint runtime files for a project's finished sprints."""
    srv = _server()
    if not srv._CLEAN_SPRINT_AVAILABLE:
        raise HTTPException(status_code=503, detail="clean_sprint_files module unavailable")

    project = (body.project or "").strip()
    if not project:
        raise HTTPException(status_code=400, detail="project is required")

    project_root = srv._project_root_path(project)
    sprints_dir = srv._commander_dir(project_root) / "sprints"
    if not sprints_dir.exists():
        return {"archived": [], "kept_count": 0, "dry_run": body.dry_run}

    finished_nums: set[int] = set()
    try:
        for label in srv._finished_sprint_summaries(project).keys():
            m = re.match(r"^sprint-(\d+)$", label)
            if m:
                finished_nums.add(int(m.group(1)))
    except Exception:
        pass

    def _running_check(_dir: Path, n: int) -> bool:
        return srv._is_sprint_running(project_root, f"sprint-{n}")

    try:
        result = srv._clean_sprint_files.run_cleanup(
            sprints_dir,
            dry_run=body.dry_run,
            has_summary_issue=lambda n: n in finished_nums,
            running_check=_running_check,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Archive failed: {exc}") from exc
    return {
        "archived": result["archived"],
        "kept_count": result["kept_count"],
        "dry_run": result["dry_run"],
    }


# ── Plan usage route ──────────────────────────────────────────────────────────

def _plan_config() -> tuple[int | None, int | None, float]:
    """Return (window_token_limit, weekly_token_limit, window_hours) from env."""
    plan_type = os.environ.get("PLAN_TYPE", "").strip()
    window_limit_raw = os.environ.get("WINDOW_TOKEN_LIMIT", "").strip()
    if not plan_type or not window_limit_raw:
        return None, None, 5.0

    try:
        window_limit = int(window_limit_raw)
    except ValueError:
        return None, None, 5.0

    weekly_raw = os.environ.get("WEEKLY_TOKEN_LIMIT", "").strip()
    weekly_limit: int | None = None
    if weekly_raw:
        try:
            weekly_limit = int(weekly_raw)
        except ValueError:
            pass

    window_hours_raw = os.environ.get("WINDOW_DURATION_HOURS", "5").strip()
    try:
        window_hours = float(window_hours_raw)
    except ValueError:
        window_hours = 5.0

    return window_limit, weekly_limit, window_hours


@router.get("/api/plan-usage")
def get_plan_usage():
    """Return Plan Usage data for the rolling token window."""
    window_limit, weekly_limit, window_hours = _plan_config()
    if window_limit is None:
        raise HTTPException(status_code=404, detail="Plan usage not configured")

    window_duration = timedelta(hours=window_hours)
    now_utc = datetime.now(timezone.utc)

    earliest_ts_str = db.get_earliest_token_row_after(None)

    if earliest_ts_str is None:
        return {
            "window_tokens":    0,
            "window_limit":     window_limit,
            "window_pct":       0.0,
            "window_start":     None,
            "window_resets_at": None,
            "seconds_remaining": 0,
            "weekly_tokens":    None,
            "weekly_limit":     weekly_limit,
            "status":           "no_activity",
        }

    def _parse_utc(ts: str) -> datetime:
        if ts.endswith("Z"):
            ts = ts[:-1]
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

    window_start = _parse_utc(earliest_ts_str)

    while True:
        window_end = window_start + window_duration
        if window_end > now_utc:
            break
        window_end_str = window_end.strftime("%Y-%m-%dT%H:%M:%S")
        next_ts_str = db.get_earliest_token_row_after(window_end_str)
        if next_ts_str is None:
            window_tokens_str = window_start.strftime("%Y-%m-%dT%H:%M:%S")
            window_tokens = db.get_window_usage(window_tokens_str)
            window_pct = round(min(window_tokens / window_limit * 100, 100.0), 2)
            weekly_tokens: int | None = None
            if weekly_limit is not None:
                week_start = (now_utc - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
                weekly_tokens = db.get_window_usage(week_start)
            return {
                "window_tokens":    window_tokens,
                "window_limit":     window_limit,
                "window_pct":       window_pct,
                "window_start":     window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_resets_at": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "seconds_remaining": 0,
                "weekly_tokens":    weekly_tokens,
                "weekly_limit":     weekly_limit,
                "status":           "expired",
            }
        window_start = _parse_utc(next_ts_str)

    window_end = window_start + window_duration
    window_start_str = window_start.strftime("%Y-%m-%dT%H:%M:%S")
    window_tokens = db.get_window_usage(window_start_str)
    window_pct = round(min(window_tokens / window_limit * 100, 100.0), 2)
    seconds_remaining = max(0, int((window_end - now_utc).total_seconds()))

    weekly_tokens = None
    if weekly_limit is not None:
        week_start = (now_utc - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        weekly_tokens = db.get_window_usage(week_start)

    return {
        "window_tokens":    window_tokens,
        "window_limit":     window_limit,
        "window_pct":       window_pct,
        "window_start":     window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_resets_at": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds_remaining": seconds_remaining,
        "weekly_tokens":    weekly_tokens,
        "weekly_limit":     weekly_limit,
        "status":           "active",
    }


# ── Estimator health & on-demand estimate routes ──────────────────────────────

@router.get("/api/estimator/health")
def estimator_health():
    """Check whether the estimator agent (claude CLI) is available."""
    available = shutil.which("claude") is not None
    return {"available": available}


@router.post("/api/issues/{issue_id}/estimate")
def estimate_issue_on_demand(request: Request, issue_id: int, repo: str, force: bool = True):
    """Run the issue estimator on demand and apply the size label.

    Returns {"ok": True, "size": "S"|"M"|"L"|"XL"} on success.
    The force param is accepted for API compatibility; the endpoint always runs fresh.
    """
    import json  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    srv = _server()
    srv._slog.event("route.entry", project="dashboard", request_id=request.state.request_id,
                    route="/api/issues/{issue_id}/estimate", method="POST", issue_id=issue_id)
    try:
        issue_data = srv._ei_fetch_issue(issue_id, repo)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        detail = f"Could not fetch issue #{issue_id}: {e}"
        if stderr:
            detail += f" — stderr: {stderr}"
        raise HTTPException(404, detail=detail)

    estimate, error_type = srv._ei_run_estimator(issue_id, issue_data)
    if estimate is None:
        raise HTTPException(
            500,
            detail={"message": f"Estimation failed for #{issue_id}", "error_type": error_type},
        )

    size = estimate.get("size")
    if not size:
        raise HTTPException(500, detail="Estimator returned no size")

    minutes: int = estimate.get("minutes") or srv._minutes_from_letter(size)
    if not estimate.get("minutes"):
        estimate["minutes"] = minutes

    try:
        srv._ei_apply_label(issue_id, repo, size)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        detail = f"Failed to apply size label: {e}"
        if stderr:
            detail += f" — stderr: {stderr}"
        raise HTTPException(500, detail=detail)

    srv._ei_apply_estimated_status(issue_id, repo)

    project_root = srv._project_root_path(repo)
    estimates_dir = srv._commander_dir(project_root) / "estimates"
    estimates_dir.mkdir(parents=True, exist_ok=True)
    estimate_path = estimates_dir / f"issue-{issue_id}.json"
    estimate_path.write_text(json.dumps(estimate, indent=2), encoding="utf-8")

    return {"ok": True, "size": size, "minutes": minutes}
