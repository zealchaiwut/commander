"""Brief assembly endpoints (issue #839).

Two read-only endpoints that assemble a deterministic brief payload from local
DB tables — no LLM, no network. The per-project endpoint serves one project's
sections; the home endpoint rolls up across all projects.

The route handlers are thin; all assembly lives in ``brief_service``. This
router is mounted onto an already-wired router (``sprints``) so no route is
added to ``server.py`` (COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

from typing import Optional

import config
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import projects as _projects_module
from . import brief_artifact
from . import brief_service
from . import brief_summary

router = APIRouter(tags=["brief"])


def _require_brief_enabled() -> None:
    if config.brief_disabled():
        raise HTTPException(404, detail="Brief is disabled")


# ── response models ───────────────────────────────────────────────────────────

class CurrentTicket(BaseModel):
    issue_number: Optional[int] = None
    title: str = ""


class Progress(BaseModel):
    done: int = 0
    total: int = 0
    percent: int = 0


class ShippedSprint(BaseModel):
    label: Optional[str] = None
    goal: str = ""
    features: list[str] = []
    done: int = 0
    skipped: int = 0
    duration: Optional[int] = None
    pr_number: Optional[int] = None
    summary_issue_number: Optional[int] = None


class InProgress(BaseModel):
    sprint_label: Optional[str] = None
    current_ticket: Optional[CurrentTicket] = None
    active_agent: Optional[str] = None
    progress: Progress = Progress()
    elapsed: Optional[int] = None


class UpNext(BaseModel):
    label: Optional[str] = None
    ticket_count: int = 0
    ready: bool = False


class BlockedTicket(BaseModel):
    issue_number: Optional[int] = None
    title: str = ""
    type: str


class ProjectKpis(BaseModel):
    sprints_shipped: int = 0
    tickets_done: int = 0
    in_progress: bool = False
    in_progress_percent: int = 0
    needs_you: int = 0


class ActivityItem(BaseModel):
    time: Optional[str] = None
    source: Optional[str] = None
    message: str = ""


class RanOvernightEntry(BaseModel):
    """A sprint that finished within the overnight window (issue #864)."""
    label: Optional[str] = None
    outcome: str = ""  # "success" | "failure" | "partial"
    summary_issue_number: Optional[int] = None
    brief_path: Optional[str] = None
    project: Optional[str] = None  # set in the home roll-up


class SuggestedNextItem(BaseModel):
    """One advisor suggestion or look-ahead entry for the brief (issue #884)."""
    text: str = ""
    type: str = "suggestion"  # "suggestion" | "look_ahead"
    slug: str = ""


class WaitingSprint(BaseModel):
    """A sprint pending human sign-off (issue #864)."""
    label: Optional[str] = None
    ticket_count: int = 0
    estimated_hours: float = 0.0
    project: Optional[str] = None  # set in the home roll-up


class ProjectBrief(BaseModel):
    project: str
    date: str
    shipped: list[ShippedSprint] = []
    in_progress: Optional[InProgress] = None
    up_next: Optional[UpNext] = None
    blocked: list[BlockedTicket] = []
    kpis: ProjectKpis = ProjectKpis()
    recent_activity: list[ActivityItem] = []
    ran_overnight: list[RanOvernightEntry] = []
    waiting_on_you: list[WaitingSprint] = []
    suggested_next: list[SuggestedNextItem] = []


class GlobalKpis(BaseModel):
    sprints_shipped: int = 0
    tickets_done: int = 0
    in_progress: int = 0
    needs_your_call: int = 0
    awaiting_signoff: int = 0


class Decision(BaseModel):
    project: str
    type: str
    label: Optional[str] = None
    suggested_action: str


class HomeBrief(BaseModel):
    date: str
    global_kpis: GlobalKpis = GlobalKpis()
    decisions: list[Decision] = []
    projects: list[ProjectBrief] = []
    ran_overnight: list[RanOvernightEntry] = []
    waiting_on_you: list[WaitingSprint] = []
    suggested_next: list[SuggestedNextItem] = []


class ProjectSummary(BaseModel):
    project: Optional[str] = None
    date: str
    summary: str
    source: str  # "model" | "fallback"


class HomeSummary(BaseModel):
    date: str
    summary: str
    source: str  # "model" | "fallback"


class DailyArtifact(BaseModel):
    """A stored daily brief artifact (issue #841).

    ``available`` is ``False`` for a date with no stored brief, in which case
    ``brief``/``summary``/``generated_at`` are null and ``message`` carries the
    empty-state text (never an error).
    """
    scope: Optional[str] = None
    project: Optional[str] = None
    date: str
    available: bool
    covering_since: Optional[str] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    generated_at: Optional[str] = None
    summary: Optional[str] = None
    summary_source: Optional[str] = None
    brief: Optional[dict] = None
    message: Optional[str] = None


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/api/projects/{slug}/brief", response_model=ProjectBrief)
def get_project_brief(slug: str, date: Optional[str] = None):
    """Assemble one project's brief for the given (or today's) window."""
    return brief_service.build_project_brief(slug, date=date)


@router.get("/api/brief", response_model=HomeBrief)
def get_home_brief(date: Optional[str] = None):
    """Assemble the home roll-up across all tracked projects."""
    return brief_service.build_home_brief(date=date)


# ── LLM summary (issue #840) ──────────────────────────────────────────────────
# Kept separate from the structured-brief routes above so brief assembly stays
# LLM-free (#839 AC5). The summary path generates+caches a Haiku narrative and
# always falls back to a deterministic templated string (never 5xx — AC6).

@router.get("/api/projects/{slug}/brief/summary", response_model=ProjectSummary)
def get_project_brief_summary(slug: str, date: Optional[str] = None):
    """Return the cached (or freshly generated) summary for a project brief."""
    _require_brief_enabled()
    return brief_summary.get_or_create_project_summary(slug, date=date)


@router.post("/api/projects/{slug}/brief/summary/regenerate",
             response_model=ProjectSummary)
def regenerate_project_brief_summary(slug: str, date: Optional[str] = None):
    """Clear the stored summary and re-invoke the model (Regenerate, AC4)."""
    _require_brief_enabled()
    return brief_summary.get_or_create_project_summary(slug, date=date, force=True)


@router.get("/api/brief/summary", response_model=HomeSummary)
def get_home_brief_summary(date: Optional[str] = None):
    """Return the cached (or freshly generated) one-line home recap (AC7)."""
    _require_brief_enabled()
    return brief_summary.get_or_create_home_summary(date=date)


@router.post("/api/brief/summary/regenerate", response_model=HomeSummary)
def regenerate_home_brief_summary(date: Optional[str] = None):
    """Clear the stored home recap and re-invoke the model (Regenerate)."""
    _require_brief_enabled()
    return brief_summary.get_or_create_home_summary(date=date, force=True)


# ── daily artifact store (issue #841) ─────────────────────────────────────────
# Persist the full daily brief per (project, date) so it is generated once and
# served instantly thereafter. The current day is lazily generated on first
# load; past dates are served from the store, with a clear empty state when none
# was ever stored (never a recompute, never a 5xx).

@router.get("/api/projects/{slug}/brief/daily", response_model=DailyArtifact)
def get_project_daily_brief(slug: str, date: Optional[str] = None):
    """Return the stored (or lazily generated) daily brief for a project."""
    return brief_artifact.get_or_create_project_artifact(slug, date=date)


@router.post("/api/projects/{slug}/brief/daily/regenerate",
             response_model=DailyArtifact)
def regenerate_project_daily_brief(slug: str, date: Optional[str] = None):
    """Rebuild and re-store the daily brief, advancing the timestamp (AC7)."""
    return brief_artifact.get_or_create_project_artifact(slug, date=date, force=True)


def _enrich_home_artifact(artifact: dict) -> None:
    """Embed per-project metadata into the home artifact's project entries.

    Adds ``repo``, ``name``, ``icon``, ``color``, and ``briefSummary`` to each
    entry in ``artifact["brief"]["projects"]`` so the home page can render with
    one HTTP request instead of 1+N (issue #1778 AC2).
    """
    brief = artifact.get("brief")
    if not brief:
        return
    date = artifact.get("date")
    try:
        all_projects = _projects_module.load_projects()
    except Exception:
        all_projects = []
    proj_by_slug: dict = {
        p["repo"].split("/")[-1]: p
        for p in all_projects
        if p.get("repo")
    }
    for p in (brief.get("projects") or []):
        slug = p.get("project") or ""
        cfg = proj_by_slug.get(slug, {})
        repo = cfg.get("repo", "")
        p["repo"] = repo
        p["name"] = cfg.get("name", slug)
        p["icon"] = cfg.get("icon", "ti-folder")
        p["color"] = cfg.get("color", "gray")
        try:
            summary = brief_summary.get_or_create_project_summary(slug, date=date)
            p["briefSummary"] = (summary or {}).get("summary", "")
        except Exception:
            p["briefSummary"] = ""


@router.get("/api/brief/daily", response_model=DailyArtifact)
def get_home_daily_brief(date: Optional[str] = None):
    """Return the home roll-up artifact enriched with per-project metadata.

    Embeds ``briefSummary``, ``repo``, ``name``, ``icon``, and ``color`` so the
    home page needs only this one call (issue #1778 AC2).
    """
    artifact = brief_artifact.get_or_create_home_artifact(date=date)
    if artifact.get("available"):
        _enrich_home_artifact(artifact)
    return artifact


@router.post("/api/brief/daily/regenerate", response_model=DailyArtifact)
def regenerate_home_daily_brief(date: Optional[str] = None):
    """Rebuild and re-store the daily home roll-up, advancing the timestamp."""
    return brief_artifact.get_or_create_home_artifact(date=date, force=True)
