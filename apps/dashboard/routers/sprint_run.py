"""Sprint run route handlers extracted from server.py (issues #1262, #1263).

GET read-only routes moved in issue #1262:
  GET /api/sprints/{sprint_label}/branch-status
  GET /api/sprints/{sprint_label}/rerun/preview
  GET /api/sprints/{sprint_label}/rerun-preview

POST/DELETE write routes moved in issue #1263:
  POST   /api/sprints/run
  DELETE /api/sprints/run/{sprint_label}
  POST   /api/sprints/{sprint_label}/rerun

Subprocess/process glue lives in sprint_run_service.py. All server.py helpers
are accessed via the deferred ``_server()`` import to avoid circular imports.
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from . import sprint_run_service
from .sprint_run_service import SprintMgmtRunBody, SprintRerunV2Body
from .board_cache import invalidate_board
from . import brief_invalidation
from .board_service import SPRINT_LABEL_FORMAT_ERROR
from .hermes_models import SprintRunResponse

router = APIRouter(tags=["sprint_run"])


def _server():
    """Deferred import of the monolith — safe at request time, avoids circular import."""
    import server  # noqa: PLC0415
    return server


def _ticket_rerun_category(labels: set[str]) -> str:
    """Map a ticket's current labels to a rerun category string."""
    if labels & {"UAT", "UAT-approved"}:
        return "UAT"
    if "SIT" in labels:
        return "SIT"
    if "needs-rework" in labels or "need-rework" in labels:
        return "needs-rework"
    return "queued"


_NON_WORK_LABELS_RR = frozenset({"sprint-summary", "docs", "documentation"})


def _int_ticket_numbers(raw_numbers) -> list[int]:
    nums: list[int] = []
    for n in raw_numbers or []:
        try:
            nums.append(int(n))
        except (TypeError, ValueError):
            continue
    return nums


def _artifact_rerun_ticket_numbers(srv, project_root, sprint_label: str, project: str) -> list[int]:
    """Ticket ids from plan/state/DB when GitHub no longer carries the sprint label.

    Child reruns that crash with ``process lost`` before relabel finishes leave
    an empty GitHub column while plan.json / state.json still list the roster.
    """
    seen: set[int] = set()
    ordered: list[int] = []

    def _add(raw_numbers) -> None:
        for n in _int_ticket_numbers(raw_numbers):
            if n not in seen:
                seen.add(n)
                ordered.append(n)

    plan = srv._read_plan_json(project_root, sprint_label) or {}
    if isinstance(plan, dict):
        _add(plan.get("tickets"))

    commander = srv._commander_dir(project_root)
    from routers import sprint_artifact_service  # noqa: PLC0415

    resolved = sprint_artifact_service.resolve_state_path(commander / "sprints", sprint_label)
    if resolved is not None and resolved.is_file():
        try:
            state = json.loads(resolved.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
        state_nums: list[int] = []
        for iss in state.get("issues") or []:
            n = iss.get("number", iss.get("ticket_id"))
            if n is None:
                continue
            st = (iss.get("status") or "").lower()
            agent = (iss.get("agent_status") or "").lower()
            if st == "done" or agent in ("completed", "done", "merged"):
                continue
            try:
                state_nums.append(int(n))
            except (TypeError, ValueError):
                continue
        _add(state_nums)

    try:
        import db  # noqa: PLC0415

        row = db.get_sprint(sprint_label, project=project or None)
        if row and row.get("issues_json"):
            db_nums: list[int] = []
            for iss in json.loads(row["issues_json"] or "[]"):
                n = iss.get("ticket_id", iss.get("number"))
                if n is None:
                    continue
                st = (iss.get("state") or "").lower()
                agent = (iss.get("agent_status") or "").lower()
                if st == "merged" or agent in ("completed", "done"):
                    continue
                try:
                    db_nums.append(int(n))
                except (TypeError, ValueError):
                    continue
            _add(db_nums)
    except Exception:
        pass

    return ordered


def _resolve_rerun_sprint_issues(srv, project: str, sprint_label: str, project_root) -> list[dict]:
    """Issues eligible for rerun preview/POST — GitHub column first, artifacts second."""
    sprint_issues = srv._get_sprint_issues(project, sprint_label)
    if sprint_issues:
        return sprint_issues

    fallback_nums = _artifact_rerun_ticket_numbers(
        srv, project_root, sprint_label, project,
    )
    if not fallback_nums:
        return []

    try:
        all_open = srv.github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError:
        all_open = []
    by_num = {iss["number"]: iss for iss in all_open}

    resolved: list[dict] = []
    for num in fallback_nums:
        iss = by_num.get(num)
        if iss is not None:
            resolved.append(iss)
        else:
            resolved.append({
                "number": num,
                "title": f"Issue #{num}",
                "labels": [],
            })
    return resolved


def _rerun_remove_sprint_labels(srv, sprint_label: str, current_labels: set[str]) -> list[str]:
    """Sprint labels to strip when moving a ticket into a rerun child.

    Normally the ticket's primary label matches *sprint_label*. After a
    ``process lost`` crash the roster may still live on a parent label
    (e.g. sprint-15.1) while History reruns the empty child (sprint-15.2).
    """
    sprint_lbls = sorted(
        lbl for lbl in current_labels
        if srv._SPRINT_LABEL_RE.match(lbl)
    )
    return sprint_lbls or [sprint_label]


@router.get("/api/sprints/{sprint_label}/branch-status")
def get_sprint_branch_status(sprint_label: str, project: str):
    """Check if the sprint branch exists on GitHub.

    Uses gh CLI with a 2-second hard timeout; returns {exists, branch}.
    If the CLI times out or fails, returns exists=False so the UI shows
    the amber fallback without blocking page load.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        repo = srv.github_client.get_repo_for_operation(project)
    except Exception:
        return {"exists": False, "branch": f"sprint/{sprint_label}"}

    branch_name = f"sprint/{sprint_label}"
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/git/ref/heads/{branch_name}"],
            capture_output=True, text=True, timeout=2,
        )
        exists = result.returncode == 0
    except Exception:
        exists = False

    # Check for open PR from sprint branch → develop or master
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    pr_title: Optional[str] = None
    try:
        pr_result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", branch_name,
             "--state", "open", "--json", "number,url,title", "--limit", "1"],
            capture_output=True, text=True, timeout=3,
        )
        if pr_result.returncode == 0 and pr_result.stdout.strip():
            prs = json.loads(pr_result.stdout)
            if prs:
                pr_url = prs[0].get("url")
                pr_number = prs[0].get("number")
                pr_title = prs[0].get("title")
    except Exception as pr_err:
        srv._slog.warn(
            "sprint_pr_lookup_failed",
            f"PR lookup for {branch_name!r} failed: {pr_err}",
            branch=branch_name,
            error=str(pr_err),
        )

    return {"exists": exists, "branch": branch_name,
            "pr_url": pr_url, "pr_number": pr_number, "pr_title": pr_title}


@router.get("/api/sprints/{sprint_label}/rerun/preview")
def rerun_sprint_preview(sprint_label: str, project: str):
    """Return per-ticket rerun preview counts without executing anything (legacy).

    Response schema:
      { new_label, redispatch_count, tester_count, skip_count, by_ticket: [
          { issue_num, issue_title, action }  # action: dispatch_coder|dispatch_tester|skip
        ]
      }
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(project)
    try:
        sprint_issues = _resolve_rerun_sprint_issues(srv, project, sprint_label, project_root)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    redispatch_count = 0
    tester_count = 0
    skip_count = 0
    by_ticket: list[dict] = []

    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        action, _ = srv._rerun_policy(current_labels)
        if action == "dispatch_coder":
            redispatch_count += 1
        elif action == "dispatch_tester":
            tester_count += 1
        else:
            skip_count += 1
        by_ticket.append({
            "issue_num": iss["number"],
            "issue_title": iss["title"],
            "action": action,
        })

    existing_label_names = {lbl["name"] for lbl in srv.github_client.list_labels(repo_name=project)}
    new_label = srv._next_sprint_sublabel(sprint_label, existing_label_names, project_root)

    return {
        "new_label": new_label,
        "redispatch_count": redispatch_count,
        "tester_count": tester_count,
        "skip_count": skip_count,
        "by_ticket": by_ticket,
    }


@router.get("/api/sprints/{sprint_label}/rerun-preview")
def rerun_sprint_preview_v2(sprint_label: str, project: str):
    """Return per-ticket rerun preview with checkbox-ready ticket list.

    Response schema:
      {
        suggested_versioned_label: str,
        tickets: [{ number, title, category, checked }]
          # category: UAT | SIT | needs-rework | queued
          # checked: true for non-UAT tickets (default selection)
      }
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(project)
    try:
        sprint_issues = _resolve_rerun_sprint_issues(srv, project, sprint_label, project_root)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    existing_label_names = {lbl["name"] for lbl in srv.github_client.list_labels(repo_name=project)}
    suggested_versioned_label = srv._next_sprint_sublabel(
        sprint_label, existing_label_names, project_root,
    )

    tickets = []
    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if current_labels & _NON_WORK_LABELS_RR:
            continue  # skip sprint-summary / docs tickets
        category = _ticket_rerun_category(current_labels)
        tickets.append({
            "number": iss["number"],
            "title": iss["title"],
            "category": category,
            "checked": category != "UAT",
        })

    return {
        "suggested_versioned_label": suggested_versioned_label,
        "tickets": tickets,
    }


# ── Write/dispatch routes (issue #1263) ───────────────────────────────────────


def _build_run_argv_extras(body: "SprintMgmtRunBody", base_argv: list[str]) -> list[str]:
    """Append overnight / target-branch flags to sprint_manager argv (issue #1946).

    When mode=overnight and no explicit target_branch, defaults --target-branch
    to "develop" (the legacy-behaviour flag wired to the overnight default).
    Always appends --overnight when mode=overnight so sprint_manager runs
    per-ticket test invocations after each merge.
    """
    argv = list(base_argv)
    effective_target = body.target_branch
    if effective_target is None and body.mode == "overnight":
        effective_target = "develop"
    if effective_target is not None:
        argv.extend(["--target-branch", effective_target])
    if body.mode == "overnight":
        argv.append("--overnight")
    return argv


@router.post("/api/sprints/run", status_code=202, response_model=SprintRunResponse)
def run_sprint_managed(request: Request, body: SprintMgmtRunBody):
    """Spawn sprint_manager.py for the given project + sprint.

    - cwd = project's coder clone
    - ANTHROPIC_API_KEY stripped from subprocess env
    - stdout/stderr → .commander/logs/sprint-run-<label>-<ts>.log
    - PID → .commander/sprints/<label>-pid
    - migrate_from: list of sprint numbers whose open tickets are moved to target
      sprint before dispatch; rollback on any failure.
    """
    srv = _server()

    srv._slog.event(
        "route.entry",
        project="dashboard",
        request_id=request.state.request_id,
        route="/api/sprints/run",
        method="POST",
        sprint_label=body.sprint_label,
        target_project=body.project,
    )
    if not srv._SPRINT_LABEL_RE.match(body.sprint_label):
        srv._slog.event(
            "route.error",
            project="dashboard",
            request_id=request.state.request_id,
            route="/api/sprints/run",
            level="error",
            sprint_label=body.sprint_label,
            error="invalid sprint label",
        )
        raise HTTPException(400, detail=f"{body.sprint_label!r} — {SPRINT_LABEL_FORMAT_ERROR}")
    # mode validation (issue #1946): return 400 immediately for unknown values.
    _VALID_RUN_MODES: frozenset[str] = frozenset({"overnight"})
    if body.mode is not None and body.mode not in _VALID_RUN_MODES:
        raise HTTPException(
            400,
            detail=(
                f"Invalid mode {body.mode!r}. "
                f"Valid values: {sorted(_VALID_RUN_MODES)}"
            ),
        )
    # Per-run provider (anthropic | ica) — same validation as the global toggle.
    # Omitted (None) resolves to the global llmProvider setting so the global
    # toggle is the default and the modal choice is a per-run override.
    from routers import llm_provider_service  # noqa: PLC0415
    if body.llm_provider is None:
        body.llm_provider = llm_provider_service.get_provider()["provider"]
    llm_provider_service.validate_provider(body.llm_provider)

    if not srv.SPRINT_MANAGER_PATH.exists():
        srv._slog.event(
            "route.error",
            project="dashboard",
            request_id=request.state.request_id,
            route="/api/sprints/run",
            level="error",
            sprint_label=body.sprint_label,
            error="sprint_manager.py not found",
        )
        raise HTTPException(502, detail=f"sprint_manager.py not found at {srv.SPRINT_MANAGER_PATH}")

    project_root = srv._project_root_path(body.project)
    running = srv._any_sprint_running(project=body.project)
    if running:
        srv._slog.event(
            "route.error",
            project="dashboard",
            request_id=request.state.request_id,
            route="/api/sprints/run",
            level="error",
            sprint_label=body.sprint_label,
            error="sprint already running",
            running_sprint=running.get("sprint_label"),
        )
        raise HTTPException(
            409,
            detail=(
                f"Cannot start sprint: {running['sprint_label']} is currently running"
                f" on {running['project']}"
            ),
        )
    coder_path = srv._coder_clone_path(project_root)
    commander = srv._commander_dir(project_root)

    srv._reject_terminal_label_redispatch(project_root, body.sprint_label, body.project)
    srv._assert_sprint_signed_off(project_root, body.sprint_label)

    # Block empty runs before spawn
    from services.sprint_manager import sprint_manager as _sm_run
    backlog = _sm_run.list_backlog_issues(body.sprint_label, body.project)
    if not backlog:
        # GitHub's label-search index lags briefly right after a re-run relabels
        # tickets onto the child sprint, so list_backlog_issues can return empty
        # even though the tickets exist — and the run subprocess would pick them up
        # via its plan.json fallback. Don't block if the sprint's plan still lists
        # tickets; let the run proceed (the subprocess resets cleanly if it's truly
        # empty once gh propagates).
        _plan = srv._read_plan_json(project_root, body.sprint_label) or {}
        _plan_tickets = [n for n in (_plan.get("tickets") or []) if isinstance(n, int)]
        backlog = bool(_plan_tickets)
    if not backlog:
        child = srv._sprint_rerun_into_map(project_root).get(body.sprint_label)
        if child:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No dispatchable tickets on {body.sprint_label}. "
                    f"Tickets were moved to {child} — use Run on that sprint instead."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail=(
                f"No dispatchable tickets on {body.sprint_label}. "
                "Use Re-run to create a sub-sprint, or restore sprint labels on GitHub."
            ),
        )

    # Cycle detection
    if srv._DAG_BUILDER_AVAILABLE:
        try:
            _cycle_issues = srv.github_client.list_open_issues_with_body(
                repo_name=body.project, limit=200
            )
            _cycle_sprint_issues = [
                iss for iss in _cycle_issues
                if any(lbl["name"] == body.sprint_label for lbl in iss.get("labels", []))
            ]
            _cycle_tickets = srv._sprint_dag_tickets(project_root, _cycle_sprint_issues)
            _dag_result = srv._build_dag(_cycle_tickets)
            if isinstance(_dag_result, srv._CycleError):
                _payload = _dag_result.to_payload()
                srv._slog.event(
                    "route.error",
                    project="dashboard",
                    request_id=request.state.request_id,
                    route="/api/sprints/run",
                    level="error",
                    sprint_label=body.sprint_label,
                    error="cycle_detected",
                    cycles=_payload["cycles"],
                )
                raise HTTPException(422, detail=_payload)
        except HTTPException:
            raise
        except subprocess.CalledProcessError as e:
            raise srv._gh_error(e)

    # Mis-sizing flags
    if srv._MIS_SIZING_AVAILABLE:
        if srv._mis_sizing.has_unresolved_flags(commander, body.sprint_label):
            raise HTTPException(
                422,
                detail=(
                    "Cannot start sprint: unresolved mis-sizing flags in review queue. "
                    "Open the Run Sprint dialog and resolve all flagged tickets first."
                ),
            )

    # Migration: move open tickets from earlier sprints to target
    migration_log_lines: list[str] = []
    migrated_count = 0
    if body.migrate_from:
        target_label = body.sprint_label
        ts_mig = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        mig_log_dir = commander / "logs"
        mig_log_dir.mkdir(parents=True, exist_ok=True)
        mig_log_path = mig_log_dir / f"sprint-migration-{ts_mig}.log"

        try:
            all_issues = srv.github_client.list_open_issues_with_body(
                repo_name=body.project, limit=200
            )
        except subprocess.CalledProcessError as e:
            raise srv._gh_error(e)

        pending: list[tuple[int, str, list[str]]] = []
        for src_num in body.migrate_from:
            src_label = f"sprint-{src_num}"
            src_issues = [
                iss for iss in all_issues
                if any(lbl["name"] == src_label for lbl in iss.get("labels", []))
            ]
            for iss in src_issues:
                current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
                status_to_remove = list(current_labels & srv._MIGRATION_STATUS_LABELS)
                pending.append((iss["number"], src_label, status_to_remove))

        applied: list[tuple[int, str, list[str]]] = []
        try:
            for issue_num, src_label, status_to_remove in pending:
                remove_labels = [src_label] + status_to_remove
                add_labels = [target_label]
                srv.github_client.update_labels(
                    issue_num,
                    add=add_labels,
                    remove=remove_labels,
                    repo_name=body.project,
                )
                applied.append((issue_num, src_label, status_to_remove))
                migration_log_lines.append(
                    f"Moved #{issue_num} from {src_label} to {target_label}"
                    + (f"; stripped status: {status_to_remove}" if status_to_remove else "")
                )
                migrated_count += 1
        except subprocess.CalledProcessError as rollback_err:
            rollback_errors: list[str] = []
            for issue_num, src_label, status_to_remove in reversed(applied):
                try:
                    srv.github_client.update_labels(
                        issue_num,
                        add=[src_label] + status_to_remove,
                        remove=[target_label],
                        repo_name=body.project,
                    )
                    migration_log_lines.append(f"ROLLBACK #{issue_num}: restored {src_label}")
                except subprocess.CalledProcessError as rb_e:
                    rollback_errors.append(f"#{issue_num}: {rb_e.stderr.strip()}")
                    migration_log_lines.append(f"ROLLBACK FAILED #{issue_num}")

            mig_log_path.write_text("\n".join(migration_log_lines) + "\n", encoding="utf-8")
            detail = f"Migration failed on #{pending[len(applied)][0]}: {rollback_err.stderr.strip()}"
            if rollback_errors:
                detail += f"; rollback errors: {'; '.join(rollback_errors)}"
            raise HTTPException(500, detail=detail)

        mig_log_path.write_text("\n".join(migration_log_lines) + "\n", encoding="utf-8")
        srv.github_client.invalidate("open_issues_body:")
        srv.github_client.invalidate("open_issues:")
        srv.github_client.invalidate("issues:")

    log_dir = commander / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = log_dir / f"sprint-run-{body.sprint_label}-{ts}.log"

    pid_dir = commander / "sprints"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path = pid_dir / f"{body.sprint_label}-pid"
    pending_path = pid_dir / f"{body.sprint_label}-pid.pending"

    _started_at = datetime.now(timezone.utc).isoformat()
    _plan_extra: dict = {
        "started_at": _started_at,
        "use_cline_followups": body.use_cline_followups,
        "llm_provider": body.llm_provider,
    }
    # Store trigger-owner and mode so end-of-run report can echo them (issue #1946).
    if body.by:
        _plan_extra["triggered_by"] = body.by
    if body.mode:
        _plan_extra["run_mode"] = body.mode
    try:
        srv._plan_json_set_state(
            project_root,
            body.sprint_label,
            "running",
            **_plan_extra,
        )
    except Exception:
        pass
    srv._sprint_db_set_state(
        body.sprint_label, body.project, "running", started_at=_started_at
    )

    stripped_env = srv._build_sprint_subprocess_env()
    goal_path = srv._sprint_goal_path(project_root, body.sprint_label)
    if goal_path.exists():
        stripped_env["SPRINT_GOAL"] = goal_path.read_text(encoding="utf-8").strip()

    _run_argv = _build_run_argv_extras(
        body, srv._sprint_manager_argv(body.sprint_label, body.project, project_root)
    )
    proc = sprint_run_service.spawn_sprint_process(
        _run_argv,
        cwd=str(coder_path),
        env=stripped_env,
        log_path=log_path,
        pid_path=pid_path,
        pending_path=pending_path,
        sprint_label=body.sprint_label,
        project=body.project,
    )

    srv._slog.event(
        "sprint.dispatch",
        project="dashboard",
        request_id=request.state.request_id,
        sprint_label=body.sprint_label,
        target_project=body.project,
        dispatch_type="managed",
        pid=proc.pid,
    )
    srv._emit_dashboard_event(
        project=body.project,
        type="sprint_started",
        target=body.sprint_label,
        detail={"sprint_id": body.sprint_label},
        action_id=str(uuid.uuid4()),
    )
    invalidate_board(body.project)
    brief_invalidation.invalidate_home_brief_today()
    return {
        "ok": True,
        "sprint_label": body.sprint_label,
        "pid": proc.pid,
        "log": str(log_path),
        "migrated_count": migrated_count,
        "migrate_from": body.migrate_from,
    }


@router.delete("/api/sprints/run/{sprint_label}", status_code=200)
def kill_sprint(sprint_label: str, project: str):
    """SIGTERM then SIGKILL the running sprint process for the given project/label.

    Unix only (macOS, Linux). Windows is not a supported platform for process
    termination — os.kill() with SIGTERM/SIGKILL is unavailable there.
    """
    if sys.platform == "win32":
        raise HTTPException(
            status_code=501,
            detail=(
                "Process termination via SIGTERM/SIGKILL is not supported on Windows. "
                "Run Commander on macOS or Linux."
            ),
        )
    if not _server()._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    srv = _server()
    project_root = srv._project_root_path(project)
    sprints_dir = srv._commander_dir(project_root) / "sprints"
    pid_file = sprints_dir / f"{sprint_label}-pid"
    pending_file = sprints_dir / f"{sprint_label}-pid.pending"

    _kill_started_at: Optional[str] = None
    try:
        _kill_plan = srv._read_plan_json(project_root, sprint_label) or {}
        _kill_started_at = _kill_plan.get("started_at")
    except Exception:
        pass

    active_file = pid_file if pid_file.exists() else (pending_file if pending_file.exists() else None)
    if active_file is None:
        raise HTTPException(404, detail=f"No running sprint found for {sprint_label}")

    try:
        pid = int(active_file.read_text(encoding="utf-8").strip())
    except ValueError:
        for f in (pid_file, pending_file):
            try:
                f.unlink()
            except OSError:
                pass
        raise HTTPException(404, detail=f"Invalid PID file for {sprint_label}")

    sprint_run_service.kill_sprint_process(pid)

    for f in (pid_file, pending_file):
        try:
            f.unlink()
        except OSError:
            pass

    _cancel_reason = "stopped by user"
    _in_flight_agent = frozenset({
        "coder_dispatched", "coder_running", "tester_dispatched", "tester_running",
    })
    state_path = sprints_dir / f"{sprint_label}-state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            ended_iso = datetime.now(timezone.utc).isoformat()
            state["ended_at"] = ended_iso
            for iss in state.get("issues", []):
                if (
                    iss.get("agent_status") in _in_flight_agent
                    or iss.get("status") == "in-progress"
                ):
                    iss["agent_status"] = "failed"
                    if iss.get("status") not in ("done", "skipped"):
                        iss["status"] = "skipped"
                    iss.setdefault("failure_reason", _cancel_reason)
                    iss.setdefault("category", "CANCELLED")
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass

    project_root = srv._project_root_path(project)
    json_path = srv._sprint_json_path(project_root, sprint_label)
    data = srv._sprint_json_read(json_path)
    if data:
        data["status"] = "needs_rework"
        data["end_reason"] = _cancel_reason
        srv._sprint_json_write(json_path, data)

    try:
        srv._plan_json_set_state(project_root, sprint_label, "needs_rework",
                                 end_reason=_cancel_reason)
    except Exception:
        pass
    srv._sprint_db_set_state(sprint_label, project, "needs_rework",
                             end_reason=_cancel_reason)

    try:
        _summary_url = srv._read_sprint_summary_url(project_root, sprint_label, project)
        _summary_num = None
        if _summary_url:
            import re as _re
            _m_sum = _re.search(r"/issues/(\d+)", _summary_url)
            _summary_num = int(_m_sum.group(1)) if _m_sum else None
        if _summary_num:
            srv.github_client.add_comment(
                _summary_num,
                f"⚠️ Sprint `{sprint_label}` was stopped by the user — marked "
                f"**needs_rework** (end reason: *{_cancel_reason}*). Tickets were "
                "not labeled `need-rework`; re-run via a child sub-sprint.",
                repo_name=project,
            )
    except Exception:
        pass

    srv._emit_dashboard_event(
        project=project or "dashboard",
        type="sprint_cancelled",
        target=sprint_label,
        detail={"sprint_id": sprint_label},
        action_id=str(uuid.uuid4()),
    )
    invalidate_board(project)

    return {"ok": True}


@router.post("/api/sprints/{sprint_label}/rerun")
def rerun_sprint(sprint_label: str, project: str, body: SprintRerunV2Body):
    """Create an independent sub-sprint from selected non-UAT tickets.

    Moves the chosen non-UAT tickets carrying sprint_label to a new versioned
    sub-sprint label (sprint-N.1, sprint-N.2, …), creates plan.json with a parent
    reference, and (when auto_run) dispatches sprint_manager for the sub-sprint.
    The original sprint label, plan.json, branch, and PR are NOT modified.

    Body: { ticket_numbers?: int[], auto_run?: bool }
      - ticket_numbers: which non-UAT tickets to move; empty = all of them.
      - auto_run: dispatch immediately (default true); false leaves them queued.
    Response: { sub_label, noop, dispatch_count, decisions, moved, [errors] }
    """
    srv = _server()

    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = srv._project_root_path(project)

    if srv._is_sprint_running(project_root, sprint_label):
        raise HTTPException(409, detail=f"Sprint {sprint_label} is currently running")

    try:
        sprint_issues = _resolve_rerun_sprint_issues(srv, project, sprint_label, project_root)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    decisions: list[dict] = []
    issue_labels: dict[int, set[str]] = {}
    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if current_labels & _NON_WORK_LABELS_RR:
            continue
        issue_labels[iss["number"]] = current_labels
        action, _ = srv._rerun_policy(current_labels)
        decisions.append({
            "issue_num": iss["number"],
            "issue_title": iss["title"],
            "action": action,
        })

    to_move = [d for d in decisions if d["action"] != "skip"]

    if body.ticket_numbers:
        selected = set(body.ticket_numbers)
        to_move = [d for d in to_move if d["issue_num"] in selected]

    if not to_move:
        return {
            "noop": True,
            "dispatch_count": 0,
            "message": "No issues require re-dispatch; all are UAT or UAT-approved.",
            "decisions": decisions,
        }

    existing_label_names = {lbl["name"] for lbl in srv.github_client.list_labels(repo_name=project)}
    sub_label = srv._next_sprint_sublabel(sprint_label, existing_label_names, project_root)

    parent_color = srv.github_client.get_label_color(sprint_label, repo_name=project) or "0075ca"
    srv.github_client.create_label(
        sub_label, parent_color,
        description=f"Re-run of {sprint_label}",
        repo_name=project,
    )

    errors: list[str] = []
    moved: list[int] = []
    stripped_labels: list[dict] = []

    for d in to_move:
        issue_num = d["issue_num"]
        current = issue_labels.get(issue_num, set())
        stale = srv._stale_session_labels(current)
        remove_sprint = _rerun_remove_sprint_labels(srv, sprint_label, current)
        try:
            srv.github_client.update_labels(
                issue_num,
                add=[sub_label],
                remove=[*remove_sprint, *stale],
                repo_name=project,
            )
        except subprocess.CalledProcessError as e:
            errors.append(f"#{issue_num} label swap failed: {e.stderr.strip() if e.stderr else str(e)}")
            continue
        moved.append(issue_num)
        if stale:
            stripped_labels.append({"issue_num": issue_num, "removed_labels": stale})

    commander = srv._commander_dir(project_root)
    sprints_dir = commander / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)

    logs_dir = commander / "logs"
    rotated_logs: list[int] = []
    for issue_num in moved:
        old_log = logs_dir / f"sprint-issue-{issue_num}.log"
        if old_log.exists():
            try:
                old_log.replace(old_log.parent / f"{old_log.name}.prev")
                rotated_logs.append(issue_num)
            except OSError:
                pass

    srv._write_plan_json(project_root, sub_label, {
        "state": "draft",
        "tickets": moved,
        "parent": sprint_label,
    })
    # DB-side lineage link alongside the plan.json copy above (issue #1691) —
    # best-effort; a failure here must not block the rerun.
    try:
        srv.db.set_sprint_immediate_parent(sub_label, project, sprint_label)
    except Exception:
        pass

    parent_state_path = sprints_dir / f"{sprint_label}-state.json"
    if parent_state_path.exists():
        try:
            state_data = json.loads(parent_state_path.read_text(encoding="utf-8"))
            state_data["rerun_into"] = sub_label
            parent_state_path.write_text(json.dumps(state_data), encoding="utf-8")
        except Exception:
            pass

    srv.github_client.invalidate("open_issues_body:")
    srv.github_client.invalidate("open_issues:")
    srv.github_client.invalidate("issues:")
    srv.github_client.invalidate("sprint_labels:")

    confirmed_moved = srv._await_rerun_relabel(project, sub_label, moved)
    all_moved = set(moved).issubset(confirmed_moved)

    srv._emit_dashboard_event(
        project=project,
        type="sprint_rerun",
        target=sub_label,
        detail={
            "sprint_id": sprint_label,
            "sub_label": sub_label,
            "stripped_labels": stripped_labels,
        },
        action_id=str(uuid.uuid4()),
    )
    invalidate_board(project)

    if stripped_labels:
        print(
            f"[rerun] {sprint_label} → {sub_label}: stripped stale session "
            f"labels from {len(stripped_labels)} item(s): "
            + ", ".join(
                f"#{e['issue_num']}={'+'.join(e['removed_labels'])}"
                for e in stripped_labels
            ),
            flush=True,
        )

    result: dict = {
        "noop": False,
        "sub_label": sub_label,
        "parent_label": sprint_label,
        "dispatch_count": len(moved),
        "moved": moved,
        "moved_confirmed": sorted(confirmed_moved),
        "all_moved": all_moved,
        "decisions": decisions,
        "stripped_labels": stripped_labels,
        "rotated_logs": rotated_logs,
    }
    if errors:
        result["errors"] = errors

    # The child's plan.json is created fresh (only `parent` is carried) — copy
    # the parent's per-run llm_provider so a rerun keeps routing through the
    # same provider instead of silently reverting to anthropic.
    _parent_plan = srv._read_plan_json(project_root, sprint_label) or {}
    _parent_provider = _parent_plan.get("llm_provider")
    _provider_field = {"llm_provider": _parent_provider} if _parent_provider else {}

    if not body.auto_run:
        result["queued"] = True
        # Plan is at "draft" — promote so History shows a Run button for this child.
        try:
            srv._plan_json_set_state(project_root, sub_label, "needs_rework",
                                     end_reason="queued", parent=sprint_label,
                                     **_provider_field)
        except Exception:
            pass
        # DB-side draft row (issue #1693) — deliberately kept at `draft`, not
        # mirroring plan.json's needs_rework/queued display hack: that value
        # exists only to make History show a Run button and was never a real
        # lifecycle transition (no `running` boundary was crossed). Best-effort.
        try:
            srv.db.ensure_sprint_draft_row(sub_label, project)
        except Exception:
            pass
        return result

    # Auto-run: dispatch sprint_manager for the sub-sprint
    if not srv.SPRINT_MANAGER_PATH.exists():
        # Plan is at "draft" — promote so History shows a Run button for this child.
        try:
            srv._plan_json_set_state(project_root, sub_label, "needs_rework",
                                     end_reason="sprint_manager_missing", parent=sprint_label)
        except Exception:
            pass
        return result

    log_dir = commander / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = str(uuid.uuid4())
    coder_path = srv._coder_clone_path(project_root)
    pid_path = sprints_dir / f"{sub_label}-pid"
    pending_path = sprints_dir / f"{sub_label}-pid.pending"
    run_log_path = log_dir / f"sprint-run-{sub_label}-{ts}.log"

    _sub_started_at = datetime.now(timezone.utc).isoformat()
    try:
        srv._plan_json_set_state(project_root, sub_label, "running",
                                 started_at=_sub_started_at,
                                 parent=sprint_label,
                                 **_provider_field)
    except Exception:
        pass
    srv._sprint_db_set_state(sub_label, project, "running", started_at=_sub_started_at)

    stripped_env = srv._build_sprint_subprocess_env()
    goal_path = srv._sprint_goal_path(project_root, sprint_label)
    if not goal_path.exists():
        goal_path = srv._sprint_goal_path(project_root, sub_label)
    if goal_path.exists():
        stripped_env["SPRINT_GOAL"] = goal_path.read_text(encoding="utf-8").strip()

    # Write a rerun manifest so sprint_manager uses the exact moved ticket list
    # instead of querying GitHub (which is eventually consistent and may lag label
    # edits by seconds, causing the manager to dispatch a stale subset — issue #1827).
    _moved_set = set(moved)
    _manifest_decisions = [d for d in to_move if d["issue_num"] in _moved_set]
    _manifest_path = sprints_dir / f"{sub_label}-rerun-manifest.json"
    try:
        _manifest_path.write_text(
            json.dumps({"decisions": _manifest_decisions, "all_moved": all_moved}),
            encoding="utf-8",
        )
    except OSError as _manifest_exc:
        # Tickets are already relabeled to sub_label but sprint_manager was never
        # dispatched — the "stranded moved tickets" failure mode (#1827) triggered
        # by I/O error instead of label-lag race. Roll back state and fail loudly
        # naming the stranded tickets (issue #1840).
        _moved_str = ", ".join(f"#{n}" for n in moved)
        try:
            srv._plan_json_set_state(project_root, sub_label, "needs_rework",
                                     end_reason="manifest_write_failed",
                                     parent=sprint_label)
            srv._sprint_db_set_state(sub_label, project, "needs_rework",
                                     end_reason="manifest_write_failed")
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=(
                f"Manifest write failed ({_manifest_exc}): tickets {_moved_str} were "
                f"relabeled to {sub_label!r} but sprint_manager was never dispatched. "
                "Fix the disk/permission issue and retry the rerun."
            ),
        )
    _spawn_argv = srv._sprint_manager_argv(sub_label, project, project_root)
    _spawn_argv += ["--rerun-manifest", str(_manifest_path)]

    try:
        proc = sprint_run_service.spawn_sprint_process(
            _spawn_argv,
            cwd=str(coder_path),
            env=stripped_env,
            log_path=run_log_path,
            pid_path=pid_path,
            pending_path=pending_path,
            sprint_label=sub_label,
            project=project,
        )
    except HTTPException:
        # Spawn failed — roll plan/DB back to needs_rework so History can re-dispatch.
        try:
            srv._plan_json_set_state(project_root, sub_label, "needs_rework",
                                     end_reason="spawn_failed", parent=sprint_label)
            srv._sprint_db_set_state(sub_label, project, "needs_rework",
                                     end_reason="spawn_failed")
        except Exception:
            pass
        raise

    result["run_id"] = run_id
    result["pid"] = proc.pid
    result["log"] = str(run_log_path)
    return result
