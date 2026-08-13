"""Preflight sprint endpoints (extracted from server.py, issue #1254).

Handles the two routes that check sprint health before dispatch:
  GET  /api/sprints/{sprint_label}/preflight
  POST /api/sprints/{sprint_label}/preflight-fix

The standalone cycle-check, conflicts, and dep-order endpoints were removed in
issue #2234 (zero frontend callers); their data is still returned inline by the
aggregate GET preflight response.

Helpers that are also used by other routes in server.py (e.g. _sprint_dag_tickets,
_check_estimate_stale, _resolve_issue_estimate) are accessed via a deferred
import of server at request time to avoid the import-time circular dependency.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from services.sprint_manager import fill_acceptance_criteria as _fill_ac
from services.sprint_manager.ticket_spec import parse_ticket_spec as _parse_ticket_spec
from services.sprint_manager.sizing import SIZE_TO_MINUTES as _SIZE_TO_MINUTES
from services.sprint_manager.definition_of_ready import check_ticket_readiness as _check_dor
import services.sprint_manager.settings_repo as _settings_repo
from services.sprint_manager.settings_schema import (
    APP_CONFIG_KEY as _APP_CONFIG_KEY,
    build_effective_response as _build_effective_response,
)
from services.sprint_manager.estimate_issue import (
    apply_estimated_status as _ei_apply_estimated_status,
    apply_label as _ei_apply_label,
    fetch_issue as _ei_fetch_issue,
    run_estimator as _ei_run_estimator,
)

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from . import xl_suggestions_service as _xl_svc  # noqa: E402

router = APIRouter(tags=["sprint_preflight"])

_STALE_ESTIMATE_DAYS = 7


def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415
    return server


# ── Local helpers (only used by the five preflight handlers) ─────────────────

def _issue_has_estimate(iss: dict, estimates_dir: Path) -> bool:
    srv = _server()
    return srv._resolve_issue_estimate(iss, estimates_dir)["estimated"]


def _preflight_estimate_one(issue_num: int, repo: str) -> bool:
    """Estimate one issue and apply its size label. Returns True on success."""
    issue_data = _ei_fetch_issue(issue_num, repo)
    estimate, _err = _ei_run_estimator(issue_num, issue_data)
    if not estimate or not estimate.get("size"):
        return False
    _ei_apply_label(issue_num, repo, estimate["size"])
    _ei_apply_estimated_status(issue_num, repo)
    return True


# ── Route handlers ────────────────────────────────────────────────────────────

@router.post("/api/sprints/{sprint_label}/preflight-fix")
async def preflight_fix(sprint_label: str, project: str):
    """Fix auto-fixable pre-flight issues for a sprint, streaming progress as SSE.

    For each work ticket in the sprint that is missing acceptance criteria or a
    size estimate: generate AC (append-only, idempotent) and/or run the estimator
    to apply a size label. Streams `log` events (the current action) and a final
    `done` event with counts. Conflicts (file-overlap / dep-order) are not
    auto-fixable and are left untouched.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    repo = srv.github_client.get_repo_for_operation(project)
    try:
        issues = srv._get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise srv._gh_error(e)

    project_root = srv._project_root_path(project)
    estimates_dir = srv._commander_dir(project_root) / "estimates"

    work: list[dict] = []
    for iss in issues:
        labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if labels & srv._PF_NON_WORK:
            continue
        needs_ac = not _fill_ac.has_acceptance_criteria(iss.get("body") or "")
        needs_size = not _issue_has_estimate(iss, estimates_dir)
        if needs_ac or needs_size:
            work.append({
                "num": iss["number"],
                "needs_ac": needs_ac,
                "needs_size": needs_size,
            })

    def _sse(event: str, payload) -> str:
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    async def _stream():
        total = len(work)
        if total == 0:
            yield _sse("log", "No auto-fixable pre-flight issues found.")
            yield _sse(
                "done",
                {
                    "filled": 0,
                    "estimated": 0,
                    "skipped": 0,
                    "errors": [],
                    "total": 0,
                },
            )
            return

        filled = estimated = skipped = 0
        errors: list[str] = []
        yield _sse("log", f"Fixing {total} pre-flight ticket(s)…")

        for idx, item in enumerate(work, start=1):
            num = item["num"]
            if item["needs_ac"]:
                yield _sse(
                    "log",
                    f"Generating acceptance criteria for #{num} ({idx}/{total})…",
                )
                try:
                    status, err = await asyncio.to_thread(
                        _fill_ac.fill_issue,
                        num,
                        repo,
                        False,
                    )
                    if status == "filled":
                        filled += 1
                    elif status == "skipped":
                        skipped += 1
                    else:
                        errors.append(f"#{num} AC: {err or 'failed'}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"#{num} AC: {e}")

            if item["needs_size"]:
                yield _sse("log", f"Estimating #{num} ({idx}/{total})…")
                try:
                    ok = await asyncio.to_thread(_preflight_estimate_one, num, repo)
                    if ok:
                        estimated += 1
                    else:
                        errors.append(f"#{num} estimate: failed")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"#{num} estimate: {e}")

        srv.github_client.invalidate("open_issues_body:")
        srv.github_client.invalidate("open_issues:")
        summary = {
            "filled": filled,
            "estimated": estimated,
            "skipped": skipped,
            "errors": errors,
            "total": total,
        }
        msg = f"Done — {filled} AC added, {estimated} estimated"
        if errors:
            msg += f", {len(errors)} error(s)"
        msg += "."
        yield _sse("log", msg)
        yield _sse("done", summary)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/sprints/{sprint_label}/preflight")
def get_sprint_preflight(sprint_label: str, project: str):
    """Preflight check returned before running a sprint.

    Returns DAG visualization data (layers, edges, ticket metadata) alongside ok flag,
    warnings (unestimated, stale_estimates, missing_ac), and cycle path if detected.
    """
    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    dag_payload: dict | None = None
    warnings: dict = {"unestimated": [], "stale_estimates": [], "missing_ac": []}
    cycle_path: list[str] | None = None
    sprint_issues: list[dict] = []

    try:
        sprint_issues = srv._get_sprint_issues(project, sprint_label)
        if sprint_issues:
            project_root = srv._project_root_path(project)
            estimates_dir = srv._commander_dir(project_root) / "estimates"
            stale_cutoff = (
                datetime.now(timezone.utc) - timedelta(days=_STALE_ESTIMATE_DAYS)
            )

            ticket_map: dict[str, dict] = {}
            for iss in sprint_issues:
                num = iss["number"]
                label_names = {lbl["name"] for lbl in iss.get("labels", [])}
                if "blocked" in label_names:
                    state = "blocked"
                elif "UAT" in label_names:
                    state = "UAT"
                elif "SIT" in label_names:
                    state = "SIT"
                elif "in-progress" in label_names:
                    state = "in-progress"
                else:
                    state = "backlog"

                size: str | None = None
                files_touched: list[str] = []
                est_stale = False
                resolved = srv._resolve_issue_estimate(iss, estimates_dir)
                size = resolved["size"]
                files_touched = resolved["files"]
                est_path = estimates_dir / f"issue-{num}.json"
                if est_path.exists():
                    try:
                        mtime = datetime.fromtimestamp(
                            est_path.stat().st_mtime,
                            tz=timezone.utc,
                        )
                        est_stale = mtime < stale_cutoff
                    except OSError:
                        pass

                body = iss.get("body") or ""
                has_ac = bool(_parse_ticket_spec(body)["acceptance_criteria"])

                tid = f"#{num}"
                ticket_map[tid] = {
                    "id": tid,
                    "number": num,
                    "title": iss.get("title", ""),
                    "state": state,
                    "size": size,
                    "files_touched": files_touched,
                }

                if not resolved["estimated"]:
                    warnings["unestimated"].append(tid)
                if est_stale:
                    warnings["stale_estimates"].append(tid)
                if not has_ac:
                    warnings["missing_ac"].append(tid)

            layers: list[list[str]]
            edges: list[list[str]]
            if srv._DAG_BUILDER_AVAILABLE:
                dag_tickets = [
                    {"id": tid, "files_touched": ticket_map[tid]["files_touched"]}
                    for tid in ticket_map
                ]
                dag_result = srv._build_dag(dag_tickets)
                if isinstance(dag_result, srv._CycleError):
                    layers = [list(ticket_map.keys())]
                    edges = []
                    if dag_result.cycles:
                        cycle_path = dag_result.cycles[0]
                else:
                    layers = dag_result.layers
                    edges = [[e[0], e[1]] for e in dag_result.edges]
            else:
                layers = [list(ticket_map.keys())]
                edges = []

            dag_payload = {
                "layers": layers,
                "edges": edges,
                "tickets": list(ticket_map.values()),
            }
    except subprocess.CalledProcessError:
        pass  # DAG is decorative — don't fail the preflight

    mis_sizing_flags: dict | None = None
    if srv._MIS_SIZING_AVAILABLE:
        try:
            _ms_commander = srv._commander_dir(srv._project_root_path(project))
            _ms_issues = sprint_issues if sprint_issues else []
            mis_sizing_flags = srv._mis_sizing.generate_and_save_flags(
                _ms_commander, sprint_label, _ms_issues
            )
        except Exception:
            pass  # Flags are decorative — don't fail the preflight

    # ── XL split suggestions (issue #1424) ───────────────────────────────────
    xl_suggestions: list[dict] = []
    xl_minutes_saved: int = 0
    strict_xl_gate: bool = False
    dor_mode: str = "warn"
    try:
        project_root = srv._project_root_path(project)
        _stored = _settings_repo.get_setting(_APP_CONFIG_KEY, project=project)
        _eff = _build_effective_response(_stored)
        xl_threshold: int = int(_eff.get("xl_minute_threshold", 90))
        strict_xl_gate = bool(_eff.get("strict_xl_gate", False))
        dor_mode = str(_eff.get("definition_of_ready_mode", "warn"))
        dismissed = _xl_svc.load_xl_dismissed(project_root, sprint_label)
        _xl_mins: list[int] = []
        _pf_non_work = getattr(
            srv,
            "_PF_NON_WORK",
            {"sprint-summary", "docs", "documentation"},
        )
        for iss in sprint_issues:
            num = iss["number"]
            label_names = {lbl["name"] for lbl in iss.get("labels", [])}
            if label_names & _pf_non_work:
                continue
            if num in dismissed:
                continue
            resolved = srv._resolve_issue_estimate(
                iss, srv._commander_dir(project_root) / "estimates"
            )
            size = resolved.get("size")
            est_minutes = _SIZE_TO_MINUTES.get(size, 0) if size else 0
            if _xl_svc.is_xl_suggestion(size=size, estimated_minutes=est_minutes,
                                        threshold=xl_threshold):
                xl_suggestions.append({
                    "issue_number": num,
                    "title": iss.get("title", ""),
                    "size": size,
                    "estimated_minutes": est_minutes,
                })
                _xl_mins.append(est_minutes)
        xl_minutes_saved = _xl_svc.compute_minutes_saved(_xl_mins)
    except Exception:
        pass  # XL suggestions are advisory — don't fail the preflight

    # ── Definition of Ready readiness check (issue #1486) ────────────────────
    readiness: dict | None = None
    if dor_mode != "off":
        try:
            ready_nums: list[int] = []
            not_ready_entries: list[dict] = []
            _pf_non_work = getattr(
                srv,
                "_PF_NON_WORK",
                {"sprint-summary", "docs", "documentation"},
            )
            for iss in sprint_issues:
                label_names = {lbl["name"] for lbl in iss.get("labels", [])}
                if label_names & _pf_non_work:
                    continue
                spec = _parse_ticket_spec(iss.get("body") or "")
                _dor_proj_root = srv._project_root_path(project)
                _dor_est_dir = srv._commander_dir(_dor_proj_root) / "estimates"
                resolved = srv._resolve_issue_estimate(iss, _dor_est_dir)
                is_ready_flag, missing_reasons = _check_dor(spec, resolved)
                if is_ready_flag:
                    ready_nums.append(iss["number"])
                else:
                    not_ready_entries.append({
                        "number": iss["number"],
                        "missing": missing_reasons,
                    })
            readiness = {"ready": ready_nums, "not_ready": not_ready_entries}
        except Exception:
            pass  # DoR is advisory — don't fail preflight

    ok = True
    if dor_mode == "block" and readiness and readiness.get("not_ready"):
        ok = False

    response: dict = {
        "ok": ok,
        "sprint_label": sprint_label,
        "project": project,
        "dag": dag_payload,
        "warnings": warnings,
        "cycle": cycle_path,
        "stale_threshold_days": _STALE_ESTIMATE_DAYS,
        "mis_sizing_flags": mis_sizing_flags,
        "models": srv._effective_agent_models(srv._project_root_path(project)),
        "xl_suggestions": xl_suggestions,
        "xl_minutes_saved": xl_minutes_saved,
        "strict_xl_gate": strict_xl_gate,
        "dor_mode": dor_mode,
    }
    if readiness is not None:
        response["readiness"] = readiness
    return response
