from __future__ import annotations
import os, sys, re, uuid, subprocess, json
from pathlib import Path
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db
import projects as projects_module
import github_client
from services.logging import log as _slog

_PROJECTS_BASE = Path.home() / "dev"

router = APIRouter()

def _server():
    import server
    return server


# ── mis_sizing module import ──────────────────────────────────────────────────

try:
    import mis_sizing as _mis_sizing
    _MIS_SIZING_AVAILABLE = True
except ImportError:
    _mis_sizing = None  # type: ignore[assignment]
    _MIS_SIZING_AVAILABLE = False

_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")


# ── Local helpers ─────────────────────────────────────────────────────────────

def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _gh_error(e: subprocess.CalledProcessError) -> HTTPException:
    detail = e.stderr.strip() if e.stderr else str(e)
    if "rate limit" in detail.lower():
        return HTTPException(status_code=429, detail="GitHub API rate limit reached. It refills hourly; retry shortly.")
    return HTTPException(status_code=502, detail=detail)


def _get_sprint_issues(project: str, sprint_label: str) -> list[dict]:
    """Fetch open issues whose primary sprint label matches sprint_label."""
    issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)

    def _primary_sprint_label(iss: dict) -> Optional[str]:
        for lbl in iss.get("labels", []):
            name = lbl.get("name", "")
            if _SPRINT_LABEL_RE.match(name):
                return name
        return None

    return [iss for iss in issues if _primary_sprint_label(iss) == sprint_label]


# ── Pydantic models ───────────────────────────────────────────────────────────

class MisSizingActionBody(BaseModel):
    action: str
    new_size: Optional[str] = None
    note: Optional[str] = None


class MisSizingConfigBody(BaseModel):
    tier_threshold: int
    min_events: int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/sprints/{sprint_label}/mis-sizing-flags")
def get_mis_sizing_flags(sprint_label: str, project: str):
    """Return the current mis-sizing flags for a sprint.

    Does NOT regenerate; returns whatever is persisted on disk.
    Call POST /generate to regenerate from current sprint issues.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not _MIS_SIZING_AVAILABLE:
        return {"sprint_label": sprint_label, "flags": [], "audit_log": [], "generated_at": None, "config": {}}
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    return _mis_sizing.load_flags(commander, sprint_label)


@router.post("/api/sprints/{sprint_label}/mis-sizing-flags/generate")
def generate_mis_sizing_flags(sprint_label: str, project: str):
    """Regenerate mis-sizing flags for a sprint from current GitHub issue data."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not _MIS_SIZING_AVAILABLE:
        return {"sprint_label": sprint_label, "flags": [], "audit_log": [], "generated_at": None, "config": {}}

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    return _mis_sizing.generate_and_save_flags(commander, sprint_label, sprint_issues)


@router.post("/api/sprints/{sprint_label}/mis-sizing-flags/{issue_id}/action")
def act_on_mis_sizing_flag(sprint_label: str, issue_id: int, body: MisSizingActionBody, project: str):
    """Take an action on a mis-sizing flag: approved, reestimated, or dismissed.

    For reestimated, also updates the estimate file and GitHub size label.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not _MIS_SIZING_AVAILABLE:
        raise HTTPException(501, detail="Mis-sizing module not available")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    try:
        data = _mis_sizing.record_action(
            commander,
            sprint_label,
            issue_id,
            body.action,
            new_size=body.new_size,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except KeyError as e:
        raise HTTPException(404, detail=str(e))

    # If re-estimating, update the estimate file and GitHub label
    if body.action == "reestimated" and body.new_size:
        estimates_dir = commander / "estimates"
        est_path = estimates_dir / f"issue-{issue_id}.json"
        if est_path.exists():
            try:
                est_data = json.loads(est_path.read_text(encoding="utf-8"))
                est_data["size"] = body.new_size
                est_data["minutes"] = _mis_sizing.CANONICAL_MINUTES.get(body.new_size, 0)
                est_path.write_text(json.dumps(est_data, indent=2), encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass
        # Update GitHub label: remove old size-*, add new size-*
        try:
            size_labels = [f"size-{s}" for s in _mis_sizing.SIZE_TIERS]
            github_client.update_labels(
                issue_id,
                add=[f"size-{body.new_size}"],
                remove=size_labels,
                repo_name=project,
            )
        except subprocess.CalledProcessError:
            pass  # Label update is best-effort

    return data


@router.get("/api/mis-sizing/history")
def get_mis_sizing_history(project: str):
    """Return the full mis-sizing history for a project."""
    if not _MIS_SIZING_AVAILABLE:
        return {"version": 1, "events_by_label": {}, "last_rebuilt": None}
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    return _mis_sizing.load_history(commander)


@router.post("/api/mis-sizing/rebuild")
def rebuild_mis_sizing_history(project: str):
    """Rebuild mis-sizing history by scanning all sprint state files.

    Fetches issue labels from GitHub for completed tickets (one batch call).
    This is an expensive operation — run once or when history is stale.
    """
    if not _MIS_SIZING_AVAILABLE:
        raise HTTPException(501, detail="Mis-sizing module not available")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"

    # Collect all completed tickets from sprint state files
    raw_completed: list[dict] = []
    if sprints_dir.exists():
        for state_path in sorted(sprints_dir.glob("sprint-*-state.json")):
            m = re.search(r"sprint-(\d+)-state", state_path.name)
            sprint_label_str = f"sprint-{m.group(1)}" if m else state_path.stem
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for iss in state_data.get("issues", []):
                if iss.get("status") not in ("done", "passed"):
                    continue
                num = iss.get("number")
                if num is None:
                    continue
                # Only include tickets that have an estimate file
                est_path = estimates_dir / f"issue-{num}.json"
                if not est_path.exists():
                    continue
                try:
                    est_data = json.loads(est_path.read_text(encoding="utf-8"))
                    estimated_size = est_data.get("size") or None
                except (json.JSONDecodeError, OSError):
                    continue
                if not estimated_size:
                    continue
                raw_completed.append({
                    "number": num,
                    "sprint": sprint_label_str,
                    "estimated_size": estimated_size,
                    "coder_started_at": iss.get("coder_started_at"),
                    "tester_finished_at": iss.get("tester_finished_at"),
                    "status_changed_at": iss.get("status_changed_at"),
                })

    if not raw_completed:
        history = _mis_sizing.build_history_from_completed([])
        _mis_sizing.save_history(commander, history)
        return {"message": "No completed estimated tickets found", "total_events": 0, "labels_with_history": 0}

    # Batch-fetch labels for all completed issues from GitHub
    issue_numbers = {c["number"] for c in raw_completed}
    labels_by_num: dict[int, list[str]] = {}
    try:
        repo = github_client.get_repo_for_operation(project)
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo,
             "--state", "all", "--json", "number,labels", "--limit", "1000"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            for iss in json.loads(result.stdout or "[]"):
                n = iss.get("number")
                if n in issue_numbers:
                    labels_by_num[n] = [
                        lbl["name"] for lbl in iss.get("labels", [])
                    ]
    except Exception:
        pass  # Proceed without labels — events will be skipped

    # Attach labels to completed tickets
    for rec in raw_completed:
        rec["labels"] = labels_by_num.get(rec["number"], [])

    history = _mis_sizing.build_history_from_completed(raw_completed)
    _mis_sizing.save_history(commander, history)

    total_events = sum(len(v) for v in history.get("events_by_label", {}).values())
    labels_count = len(history.get("events_by_label", {}))
    return {
        "message": f"History rebuilt: {total_events} events across {labels_count} labels",
        "labels_with_history": labels_count,
        "total_events": total_events,
        "last_rebuilt": history.get("last_rebuilt"),
    }


@router.get("/api/mis-sizing/config")
def get_mis_sizing_config(project: str):
    """Return the current mis-sizing detection thresholds."""
    if not _MIS_SIZING_AVAILABLE:
        return {"tier_threshold": 2, "min_events": 2}
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    return _mis_sizing.load_config(commander)


@router.post("/api/mis-sizing/config")
def update_mis_sizing_config(body: MisSizingConfigBody, project: str):
    """Update the mis-sizing detection thresholds."""
    if not _MIS_SIZING_AVAILABLE:
        raise HTTPException(501, detail="Mis-sizing module not available")
    if body.tier_threshold < 1 or body.tier_threshold > 3:
        raise HTTPException(400, detail="tier_threshold must be 1–3")
    if body.min_events < 1:
        raise HTTPException(400, detail="min_events must be >= 1")
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    config = {"tier_threshold": body.tier_threshold, "min_events": body.min_events}
    _mis_sizing.save_config(commander, config)
    return config
