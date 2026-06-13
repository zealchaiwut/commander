"""Scheduled overnight sprint queue (issue #863).

Opt-in, project-level scheduler that fires approved sprints sequentially at a
configured local time, enabling unattended overnight batch execution without
changing any existing manual workflow.

Design split:
  * Pure logic (validation, ``should_fire``, ``collect_queue``, ``run_queue``)
    has no I/O and is unit-tested directly.
  * Storage helpers (``get_scheduled_time`` / ``set_scheduled_time`` /
    ``get_run_on_schedule`` / ``set_run_on_schedule`` / last-fired bookkeeping)
    persist through ``settings_repo`` so they ride the existing Neon-optional
    settings store and degrade to the local JSON fallback automatically.
  * ``fire_if_due`` ties the two together and takes every side-effecting
    collaborator (sprint provider, runner, lock check, clock, alert sink) as an
    injected callable so the orchestration is testable end-to-end without a live
    dashboard, subprocesses, or the wall clock.

The actual minute-by-minute trigger is external (launchd / cron POSTing to
``/api/scheduler/tick``) — see ``apps/dashboard/routers/scheduler.py``. This
matches Commander's launchd-is-the-authoritative-runner model and keeps a
background thread out of ``server.py`` (COMMANDER_GATE_MONOLITH, issue #761).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from services.sprint_manager import settings_repo

# ── Approved-status vocabulary ───────────────────────────────────────────────
# AC2: the Run-on-schedule toggle is meaningful only on sprints in the
# "Approved" status — a sprint that has been arranged and confirmed and is
# waiting to be dispatched. In the unified sprint lifecycle
# (docs/architecture/sprint-lifecycle.md) that pre-dispatch, ready-to-run state
# is `planned`; the board surfaces it as the "Run Sprint" planning card. We
# accept both the explicit "approved" label and the lifecycle alias so the
# scheduler stays correct regardless of which vocabulary the caller speaks.
APPROVED_STATUSES: frozenset[str] = frozenset({"approved", "planned"})

# Settings keys (project scope).
_TIME_KEY = "scheduler_time"          # {"value": "01:00"}
_TOGGLE_KEY = "scheduled_sprints"     # {"<label>": true, ...}
_LAST_FIRED_KEY = "scheduler_last_fired"  # {"date": "2026-06-13"}

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    """Raised when a scheduled-run-time string is malformed."""


def is_approved_status(status: Optional[str]) -> bool:
    """True when *status* is an approved/ready-to-dispatch sprint state (AC2)."""
    if not status:
        return False
    return status.strip().lower() in APPROVED_STATUSES


def normalize_time(raw: Optional[str]) -> str:
    """Validate and canonicalize a 24-hour ``HH:MM`` time string.

    An empty / whitespace-only / None value is the disabled sentinel and is
    returned as ``""`` (AC1 — leaving it empty disables all auto-scheduling).
    A non-empty value that is not a valid 24-hour ``HH:MM`` raises
    ``ScheduleError`` so the API can reject it with a 400.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    if not _HHMM_RE.match(s):
        raise ScheduleError(
            f"Invalid scheduled run time {raw!r}: expected 24-hour HH:MM (e.g. 01:00)"
        )
    return s


def should_fire(
    scheduled_time: Optional[str],
    now_hhmm: str,
    last_fired_date: Optional[str],
    today: str,
) -> bool:
    """Decide whether the queue should fire right now.

    Fires only when a non-empty scheduled time is configured (AC1/AC9), the
    current minute matches it, and the queue has not already fired today
    (idempotent — guards a per-minute poll against double-firing the same day).
    """
    time = normalize_time(scheduled_time) if scheduled_time else ""
    if not time:
        return False
    if now_hhmm != time:
        return False
    if last_fired_date == today:
        return False
    return True


def collect_queue(
    sprints: Iterable[dict],
    run_on_schedule_map: dict,
) -> list[str]:
    """Return opted-in approved sprint labels ordered by board position (AC4).

    *sprints* is an iterable of dicts carrying at least ``label``, ``status``,
    and ``position``. A sprint is queued only when it is in an approved status
    AND its Run-on-schedule toggle is enabled. The result is ordered by
    ``position`` ascending (the board's top-to-bottom order); ties fall back to
    label for a stable order.
    """
    eligible = [
        s for s in sprints
        if is_approved_status(s.get("status"))
        and bool(run_on_schedule_map.get(s.get("label")))
    ]
    eligible.sort(key=lambda s: (s.get("position", 0), str(s.get("label"))))
    return [s["label"] for s in eligible]


# ── Queue execution ──────────────────────────────────────────────────────────

@dataclass
class QueueResult:
    """Outcome of a single queue fire."""
    ran: list[str] = field(default_factory=list)         # completed successfully, in order
    failed: Optional[str] = None                         # label that failed (halts the queue)
    halted: bool = False                                 # True if a failure stopped the queue
    skipped: bool = False                                # True if the whole fire was skipped
    skipped_reason: Optional[str] = None
    alert: Optional[dict] = None                         # alert payload when halted

    def to_dict(self) -> dict:
        return {
            "ran": list(self.ran),
            "failed": self.failed,
            "halted": self.halted,
            "skipped": self.skipped,
            "skipped_reason": self.skipped_reason,
            "alert": self.alert,
        }


def run_queue(
    labels: list[str],
    runner: Callable[[str], bool],
    is_locked: Callable[[], Any],
    alert_fn: Optional[Callable[[dict], None]] = None,
) -> QueueResult:
    """Execute *labels* one at a time, in order, respecting the run lock.

    Contract (AC5/AC6/AC7):
      * AC6 — if a sprint is already running when the queue starts
        (``is_locked()`` truthy), the entire fire is skipped gracefully and the
        runner is never invoked, so there is no silent duplicate run.
      * AC5 — ``runner(label)`` is expected to block until that sprint
        completes; the next label is only dispatched after the previous one
        returns. Sequencing is therefore guaranteed by the synchronous loop.
      * AC7 — the first label whose ``runner`` returns falsy halts the queue
        immediately: subsequent labels do not run, ``halted`` is set, and an
        alert payload is produced (and pushed through *alert_fn* if given).
    """
    result = QueueResult()

    lock = is_locked()
    if lock:
        result.skipped = True
        busy = lock.get("sprint_label") if isinstance(lock, dict) else None
        result.skipped_reason = (
            f"A sprint is already running ({busy}); scheduled queue slot busy — skipped"
            if busy
            else "A sprint is already running; scheduled queue slot busy — skipped"
        )
        return result

    for label in labels:
        ok = runner(label)
        if ok:
            result.ran.append(label)
            continue
        # Failure: halt the queue, surface an alert, run nothing further (AC7).
        result.failed = label
        result.halted = True
        remaining = labels[labels.index(label) + 1:]
        result.alert = {
            "title": f"Scheduled sprint queue halted — {label} failed",
            "body": (
                f"{label} failed during the scheduled overnight run. "
                f"The queue stopped immediately and did not start the remaining "
                f"sprint(s): {', '.join(remaining) if remaining else 'none'}."
            ),
            "category": "scheduler",
        }
        if alert_fn is not None:
            alert_fn(result.alert)
        break

    return result


# ── Storage (settings_repo backed; Neon-optional safe) ───────────────────────

def get_scheduled_time(project: str) -> str:
    """Return the project's configured scheduled run time, or ``""`` if unset."""
    stored = settings_repo.get_setting_scoped("project", _TIME_KEY, project) or {}
    return normalize_time(stored.get("value")) if stored else ""


def set_scheduled_time(project: str, value: Optional[str]) -> str:
    """Validate and persist the project's scheduled run time. Returns the stored value.

    Raises ``ScheduleError`` for a malformed non-empty value.
    """
    canonical = normalize_time(value)
    settings_repo.set_setting("project", _TIME_KEY, {"value": canonical}, project)
    return canonical


def get_run_on_schedule_map(project: str) -> dict:
    """Return the ``{label: bool}`` Run-on-schedule map for the project."""
    stored = settings_repo.get_setting_scoped("project", _TOGGLE_KEY, project) or {}
    return {k: bool(v) for k, v in stored.items()}


def get_run_on_schedule(project: str, sprint_label: str) -> bool:
    """Return whether Run-on-schedule is enabled for a sprint (default off — AC3)."""
    return bool(get_run_on_schedule_map(project).get(sprint_label, False))


def set_run_on_schedule(project: str, sprint_label: str, enabled: bool) -> bool:
    """Persist the Run-on-schedule toggle for a sprint. Returns the new value."""
    current = get_run_on_schedule_map(project)
    if enabled:
        current[sprint_label] = True
    else:
        # Store off as absence so the default-off invariant (AC3) holds and the
        # map does not accumulate stale labels.
        current.pop(sprint_label, None)
    settings_repo.set_setting("project", _TOGGLE_KEY, current, project)
    return bool(enabled)


def get_last_fired_date(project: str) -> Optional[str]:
    stored = settings_repo.get_setting_scoped("project", _LAST_FIRED_KEY, project) or {}
    return stored.get("date")


def set_last_fired_date(project: str, date: str) -> None:
    settings_repo.set_setting("project", _LAST_FIRED_KEY, {"date": date}, project)


# ── Orchestration ────────────────────────────────────────────────────────────

def fire_if_due(
    project: str,
    *,
    now_hhmm: str,
    today: str,
    sprints_provider: Callable[[], Iterable[dict]],
    runner: Callable[[str], bool],
    is_locked: Callable[[], Any],
    alert_fn: Optional[Callable[[dict], None]] = None,
    scheduled_time: Optional[str] = None,
    last_fired_date: Optional[str] = None,
    record_fired: Optional[Callable[[str], None]] = None,
) -> dict:
    """Fire the scheduled queue for *project* iff due, and return a result dict.

    Collaborators are injected so the orchestration is fully testable:
      * *sprints_provider* — returns the project's sprints (label/status/position).
      * *runner* — runs one sprint to completion, returning success (AC5).
      * *is_locked* — the one-sprint-per-project lock check (AC6).
      * *alert_fn* — sink for the halt alert (AC7).
      * *record_fired* — persists today's fire so the same day cannot re-fire.

    ``scheduled_time`` / ``last_fired_date`` default to the persisted project
    values when not supplied, so the dashboard tick can call this with just the
    clock.
    """
    if scheduled_time is None:
        scheduled_time = get_scheduled_time(project)
    if last_fired_date is None:
        last_fired_date = get_last_fired_date(project)

    if not should_fire(scheduled_time, now_hhmm, last_fired_date, today):
        return {"fired": False, "reason": "not_due"}

    toggle_map = get_run_on_schedule_map(project)
    queue = collect_queue(list(sprints_provider()), toggle_map)

    result = run_queue(queue, runner=runner, is_locked=is_locked, alert_fn=alert_fn)

    # Mark the day as fired even when the queue was empty or skipped, so a
    # per-minute poll does not retry every minute for the rest of the matching
    # minute. A skipped (slot-busy) fire still counts — the next opportunity is
    # the next scheduled day.
    recorder = record_fired or (lambda d: set_last_fired_date(project, d))
    recorder(today)

    return {"fired": True, "queue": queue, "result": result.to_dict()}
