"""Dev Report endpoint — per-project actionable snapshot (issue #1961).

Serves GET /api/dev-report which returns a structured view of each project's
shipped sprints, stale blockers, waiting sign-offs, and run-ready backlog.
Used by home.html instead of the old /api/brief/daily flow.

Returns 404 when no projects are configured.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import brief_service

router = APIRouter(tags=["dev-report"])


# ── Pydantic response models ──────────────────────────────────────────────────

class ShippedEntry(BaseModel):
    label: Optional[str] = None
    goal: str = ""
    done: int = 0
    pr_number: Optional[int] = None


class FixedEntry(BaseModel):
    issue_number: Optional[int] = None
    title: str = ""


class StaleEntry(BaseModel):
    kind: str = "blocked"
    issue_number: Optional[int] = None
    title: str = ""
    age_days: Optional[float] = None


class WaitingEntry(BaseModel):
    label: Optional[str] = None
    ticket_count: int = 0
    estimated_hours: float = 0.0


class DevReportCounts(BaseModel):
    shipped: int = 0
    fixed: int = 0
    stale: int = 0
    waiting: int = 0


class InProgressEntry(BaseModel):
    sprint_label: Optional[str] = None
    started_at: Optional[str] = None


class UpNextEntry(BaseModel):
    label: Optional[str] = None
    ticket_count: int = 0
    ready: bool = False


class ProjectEntry(BaseModel):
    project: str
    name: str = ""
    status: str = "idle"
    in_progress: Optional[InProgressEntry] = None
    up_next: Optional[UpNextEntry] = None
    shipped: list[ShippedEntry] = []
    fixed: list[FixedEntry] = []
    stale: list[StaleEntry] = []
    waiting: list[WaitingEntry] = []
    counts: DevReportCounts = DevReportCounts()


class DevReport(BaseModel):
    for_date: str
    window_start: str
    window_end: str
    cost: Optional[str] = None
    projects: list[ProjectEntry] = []


# ── helpers ───────────────────────────────────────────────────────────────────

def _project_status(brief: dict) -> str:
    if brief.get("in_progress"):
        return "running"
    if brief.get("blocked"):
        return "blocked"
    return "idle"


def _stale_from_brief(brief: dict, now: datetime) -> list[dict]:
    """Convert brief blocked tickets to stale entries."""
    out = []
    for b in brief.get("blocked", []):
        out.append({
            "kind": b.get("type", "blocked"),
            "issue_number": b.get("issue_number"),
            "title": b.get("title", ""),
            "age_days": None,
        })
    return out


def _build_project_entry(slug: str, proj_meta: dict, brief: dict) -> dict:
    name = proj_meta.get("name") or slug
    status = _project_status(brief)

    shipped = [
        {"label": s.get("label"), "goal": s.get("goal", ""),
         "done": s.get("done", 0), "pr_number": s.get("pr_number")}
        for s in brief.get("shipped", [])
    ]
    waiting = [
        {"label": w.get("label"), "ticket_count": w.get("ticket_count", 0),
         "estimated_hours": w.get("estimated_hours", 0.0)}
        for w in brief.get("waiting_on_you", [])
    ]
    now = datetime.now(timezone.utc)
    stale = _stale_from_brief(brief, now)
    fixed: list[dict] = []

    ip_raw = brief.get("in_progress")
    in_progress = None
    if ip_raw:
        in_progress = {
            "sprint_label": ip_raw.get("sprint_label"),
            "started_at": None,
        }

    up_next_raw = brief.get("up_next")
    up_next = None
    if up_next_raw:
        up_next = {
            "label": up_next_raw.get("label"),
            "ticket_count": up_next_raw.get("ticket_count", 0),
            "ready": bool(up_next_raw.get("ready", False)),
        }

    counts = {
        "shipped": len(shipped),
        "fixed": len(fixed),
        "stale": len(stale),
        "waiting": len(waiting),
    }

    return {
        "project": slug,
        "name": name,
        "status": status,
        "in_progress": in_progress,
        "up_next": up_next,
        "shipped": shipped,
        "fixed": fixed,
        "stale": stale,
        "waiting": waiting,
        "counts": counts,
    }


# ── route ─────────────────────────────────────────────────────────────────────

@router.get("/api/dev-report", response_model=DevReport)
def get_dev_report(date: Optional[str] = None):
    """Per-project dev report: shipped, stale, waiting, and run-ready sprints."""
    try:
        project_list = brief_service._load_projects()
    except Exception:
        project_list = []

    if not project_list:
        raise HTTPException(
            status_code=404,
            detail="No report yet — runs nightly at 05:45",
        )

    d, start, end = brief_service._window(date)

    entries = []
    for proj in project_list:
        repo = proj.get("repo", "")
        slug = repo.split("/")[-1] if repo else repo
        if not slug:
            continue
        try:
            brief = brief_service.build_project_brief(slug, date=d)
        except Exception:
            brief = {}
        entry = _build_project_entry(slug, proj, brief)
        entries.append(entry)

    return {
        "for_date": d,
        "window_start": start,
        "window_end": end,
        "cost": None,
        "projects": entries,
    }
