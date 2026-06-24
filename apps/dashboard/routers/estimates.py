from __future__ import annotations
import hashlib
import json
import subprocess
import sys
import re
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

import db  # noqa: E402
import github_client  # noqa: E402
from sizing import SIZE_TO_MINUTES as _SIZE_TO_MINUTES  # noqa: E402

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


# ── Constants ─────────────────────────────────────────────────────────────────

_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")

_SIZE_LABELS = {"size-S", "size-M", "size-L", "size-XL"}
_SIZE_LETTER_BY_LABEL = {"size-S": "S", "size-M": "M", "size-L": "L", "size-XL": "XL"}
_PF_NON_WORK = {"sprint-summary", "docs", "documentation"}

# Merge Sprint (completed) closes the chain; ready_to_merge is still open work.
_CHILD_SETTLED_STATES = frozenset({"completed", "deleted"})
_SPRINT_WORK_EXCLUDE_LABELS = frozenset({"sprint-summary", "docs", "documentation"})
_SPRINT_UAT_LABELS = frozenset({"UAT", "UAT-approved", "released"})
# Canonical lifecycle states that mean the sprint has finished (issue #1093).
_OUTCOME_TERMINAL_STATES = frozenset({"completed", "needs_rework", "ready_to_merge", "deleted"})


# ── Error helper ──────────────────────────────────────────────────────────────

def _gh_error(e: subprocess.CalledProcessError) -> HTTPException:
    return HTTPException(
        502,
        detail=f"GitHub CLI error (exit {e.returncode}): {(e.stderr or '').strip()[:300]}",
    )


# ── Plan JSON helpers ─────────────────────────────────────────────────────────

def _sprint_plan_path(project_root: Path, sprint_label: str) -> Path:
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}-plan.json"


def _read_plan_json(project_root: Path, sprint_label: str) -> Optional[dict]:
    """Read plan.json; handles old list format and new dict format."""
    path = _sprint_plan_path(project_root, sprint_label)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {"tickets": raw}
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return None


# ── Sprint issue helpers ──────────────────────────────────────────────────────

def _primary_sprint_label(iss: dict) -> str | None:
    """Return the sprint label used for board column grouping (first sprint-* label)."""
    for lbl in iss.get("labels", []):
        if _SPRINT_LABEL_RE.match(lbl["name"]):
            return lbl["name"]
    return None


def _get_sprint_issues(project: str, sprint_label: str) -> list[dict]:
    """Fetch open issues whose primary sprint label matches sprint_label."""
    issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    return [iss for iss in issues if _primary_sprint_label(iss) == sprint_label]


# ── Size/estimate helpers ─────────────────────────────────────────────────────

def _size_to_minutes(size: str) -> int:
    """Map a T-shirt size label to agent-effort minutes via SIZE_TO_MINUTES."""
    return _SIZE_TO_MINUTES.get(size, 0)


def _size_from_github_labels(label_names: set[str]) -> str | None:
    """Return S/M/L/XL from GitHub size-* labels, or None."""
    for lbl in _SIZE_LABELS:
        if lbl in label_names:
            return _SIZE_LETTER_BY_LABEL[lbl]
    return None


def _load_issue_estimate_json(estimates_dir: Path, issue_num: int) -> dict | None:
    est_path = estimates_dir / f"issue-{issue_num}.json"
    if not est_path.exists():
        return None
    try:
        return json.loads(est_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_issue_estimate(iss: dict, estimates_dir: Path) -> dict:
    """Single source of truth: merge local estimate JSON + GitHub size-* labels.

    Returns ``{size, files, estimated, source}`` where ``source`` is
    ``'json'``, ``'label'``, or ``None``. Either JSON or a GitHub label counts
    as estimated; ``files`` always come from JSON when present.
    """
    label_names = {lbl["name"] for lbl in iss.get("labels", [])}
    est = _load_issue_estimate_json(estimates_dir, iss["number"])
    files: list[str] = []
    size: str | None = None
    source: str | None = None
    if est:
        files = list(est.get("files_likely_affected") or [])
        raw_size = est.get("size")
        if raw_size:
            size = str(raw_size)
            source = "json"
    if not size:
        label_size = _size_from_github_labels(label_names)
        if label_size:
            size = label_size
            source = "label"
    return {
        "size": size,
        "files": files,
        "estimated": size is not None,
        "source": source,
    }


def _check_estimate_stale(issue_num: int, current_body: str, estimates_dir) -> bool:
    """Return True if the stored estimate is stale (body changed or hash missing)."""
    if estimates_dir is None:
        return False
    est_path = estimates_dir / f"issue-{issue_num}.json"
    if not est_path.exists():
        return False
    try:
        est = json.loads(est_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    stored_hash = est.get("body_hash")
    if not stored_hash:
        return True
    current_hash = hashlib.sha256(current_body.encode()).hexdigest()
    return current_hash != stored_hash


# ── Sprint running / outcome helpers ─────────────────────────────────────────

def _is_sprint_running(project_root: Path, sprint_label: str) -> bool:
    """Delegate to server._is_sprint_running for authoritative state."""
    return _server()._is_sprint_running(project_root, sprint_label)


def _state_data_is_dry_run_only(state_data: dict) -> bool:
    """True when state.json reflects a --dry-run pass (no coder/tester dispatch)."""
    issues = state_data.get("issues") or []
    if not issues:
        return False
    if any(i.get("coder_started_at") or i.get("tester_started_at") for i in issues):
        return False
    return any((i.get("skip_reason") or "").lower() == "dry-run" for i in issues)


def _sprint_has_own_run_outcome(project_root: Path, sprint_label: str, project: str = "") -> bool:
    """True when outcome data for *this* label exists (not a sibling/base run)."""
    plan = _read_plan_json(project_root, sprint_label)
    if plan and plan.get("state") in ("planning", "draft", "planned"):
        return False

    # Scope by project: sprint labels are unique only per repo, so an unscoped
    # lookup leaks another project's same-numbered sprint (e.g. crux sprint-9
    # picking up commander's sprint-9 row).
    row = db.get_sprint(sprint_label, project=project or None)
    if row and row.get("run_ingested_at"):
        return True

    from routers import sprint_artifact_service  # noqa: PLC0415
    sprints_dir = _commander_dir(project_root) / "sprints"
    resolved = sprint_artifact_service.resolve_state_path(sprints_dir, sprint_label)
    if resolved is None:
        return False
    try:
        state_data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    return not _state_data_is_dry_run_only(state_data)


def _has_rework_tickets(sprint_label: str, project: str) -> bool:
    """True when the sprint has open work tickets with rework labels."""
    NON_WORK = {"sprint-summary", "docs", "documentation"}
    REWORK = {"needs-rework", "need-rework", "tester-rejected"}
    DONE = {"UAT", "UAT-approved", "released"}
    try:
        issues = _get_sprint_issues(project, sprint_label)
    except Exception:
        return False
    for iss in issues:
        labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if labels & NON_WORK:
            continue
        if labels & REWORK:
            return True
        if not (labels & DONE):
            return True
    return False


def _sprint_work_tickets_all_uat(project: str, sprint_label: str) -> bool:
    """True when every non-summary open issue on the label is UAT (or label is empty)."""
    try:
        issues = _get_sprint_issues(project, sprint_label)
    except Exception:
        return False
    work = [
        iss for iss in issues
        if not ({lbl["name"] for lbl in iss.get("labels", [])} & _SPRINT_WORK_EXCLUDE_LABELS)
    ]
    if not work:
        return True
    return all(
        bool({lbl["name"] for lbl in iss.get("labels", [])} & _SPRINT_UAT_LABELS)
        for iss in work
    )


def _derive_outcome_lifecycle(
    sprint_label: str,
    project_root: Path,
    project: str,
    plan_state: str,
    pane_state: str,
    failed_count: int,
) -> str:
    """Board/history lifecycle — DB-only: derives partial_finished when a child is unsettled.

    Reads parent canonical state and child rows exclusively from the sprints DB
    table (issue #1093). No GitHub label lookups, no disk globs.
    """
    row = db.get_sprint(sprint_label, project=project or None)
    if row is None:
        return db.canonical_lifecycle(pane_state)
    parent_state = db.canonical_lifecycle(row["state"])
    if parent_state not in _OUTCOME_TERMINAL_STATES:
        return parent_state
    children = db.get_sprint_children(sprint_label, project=project or None)
    if not children:
        return parent_state
    unsettled = [
        c for c in children
        if db.canonical_lifecycle(c["state"]) not in _CHILD_SETTLED_STATES
    ]
    if unsettled:
        return "partial_finished"
    return parent_state


def _outcome_from_ingested_row(
    row: dict,
    sprint_label: str,
    project: str,
) -> dict:
    """Build outcome payload from DB-ingested run artifacts (lifecycle P3)."""
    from routers import sprint_artifact_service  # noqa: PLC0415

    enrich = sprint_artifact_service.enrichment_from_db_row(row)
    stored_state = row.get("state") or ""
    lifecycle = db.canonical_lifecycle(stored_state)
    end_reason = row.get("end_reason")
    if lifecycle == "needs_rework" and (end_reason or "") == "natural":
        try:
            _raw = json.loads(row.get("issues_json") or "[]")
            if _raw and all(
                (i.get("state") or "").lower() == "merged"
                or (i.get("agent_status") or "").lower() in ("completed", "done")
                for i in _raw
            ):
                lifecycle = "ready_to_merge"
        except (json.JSONDecodeError, TypeError):
            pass
    is_cancelled = lifecycle == "needs_rework" and (end_reason or "").startswith("stopped")

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
            "elapsed_secs": iss.get("time_spent"),
            "failure_reason": fr,
        })

    # Rec 2c — union agent_runs so the outcome band agrees with the History
    # ledger. Additive only — never rewrites a ticket already in result_issues.
    try:
        from routers import sprint_history_service  # noqa: PLC0415
        _seen = {str(i["number"]) for i in result_issues if i.get("number") is not None}
        for _extra in sprint_history_service._issues_from_agent_runs(sprint_label, project):
            _tid = _extra.get("ticket_id")
            if _tid is None:
                _tid = _extra.get("number")
            if _tid is None or int(_tid) <= 0:
                continue
            _eid = str(_tid)
            if _eid in _seen:
                continue
            _st = (_extra.get("state") or "").lower()  # merged | closed | open
            _oc = "done" if _st == "merged" else ("failed" if _st == "closed" else "skipped")
            result_issues.append({
                "number": int(_tid),
                "title": _extra.get("title", ""),
                "outcome": _oc,
                "elapsed_secs": None,
                "failure_reason": None,
            })
            _seen.add(_eid)
    except Exception:
        pass

    if is_cancelled:
        pane_state = "cancelled"
        sprint_status = "stopped"
    elif _has_rework_tickets(sprint_label, project):
        pane_state = "has_rework"
        sprint_status = "stopped"
    else:
        pane_state = "completed"
        sprint_status = "completed"

    done_count = sum(1 for i in result_issues if i["outcome"] == "done")
    failed_count = sum(1 for i in result_issues if i["outcome"] == "failed")
    skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

    surl = enrich.get("summary_issue_url")
    summary_issue_num = enrich.get("summary_issue_num")
    pr_number = row.get("pr_number") if row.get("pr_number") is not None else enrich.get("pr_number")
    pr_url = None
    if pr_number:
        try:
            pr_repo = github_client.get_repo_for_operation(project)
            pr_url = f"https://github.com/{pr_repo}/pull/{int(pr_number)}"
        except Exception:
            pr_url = None

    return {
        "sprint_label": sprint_label,
        "state": pane_state,
        "lifecycle": lifecycle,
        "end_reason": end_reason,
        "sprint_status": sprint_status,
        "counts": {
            "done": done_count,
            "failed": failed_count,
            "skipped": skipped_count,
        },
        "wall_clock_secs": enrich.get("duration") or row.get("wall_clock_secs") or 0,
        "ended_at": None,
        "issues": result_issues,
        "log_line_count": 0,
        "summary_issue_url": surl,
        "summary_issue_num": summary_issue_num,
        "pr_number": pr_number,
        "pr_url": pr_url,
    }


def _parse_summary_file(path: Path) -> dict:
    """Parse metadata from a sprint summary markdown file."""
    name = path.stem  # sprint-3-summary-2026-05-24
    m = re.match(r"sprint-(\d+)-summary-(\d{4}-\d{2}-\d{2})", name)
    sprint_num = int(m.group(1)) if m else None
    date = m.group(2) if m else ""

    content = path.read_text(encoding="utf-8")

    status_m = re.search(r"^## Sprint \S+ — (\S+)", content, re.MULTILINE)
    status = status_m.group(1) if status_m else "unknown"

    shipped_count = 0
    in_shipped = False
    for line in content.splitlines():
        if line.startswith("## Pending UAT Review") or line.startswith("## What Shipped"):
            in_shipped = True
            continue
        if in_shipped and line.startswith("## "):
            break
        if in_shipped and line.startswith("|") and not line.startswith("| Issue") and "|---|" not in line:
            cell = line.split("|")[1].strip()
            if cell and cell != "—":
                shipped_count += 1

    skipped_count = 0
    in_skipped = False
    for line in content.splitlines():
        if line.startswith("## What Didn't Ship"):
            in_skipped = True
            continue
        if in_skipped and line.startswith("## "):
            break
        if in_skipped and line.startswith("|") and not line.startswith("| Issue") and "|---|" not in line:
            cell = line.split("|")[1].strip()
            if cell and cell != "—":
                skipped_count += 1

    total_tokens = 0
    tok_m = re.search(r"\|\s*Total tokens\s*\|\s*(\d+)\s*\|", content)
    if tok_m:
        total_tokens = int(tok_m.group(1))

    return {
        "sprint_num": sprint_num,
        "date": date,
        "status": status,
        "shipped_count": shipped_count,
        "skipped_count": skipped_count,
        "total_tokens": total_tokens,
    }


# ── Session state / rerun helpers ─────────────────────────────────────────────

_SESSION_STATE_LABELS = frozenset({
    "needs-rework",
    "need-rework",
    "in-progress",
    "sit-away",
    "tester-rejected",
})


def _stale_session_labels(labels) -> list[str]:
    """Return the session-state labels present in `labels`, sorted."""
    return sorted(set(labels) & _SESSION_STATE_LABELS)


def _rerun_policy(labels: set[str]) -> tuple[str, list[str]]:
    """Return (action, labels_to_strip) for a sprint ticket based on its current labels."""
    if labels & {"UAT", "UAT-approved"}:
        return "skip", []
    if "SIT" in labels:
        return "dispatch_tester", []
    if "tester-rejected" in labels:
        return "dispatch_coder", ["tester-rejected"]
    if "needs-rework" in labels or "need-rework" in labels:
        to_strip = ["in-progress"] if "in-progress" in labels else []
        return "dispatch_coder", to_strip
    if "in-progress" in labels:
        return "dispatch_coder", ["in-progress"]
    return "dispatch_coder", []


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/sprints/{sprint_label}/estimate-summary")
def get_sprint_estimate_summary(sprint_label: str, project: str):
    """Return a rolled-up estimate summary for a sprint.

    Fetches open issues for the sprint via the existing list_open_issues_with_body
    plumbing, reads size-S/M/L/XL labels from each ticket, and returns:
      - size_counts: dict mapping size -> count (e.g. {"S": 2, "M": 3, "L": 1})
      - total_minutes: int, sum of _size_to_minutes for all sized tickets
      - unsized_numbers: list of issue numbers with no size label
      - sprint_name: human-readable sprint name (e.g. "Sprint 15")
      - total_tickets: total open ticket count in the sprint
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    _LOCAL_SIZE_LABELS = ["S", "M", "L", "XL"]  # ordered smallest to largest
    size_counts: dict[str, int] = {}
    unsized_numbers: list[int] = []
    total_minutes = 0

    for iss in sprint_issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        found_size = None
        for size in _LOCAL_SIZE_LABELS:
            if f"size-{size}" in label_names:
                found_size = size
                break
        if found_size:
            size_counts[found_size] = size_counts.get(found_size, 0) + 1
            total_minutes += _size_to_minutes(found_size)
        else:
            unsized_numbers.append(iss["number"])

    # Extract sprint number for human-readable name
    m = re.search(r"(\d+)", sprint_label)
    sprint_num = int(m.group(1)) if m else None
    sprint_name = f"Sprint {sprint_num}" if sprint_num is not None else sprint_label

    return {
        "sprint_name": sprint_name,
        "sprint_label": sprint_label,
        "total_tickets": len(sprint_issues),
        "size_counts": size_counts,
        "total_minutes": total_minutes,
        "unsized_numbers": unsized_numbers,
    }


@router.get("/api/sprints/{sprint_label}/estimate")
def get_sprint_estimate(sprint_label: str, project: str):
    """Return the sprint estimate JSON file content for sprint_label.

    Returns the parsed JSON from <sprints_dir>/sprint-<N>-estimate.json,
    or 404 if the file has not been generated yet.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    # Extract sprint number from label like "sprint-9" → "9"
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    estimate_path = commander / "sprints" / f"sprint-{n}-estimate.json"

    if not estimate_path.exists():
        raise HTTPException(
            404,
            detail=f"Estimate not found for {sprint_label!r}. Run the estimator first.",
        )

    try:
        data = json.loads(estimate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read estimate file: {e}")

    return data


@router.get("/api/estimates/batch")
def get_estimates_batch(project: str, issues: str = ""):
    """Return summed estimated_hours and per-issue size/confidence for a list of issue numbers.

    Query params:
      - project: repo slug (owner/repo)
      - issues: comma-separated issue numbers, e.g. "431,432,433"

    Returns:
      total_hours: float|null — null when any issue lacks an estimate
      complete: bool — true when every issue has an estimate
      issues: {num_str: {size, confidence, ...}|null}
      total_tokens: int|null — aggregated estimated tokens; null when no issue has an estimate
      total_cost_usd: float|null — estimated cost at Haiku 4.5 rates; null when no estimate
      estimated_count: int — number of issues with a cached estimate
      partial: bool — true when some but not all issues have an estimate
    """
    # Estimated tokens per size (mirrors SIZE_TO_MINUTES * 1000 ratio from sizing.py).
    # Haiku 4.5 blended cost: 60 % input at $0.80/M + 40 % output at $4.00/M = $2.08/M.
    _SIZE_TOKENS: dict[str, int] = {"S": 5_000, "M": 15_000, "L": 30_000, "XL": 60_000}
    _COST_PER_TOKEN: float = (0.80 * 0.6 + 4.00 * 0.4) / 1_000_000  # $2.08 per million

    issue_nums = [int(p) for p in issues.split(",") if p.strip().isdigit()]

    if not issue_nums:
        return {"total_hours": 0.0, "complete": True, "issues": {},
                "total_tokens": None, "total_cost_usd": None,
                "estimated_count": 0, "partial": False}

    try:
        project_root = _project_root_path(project)
        estimates_dir = _commander_dir(project_root) / "estimates"
        if not estimates_dir.is_dir():
            return {"total_hours": None, "complete": False,
                    "issues": {str(n): None for n in issue_nums},
                    "total_tokens": None, "total_cost_usd": None,
                    "estimated_count": 0, "partial": False}
    except Exception:
        return {"total_hours": None, "complete": False,
                "issues": {str(n): None for n in issue_nums},
                "total_tokens": None, "total_cost_usd": None,
                "estimated_count": 0, "partial": False}

    total = 0.0
    total_tokens = 0
    complete = True
    estimated_count = 0
    per_issue: dict = {}
    for num in issue_nums:
        path = estimates_dir / f"issue-{num}.json"
        if not path.exists():
            complete = False
            per_issue[str(num)] = None
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            h = data.get("estimated_hours")
            size = data.get("size")
            confidence = data.get("confidence")
            if h is None:
                complete = False
            else:
                total += float(h)
            tokens = _SIZE_TOKENS.get(size or "", 0)
            total_tokens += tokens
            estimated_count += 1
            per_issue[str(num)] = {
                "size": size,
                "confidence": confidence,
                "files_likely_affected": data.get("files_likely_affected", []),
                "risk_flags": data.get("risk_flags", []),
                "summary": data.get("summary", ""),
            }
        except (json.JSONDecodeError, OSError, ValueError):
            complete = False
            per_issue[str(num)] = None

    has_any = estimated_count > 0
    return {
        "total_hours": total if complete else None,
        "complete": complete,
        "issues": per_issue,
        "total_tokens": total_tokens if has_any else None,
        "total_cost_usd": total_tokens * _COST_PER_TOKEN if has_any else None,
        "estimated_count": estimated_count,
        "partial": has_any and estimated_count < len(issue_nums),
    }


@router.get("/api/sprints/{sprint_label}/outcome")
def get_sprint_outcome(sprint_label: str, project: str):
    """Return frozen outcome data for a completed or stopped sprint.

    Reads sprint-N-state.json plus the latest sprint-run-<label>-*.log to produce:
      - state: "running" | "completed" | "has_rework" | "cancelled"
      - sprint_status: "completed" | "stopped" | None (still running or not found)
      - counts: { done, failed, skipped }
      - wall_clock_secs: total duration
      - ended_at: ISO 8601 timestamp of sprint end (from last issue status_changed_at)
      - issues: list of { number, title, outcome, elapsed_secs } for each issue
      - log_line_count: number of lines in the archived run log
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    # Running sprints return immediately — no state file required
    if _is_sprint_running(project_root, sprint_label):
        return {"sprint_label": sprint_label, "state": "running", "lifecycle": "running"}

    if not _sprint_has_own_run_outcome(project_root, sprint_label, project):
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r} (not run yet)")

    ingested = db.get_sprint(sprint_label, project=project or None)
    if ingested and ingested.get("run_ingested_at"):
        return _outcome_from_ingested_row(ingested, sprint_label, project)

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    # Check sprint-N.json for a stopped status (may exist even without a state file)
    json_path = _sprint_json_path(project_root, sprint_label)
    sprint_json = _sprint_json_read(json_path)
    is_cancelled: bool = sprint_json.get("status") in ("cancelled", "needs_rework")

    state_path = commander / "sprints" / f"sprint-{n}-state.json"
    from routers import sprint_artifact_service  # noqa: PLC0415
    resolved = sprint_artifact_service.resolve_state_path(commander / "sprints", sprint_label)
    if resolved is not None:
        state_path = resolved
    if not state_path.exists():
        if is_cancelled:
            return {"sprint_label": sprint_label, "state": "cancelled",
                    "lifecycle": "needs_rework",
                    "end_reason": sprint_json.get("end_reason")}
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r}")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    if _state_data_is_dry_run_only(state_data):
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r} (dry-run only)")

    plan = _read_plan_json(project_root, sprint_label)
    plan_state = (plan.get("state") or "").lower() if plan else ""
    if plan_state in ("draft", "planned", "planning"):
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r} (not run yet)")

    # Lazy-ingest (lifecycle P3 drift fix): a finished run reached this disk
    # fallback because its artifacts were never ingested (run_ingested_at is
    # null). Persist the disk state now so the NEXT read takes the DB path.
    if ingested and not ingested.get("run_ingested_at"):
        try:
            db.ingest_sprint_run_artifact(sprint_label, state_data, project=project)
        except Exception:
            pass

    def _parse_iso(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    def _fmt_iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")

    # Derive sprint status from summary file (most authoritative)
    sprint_status: Optional[str] = None
    for sf in sorted(
        list((commander / "sprints").glob(f"{sprint_label}-summary-*.md"))
        + list((commander / "sprints").glob(f"sprint-{n}-summary-*.md")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            meta = _parse_summary_file(sf)
            raw = (meta.get("status") or "").lower()
            if raw in ("complete", "completed"):
                sprint_status = "completed"
            elif raw in ("stopped", "failed", "cancelled"):
                sprint_status = "stopped"
                if raw == "cancelled":
                    is_cancelled = True
        except Exception:
            pass
        break

    # Fallback: derive from issue statuses
    issues_raw = state_data.get("issues", [])
    if sprint_status is None and issues_raw:
        has_pending = any(i.get("status") == "pending" for i in issues_raw)
        has_failed = any(
            i.get("agent_status") == "failed" or i.get("failure_reason")
            for i in issues_raw
        )
        if not has_pending:
            sprint_status = "stopped" if has_failed else "completed"

    if sprint_status is None:
        raise HTTPException(404, detail=f"Cannot determine outcome for {sprint_label!r}")

    # Derive 4-state outcome for pane coloring
    if plan_state == "needs_rework":
        pane_state = "has_rework"
    elif is_cancelled:
        pane_state = "cancelled"
    elif _has_rework_tickets(sprint_label, project):
        pane_state = "has_rework"
    else:
        pane_state = "completed"

    # Build issue outcome list
    result_issues = []
    ended_ts: Optional[float] = None
    for iss in issues_raw:
        start_ts = _parse_iso(iss.get("coder_started_at"))
        end_ts = (
            _parse_iso(iss.get("tester_finished_at"))
            or _parse_iso(iss.get("status_changed_at"))
        )
        elapsed_secs = None
        if start_ts is not None and end_ts is not None:
            elapsed_secs = max(0.0, end_ts - start_ts)

        if end_ts and (ended_ts is None or end_ts > ended_ts):
            ended_ts = end_ts

        iss_status = iss.get("status", "pending")
        iss_agent = iss.get("agent_status")
        failure_reason = iss.get("failure_reason")

        if iss_status == "done":
            outcome = "done"
        elif iss_agent == "failed" or failure_reason:
            outcome = "failed"
        elif iss_status == "skipped":
            outcome = "skipped"
        else:
            outcome = "skipped"

        result_issues.append({
            "number":       iss.get("number"),
            "title":        iss.get("title", ""),
            "outcome":      outcome,
            "elapsed_secs": round(elapsed_secs) if elapsed_secs is not None else None,
            "failure_reason": failure_reason,
        })

    # Retroactive label override: if an issue is marked "failed" in state.json but
    # currently carries a UAT label on GitHub (manually applied after the sprint ran),
    # treat it as "done" so the card reflects the true current state.
    failed_nums = [i["number"] for i in result_issues if i["outcome"] == "failed" and i["number"]]
    if failed_nums:
        try:
            repo = github_client.get_repo_for_operation(project)
            r = subprocess.run(
                ["gh", "issue", "list", "--repo", repo,
                 "--state", "all", "--label", "UAT",
                 "--json", "number",
                 "--limit", "200"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                uat_nums = {i["number"] for i in (json.loads(r.stdout) or [])}
                for ri in result_issues:
                    if ri["outcome"] == "failed" and ri["number"] in uat_nums:
                        ri["outcome"] = "done"
        except Exception:
            pass

    # Issues moved to a child re-run label are not parent failures anymore.
    on_label_nums: set[int] | None = None
    try:
        on_label_nums = {iss["number"] for iss in _get_sprint_issues(project, sprint_label)}
    except Exception:
        pass
    if on_label_nums is not None:
        for ri in result_issues:
            if ri["outcome"] == "failed" and ri["number"] not in on_label_nums:
                ri["outcome"] = "rerun"

    # Counts (rerun/moved tickets are not failures on this label)
    done_count = sum(1 for i in result_issues if i["outcome"] == "done")
    failed_count = sum(1 for i in result_issues if i["outcome"] == "failed")
    skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

    # Retroactive override may have cleared all failures — upgrade stopped → completed
    if sprint_status == "stopped" and failed_count == 0:
        sprint_status = "completed"

    # Log line count from most recent run log
    log_line_count = 0
    log_dir = commander / "logs"
    if log_dir.exists():
        candidates = sorted(
            log_dir.glob(f"sprint-run-{sprint_label}-*.log"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            try:
                log_line_count = len(candidates[0].read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass

    # Sprint Summary issue (issue #613: finished outcome band links)
    summary_issue_url: Optional[str] = state_data.get("summary_issue_url")
    summary_issue_num: Optional[int] = None
    if summary_issue_url:
        m_sn = re.search(r"/issues/(\d+)", summary_issue_url)
        if m_sn:
            summary_issue_num = int(m_sn.group(1))

    return {
        "sprint_label":      sprint_label,
        "state":             pane_state,
        # Unified lifecycle (sprint-lifecycle.md): pane vocabulary mapped to
        # the one enum shared with the History pane.
        "lifecycle":         _derive_outcome_lifecycle(
            sprint_label, project_root, project, plan_state, pane_state, failed_count,
        ),
        "end_reason":        (plan.get("end_reason") if plan else None) or sprint_json.get("end_reason"),
        "sprint_status":     sprint_status,
        "counts": {
            "done":    done_count,
            "failed":  failed_count,
            "skipped": skipped_count,
        },
        "wall_clock_secs":   state_data.get("wall_clock_secs", 0.0),
        "ended_at":          _fmt_iso(ended_ts),
        "issues":            result_issues,
        "log_line_count":    log_line_count,
        "summary_issue_url": summary_issue_url,
        "summary_issue_num": summary_issue_num,
    }


@router.get("/api/sprints/{sprint_label}/estimate-vs-actual")
def get_sprint_estimate_vs_actual(sprint_label: str, project: str):
    """Return per-ticket estimate-vs-actual comparison for a finished sprint.

    Response schema:
      {
        "sprint_label": "sprint-43",
        "tickets": [
          {
            "ticket_id": 574,
            "title": "...",
            "estimated_size": "L",          # null if no estimate
            "estimated_minutes": 30,        # null if no estimate
            "actual_elapsed_seconds": 1234, # null if timestamps missing
            "actual_elapsed_minutes": 20.6, # null if timestamps missing
            "delta_minutes": -9.4,          # actual - estimated; null if either is null
            "status": "done"
          }
        ]
      }

    Returns 404 if the sprint is not finished (still running, planning, or not found).
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    if _is_sprint_running(project_root, sprint_label):
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} is still in progress")

    plan = _read_plan_json(project_root, sprint_label)
    plan_state = (plan or {}).get("state", "")
    if plan_state in ("planning", "draft", "planned", "running"):
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} is not finished")
    if plan_state == "cancelled":
        # Legacy files only — new stops land in needs_rework, which DID run
        # and may have a meaningful estimate-vs-actual report.
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} was cancelled")

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    state_path = commander / "sprints" / f"sprint-{n}-state.json"

    if not state_path.exists():
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    issues_raw = state_data.get("issues", [])

    # If plan.json has no definitive "completed" state, derive from issue statuses
    if plan_state != "completed" and issues_raw:
        has_pending = any(i.get("status") == "pending" for i in issues_raw)
        if has_pending:
            raise HTTPException(404, detail=f"Sprint {sprint_label!r} is not finished")
    elif plan_state != "completed" and not issues_raw:
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    def _parse_ts(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    estimates_dir = commander / "estimates"
    tickets = []

    for iss in issues_raw:
        issue_num = iss.get("number")
        title = iss.get("title", "")
        status = iss.get("status", "pending")

        estimated_size: Optional[str] = None
        estimated_minutes: Optional[int] = None
        if issue_num is not None:
            est_path = estimates_dir / f"issue-{issue_num}.json"
            if est_path.exists():
                try:
                    est_data = json.loads(est_path.read_text(encoding="utf-8"))
                    estimated_size = est_data.get("size") or None
                    if estimated_size:
                        estimated_minutes = _size_to_minutes(estimated_size) or None
                except (json.JSONDecodeError, OSError):
                    pass

        start_ts = _parse_ts(iss.get("coder_started_at"))
        end_ts = (
            _parse_ts(iss.get("tester_finished_at"))
            or _parse_ts(iss.get("status_changed_at"))
        )
        actual_elapsed_seconds: Optional[float] = None
        actual_elapsed_minutes: Optional[float] = None
        if start_ts is not None and end_ts is not None:
            actual_elapsed_seconds = max(0.0, end_ts - start_ts)
            actual_elapsed_minutes = round(actual_elapsed_seconds / 60, 1)

        delta_minutes: Optional[float] = None
        if estimated_minutes is not None and actual_elapsed_minutes is not None:
            delta_minutes = round(actual_elapsed_minutes - estimated_minutes, 1)

        tickets.append({
            "ticket_id":              issue_num,
            "title":                  title,
            "estimated_size":         estimated_size,
            "estimated_minutes":      estimated_minutes,
            "actual_elapsed_seconds": round(actual_elapsed_seconds) if actual_elapsed_seconds is not None else None,
            "actual_elapsed_minutes": actual_elapsed_minutes,
            "delta_minutes":          delta_minutes,
            "status":                 status,
        })

    return {
        "sprint_label": sprint_label,
        "tickets":      tickets,
    }
