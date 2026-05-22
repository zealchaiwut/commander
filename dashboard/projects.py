"""
Project aggregation layer.
Loads projects.json (auto-creates from TRACKED_REPOS if missing),
fetches GitHub data per project, and computes ETA / progress / agent metrics.
"""
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import db
import github_client

PROJECTS_FILE = Path(__file__).parent / "projects.json"
SPRINT_RE = re.compile(r"^sprint-(\d+)$")

COLOR_HEX = {
    "blue":   "#3b82f6",
    "purple": "#6366f1",
    "green":  "#16a34a",
    "amber":  "#d97706",
    "red":    "#dc2626",
    "pink":   "#ec4899",
    "cyan":   "#0891b2",
    "gray":   "#6b7280",
}
_CYCLE = ["blue", "purple", "green", "amber", "pink", "cyan"]


def _sprint_duration() -> int:
    return int(os.environ.get("SPRINT_DURATION_DAYS", "14"))


# ── project config ─────────────────────────────────────────────────────────────

def load_projects() -> list[dict]:
    if PROJECTS_FILE.exists():
        return json.loads(PROJECTS_FILE.read_text())

    tracked = os.environ.get("TRACKED_REPOS", "").strip()
    repos = [r.strip() for r in tracked.split(",") if r.strip()]
    if not repos:
        try:
            repos = [github_client.repo()]
        except ValueError:
            return []

    projects = []
    for i, repo in enumerate(repos):
        name = repo.split("/")[-1].replace("-", " ").title()
        projects.append({
            "repo": repo,
            "name": name,
            "icon": "ti-folder",
            "color": _CYCLE[i % len(_CYCLE)],
            "active_sprints": {},
        })

    PROJECTS_FILE.write_text(json.dumps(projects, indent=2))
    return projects


def _color_hex(proj: dict) -> str:
    color = proj.get("color", "gray")
    return COLOR_HEX.get(color, color)  # fall back to raw value (e.g. "#abc123")


def _repo_id(repo: str) -> str:
    return repo.replace("/", "-")


# ── ETA calculation ────────────────────────────────────────────────────────────

def _compute_eta(issues: list[dict], sprint_info: Optional[dict]) -> dict:
    duration = _sprint_duration()

    if not sprint_info or not sprint_info.get("started_at"):
        return {"value": "TBD", "sub": "awaiting kickoff", "status": "idle"}

    started = date.fromisoformat(sprint_info["started_at"])
    end     = started + timedelta(days=duration)
    today   = date.today()
    elapsed = (today - started).days

    open_issues   = [i for i in issues if i.get("state") == "open"]
    closed_issues = [i for i in issues if i.get("state") == "closed"]

    if today > end:
        late = (today - end).days
        return {"value": f"{late}d late", "sub": "overdue", "status": "late"}

    # closure rate
    if elapsed >= 7:
        cutoff = (today - timedelta(days=7)).isoformat()
        recent = [i for i in closed_issues if (i.get("updatedAt") or "")[:10] >= cutoff[:10]]
        rate = len(recent) / 7.0
    else:
        rate = len(closed_issues) / max(elapsed, 1)

    remaining  = len(open_issues)
    days_left  = (end - today).days
    pct_time   = elapsed / duration if duration else 0

    has_blocker = any(
        any(l["name"] == "blocked" for l in i.get("labels", []))
        for i in open_issues
    )
    if has_blocker:
        return {"value": "TBD", "sub": "blocked", "status": "late"}

    if remaining == 0:
        return {"value": "done", "sub": "all closed", "status": "good"}

    if rate == 0:
        return {"value": "TBD", "sub": "no velocity", "status": "idle"}

    days_needed = remaining / rate
    if days_needed < 1:
        val = f"~{max(1, int(days_needed * 24))}h"
    else:
        val = f"~{int(days_needed)}d"

    if days_needed <= days_left:
        if days_needed < days_left * 0.5:
            sub, status = "ahead of schedule", "good"
        elif pct_time >= 0.8:
            sub, status = "watch closely", "warning"
        else:
            sub, status = "on track", "good"
    else:
        sub, status = "at risk", "warning"

    return {"value": val, "sub": sub, "status": status}


def _bar_status(sprint_info: Optional[dict], eta_status: str) -> str:
    if not sprint_info:
        return "idle"
    if eta_status == "late":
        return "late"
    started  = date.fromisoformat(sprint_info["started_at"])
    elapsed  = (date.today() - started).days
    pct_time = elapsed / _sprint_duration()
    return "warning" if pct_time >= 0.8 else "good"


def _compute_progress(issues: list[dict]) -> dict:
    total  = len(issues)
    closed = sum(1 for i in issues if i.get("state") == "closed")
    pct    = round(closed / total * 100) if total else 0
    return {"closed": closed, "total": total, "pct": pct}


# ── agent mapping ─────────────────────────────────────────────────────────────

def _agent_repo(agent: dict) -> str:
    """Extract the repo/folder identifier from the agent name.

    New format: "role·repo·branch·#short" — use the repo field (index 1).
    Old format: plain basename — fall back to working_dir matching.
    """
    parts = (agent.get("name") or "").split("·")
    if len(parts) == 4:
        return parts[1].lower()
    return (agent.get("working_dir") or "").rstrip("/").split("/")[-1].lower()


def _project_agents(proj: dict, agents: list[dict]) -> list[dict]:
    repo_name = proj["repo"].split("/")[-1].lower()
    return [a for a in agents if _agent_repo(a) == repo_name]


# ── ticket status ─────────────────────────────────────────────────────────────

def _ticket_status(issue: dict) -> str:
    labels = {l["name"] for l in issue.get("labels", [])}
    if "blocked" in labels:    return "blocked"
    if "UAT" in labels:        return "UAT"
    if "SIT" in labels:        return "SIT"
    if "in-progress" in labels: return "in-progress"
    return "backlog"


# ── Token cost helpers ─────────────────────────────────────────────────────────

_INPUT_COST_PER_M  = 3.0   # USD per million input tokens (Claude Sonnet)
_OUTPUT_COST_PER_M = 15.0  # USD per million output tokens (Claude Sonnet)


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens * _INPUT_COST_PER_M + output_tokens * _OUTPUT_COST_PER_M) / 1_000_000,
        2,
    )


# ── public API ────────────────────────────────────────────────────────────────

def get_all_projects(agents: list[dict]) -> dict:
    projects = load_projects()
    result   = []
    total_open = 0
    total_uat  = 0
    active_sprint_set: set[str] = set()

    for proj in projects:
        repo     = proj["repo"]
        as_map   = proj.get("active_sprints", {})
        sprint_num  = max((int(k) for k in as_map), default=None)
        sprint_info = as_map.get(str(sprint_num)) if sprint_num else None

        issues: list[dict] = []
        if sprint_num:
            try:
                issues = github_client.list_issues(sprint_num, repo_name=repo)
            except Exception:
                pass

        open_i = [i for i in issues if i.get("state") == "open"]
        uat_i  = [i for i in issues if any(l["name"] == "UAT" for l in i.get("labels", []))]

        if sprint_num and open_i:
            active_sprint_set.add(f"{repo}:sprint-{sprint_num}")

        total_open += len(open_i)
        total_uat  += len(uat_i)

        eta      = _compute_eta(issues, sprint_info)
        progress = _compute_progress(issues)
        proj_agents = _project_agents(proj, agents)

        result.append({
            "repo":          repo,
            "id":            _repo_id(repo),
            "name":          proj.get("name", repo.split("/")[-1]),
            "icon":          proj.get("icon", "ti-folder"),
            "color":         _color_hex(proj),
            "current_sprint": sprint_num,
            "sprint_theme":  (sprint_info or {}).get("theme", ""),
            "progress":      progress,
            "bar_status":    _bar_status(sprint_info, eta["status"]),
            "eta":           eta,
            "agents":        [
                {"name": a["name"], "status": a["status"], "session_id": a["session_id"]}
                for a in proj_agents
            ],
            "uatCount":  len(uat_i),
            "openCount": len(open_i),
        })

    working_agents = sum(1 for a in agents if a.get("status") == "working")

    # Global token totals for today
    global_tok = db.get_tokens_today()
    global_total = global_tok["input_tokens"] + global_tok["output_tokens"]

    return {
        "projects": result,
        "metrics": {
            "active_sprints":  len(active_sprint_set),
            "open_tickets":    total_open,
            "awaiting_uat":    total_uat,
            "tokens_today":    global_total,
            "cost_today_usd":  _cost_usd(global_tok["input_tokens"], global_tok["output_tokens"]),
            "working_agents":  working_agents,
        },
    }


def get_project_details(repo: str, agents: list[dict]) -> dict:
    projects   = load_projects()
    proj       = next((p for p in projects if p["repo"] == repo), None)
    if not proj:
        return {"tickets": [], "agents": [], "github_url": f"https://github.com/{repo}/issues"}

    as_map     = proj.get("active_sprints", {})
    sprint_num = max((int(k) for k in as_map), default=None)

    issues: list[dict] = []
    if sprint_num:
        try:
            issues = github_client.list_issues(sprint_num, repo_name=repo)
        except Exception:
            pass

    # For no-sprint projects, show recent closed tickets
    if not sprint_num:
        try:
            issues = github_client.list_recent_closed(repo_name=repo, limit=5)
        except Exception:
            pass

    # Sort by updatedAt desc; show open tickets first (up to 5)
    open_issues = sorted(
        [i for i in issues if i.get("state") == "open"],
        key=lambda i: i.get("updatedAt", ""), reverse=True
    )[:5]

    feature_branches: dict[int, str] = {}
    try:
        feature_branches = github_client.list_feature_branches(repo_name=repo)
    except Exception:
        pass

    tickets = []
    for issue in open_issues:
        status = _ticket_status(issue)
        tickets.append({
            "number":         issue["number"],
            "title":          issue["title"],
            "status":         status,
            "url":            issue["url"],
            "assignee":       (issue.get("assignees") or [{}])[0].get("login"),
            "updated_at":     issue.get("updatedAt", ""),
            "is_uat":         status == "UAT",
            "feature_branch": feature_branches.get(issue["number"]),
        })

    proj_agents = _project_agents(proj, agents)
    agent_details = [
        {
            "session_id":  a["session_id"],
            "name":        a["name"],
            "status":      a["status"],
            "last_tool":   a.get("last_tool"),
            "last_seen":   a["last_seen"],
            "working_dir": a.get("working_dir", ""),
        }
        for a in proj_agents
    ]

    github_url = (
        f"https://github.com/{repo}/issues?q=is:open+label:sprint-{sprint_num}"
        if sprint_num else f"https://github.com/{repo}/issues"
    )

    # Per-project token data (keyed by repo basename to match what the hook stores)
    proj_name = repo.split("/")[-1]
    proj_tok  = db.get_tokens_today(project=proj_name)
    proj_total = proj_tok["input_tokens"] + proj_tok["output_tokens"]

    return {
        "tickets":       tickets,
        "agents":        agent_details,
        "github_url":    github_url,
        "tokens_today":  proj_total,
        "cost_today_usd": _cost_usd(proj_tok["input_tokens"], proj_tok["output_tokens"]),
    }
