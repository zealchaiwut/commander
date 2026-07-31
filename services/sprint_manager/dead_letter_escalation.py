"""Dead-letter escalation for repeat-failing tickets (issue #2033).

Tracks the total number of times a ticket has been dead-lettered across all
sprint runs.  When the cross-run count reaches the configured threshold a
LOUD escalation alert is fired and the ticket is routed out of auto-rerun by
setting the 'blocked' label (AC-2).

The ledger is a JSON file at ``{sprints_dir}/dead-letter-ledger.json`` so
counts survive process restarts and rerun chains.  Thread-safe via a
per-instance Lock — concurrent pipeline mode may dead-letter multiple tickets
simultaneously.

Public API
----------
``DeadLetterLedger``
    Persistent counter keyed by ticket_id.

``get_escalation_threshold(cfg)``
    Return the effective threshold (env-var → yaml → default 2).

``check_dead_letter_escalation(...)``
    Increment ledger; fire escalation + flag blocked when threshold is reached.
    Returns True when escalation fired.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig

_LEDGER_FILENAME = "dead-letter-ledger.json"

# Default: 2 dead-letters across runs triggers escalation.
DEFAULT_ESCALATION_THRESHOLD = 2

# Env-var override — takes precedence over sprint.yaml.
_ENV_THRESHOLD_VAR = "COMMANDER_DEAD_LETTER_THRESHOLD"


# ── threshold helper ──────────────────────────────────────────────────────────

def get_escalation_threshold(cfg: Optional["SprintConfig"] = None) -> int:
    """Return the effective dead-letter escalation threshold.

    Priority (highest to lowest):
      1. ``COMMANDER_DEAD_LETTER_THRESHOLD`` env-var
      2. ``cfg.dead_letter_escalation_threshold`` (from sprint.yaml)
      3. ``DEFAULT_ESCALATION_THRESHOLD`` (2)
    """
    env_val = os.environ.get(_ENV_THRESHOLD_VAR, "").strip()
    if env_val:
        try:
            return max(1, int(env_val))
        except (ValueError, TypeError):
            pass
    if cfg is not None:
        v = getattr(cfg, "dead_letter_escalation_threshold", None)
        if v is not None:
            try:
                return max(1, int(v))
            except (ValueError, TypeError):
                pass
    return DEFAULT_ESCALATION_THRESHOLD


# ── ledger ────────────────────────────────────────────────────────────────────

class DeadLetterLedger:
    """Persistent, thread-safe dead-letter counter indexed by ticket_id.

    The backing store is ``{sprints_dir}/dead-letter-ledger.json`` — a simple
    ``{str(ticket_id): count}`` dict.  Writes are atomic (tmp-file + rename).
    """

    def __init__(self, sprints_dir: Path) -> None:
        self._path = Path(sprints_dir) / _LEDGER_FILENAME
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(self._path))

    def increment(self, ticket_id: int) -> int:
        """Increment and persist the dead-letter count.  Returns the new count."""
        with self._lock:
            data = self._load()
            key = str(ticket_id)
            data[key] = int(data.get(key, 0)) + 1
            self._save(data)
            return data[key]

    def get_count(self, ticket_id: int) -> int:
        """Return current dead-letter count for *ticket_id* (0 if never seen)."""
        with self._lock:
            return int(self._load().get(str(ticket_id), 0))


# ── escalation check ──────────────────────────────────────────────────────────

def _resolve(module_attr: str, fallback: Callable) -> Callable:
    """Look up *module_attr* on the loaded sprint_manager module (allows patching
    via ``@patch('services.sprint_manager.sprint_manager.<attr>')`` in tests).
    Falls back to *fallback* when the module is not loaded."""
    for name in ("services.sprint_manager.sprint_manager", "sprint_manager"):
        _sm = sys.modules.get(name)
        if _sm is not None and hasattr(_sm, module_attr):
            return getattr(_sm, module_attr)
    return fallback


def check_dead_letter_escalation(
    ticket_id: int,
    title: str,
    last_error: Optional[str],
    sprints_dir: Path,
    threshold: int,
    alert_modes: list,
    cfg: Optional["SprintConfig"],
    repo: Optional[str],
    sprint_label: Optional[str],
) -> bool:
    """Increment dead-letter count; fire escalation if threshold is reached.

    When the cross-run dead-letter count for *ticket_id* reaches *threshold*:

    1. Dispatches a LOUD escalation alert through all configured channels
       (AC-1: visually/textually distinct from a first-time dead-letter alert).
    2. Applies the 'blocked' label via ``_add_blocked_label``, routing the
       ticket out of auto-rerun until the operator manually resets it to
       'backlog' (AC-2: reversible manual queue).

    Returns ``True`` when escalation was fired; ``False`` for a sub-threshold
    (first-time or subsequent-but-below-threshold) dead-letter.
    """
    ledger = DeadLetterLedger(sprints_dir)
    count = ledger.increment(ticket_id)

    if count < threshold:
        return False  # normal (sub-threshold) dead-letter — no escalation

    # ── AC-1: LOUD escalation alert ───────────────────────────────────────────
    from services.sprint_manager.alerts import dispatch_alerts as _dispatch_alerts_fallback

    dispatch_alerts_fn = _resolve("dispatch_alerts", _dispatch_alerts_fallback)

    esc_title = (
        f"[ESCALATION] Issue #{ticket_id} dead-lettered {count}× "
        f"(threshold={threshold}) — manual attention required"
    )
    esc_body = (
        f"Issue #{ticket_id} ({title}) has been dead-lettered **{count} time(s)** "
        f"across sprint runs, reaching the escalation threshold of {threshold}.\n\n"
        f"This ticket has been routed out of auto-rerun and flagged as **blocked**. "
        f"The operator must review the root cause, apply a fix, and manually reset "
        f"the label from 'blocked' → 'backlog' before the next sprint will include it.\n\n"
        f"Last recorded error:\n{last_error or '(no error recorded)'}"
    )
    try:
        dispatch_alerts_fn(
            alert_modes,
            title=esc_title,
            body=esc_body,
            issue_num=ticket_id,
            category="escalated-dead-letter",
            cfg=cfg,
            repo=repo,
            sprint_label=sprint_label,
        )
    except Exception:
        pass

    # ── AC-2: route out of auto-rerun — set 'blocked' label ──────────────────
    # Uses the same _add_blocked_label helper as hang-detection so the label
    # transition flows through the state machine's RUN_MUTABLE_LABELS guard.
    # 'blocked' IS in RUN_MUTABLE_LABELS so this is allowed during a sprint run.
    # Reversible: operator manually changes the label back to 'backlog'.
    try:
        from services.sprint_manager.label_transitions import (
            _add_blocked_label as _add_blocked_label_fallback,
        )
        _add_blocked_label_fn = _resolve("_add_blocked_label", _add_blocked_label_fallback)
        _add_blocked_label_fn(
            ticket_id,
            f"Dead-letter escalation: {count}× across runs "
            f"(threshold={threshold}). Operator must fix root cause and "
            f"reset label to 'backlog' before next sprint.",
            repo_name=repo,
            sprint_label=sprint_label,
        )
    except Exception:
        pass

    # Emit a structured log entry so operators can grep for this event.
    try:
        from services.logging import log as _sl
        _sl.warn(
            "dead_letter_escalated",
            f"#{ticket_id} dead-lettered {count}× — escalated (threshold={threshold})",
            issue_num=ticket_id,
            count=count,
            threshold=threshold,
        )
    except Exception:
        pass

    return True
