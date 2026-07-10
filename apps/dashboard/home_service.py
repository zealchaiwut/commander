"""Home-page data service — extracted from startup.py (issue #1786).

Uses aggregate_cache for per-project caching. Exposes home_project_data which
returns a per-project dict with cache: {hit, ttl_s} metadata added.

Public API::

    home_project_data(proj, running_sprints, sprint_statuses) -> dict
    invalidate_home(project: str) -> None          # project = "owner/repo"
    invalidate_home_by_slug(slug: str) -> None     # slug = repo basename
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_DIR = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_SERVICES_DIR), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Service imports ───────────────────────────────────────────────────────────

import aggregate_cache  # noqa: E402
import github_client  # noqa: E402
import projects as _projects_module  # noqa: E402
import services.sprint_manager.settings_repo as _settings_repo  # noqa: E402
from services.sprint_manager.settings_schema import APP_CONFIG_KEY  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

_HOME_NAMESPACE = "home"
_HOME_CACHE_TTL = 30.0  # seconds

_PROJECTS_BASE = Path.home() / "dev"
_SPRINTS_DIR = _DASHBOARD_ROOT / "sprints"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _parse_summary_file(path: Path) -> dict:
    name = path.stem
    m = re.match(r"sprint-(\d+)-summary-(\d{4}-\d{2}-\d{2})", name)
    sprint_num = int(m.group(1)) if m else None
    date = m.group(2) if m else ""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {"sprint_num": sprint_num, "date": date, "status": "unknown"}
    status_m = re.search(r"^## Sprint \S+ — (\S+)", content, re.MULTILINE)
    status = status_m.group(1) if status_m else "unknown"
    return {"sprint_num": sprint_num, "date": date, "status": status}


def _project_identity(repo: str, proj: dict, slug: str) -> dict[str, str]:
    icon = proj.get("icon", "ti-folder")
    color = proj.get("color", "gray")
    name = proj.get("name", slug)
    try:
        proj_override = _settings_repo.get_setting_scoped("project", APP_CONFIG_KEY, project=repo)
        if proj_override.get("icon"):
            icon = proj_override["icon"]
        if proj_override.get("color"):
            color = proj_override["color"]
        if proj_override.get("display_name"):
            name = proj_override["display_name"]
    except Exception:
        pass
    return {"name": name, "icon": icon, "color": color}


# ── Public API ─────────────────────────────────────────────────────────────────

def home_project_data(
    proj: dict,
    running_sprints: list[dict],
    sprint_statuses: Optional[dict] = None,
) -> dict:
    """Return per-project home payload for *proj*, with cache: {hit, ttl_s} metadata.

    Data is cached 30 s per project (keyed by owner/repo). Always returns a
    valid dict — failing GitHub calls degrade to an idle sentinel.

    Args:
        proj: project dict from projects.json (must have "repo" key).
        running_sprints: list of currently-running sprint dicts.
        sprint_statuses: in-memory sprint-status dict (from server._sprint_statuses).
    """
    if sprint_statuses is None:
        sprint_statuses = {}

    repo = proj["repo"]
    slug = repo.split("/")[-1]
    identity = _project_identity(repo, proj, slug)
    name = identity["name"]
    icon = identity["icon"]
    color = identity["color"]

    # ── Cache hit ─────────────────────────────────────────────────────────────
    cached = aggregate_cache.get(repo, _HOME_NAMESPACE)
    if cached is not None:
        data, meta = cached
        return {**data, "cache": {"hit": True, "ttl_s": meta.ttl_s}}

    # ── Cache miss: compute ───────────────────────────────────────────────────

    def _idle() -> dict:
        sentinel: dict = {
            "name": name, "slug": slug, "repo": repo, "icon": icon, "color": color,
            "status": "idle", "uat_count": 0, "backlog_count": 0,
            "last_activity_at": None,
        }
        aggregate_cache.store(repo, _HOME_NAMESPACE, sentinel, _HOME_CACHE_TTL)
        return {**sentinel, "cache": {"hit": False, "ttl_s": int(_HOME_CACHE_TTL)}}

    try:
        all_open = github_client.list_all_open_issues(repo_name=repo)
    except Exception:
        return _idle()

    proj_running = [r for r in running_sprints if r["project"] == repo]
    sprint_running_field: dict | None = None
    if proj_running:
        r0 = proj_running[0]
        status_data = sprint_statuses.get((r0["project"], r0["sprint_label"]), {})
        start_ts = status_data.get("start_timestamp")
        elapsed_sec = 0
        if start_ts:
            try:
                start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                elapsed_sec = int((datetime.now(timezone.utc) - start_dt).total_seconds())
            except Exception:
                pass
        sprint_running_field = {"label": r0["sprint_label"], "elapsed_sec": elapsed_sec}

    uat_issues = [
        i for i in all_open if any(lbl["name"] == "UAT" for lbl in i.get("labels", []))
    ]
    backlog_issues = [i for i in all_open if github_client.classify_issue(i) == "backlog"]

    if proj_running:
        status = "running"
    elif uat_issues:
        status = "uat-pending"
    else:
        status = "idle"

    last_activity_at: str | None = None
    issue_timestamps = [i.get("updatedAt") for i in all_open if i.get("updatedAt")]
    if issue_timestamps:
        last_activity_at = max(issue_timestamps)

    last_sprint_data: dict | None = None
    seen_dirs: set[str] = set()
    for sprints_dir in [
        _commander_dir(_project_root_path(repo)) / "sprints",
        _SPRINTS_DIR,
    ]:
        key_str = str(sprints_dir.resolve())
        if not sprints_dir.exists() or key_str in seen_dirs:
            continue
        seen_dirs.add(key_str)
        summary_files = sorted(
            sprints_dir.glob("*-summary-*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if summary_files and last_sprint_data is None:
            try:
                meta = _parse_summary_file(summary_files[0])
                if meta.get("date"):
                    sprint_ts = meta["date"] + "T00:00:00Z"
                    if last_activity_at is None or sprint_ts > last_activity_at:
                        last_activity_at = sprint_ts
                last_sprint_data = {
                    "sprint_num": meta.get("sprint_num"),
                    "date": meta.get("date"),
                    "status": meta.get("status"),
                }
            except Exception:
                pass

    result: dict = {
        "name": name,
        "slug": slug,
        "repo": repo,
        "icon": icon,
        "color": color,
        "status": status,
        "uat_count": len(uat_issues),
        "backlog_count": len(backlog_issues),
        "last_activity_at": last_activity_at,
    }
    if sprint_running_field is not None:
        result["sprint_running"] = sprint_running_field
    if last_sprint_data is not None:
        result["last_sprint"] = last_sprint_data

    aggregate_cache.store(repo, _HOME_NAMESPACE, result, _HOME_CACHE_TTL)
    return {**result, "cache": {"hit": False, "ttl_s": int(_HOME_CACHE_TTL)}}


def invalidate_home(project: str) -> None:
    """Evict the home cache entry for *project* (owner/repo)."""
    aggregate_cache.invalidate(project, _HOME_NAMESPACE)


def invalidate_home_by_slug(slug: str) -> None:
    """Evict the home cache entry for the project with basename *slug*."""
    try:
        all_projects = _projects_module.load_projects()
        matched = next(
            (p for p in all_projects
             if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
            None,
        )
        if matched:
            aggregate_cache.invalidate(matched["repo"], _HOME_NAMESPACE)
    except Exception:
        pass
