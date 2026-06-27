from __future__ import annotations
import json
import os
import subprocess
import sys
import uuid
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import github_client  # noqa: E402
from services.logging import log as _slog  # noqa: E402

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


# ── Constants (mirrored from server.py) ──────────────────────────────────────

_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")
_SUMMARY_TITLE_RE = re.compile(r"^Sprint \d+(\.\d+)*\s+Executive Summary$")
_SUMMARY_TITLE_NUM_RE = re.compile(r"^Sprint (\d+(?:\.\d+)*)\s+Executive Summary$")

SPRINT_MANAGER_PATH = _REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
SPRINT_LOG_PATH = _DASHBOARD_ROOT / "sprints" / "sprint_run.log"
_ALERT_MODES = os.environ.get("COMMANDER_ALERT_MODES", "dashboard-banner,ntfy")

_SPRINT_LABEL_RE_ALL = re.compile(r"^sprint-\d+(\.\d+)?$")


# ── Dashboard event helpers ───────────────────────────────────────────────────

def _dashboard_actor() -> str:
    return os.environ.get("COMMANDER_USER", "dashboard")


def _emit_dashboard_event(
    project: str,
    type: str,
    target: str,
    detail: dict,
    action_id: str,
) -> None:
    try:
        db.record_event(
            project=project,
            source="dashboard",
            actor=_dashboard_actor(),
            type=type,
            target=target,
            detail=detail,
            action_id=action_id,
        )
    except Exception:
        pass


# ── Sprint label helpers ──────────────────────────────────────────────────────

def _sprint_label_sort_key(label: str) -> tuple:
    """Return (N, M) tuple for natural sprint label ordering."""
    m = re.match(r"^sprint-(\d+)(?:\.(\d+))?$", label)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2) or 0))


def _finished_sprint_summaries(repo_name: str | None) -> dict[str, dict]:
    """Map sprint-<N> label -> summary issue for sprints with an Executive Summary."""
    try:
        repo = github_client.get_repo_for_operation(repo_name)
    except Exception:
        return {}
    try:
        issues = github_client.list_summary_issues(repo_name=repo)
    except Exception:
        issues = []

    result: dict[str, dict] = {}
    for iss in issues:
        m = _SUMMARY_TITLE_NUM_RE.match(iss.get("title", "") or "")
        if not m:
            continue
        label = f"sprint-{m.group(1)}"
        prev = result.get(label)
        if prev is None or (iss.get("number") or 0) > (prev.get("number") or 0):
            result[label] = {
                "number": iss.get("number"),
                "url": iss.get("url"),
                "title": iss.get("title"),
            }
    return result


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


def _write_plan_json(project_root: Path, sprint_label: str, data: dict) -> None:
    """Write plan.json atomically."""
    path = _sprint_plan_path(project_root, sprint_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


# ── Sprint order / locally signed off ────────────────────────────────────────

def _sprint_order_path(project_root: Path) -> Path:
    return _commander_dir(project_root) / "sprint-order.json"


def _locally_signed_off_sprint_labels(project_root: Path) -> set[str]:
    """Sprint labels signed off via Merge Sprint without a GitHub Executive Summary."""
    labels: set[str] = set()
    sprints_dir = _commander_dir(project_root) / "sprints"
    if not sprints_dir.exists():
        return labels
    for plan_file in sprints_dir.glob("*-plan.json"):
        label = plan_file.name[: -len("-plan.json")]
        if not _SPRINT_LABEL_RE.match(label):
            continue
        plan = _read_plan_json(project_root, label)
        if not plan:
            continue
        if (plan.get("state") or "").lower() != "completed":
            continue
        er = (plan.get("end_reason") or "").lower()
        if er in ("merge_sprint", "bulk_complete"):
            labels.add(label)
    return labels


def _load_sprint_order(project_root: Path, all_sprint_labels: list[str]) -> list[str]:
    """Load sprint order from file; fill missing/new sprint labels in natural order."""
    order_path = _sprint_order_path(project_root)
    saved: list[str] = []
    if order_path.exists():
        try:
            saved = json.loads(order_path.read_text(encoding="utf-8"))
        except Exception:
            saved = []

    all_labels = set(all_sprint_labels)
    saved_set = set(saved)

    result = [s for s in saved if s in all_labels]
    new_sprints = sorted(all_labels - saved_set, key=_sprint_label_sort_key)
    result.extend(new_sprints)
    return result


# ── Sprint rerun helpers ──────────────────────────────────────────────────────

def _sprint_rerun_into_map(project_root: Path) -> dict[str, str]:
    """Map parent sprint labels → child re-run sub-sprint labels still in play."""
    sprints_dir = _commander_dir(project_root) / "sprints"
    result: dict[str, str] = {}
    if not sprints_dir.exists():
        return result
    for state_file in sprints_dir.glob("sprint-*-state.json"):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            parent = state_file.name.replace("-state.json", "")
            sub = data.get("rerun_into")
            if sub:
                result[parent] = sub
        except (OSError, json.JSONDecodeError):
            continue
    for plan_file in sprints_dir.glob("sprint-*-plan.json"):
        label = plan_file.name[: -len("-plan.json")]
        plan = _read_plan_json(project_root, label)
        if not plan:
            continue
        parent = plan.get("parent")
        if not parent:
            continue
        result[parent] = label
    return result


# ── Sign-off gate helpers ─────────────────────────────────────────────────────

def _sprint_signoff_state(project_root: Path, sprint_label: str) -> Optional[str]:
    """Return 'pending', 'approved', or None for a sprint's sign-off gate.

    When sign-off is disabled globally (COMMANDER_DISABLE_SIGNOFF / the
    disable_sprint_signoff setting), the gate does not apply — return None so a
    draft stamped with a stale ``signoff: pending`` (sprints always stamp it at
    creation) neither blocks the run path (_assert_sprint_signed_off) nor shows
    a board badge. Without this, disabling sign-off left a dead-end: the Approve
    UI + approve API are off, but the run still refused on the stale gate.
    """
    try:
        import config  # noqa: PLC0415 — lazy, matches sibling modules
        if config.sprint_signoff_disabled():
            return None
    except Exception:
        pass
    plan = _read_plan_json(project_root, sprint_label)
    if not plan:
        return None
    signoff = plan.get("signoff")
    if isinstance(signoff, dict):
        st = signoff.get("state")
        if st in ("pending", "approved"):
            return st
    return None


# ── Sprint has-run helper ─────────────────────────────────────────────────────

def _sprint_has_own_run_outcome(project_root: Path, sprint_label: str, project: str = "") -> bool:
    """True when outcome data for *this* label exists (not a sibling/base run)."""
    plan = _read_plan_json(project_root, sprint_label)
    if plan and plan.get("state") in ("planning", "draft", "planned"):
        return False

    # Scope by project — sprint labels are unique only per repo (cross-project leak).
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

    # Check for dry-run only
    issues = state_data.get("issues") or []
    if not issues:
        return False
    if any(i.get("coder_started_at") or i.get("tester_started_at") for i in issues):
        return True
    return not any((i.get("skip_reason") or "").lower() == "dry-run" for i in issues)


# ── Estimate stale check ──────────────────────────────────────────────────────

def _check_estimate_stale(issue_num: int, current_body: str, estimates_dir) -> bool:
    """Return True if the stored estimate is stale (body changed or hash missing)."""
    import hashlib
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


# ── Models ────────────────────────────────────────────────────────────────────

class SprintRunBody(BaseModel):
    label: str
    goal: str
    budget: Optional[int] = None


class SprintMgmtRunBody(BaseModel):
    project: str
    sprint_label: str
    migrate_from: list[int] = []
    use_cline_followups: bool = False


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/sprint-run")
def run_sprint(request: Request, body: SprintRunBody):
    """Spawn sprint_manager.py as a detached background process."""
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/sprint-run", method="POST", sprint_label=body.label)
    if not _SPRINT_LABEL_RE.match(body.label):
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprint-run", level="error", sprint_label=body.label, error="invalid sprint label")
        raise HTTPException(400, detail=f"Invalid sprint label: {body.label!r}")
    if not SPRINT_MANAGER_PATH.exists():
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprint-run", level="error", sprint_label=body.label, error="sprint_manager.py not found")
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    SPRINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(SPRINT_LOG_PATH, "a")

    cmd = [sys.executable, str(SPRINT_MANAGER_PATH), body.label]
    if body.budget is not None:
        cmd += [f"--budget={body.budget}"]
    cmd += ["--alert-mode", _ALERT_MODES]

    subprocess.Popen(
        cmd,
        env={**os.environ, "SPRINT_GOAL": body.goal},
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    _slog.event("sprint.dispatch", project="dashboard", request_id=request.state.request_id, sprint_label=body.label, dispatch_type="simple")
    _emit_dashboard_event(
        project="dashboard",
        type="sprint_run",
        target=body.label,
        detail={"sprint_id": body.label},
        action_id=str(uuid.uuid4()),
    )
    return {"ok": True, "label": body.label}


@router.get("/api/sprint-management/issues")
def get_sprint_management_issues(repo: str):
    """Return all open issues + sprint list + display order for a project.

    Also returns:
    - empty_sprint_labels: sprint labels that have 0 open tickets (stale/ghost sprints)
    - placeholder_sprint: the next sprint number to show as a drop target (max+1)

    Issues include a sprint_label field (e.g. "sprint-15" or "sprint-15.1") that
    identifies their exact sprint label, including dotted sub-labels.
    """
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_issues = pool.submit(github_client.list_open_issues_with_body, repo, 200)
            f_sprints = pool.submit(github_client.list_sprints, repo)
            f_labels = pool.submit(github_client.list_sprint_labels, repo)
            issues = f_issues.result()
            sprints = f_sprints.result()
            all_sprint_labels = f_labels.result()
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    sprint_label_re = re.compile(r"^sprint-(\d+(?:\.\d+)*)$")
    result_issues = []
    # Count open tickets per sprint label (both plain and dotted)
    sprint_ticket_counts: dict[str, int] = {lbl: 0 for lbl in all_sprint_labels}

    # Resolve estimates dir once for stale-hash checks (issue #453)
    try:
        _est_project_root = _project_root_path(repo)
        _estimates_dir = _commander_dir(_est_project_root) / "estimates"
    except Exception:
        _estimates_dir = None

    for iss in issues:
        _is_summary = (
            any(lbl["name"] == "sprint-summary" for lbl in iss.get("labels", []))
            or bool(_SUMMARY_TITLE_RE.match(iss.get("title", "") or ""))
        )
        if _is_summary:
            continue  # hide sprint-summary issues from pane (AC P1-3)
        sprint_num = None
        found_sprint_label = None
        for lbl in iss.get("labels", []):
            m = sprint_label_re.match(lbl["name"])
            if m:
                found_sprint_label = lbl["name"]
                sprint_num = int(m.group(1).split(".")[0])
                break

        iss_body = iss.get("body", "") or ""
        estimate_stale = _check_estimate_stale(iss["number"], iss_body, _estimates_dir)

        result_issues.append({
            "number": iss["number"],
            "title": iss["title"],
            "body": iss_body,
            "labels": iss.get("labels", []),
            "sprint": sprint_num,
            "sprint_label": found_sprint_label,
            "status": github_client.classify_issue(iss),
            "url": iss.get("url", ""), "created_at": iss.get("createdAt", "") or iss.get("created_at", ""),
            "estimate_stale": estimate_stale, "milestone": github_client.milestone_view(iss),
        })
        if found_sprint_label is not None and found_sprint_label in sprint_ticket_counts:
            sprint_ticket_counts[found_sprint_label] += 1

    # For "empty sprint" cleanup: only care about plain sprint-N labels
    plain_sprint_counts = {
        n: sprint_ticket_counts.get(f"sprint-{n}", 0) for n in sprints
    }
    active_sprint_nums = [n for n, count in plain_sprint_counts.items() if count > 0]
    # Also consider sub-labels when determining if a base sprint is active
    for lbl in all_sprint_labels:
        m = sprint_label_re.match(lbl)
        if m and "." in m.group(1) and sprint_ticket_counts.get(lbl, 0) > 0:
            base = int(m.group(1).split(".")[0])
            if base not in active_sprint_nums:
                active_sprint_nums.append(base)
    min_active_sprint = min(active_sprint_nums) if active_sprint_nums else None

    empty_sprint_labels = [
        f"sprint-{n}" for n in sorted(plain_sprint_counts.keys())
        if plain_sprint_counts[n] == 0
        and min_active_sprint is not None
        and n < min_active_sprint
    ]

    project_root = _project_root_path(repo)

    # Finished sprints = those with a posted "Sprint N Executive Summary" issue,
    # plus locally signed-off merges (merge_sprint / bulk_complete).
    #
    # We track two sets that differ only for a finished sprint that still has an
    # open ticket (typically awaiting UAT sign-off):
    #   - merged_set:  truly shipped — locally signed-off, lifecycle-completed,
    #                  or merged-branch. Belongs in History; the board drops it
    #                  even with an open UAT ticket so it stops double-showing as
    #                  a Ready-to-merge card while History lists it Completed.
    #   - finished_set: merged_set PLUS bare run-end summaries. A summary is
    #                  posted at run end, BEFORE the human merges, so a
    #                  summary-only sprint may still need its merge card — it
    #                  stays on the board while it has open tickets.
    finished_map = _finished_sprint_summaries(repo)
    merged_set: set[str] = set(_locally_signed_off_sprint_labels(project_root))
    # Treat any sprint the lifecycle DB marks completed as merged, so the board
    # honours DB-level sign-offs (Merge Sprint, bulk complete, reconciler
    # auto-complete, manual repair) even when plan.json wasn't dual-written. The
    # sprints table is the single source of truth for lifecycle state.
    try:
        merged_set |= {
            r.get("label")
            for r in db.list_sprints_lifecycle()
            if r.get("label")
            and (r.get("project") or "") == repo
            and db.canonical_lifecycle(r.get("state") or "") == "completed"
        }
    except Exception:
        pass

    # Display-only cross-clone signal: a sprint whose branch was merged (a merged
    # PR exists for sprint/<label>) AND whose tickets are all closed (0 open) is
    # shipped — drop it from the board even without an Executive Summary issue or
    # a local sign-off, so a sprint completed on another clone stops lingering
    # here. One cached gh call per load (not per sprint).
    try:
        merged_heads = github_client.list_merged_sprint_branches(repo_name=repo)
        if merged_heads:
            for lbl in all_sprint_labels:
                if (
                    sprint_label_re.match(lbl)
                    and sprint_ticket_counts.get(lbl, 0) == 0
                    and f"sprint/{lbl}" in merged_heads
                ):
                    merged_set.add(lbl)
    except Exception:
        pass

    finished_set = set(finished_map.keys()) | merged_set

    # Sprint labels to render as panes: any with tickets, PLUS empty labels that
    # are NOT finished — so a freshly-created sprint (0 tickets, no summary) still
    # shows as a drop target. Finished sprints whose tickets are all closed are
    # not resurrected as empty planning panes. Superseded empty ghosts — a 0-ticket
    # sprint (incl. an abandoned draft) numbered below the lowest active sprint —
    # are dropped so they stop lingering on the board after later sprints ran
    # (e.g. an empty Sprint 9 draft left behind after the project ran to 11).
    _empty_ghosts = set(empty_sprint_labels)
    renderable_sprint_labels = [
        lbl for lbl in all_sprint_labels
        if (sprint_ticket_counts.get(lbl, 0) > 0 or lbl not in finished_set)
        and lbl not in _empty_ghosts
    ]
    order = _load_sprint_order(project_root, renderable_sprint_labels)

    # Apply per-sprint plan.json ordering; fallback to ascending issue number (issue #441)
    sprint_issues_map: dict[str, list] = {}
    unassigned_issues = []
    for iss in result_issues:
        lbl = iss.get("sprint_label")
        if lbl:
            sprint_issues_map.setdefault(lbl, []).append(iss)
        else:
            unassigned_issues.append(iss)
    ordered_result: list = []
    sprint_parents: dict[str, Optional[str]] = {}
    sprint_plan_states: dict[str, str] = {}
    for lbl, iss_list in sprint_issues_map.items():
        plan_data = _read_plan_json(project_root, lbl)
        if plan_data is not None:
            sprint_parents[lbl] = plan_data.get("parent")
            if isinstance(plan_data, dict) and plan_data.get("state"):
                sprint_plan_states[lbl] = plan_data["state"]
            try:
                raw_tickets = plan_data.get("tickets", plan_data) if isinstance(plan_data, dict) else plan_data
                plan_order: list[int] = raw_tickets if isinstance(raw_tickets, list) else []
                plan_idx = {n: i for i, n in enumerate(plan_order)}
                iss_list.sort(key=lambda i: plan_idx.get(i["number"], len(plan_order)))
            except Exception:
                iss_list.sort(key=lambda i: i["number"])
        else:
            sprint_parents[lbl] = None
            iss_list.sort(key=lambda i: i["number"])
        ordered_result.extend(iss_list)
    ordered_result.extend(unassigned_issues)
    result_issues = ordered_result

    # Finished sprints (computed above) are surfaced so the board marks them
    # finished and stops showing NEXT UP / pre-flight — same GitHub-backed signal
    # the nav pill uses (cross-machine).
    finished_sprints = sorted(finished_set)

    # Placeholder/next sprint = high-water mark + 1 over labels, finished
    # summaries, lifecycle DB, sprint_history, and local artifacts — not GitHub
    # labels alone (a deleted sprint-99 ledger row must not allow reusing 99).
    placeholder_sprint = _server()._next_new_sprint_number(repo)

    sprint_rerun_into = _sprint_rerun_into_map(project_root)

    # Supplement the lineage maps from the lifecycle DB so the board groups a
    # rerun lineage even when this clone is missing the plan.json files (the DB
    # parent_label is authoritative after the B1 backfill). plan.json wins where
    # present; the DB only fills gaps. Keeps the board's lineage view consistent
    # with History, which already derives lineage from the DB.
    def _sub_idx(lbl: str) -> float:
        m = re.match(r"^sprint-\d+\.(\d+)$", lbl or "")
        return float(m.group(1)) if m else 0.0

    try:
        for _row in db.list_sprints_lifecycle():
            if (_row.get("project") or "") != repo:
                continue
            _lbl = _row.get("label") or ""
            _parent = (_row.get("parent_label") or "").strip()
            if not _lbl or not _parent:
                continue
            if not sprint_parents.get(_lbl):
                sprint_parents[_lbl] = _parent
            _existing = sprint_rerun_into.get(_parent)
            if _existing is None or _sub_idx(_lbl) > _sub_idx(_existing):
                sprint_rerun_into[_parent] = _lbl
    except Exception:
        pass

    # Sign-off gate state per renderable label (issue #862) — drives the
    # PENDING SIGN-OFF badge and the muted Run Sprint button on the board.
    sprint_signoff: dict[str, str] = {}
    for lbl in renderable_sprint_labels:
        st = _sprint_signoff_state(project_root, lbl)
        if st is not None:
            sprint_signoff[lbl] = st

    # Ledger-backed "this label actually ran" — board re-run / merge affordances
    # use this instead of ticket labels (needs-rework/SIT from a prior sprint move).
    sprint_has_run: dict[str, bool] = {
        lbl: _sprint_has_own_run_outcome(project_root, lbl, repo)
        for lbl in renderable_sprint_labels
    }

    return {
        "sprints": sprints,
        "order": order,
        "issues": result_issues,
        "empty_sprint_labels": empty_sprint_labels,
        "placeholder_sprint": placeholder_sprint,
        "sprint_parents": sprint_parents,
        "sprint_plan_states": sprint_plan_states,
        "finished_sprints": finished_sprints,
        "merged_sprints": sorted(merged_set),
        "sprint_rerun_into": sprint_rerun_into,
        "sprint_signoff": sprint_signoff,
        "sprint_has_run": sprint_has_run,
    }
