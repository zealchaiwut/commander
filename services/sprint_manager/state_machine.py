"""Atomic ticket state machine for Commander.

Single source of truth for all ticket status label writes.
transition() is the only code path that may add or remove STATUS_LABELS.
"""
from __future__ import annotations

import enum
import json
import subprocess
import time
from typing import Optional

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
    DONE = "DONE"                  # pseudo-state: closed/approved


_PSEUDO_STATES: frozenset[TicketState] = frozenset({TicketState.BACKLOG, TicketState.DONE})

STATE_LABELS: dict[TicketState, frozenset[str]] = {
    TicketState.BACKLOG:      frozenset(),
    TicketState.QUEUED:       frozenset({"backlog"}),
    TicketState.IN_PROGRESS:  frozenset({"in-progress"}),
    TicketState.SIT:          frozenset({"SIT"}),
    TicketState.UAT:          frozenset({"UAT"}),
    TicketState.NEEDS_REWORK: frozenset({"need-rework"}),
    TicketState.DONE:         frozenset(),
}

STATUS_LABELS: frozenset[str] = frozenset().union(*STATE_LABELS.values())


class TransitionError(Exception):
    """Raised when a ticket label transition fails after all retries."""


def _fetch_labels(issue: int, repo: str) -> frozenset[str]:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue), "--repo", repo, "--json", "labels"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise TransitionError(
            f"gh issue view #{issue} failed: {result.stderr.strip()}"
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
        print(msg)


def transition(
    issue: int,
    target_state: TicketState,
    *,
    actor: str,
    note: Optional[str] = None,
    repo: Optional[str] = None,
) -> None:
    """Atomically transition ticket to target_state.

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

        if not to_remove and not to_add:
            _log_transition(issue, current_status, desired, actor, note, noop=True)
            return

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

        after = _fetch_labels(issue, eff_repo)
        after_status = STATUS_LABELS & after

        if after_status == desired:
            _log_transition(issue, current_status, desired, actor, note)
            return

        last_error = (
            f"Label verification failed (attempt {attempt + 1}): "
            f"expected {sorted(desired)}, got {sorted(after_status)}"
        )
        if attempt < len(_BACKOFFS):
            time.sleep(_BACKOFFS[attempt])

    raise TransitionError(last_error or "Transition failed after all retries")
