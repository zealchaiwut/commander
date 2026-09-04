"""Overnight babysitter: dispatch → reset → re-dispatch until done (#2354).

Owns the poll/rerun/dispatch loop that Claude Code would otherwise drive.
Does not mint child sprint labels, does not reorder beyond the resolve order,
and does not write sprint lifecycle state beyond what dispatch/rerun already write.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from services.sprint_manager.dispatch_runner import (
    DispatchRun,
    ProjectDispatchConfig,
    load_run,
    runtime_dir,
    start_run,
)
from services.sprint_manager.ticket_retry import (
    open_issue_numbers_for_label,
    reset_ticket,
)

DEFAULT_MAX_RETRIES = 2  # retries *after* the first failed dispatch


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OvernightRun:
    overnight_id: str
    sprint_label: str
    tickets: list[int]
    repo: Optional[str] = None
    status: str = "queued"  # queued | dispatching | retrying | done | exhausted | stopped
    phase: str = "queued"
    attempt: int = 0  # completed failed attempts that triggered a retry (0 on first try)
    max_retries: int = DEFAULT_MAX_RETRIES
    dispatch_run_ids: list[str] = field(default_factory=list)
    current_dispatch_id: Optional[str] = None
    last_failed_issue: Optional[int] = None
    started_at: str = ""
    finished_at: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "overnight_id": self.overnight_id,
            "sprint_label": self.sprint_label,
            "tickets": list(self.tickets),
            "repo": self.repo,
            "status": self.status,
            "phase": self.phase,
            "attempt": self.attempt,
            "max_retries": self.max_retries,
            "dispatch_run_ids": list(self.dispatch_run_ids),
            "current_dispatch_id": self.current_dispatch_id,
            "last_failed_issue": self.last_failed_issue,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "detail": self.detail,
        }


def overnight_path(overnight_id: str, repo_root: Path) -> Path:
    return runtime_dir(repo_root) / f"overnight-{overnight_id}.json"


def stop_flag_path(overnight_id: str, repo_root: Path) -> Path:
    return runtime_dir(repo_root) / f"overnight-{overnight_id}.stop"


def save_overnight(run: OvernightRun, repo_root: Path) -> Path:
    path = overnight_path(run.overnight_id, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    return path


def load_overnight(overnight_id: str, repo_root: Path) -> Optional[dict]:
    path = overnight_path(overnight_id, repo_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def request_stop(overnight_id: str, repo_root: Path) -> bool:
    if load_overnight(overnight_id, repo_root) is None:
        return False
    path = stop_flag_path(overnight_id, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_now(), encoding="utf-8")
    return True


def stop_requested(overnight_id: str, repo_root: Path) -> bool:
    return stop_flag_path(overnight_id, repo_root).exists()


def execute_overnight(
    run: OvernightRun,
    *,
    repo_root: Path,
    cwd: Path,
    config: Optional[ProjectDispatchConfig] = None,
    github_client=None,
    spawn: Optional[Callable[..., tuple[bool, str]]] = None,
    verify: Optional[Callable[..., tuple[bool, str]]] = None,
    start_dispatch: Optional[Callable[..., DispatchRun]] = None,
    reset_fn: Optional[Callable[..., object]] = None,
) -> OvernightRun:
    """Run the babysitter loop synchronously (used by the background thread).

    ``start_dispatch`` defaults to ``start_run(..., background=False)``.
    ``verify`` defaults to ``None`` so unit tests with a recording spawn are
    not blocked by board verification; production callers pass the real verify
    via ``start_run``'s default when ``start_dispatch`` is unset — we forward
    ``verify`` explicitly only when provided.
    """
    run.status = "dispatching"
    run.phase = "dispatching"
    run.started_at = run.started_at or _now()
    save_overnight(run, repo_root)

    tickets = list(run.tickets)
    start_dispatch = start_dispatch
    if start_dispatch is None:
        def start_dispatch(**kw):  # noqa: F811
            if verify is not None:
                kw = {**kw, "verify": verify}
            return start_run(background=False, **kw)
    reset_fn = reset_fn or reset_ticket

    while True:
        if stop_requested(run.overnight_id, repo_root):
            run.status = "stopped"
            run.phase = "stopped"
            run.finished_at = _now()
            run.detail = "stopped at dispatch/retry boundary"
            save_overnight(run, repo_root)
            return run

        if not tickets:
            run.status = "done"
            run.phase = "done"
            run.finished_at = _now()
            run.detail = "no tickets remaining"
            save_overnight(run, repo_root)
            return run

        run.phase = "dispatching"
        run.status = "dispatching"
        save_overnight(run, repo_root)

        dispatch_kwargs = dict(
            sprint_label=run.sprint_label,
            tickets=list(tickets),
            repo=run.repo,
            repo_root=repo_root,
            cwd=cwd,
            config=config,
            spawn=spawn,
        )
        # Only pass verify when the caller supplied one; otherwise let
        # start_run use its production default when start_dispatch is the real one.
        if verify is not None and start_dispatch is not None:
            pass  # custom start_dispatch owns verify
        d_run = start_dispatch(**dispatch_kwargs)

        run.current_dispatch_id = d_run.run_id
        run.dispatch_run_ids.append(d_run.run_id)
        save_overnight(run, repo_root)

        # Prefer on-disk status if the handle was backgrounded somehow.
        data = load_run(d_run.run_id, repo_root) or d_run.to_dict()
        status = data.get("status")

        if status == "done":
            run.status = "done"
            run.phase = "done"
            run.finished_at = _now()
            run.detail = "all tickets completed"
            save_overnight(run, repo_root)
            return run

        if status == "stopped":
            run.status = "stopped"
            run.phase = "stopped"
            run.finished_at = _now()
            run.detail = "nested dispatch stopped"
            save_overnight(run, repo_root)
            return run

        # failed
        failed_issue = data.get("failed_issue")
        run.last_failed_issue = failed_issue
        remaining = list(data.get("remaining") or [])
        if not remaining and failed_issue is not None:
            remaining = [failed_issue]

        if run.attempt >= run.max_retries:
            run.status = "exhausted"
            run.phase = "exhausted"
            run.finished_at = _now()
            run.detail = (
                f"failed on #{failed_issue} after {run.attempt} retries "
                f"(max_retries={run.max_retries})"
            )
            save_overnight(run, repo_root)
            return run

        if stop_requested(run.overnight_id, repo_root):
            run.status = "stopped"
            run.phase = "stopped"
            run.finished_at = _now()
            run.detail = "stopped before retry"
            save_overnight(run, repo_root)
            return run

        run.phase = "retrying"
        run.status = "retrying"
        run.attempt += 1
        save_overnight(run, repo_root)

        if failed_issue is not None and github_client is not None:
            try:
                reset_fn(
                    int(failed_issue),
                    github_client=github_client,
                    repo=run.repo,
                    repo_root=repo_root,
                    dry_run=False,
                )
            except Exception as exc:
                run.detail = f"reset failed for #{failed_issue}: {exc}"
                # Still try to re-dispatch remaining; reset is best-effort.

        tickets = remaining
        save_overnight(run, repo_root)


def start_overnight(
    sprint_label: str,
    tickets: list[int],
    *,
    repo: Optional[str],
    repo_root: Path,
    cwd: Path,
    max_retries: int = DEFAULT_MAX_RETRIES,
    config: Optional[ProjectDispatchConfig] = None,
    github_client=None,
    spawn: Optional[Callable[..., tuple[bool, str]]] = None,
    verify: Optional[Callable[..., tuple[bool, str]]] = None,
    start_dispatch: Optional[Callable[..., DispatchRun]] = None,
    reset_fn: Optional[Callable[..., object]] = None,
    background: bool = True,
) -> OvernightRun:
    """Create an overnight run and start it (background by default)."""
    run = OvernightRun(
        overnight_id=uuid.uuid4().hex[:12],
        sprint_label=sprint_label,
        tickets=list(tickets),
        repo=repo,
        max_retries=max_retries,
        started_at=_now(),
    )
    save_overnight(run, repo_root)

    kwargs = dict(
        run=run,
        repo_root=repo_root,
        cwd=cwd,
        config=config,
        github_client=github_client,
        spawn=spawn,
        verify=verify,
        start_dispatch=start_dispatch,
        reset_fn=reset_fn,
    )
    if not background:
        return execute_overnight(**kwargs)

    thread = threading.Thread(
        target=execute_overnight,
        kwargs=kwargs,
        name=f"overnight-{run.overnight_id}",
        daemon=True,
    )
    thread.start()
    return run


def resolve_overnight_tickets(github_client, sprint_label: str, repo: Optional[str]) -> list[int]:
    """Ascending open issue numbers for the sprint label (#2353 helper)."""
    return open_issue_numbers_for_label(github_client, sprint_label, repo)
