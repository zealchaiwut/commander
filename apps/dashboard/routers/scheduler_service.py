"""Dashboard-coupled glue for the scheduled sprint queue (issue #863).

Keeps every side-effecting collaborator the pure scheduler needs — the sprint
provider, the run-to-completion runner, the one-sprint-per-project lock check,
the alert sink, and the local clock — in one place so
``services.sprint_manager.sprint_scheduler`` stays I/O-free and unit-tested.

The runner drives the existing dispatch path (``POST /api/sprints/run``) and
polls ``/api/sprints/running-all`` until the sprint leaves the running set, so
the scheduler reuses the same lifecycle, gates, and locking as a manual run —
it never spawns sprints a different way.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

from services.sprint_manager import sprint_scheduler as _sched

# Local dashboard base URL — the scheduler talks to its own HTTP surface so it
# reuses the exact manual-run path (gates, locking, logging) rather than a
# parallel dispatch. Derive the port from this process's own PORT env so the
# UAT clone (8001) hits itself rather than the PRD server (8000).
_BASE_URL = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"

# Poll cadence + ceiling for waiting on a sprint to finish.
_POLL_SECS = 15
_MAX_WAIT_SECS = 6 * 60 * 60  # 6h hard ceiling per sprint, so a hung run can't wedge the queue forever

# In-process guard against two ticks racing into the same fire.
_fire_lock = threading.Lock()
_running_project: set[str] = set()


def _server():
    import server  # noqa: PLC0415 — deferred import avoids the router/monolith cycle
    return server


# ── Collaborators ────────────────────────────────────────────────────────────

def _http_post(path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_BASE_URL}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"detail": body}
        return e.code, parsed


def _http_get(path: str) -> dict:
    req = urllib.request.Request(f"{_BASE_URL}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_locked(project: str):
    """The one-sprint-per-project lock (AC6): truthy when a sprint is running."""
    srv = _server()
    return srv._any_sprint_running(project=project)


def _approved_sprints(project: str) -> list[dict]:
    """Classify the project's opted-in sprints into scheduler queue records.

    Only opted-in labels are inspected (narrows GitHub calls). A label counts as
    ``approved`` when it has dispatchable backlog tickets and is not currently
    running — i.e. exactly the board's runnable "Run Sprint" planning state.
    Board position comes from the saved sprint display order.
    """
    srv = _server()
    from routers import sprints_service  # noqa: PLC0415
    from services.sprint_manager import sprint_manager as _sm

    toggle_map = _sched.get_run_on_schedule_map(project)
    opted_in = [label for label, on in toggle_map.items() if on]
    if not opted_in:
        return []

    try:
        order = sprints_service.get_sprint_order(project).get("order", [])
    except Exception:
        order = []
    position = {label: i for i, label in enumerate(order)}

    running_labels = {r.get("sprint_label") for r in srv._all_sprints_running()}

    records = []
    for label in opted_in:
        if label in running_labels:
            status = "running"
        else:
            try:
                backlog = _sm.list_backlog_issues(label, project)
            except Exception:
                backlog = []
            status = "approved" if backlog else "done"
        records.append({
            "label": label,
            "status": status,
            "position": position.get(label, 9999),
        })
    return records


def _make_runner(project: str):
    def runner(label: str) -> bool:
        # Dispatch through the same endpoint a manual run uses.
        status, body = _http_post(
            "/api/sprints/run", {"project": project, "sprint_label": label}
        )
        if status not in (200, 202):
            return False
        # Block until the sprint leaves the running set (AC5: sequential).
        waited = 0
        while waited < _MAX_WAIT_SECS:
            time.sleep(_POLL_SECS)
            waited += _POLL_SECS
            try:
                running = _http_get("/api/sprints/running-all").get("running", [])
            except Exception:
                continue
            labels = {r.get("sprint_label") for r in running}
            if label not in labels:
                break
        else:
            return False  # exceeded ceiling — treat as failure, halt queue
        return _sprint_succeeded(project, label)
    return runner


def _sprint_succeeded(project: str, label: str) -> bool:
    """True when the finished sprint ended cleanly (no needs-rework / failure)."""
    try:
        outcome = _http_get(
            f"/api/sprints/{label}/outcome?project={urllib.parse.quote(project)}"
        )
    except Exception:
        return False
    state = (outcome.get("state") or outcome.get("sprint_status") or "").lower()
    if state in ("needs_rework", "failed", "cancelled"):
        return False
    issues = outcome.get("issues") or []
    if any((i.get("status") == "failed" or i.get("agent_status") == "failed") for i in issues):
        return False
    return True


def _alert(project: str):
    def alert_fn(payload: dict) -> None:
        body = dict(payload)
        body.setdefault("repo", project)
        try:
            _http_post("/api/alerts", body)
        except Exception:
            pass
    return alert_fn


# ── Entry point ──────────────────────────────────────────────────────────────

def tick(project: str) -> dict:
    """Fire the scheduled queue for *project* if due. Non-blocking.

    Checks the clock against the configured run time, guards against double-fire,
    and runs the sequential queue in a background thread so the per-minute poll
    request returns immediately even though the overnight batch runs for hours.
    """
    now = datetime.now()
    now_hhmm = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")

    scheduled = _sched.get_scheduled_time(project)
    last_fired = _sched.get_last_fired_date(project)
    if not _sched.should_fire(scheduled, now_hhmm, last_fired, today):
        return {"fired": False, "reason": "not_due"}

    with _fire_lock:
        if project in _running_project:
            return {"fired": False, "reason": "already_firing"}
        # Re-check under lock in case another thread recorded the fire.
        if _sched.get_last_fired_date(project) == today:
            return {"fired": False, "reason": "already_fired_today"}
        # Mark the day fired now so the next minute's tick won't re-fire while
        # this queue is still running.
        _sched.set_last_fired_date(project, today)
        _running_project.add(project)

    def _worker():
        try:
            queue = _sched.collect_queue(
                _approved_sprints(project), _sched.get_run_on_schedule_map(project)
            )
            _sched.run_queue(
                queue,
                runner=_make_runner(project),
                is_locked=lambda: _is_locked(project),
                alert_fn=_alert(project),
            )
        finally:
            with _fire_lock:
                _running_project.discard(project)

    threading.Thread(target=_worker, daemon=True, name=f"sched-queue-{project}").start()
    return {"fired": True, "project": project, "scheduled_run_time": scheduled}
