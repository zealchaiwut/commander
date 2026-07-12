"""Atomic ticket state machine for Commander.

Single source of truth for all ticket status label writes.
transition() is the only code path that may add or remove STATUS_LABELS.
"""
from __future__ import annotations

import enum
import json
import os
import subprocess
import sys
import time
from typing import Iterable, Optional

try:
    from services.logging import log as _log
    _LOG_AVAILABLE = True
except ImportError:
    _LOG_AVAILABLE = False

_BACKOFFS = (1, 3, 7)


class TicketState(enum.Enum):
    BACKLOG = "BACKLOG"            # pseudo-state: pre-sprint, no status labels
    QUEUED = "QUEUED"              # in sprint queue (label: backlog)
    IN_PROGRESS = "IN_PROGRESS"    # being worked on (label: in-progress)
    SIT = "SIT"                    # system integration testing
    UAT = "UAT"                    # user acceptance testing
    NEEDS_REWORK = "NEEDS_REWORK"  # sent back for rework
    BLOCKED = "BLOCKED"            # blocked — cannot proceed until resolved (label: blocked)
    DONE = "DONE"                  # pseudo-state: closed/approved


_PSEUDO_STATES: frozenset[TicketState] = frozenset({TicketState.BACKLOG, TicketState.DONE})

STATE_LABELS: dict[TicketState, frozenset[str]] = {
    TicketState.BACKLOG:      frozenset(),
    TicketState.QUEUED:       frozenset({"backlog"}),
    TicketState.IN_PROGRESS:  frozenset({"in-progress"}),
    TicketState.SIT:          frozenset({"SIT"}),
    TicketState.UAT:          frozenset({"UAT"}),
    TicketState.NEEDS_REWORK: frozenset({"needs-rework"}),
    TicketState.BLOCKED:      frozenset({"blocked"}),
    TicketState.DONE:         frozenset(),
}

STATUS_LABELS: frozenset[str] = frozenset().union(*STATE_LABELS.values())

# During an active sprint run only status labels may change; sprint-N and every
# other label must be frozen (issue #754). RUN_MUTABLE_LABELS is the allow-list
# enforced by the run lock — it is exactly STATUS_LABELS.
RUN_MUTABLE_LABELS: frozenset[str] = STATUS_LABELS

_RUN_LOCK_ENV = "COMMANDER_SPRINT_RUNNING"


class TransitionError(Exception):
    """Raised when a ticket label transition fails after all retries."""


def run_lock_active() -> bool:
    """True when a sprint run holds the label lock.

    The orchestrator sets COMMANDER_SPRINT_RUNNING to the *sprint label*
    (e.g. "sprint-94"), not the literal "1" — an exact "1" comparison here
    made this guard inert in manager subprocesses (issue #1689). Any
    non-empty value counts as locked, matching scripts/update_ticket.py's
    `if sprint_label := os.environ.get("COMMANDER_SPRINT_RUNNING")` check.
    """
    return bool(os.environ.get(_RUN_LOCK_ENV, "").strip())


def assert_run_mutable(add: Iterable[str], remove: Iterable[str]) -> None:
    """Guard label mutations against the sprint run lock (issue #754).

    When a sprint run is active (COMMANDER_SPRINT_RUNNING set to any non-empty
    value — issue #1689), every label being added or removed must be in
    RUN_MUTABLE_LABELS (i.e. a status label). Any non-status label in the
    add/remove sets raises TransitionError. When the lock is not held this is
    a no-op, so existing behavior is unchanged.
    """
    if not run_lock_active():
        return
    touched = frozenset(add) | frozenset(remove)
    illegal = touched - RUN_MUTABLE_LABELS
    if illegal:
        lock_value = os.environ.get(_RUN_LOCK_ENV, "")
        raise TransitionError(
            f"Sprint run active ({_RUN_LOCK_ENV}={lock_value!r}): refusing to "
            f"mutate non-status label(s) {sorted(illegal)}. Only status labels "
            f"{sorted(RUN_MUTABLE_LABELS)} may change during a run."
        )


def _fetch_labels(issue: int, repo: str) -> frozenset[str]:
    # REST (gh api) — `gh issue view` uses GraphQL and burns the 5000/hr budget
    # during sprint label transitions (issue #755 follow-up).
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{issue}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise TransitionError(
            f"gh api repos/{repo}/issues/{issue} failed: {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
        return frozenset(lbl["name"] for lbl in data.get("labels", []))
    except (json.JSONDecodeError, KeyError) as exc:
        raise TransitionError(f"Failed to parse labels response: {exc}") from exc


def _resolve_repo(repo: Optional[str]) -> str:
    if repo:
        return repo
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(repo_root / "apps" / "dashboard"))
    try:
        import github_client
        return github_client.repo()
    except Exception as exc:
        raise TransitionError(f"Cannot resolve repo: {exc}") from exc


def _log_transition(
    issue: int,
    from_labels: frozenset[str],
    to_labels: frozenset[str],
    actor: str,
    note: Optional[str],
    *,
    noop: bool = False,
) -> None:
    prefix = "[noop] " if noop else ""
    msg = (
        f"{prefix}#{issue}: {sorted(from_labels)} → {sorted(to_labels)} "
        f"actor={actor!r}"
    )
    if note:
        msg += f" note={note!r}"
    if _LOG_AVAILABLE:
        _log.info(
            "ticket_transition",
            msg,
            issue_num=issue,
            from_labels=sorted(from_labels),
            to_labels=sorted(to_labels),
            actor=actor,
            note=note,
            noop=noop,
        )
    else:
        sys.stdout.write(str(msg) + "\n")


def transition(
    issue: int,
    target_state: TicketState,
    *,
    actor: str,
    note: Optional[str] = None,
    repo: Optional[str] = None,
) -> bool:
    """Atomically transition ticket to target_state.

    Returns True if labels were changed, False if already in target state (no-op).

    Raises ValueError if target_state is a pseudo-state (BACKLOG or DONE).
    Raises TransitionError if the transition fails after all retries.
    Caller is responsible for deciding when to call this function.
    """
    if target_state in _PSEUDO_STATES:
        raise ValueError(
            f"Cannot transition to pseudo-state {target_state!r}; "
            f"choose one of {[s for s in TicketState if s not in _PSEUDO_STATES]}"
        )

    eff_repo = _resolve_repo(repo)
    desired = STATE_LABELS[target_state]
    last_error = ""

    for attempt in range(len(_BACKOFFS) + 1):
        current = _fetch_labels(issue, eff_repo)
        current_status = STATUS_LABELS & current

        to_remove = current_status - desired
        to_add = desired - current_status

        # Run lock (issue #754): refuse any add/remove that touches a non-status
        # label while a sprint run is active. transition() only ever moves status
        # labels, so this is defensive — but it makes a violation loud.
        assert_run_mutable(to_add, to_remove)

        if not to_remove and not to_add:
            _log_transition(issue, current_status, desired, actor, note, noop=True)
            return False

        cmd = ["gh", "issue", "edit", str(issue), "--repo", eff_repo]
        for lbl in sorted(to_add):
            cmd += ["--add-label", lbl]
        for lbl in sorted(to_remove):
            cmd += ["--remove-label", lbl]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            last_error = (
                f"gh issue edit failed (attempt {attempt + 1}): "
                f"{result.stderr.strip()}"
            )
            if attempt < len(_BACKOFFS):
                time.sleep(_BACKOFFS[attempt])
            continue

        # Edit succeeded. The edit response is authoritative, so we skip the
        # old post-edit verify re-fetch (issue #755) — the sync loop catches any
        # drift lazily. Write the new state through to the local ticket_status
        # table; a DB failure must NOT fail the transition.
        _write_ticket_status(issue, target_state.name, actor, note)
        _log_transition(issue, current_status, desired, actor, note)
        return True

    raise TransitionError(last_error or "Transition failed after all retries")


def _brief_today() -> str:
    """Return today's UTC date string. Seam — patched in tests (issue #1854)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def _write_ticket_status(
    issue: int,
    status: str,
    actor: str,
    note: Optional[str],
) -> None:
    """Write-through the transitioned state to the local ticket_status table.

    Best-effort: any failure (locked DB, schema error, import failure) is logged
    as a structured "db_write_failed" error and swallowed — it must never raise
    or change the transition's return value (issue #755).
    """
    try:
        import sys
        from pathlib import Path
        dash_dir = Path(__file__).parent.parent.parent / "apps" / "dashboard"
        if str(dash_dir) not in sys.path:
            sys.path.insert(0, str(dash_dir))
        import db
        db.record_ticket_status(issue=issue, status=status, actor=actor, note=note)
    except (Exception, SystemExit) as exc:
        # db.py calls sys.exit(1) when DB_PATH is unset (SystemExit derives from
        # BaseException, not Exception) — treat that as a swallowed write failure.
        msg = f"#{issue}: ticket_status write failed: {exc}"
        if _LOG_AVAILABLE:
            _log.error(
                "db_write_failed",
                msg,
                issue_num=issue,
                status=status,
                actor=actor,
                error=str(exc),
            )
        else:
            sys.stdout.write(str(msg) + "\n")

    # Brief cache invalidation (fire-and-forget, issue #1854).
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _dash_dir = _Path(__file__).parent.parent.parent / "apps" / "dashboard"
        if str(_dash_dir) not in _sys.path:
            _sys.path.insert(0, str(_dash_dir))
        import db  # noqa: PLC0415
        db.delete_brief_artifact("home", "", _brief_today())
    except Exception:
        pass
