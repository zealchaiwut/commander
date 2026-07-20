"""Server-computed Board aggregate — issue #1636.

Single-pass assembly of the full board payload from the local issues mirror
and SQLite. Zero ``gh`` subprocess calls are made per request — all reads come
from ``github_client.cached_open_issues_with_body`` (mirror-backed) and the
``db`` / ``sprint_state`` modules (SQLite only).

The response shape:

    {
      project, generated_at,
      sections: {
        running[], needs_rework[], ready_to_merge[], draft[],
        lineage[], backlog: {count, tickets[]}
      },
      capacity,
      summaries: {<label>: <summary>}
    }

Every sprint card in the non-backlog sections carries:
  lifecycle_state, tickets, mini_rail, dep_order, estimate_hours, run_stats

Rerun chains (≥ 2 sprints sharing the same base label or linked via
parent_label) are collapsed into ONE lineage entry instead of being spread
across multiple sections. The entry includes a ``chain`` list of all sprint
labels in the group.

No caching is added here — caching is deferred to the next ticket.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import github_client  # noqa: E402


def _run_stats_service():
    """Deferred import of run_stats_service (avoids relative-import failures in tests)."""
    try:
        from routers import run_stats_service as _rss  # noqa: PLC0415
        return _rss
    except ImportError:
        import run_stats_service as _rss  # noqa: PLC0415
        return _rss

_PROJECTS_BASE = Path.home() / "dev"

_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(?:\.\d+)?$")
_SUMMARY_TITLE_RE = re.compile(r"^Sprint (\d+(?:\.\d+)*)\s+Executive Summary$")

# States that indicate a sprint has had at least one run attempt.
_RUN_INDICATOR_STATES = frozenset({
    "running", "ready_to_merge", "needs_rework", "failed",
    "partial_finished", "completed",
})

# Lifecycle rank used for deduplication (higher = wins).
_MERGE_STATE_RANK: dict[str, int] = {
    "deleted": 100, "completed": 90, "ready_to_merge": 50,
    "needs_rework": 40, "failed": 35, "partial_finished": 30,
    "running": 25, "planned": 20, "draft": 15, "unknown": 10,
}

_SIZE_MINUTES: dict[str, int] = {"S": 5, "M": 15, "L": 30, "XL": 60}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _server():
    """Deferred monolith import — avoids circular import at module load time."""
    import server  # noqa: PLC0415
    return server


def _project_root(project: str) -> Path:
    slug = project.split("/")[-1] if "/" in project else project
    return _PROJECTS_BASE / slug


def _commander_dir(project: str) -> Path:
    return _project_root(project) / ".commander"


def _estimates_dir(project: str) -> Path:
    return _commander_dir(project) / "estimates"


def _sprints_dir(project: str) -> Path:
    return _commander_dir(project) / "sprints"


# ── Sprint label helpers ──────────────────────────────────────────────────────

def _sprint_num(label: str) -> int:
    m = re.match(r"^sprint-(\d+)", label)
    return int(m.group(1)) if m else 0


def _sprint_sub_index(label: str) -> int:
    m = re.match(r"^sprint-\d+\.(\d+)", label)
    return int(m.group(1)) if m else 0


def _sprint_base(label: str) -> str:
    m = re.match(r"^(sprint-\d+)", label)
    return m.group(1) if m else label


def _sprint_sort_key(label: str) -> tuple[int, int]:
    m = re.match(r"^sprint-(\d+)(?:\.(\d+))?$", label)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2) or 0))


# ── Plan file helpers ─────────────────────────────────────────────────────────

def _read_plan_json(project: str, sprint_label: str) -> Optional[dict]:
    """Read a sprint plan JSON file from disk. Returns None on error/missing."""
    path = _sprints_dir(project) / f"{sprint_label}-plan.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {"tickets": raw} if isinstance(raw, list) else (raw if isinstance(raw, dict) else None)
    except (OSError, json.JSONDecodeError):
        return None


# ── Issue helpers ─────────────────────────────────────────────────────────────

def _issue_sprint_labels(iss: dict) -> list[str]:
    """Extract sprint label strings from an issue's labels list."""
    return [
        (lbl["name"] if isinstance(lbl, dict) else lbl)
        for lbl in iss.get("labels", [])
        if _SPRINT_LABEL_RE.match(lbl["name"] if isinstance(lbl, dict) else lbl)
    ]


# ── Estimate helpers ──────────────────────────────────────────────────────────

def _read_estimate(estimates_dir: Path, issue_num: int) -> Optional[dict]:
    path = estimates_dir / f"issue-{issue_num}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_issue_estimate(iss: dict, estimates_dir: Path) -> dict:
    """Return {size, files, estimated} for an issue — disk estimate or label fallback."""
    num = iss["number"]
    est = _read_estimate(estimates_dir, num)
    if est:
        size = est.get("size") or est.get("estimate")
        files = est.get("files_likely_affected") or []
        return {"size": size, "files": list(files), "estimated": bool(size)}
    for lbl in iss.get("labels", []):
        name = lbl["name"] if isinstance(lbl, dict) else lbl
        if name in ("size-S", "size-M", "size-L", "size-XL"):
            return {"size": name.replace("size-", ""), "files": [], "estimated": True}
    return {"size": None, "files": [], "estimated": False}


def _compute_estimate_hours(sprint_issues: list[dict], estimates_dir: Path) -> float:
    """Sum estimate minutes for all sprint tickets; return as hours."""
    total = sum(
        _SIZE_MINUTES.get(_resolve_issue_estimate(iss, estimates_dir).get("size") or "", 0)
        for iss in sprint_issues
    )
    return round(total / 60, 2)


# ── Mini-rail (preview-dag) ───────────────────────────────────────────────────

def _build_mini_rail(sprint_issues: list[dict], estimates_dir: Path) -> dict:
    """Preview-DAG mini-rail for a sprint. Cache-only reads; zero gh calls."""
    pending = [i for i in sprint_issues if github_client.classify_issue(i) == "backlog"]
    pending.sort(key=lambda i: i["number"])

    ticket_files: list[dict] = []
    unestimated: list[str] = []
    for iss in pending:
        num = iss["number"]
        tid = f"#{num}"
        resolved = _resolve_issue_estimate(iss, estimates_dir)
        if not resolved["estimated"]:
            unestimated.append(tid)
        ticket_files.append({"id": tid, "number": num, "title": iss.get("title", ""), "files": resolved["files"]})

    # Pairwise file conflicts
    conflicts: list[dict] = []
    for i in range(len(ticket_files)):
        for j in range(i + 1, len(ticket_files)):
            a, b = ticket_files[i], ticket_files[j]
            shared = sorted(set(a["files"]) & set(b["files"]))
            if shared:
                conflicts.append({"ticket1_id": a["number"], "ticket2_id": b["number"], "shared_files": shared})

    partial = bool(unestimated)

    # DAG levels via server._build_dag when available
    levels: list[list[str]] = []
    same_level_conflicts = conflicts
    try:
        srv = _server()
        if getattr(srv, "_DAG_BUILDER_AVAILABLE", False) and ticket_files:
            dag_tickets = [{"id": tf["id"], "files_touched": tf["files"]} for tf in ticket_files]
            result = srv._build_dag(dag_tickets)
            if not isinstance(result, getattr(srv, "_CycleError", type(None))):
                levels = [sorted(layer, key=lambda t: int(t.lstrip("#"))) for layer in result.layers]
                level_of: dict[str, int] = {}
                for li, layer in enumerate(levels):
                    for tid in layer:
                        level_of[tid] = li
                same_level_conflicts = [
                    c for c in conflicts
                    if (
                        level_of.get(f"#{c['ticket1_id']}") is not None
                        and level_of.get(f"#{c['ticket1_id']}") == level_of.get(f"#{c['ticket2_id']}")
                    )
                ]
            else:
                levels = []
        elif ticket_files:
            levels = [[tf["id"] for tf in ticket_files]]
    except Exception:
        levels = [[tf["id"] for tf in ticket_files]] if ticket_files else []

    return {
        "levels": levels,
        "conflicts": same_level_conflicts,
        "conflict_count": len(same_level_conflicts),
        "unestimated": unestimated,
        "partial": partial,
    }


# ── Dep-order ─────────────────────────────────────────────────────────────────

def _build_dep_order(sprint_issues: list[dict], estimates_dir: Path) -> dict:
    """Dep-order hints from file-overlap DAG. Uses cached issues; zero gh calls."""
    pending = [i for i in sprint_issues if github_client.classify_issue(i) == "backlog"]
    ticket_files = []
    for iss in pending:
        resolved = _resolve_issue_estimate(iss, estimates_dir)
        ticket_files.append({"id": iss["number"], "title": iss.get("title", ""), "files": resolved["files"]})

    empty = {"has_cycle": False, "cycles": [], "in_cycle_tickets": [], "dep_hints": {}, "pending_count": len(pending)}

    try:
        srv = _server()
        build_dag = getattr(srv, "_build_dag", None)
        if build_dag is None:
            return {**empty, "warning": "dag_builder_unavailable"}

        dag_tickets = [{"id": str(tf["id"]), "files_touched": tf["files"]} for tf in ticket_files]
        title_map = {str(tf["id"]): tf["title"] for tf in ticket_files}
        result = build_dag(dag_tickets)
        CycleError = getattr(srv, "_CycleError", None)

        if CycleError and isinstance(result, CycleError):
            in_cycle = sorted({int(tid) for cycle in result.cycles for tid in cycle})
            return {"has_cycle": True, "cycles": result.cycles, "in_cycle_tickets": in_cycle,
                    "dep_hints": {}, "pending_count": len(pending)}

        upstream: dict[str, list[dict]] = {str(tf["id"]): [] for tf in ticket_files}
        downstream: dict[str, list[dict]] = {str(tf["id"]): [] for tf in ticket_files}
        for src, dst in result.edges:
            downstream.setdefault(src, []).append({"id": int(dst), "title": title_map.get(dst, "")})
            upstream.setdefault(dst, []).append({"id": int(src), "title": title_map.get(src, "")})

        dep_hints: dict[int, dict] = {}
        for tf in ticket_files:
            tid = str(tf["id"])
            u, d = upstream.get(tid, []), downstream.get(tid, [])
            if u or d:
                dep_hints[tf["id"]] = {"upstream": u, "downstream": d}

        return {"has_cycle": False, "cycles": [], "in_cycle_tickets": [], "dep_hints": dep_hints, "pending_count": len(pending)}
    except Exception:
        return empty


# ── Inline helpers for aggregate fields ──────────────────────────────────────

def _read_sprint_goal(project: str, label: str) -> str:
    """Read sprint goal from .commander/sprints/<label>-goal.txt."""
    try:
        path = _sprints_dir(project) / f"{label}-goal.txt"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def _build_conflicts_inline(sprint_issues: list[dict], est_dir: Path) -> dict:
    """Compute conflict pairs with titles. Zero gh calls."""
    pending = [i for i in sprint_issues if github_client.classify_issue(i) == "backlog"]
    ticket_files = []
    for iss in pending:
        resolved = _resolve_issue_estimate(iss, est_dir)
        ticket_files.append({
            "id": iss["number"],
            "title": iss.get("title", ""),
            "files": set(resolved["files"]),
        })
    conflicts = []
    for i in range(len(ticket_files)):
        for j in range(i + 1, len(ticket_files)):
            a, b = ticket_files[i], ticket_files[j]
            shared = sorted(a["files"] & b["files"])
            if shared:
                conflicts.append({
                    "ticket1_id": a["id"],
                    "ticket1_title": a["title"],
                    "ticket2_id": b["id"],
                    "ticket2_title": b["title"],
                    "shared_files": shared,
                })
    return {"conflicts": conflicts, "pending_count": len(pending)}


def _build_outcome_inline(label: str, project: str, lifecycle_state: str) -> Optional[dict]:
    """Build outcome payload from DB only. Zero gh/subprocess calls.

    Returns None when the sprint has not run yet. Returns a minimal outcome
    dict (matching the /api/sprints/{label}/outcome response shape) when the
    run has been ingested into the DB.
    """
    if lifecycle_state == "running":
        return {"sprint_label": label, "state": "running", "lifecycle": "running"}
    try:
        row = db.get_sprint(label, project=project)
        if not row or not row.get("run_ingested_at"):
            return None
        stored_state = row.get("state") or ""
        lifecycle = db.canonical_lifecycle(stored_state)
        end_reason = row.get("end_reason") or ""
        is_cancelled = lifecycle == "needs_rework" and end_reason.startswith("stopped")

        try:
            issues_raw = json.loads(row.get("issues_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            issues_raw = []

        result_issues = []
        for iss in issues_raw:
            tid = iss.get("ticket_id") or iss.get("number")
            agent = (iss.get("agent_status") or "").lower()
            fr = iss.get("failure_reason")
            st = (iss.get("state") or "").lower()
            if st == "merged" or agent in ("completed", "done"):
                outcome = "done"
            elif agent == "failed" or fr:
                outcome = "failed"
            else:
                outcome = "skipped"
            result_issues.append({
                "number": tid,
                "title": iss.get("title", ""),
                "outcome": outcome,
            })

        done_count = sum(1 for i in result_issues if i["outcome"] == "done")
        failed_count = sum(1 for i in result_issues if i["outcome"] == "failed")
        skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

        if is_cancelled:
            pane_state, sprint_status = "cancelled", "stopped"
        elif lifecycle in ("completed", "ready_to_merge"):
            pane_state, sprint_status = "completed", "completed"
        else:
            pane_state, sprint_status = "has_rework", "stopped"

        return {
            "sprint_label": label,
            "state": pane_state,
            "lifecycle": lifecycle,
            "sprint_status": sprint_status,
            "end_reason": end_reason or None,
            "counts": {"done": done_count, "failed": failed_count, "skipped": skipped_count},
            "wall_clock_secs": row.get("wall_clock_secs") or 0,
            "ended_at": None,
            "issues": result_issues,
            "summary_issue_url": row.get("summary_issue_url"),
            "summary_issue_num": row.get("summary_issue_num"),
            "pr_number": row.get("pr_number"),
        }
    except Exception as exc:
        log.warning("_build_outcome_inline failed for %s: %s", label, exc, exc_info=True)
        return None


def _build_finish_card_inline(label: str, project: str, lifecycle_state: str) -> dict:
    """Build finish-card payload from DB. Zero gh/subprocess calls.

    Returns a dict matching the /api/sprints/{label}/finish-card response shape.
    """
    sprint_number = _sprint_num(label)
    no_data = {"sprint_label": label, "sprint_number": sprint_number, "state": "no_data"}

    if lifecycle_state == "running":
        return {"sprint_label": label, "sprint_number": sprint_number, "state": "running"}
    try:
        row = db.get_sprint(label, project=project)
        if not row or not row.get("run_ingested_at"):
            return no_data

        stored_state = row.get("state") or ""
        lifecycle = db.canonical_lifecycle(stored_state)
        end_reason = row.get("end_reason") or ""
        is_cancelled = lifecycle == "needs_rework" and end_reason.startswith("stopped")

        try:
            issues_raw = json.loads(row.get("issues_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            issues_raw = []

        done_count = sum(
            1 for i in issues_raw
            if (i.get("state") or "").lower() == "merged"
            or (i.get("agent_status") or "").lower() in ("completed", "done")
        )
        failed_count = sum(
            1 for i in issues_raw
            if (i.get("agent_status") or "").lower() == "failed" or i.get("failure_reason")
        )
        skipped_count = sum(
            1 for i in issues_raw
            if (i.get("status") or "").lower() == "skipped"
            and not ((i.get("agent_status") or "").lower() == "failed" or i.get("failure_reason"))
        )

        if is_cancelled:
            card_state, rework_count = "cancelled", 0
        elif lifecycle in ("needs_rework", "failed"):
            card_state, rework_count = "has_rework", failed_count
        else:
            card_state, rework_count = "completed", 0

        return {
            "sprint_label": label,
            "sprint_number": sprint_number,
            "state": card_state,
            "lifecycle": lifecycle,
            "done_count": done_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "rework_count": rework_count,
            "wall_clock_secs": row.get("wall_clock_secs") or 0,
            "ended_at": None,
            "summary_issue_url": row.get("summary_issue_url"),
            "summary_issue_num": row.get("summary_issue_num"),
        }
    except Exception as exc:
        log.warning("_build_finish_card_inline failed for %s: %s", label, exc, exc_info=True)
        return no_data


def _build_branch_status_inline(label: str, project: str, lifecycle_state: str = "") -> dict:
    """Branch status with zero gh/subprocess calls.

    Infers exists=True from lifecycle_state=='running' (branch must be alive)
    or from a non-null pr_number in the DB row (branch was alive when PR opened).
    exists=False only when neither zero-quota source confirms existence.
    """
    branch = f"sprint/{label}"
    base: dict = {"exists": False, "branch": branch}
    try:
        row = db.get_sprint(label, project=project)
        if lifecycle_state == "running":
            base["exists"] = True
        elif row and row.get("pr_number"):
            base["exists"] = True
        if row and row.get("pr_number"):
            pr_number = row["pr_number"]
            base["pr_number"] = pr_number
            base["pr_url"] = f"https://github.com/{project}/pull/{pr_number}"
    except Exception as exc:
        log.warning("_build_branch_status_inline failed for %s: %s", label, exc, exc_info=True)
    return base


# ── Sprint card builder ───────────────────────────────────────────────────────

def _build_sprint_card(
    label: str,
    project: str,
    sprint_issues: list[dict],
    est_dir: Path,
    lifecycle_state: str,
) -> dict:
    """Build a sprint card with all required inline fields."""
    mini_rail = _build_mini_rail(sprint_issues, est_dir)
    dep_order = _build_dep_order(sprint_issues, est_dir)
    estimate_hours = _compute_estimate_hours(sprint_issues, est_dir)
    try:
        run_stats = _run_stats_service().sprint_run_stats(label, project=project)
    except Exception:
        run_stats = {"label": label, "has_runs": False, "split": [], "tickets": []}

    goal = _read_sprint_goal(project, label)
    outcome = _build_outcome_inline(label, project, lifecycle_state)
    conflicts = _build_conflicts_inline(sprint_issues, est_dir)
    finish_card = _build_finish_card_inline(label, project, lifecycle_state)
    branch_status = _build_branch_status_inline(label, project, lifecycle_state)

    return {
        "label": label,
        "lifecycle_state": lifecycle_state,
        "tickets": sprint_issues,
        "mini_rail": mini_rail,
        "dep_order": dep_order,
        "estimate_hours": estimate_hours,
        "run_stats": run_stats,
        "goal": goal,
        "outcome": outcome,
        "conflicts": conflicts,
        "finish_card": finish_card,
        "branch_status": branch_status,
    }


# ── Card bucket ───────────────────────────────────────────────────────────────

def _card_bucket(lifecycle_state: str, has_run: bool) -> str:
    """Map a sprint's lifecycle state to its board section name."""
    if lifecycle_state == "running":
        return "running"
    if has_run:
        if lifecycle_state in ("ready_to_merge", "completed"):
            return "ready_to_merge"
        if lifecycle_state in ("needs_rework", "failed", "partial_finished"):
            return "needs_rework"
    return "draft"


# ── Summaries ─────────────────────────────────────────────────────────────────

def _get_summaries(project: str, sprint_labels: set[str]) -> dict[str, Any]:
    """Per-sprint summary metadata from SQLite. No gh calls."""
    summaries: dict[str, Any] = {}
    for label in sprint_labels:
        try:
            row = db.get_sprint(label, project=project)
            if row:
                summaries[label] = {
                    "summary_issue_url": row.get("summary_issue_url"),
                    "summary_issue_num": row.get("summary_issue_num"),
                    "summary_path": row.get("summary_path"),
                    "pr_number": row.get("pr_number"),
                    "lifecycle_state": db.canonical_lifecycle(row.get("state")),
                }
        except Exception:
            pass
    return summaries


# ── Capacity ──────────────────────────────────────────────────────────────────

def _get_capacity(project: str) -> dict:
    """Sprint capacity from settings. No gh calls."""
    try:
        srv = _server()
        eff = srv.build_effective_response(
            srv._settings_repo.get_setting(srv.APP_CONFIG_KEY, project=project)
        )
        return {
            "budget_minutes": int(eff.get("sprint_budget_minutes", 180)),
            "size_minutes": {
                "S": int(eff.get("estimation_s_minutes", _SIZE_MINUTES["S"])),
                "M": int(eff.get("estimation_m_minutes", _SIZE_MINUTES["M"])),
                "L": int(eff.get("estimation_l_minutes", _SIZE_MINUTES["L"])),
                "XL": int(eff.get("estimation_xl_minutes", _SIZE_MINUTES["XL"])),
            },
        }
    except Exception:
        return {"budget_minutes": 180, "size_minutes": dict(_SIZE_MINUTES)}


# ── Chain grouping ────────────────────────────────────────────────────────────

def _chain_root(label: str, sprint_parents: dict[str, str]) -> str:
    """Follow parent_label links and label conventions to the root base sprint."""
    visited: set[str] = set()
    current = label
    while current in sprint_parents and current not in visited:
        visited.add(current)
        current = sprint_parents[current]
    return _sprint_base(current)


# ── Main assembly ─────────────────────────────────────────────────────────────

def assemble_board(project: str) -> dict[str, Any]:
    """Assemble the full board payload from local mirror + SQLite.

    Zero gh subprocess calls per request. Returns:
      {project, generated_at, sections: {running, needs_rework, ready_to_merge,
       draft, lineage, backlog: {count, tickets[]}}, capacity, summaries}
    """
    # 1. Issues from mirror (cache-only — zero gh calls)
    all_issues = github_client.cached_open_issues_with_body(project) or []

    # 2. Sprint lifecycle rows from SQLite
    lifecycle_rows = [
        r for r in db.list_sprints_lifecycle()
        if r.get("project") == project
    ]
    lifecycle_by_label: dict[str, dict] = {}
    for row in lifecycle_rows:
        label = row.get("label") or ""
        if not label:
            continue
        existing = lifecycle_by_label.get(label)
        if existing is None:
            lifecycle_by_label[label] = row
        else:
            rank_e = _MERGE_STATE_RANK.get(db.canonical_lifecycle(existing.get("state") or ""), 0)
            rank_n = _MERGE_STATE_RANK.get(db.canonical_lifecycle(row.get("state") or ""), 0)
            if rank_n > rank_e:
                lifecycle_by_label[label] = row

    # 3. Sprint labels: union of issue labels + DB rows
    labels_from_issues: set[str] = set()
    for iss in all_issues:
        labels_from_issues.update(_issue_sprint_labels(iss))
    all_sprint_labels: set[str] = labels_from_issues | set(lifecycle_by_label.keys())

    # 4. Rerun chain links from DB parent_label
    sprint_parents: dict[str, str] = {}
    for label, row in lifecycle_by_label.items():
        parent = (row.get("parent_label") or "").strip()
        if parent:
            sprint_parents[label] = parent

    # 5. Canonical lifecycle state for each label (DB-only — no gh calls)
    sprint_lifecycle: dict[str, str] = {}
    for label in all_sprint_labels:
        try:
            import sprint_state  # noqa: PLC0415
            sprint_lifecycle[label] = sprint_state.current(label, project)
        except Exception:
            row = lifecycle_by_label.get(label) or {}
            sprint_lifecycle[label] = db.canonical_lifecycle(row.get("state"))

    # 6. Issues grouped by sprint label; unassigned = backlog
    issues_by_sprint: dict[str, list[dict]] = {}
    for iss in all_issues:
        for sl in _issue_sprint_labels(iss):
            issues_by_sprint.setdefault(sl, []).append(iss)
    unassigned_issues = [i for i in all_issues if not _issue_sprint_labels(i)]

    # 7. "has_run" per label: DB state implies at least one run attempt
    def _has_run(label: str) -> bool:
        lc = sprint_lifecycle.get(label, "")
        if lc in _RUN_INDICATOR_STATES:
            return True
        row = lifecycle_by_label.get(label) or {}
        return bool(row.get("run_ingested_at"))

    # 8. Finished set: completed/deleted in DB — these belong to History only
    finished_set: set[str] = {
        label for label, lc in sprint_lifecycle.items()
        if lc in ("completed", "deleted")
    }
    # Also mark sprints with an Executive Summary issue as finished
    for iss in all_issues:
        m = _SUMMARY_TITLE_RE.match(iss.get("title", ""))
        if m:
            lbl = f"sprint-{m.group(1)}"
            if lbl in all_sprint_labels:
                finished_set.add(lbl)

    # 9. Group sprint labels by rerun chain root
    chain_groups: dict[str, list[str]] = {}
    for label in all_sprint_labels:
        root = _chain_root(label, sprint_parents)
        chain_groups.setdefault(root, []).append(label)

    # 10. Estimates dir
    est_dir = _estimates_dir(project)

    # 11. Build sections
    sections: dict[str, Any] = {
        "running": [],
        "needs_rework": [],
        "ready_to_merge": [],
        "draft": [],
        "lineage": [],
        "backlog": {"count": len(unassigned_issues), "tickets": unassigned_issues},
    }

    for root, members in sorted(chain_groups.items(), key=lambda x: _sprint_sort_key(x[0])):
        members_sorted = sorted(members, key=_sprint_sort_key)

        if len(members_sorted) >= 2:
            # Rerun chain → single lineage entry
            # Skip chains where ALL members are completed/deleted (belongs to History)
            active_members = [m for m in members_sorted if m not in finished_set]
            if not active_members:
                continue  # whole chain merged, History only

            # Use the latest non-finished member as the card's data source
            latest = active_members[-1]
            lc = sprint_lifecycle.get(latest, "unknown")
            sprint_issues = issues_by_sprint.get(latest, [])
            card = _build_sprint_card(latest, project, sprint_issues, est_dir, lc)
            card["chain"] = members_sorted
            sections["lineage"].append(card)

        else:
            # Singleton sprint → bucket by lifecycle state
            label = members_sorted[0]
            lc = sprint_lifecycle.get(label, "unknown")

            # Skip if fully merged / belongs to History
            if label in finished_set:
                continue

            # Skip empty, never-run sprints that have no DB row either
            sprint_issues = issues_by_sprint.get(label, [])
            has_db_row = label in lifecycle_by_label
            if not sprint_issues and not has_db_row:
                continue

            card = _build_sprint_card(label, project, sprint_issues, est_dir, lc)
            bucket = _card_bucket(lc, _has_run(label))
            sections[bucket].append(card)

    # 12. Capacity and summaries (DB/settings-only — zero gh calls)
    capacity = _get_capacity(project)
    summaries = _get_summaries(project, all_sprint_labels)

    return {
        "project": project,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
        "capacity": capacity,
        "summaries": summaries,
    }
