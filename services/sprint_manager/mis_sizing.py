"""Mis-sizing detection service (issue #578).

Flags tickets with a label history of large estimate-vs-actual gaps so they
can be surfaced in a pre-sprint review queue before the sprint is started.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:  # importable as top-level when services/sprint_manager is on sys.path
    from sizing import SIZE_TO_MINUTES, letter_from_minutes
except ImportError:  # pragma: no cover - fallback for package-qualified import
    from services.sprint_manager.sizing import SIZE_TO_MINUTES, letter_from_minutes

SIZE_TIERS: dict[str, int] = {"S": 1, "M": 2, "L": 3, "XL": 4}
# Canonical minutes inherit the single source of truth in sizing.py (issue #766).
CANONICAL_MINUTES: dict[str, int] = SIZE_TO_MINUTES
_VALID_ACTIONS = frozenset({"approved", "reestimated", "dismissed"})
_META_LABELS = frozenset({
    "enhancement", "bug", "in-progress", "SIT", "UAT", "backlog",
    "blocked", "needs-rework", "estimated", "need-rework",
})


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


def _is_meta_label(name: str) -> bool:
    return (
        name in _META_LABELS
        or name.startswith("size-")
        or bool(re.match(r"^sprint-\d", name))
    )


def load_config(commander_dir: Path) -> dict:
    """Load mis-sizing config from .commander/mis-sizing-config.json."""
    path = commander_dir / "mis-sizing-config.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "tier_threshold": int(data.get("tier_threshold", 2)),
                "min_events": int(data.get("min_events", 2)),
            }
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            pass
    return {"tier_threshold": 2, "min_events": 2}


def save_config(commander_dir: Path, config: dict) -> None:
    commander_dir.mkdir(parents=True, exist_ok=True)
    (commander_dir / "mis-sizing-config.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )


def load_history(commander_dir: Path) -> dict:
    """Load mis-sizing history from .commander/mis-sizing-history.json."""
    path = commander_dir / "mis-sizing-history.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "events_by_label": {}, "last_rebuilt": None}


def save_history(commander_dir: Path, history: dict) -> None:
    commander_dir.mkdir(parents=True, exist_ok=True)
    (commander_dir / "mis-sizing-history.json").write_text(
        json.dumps(history, indent=2, default=str), encoding="utf-8"
    )


def build_history_from_completed(
    completed_tickets: list[dict],
) -> dict:
    """Build a history dict from a list of completed ticket records.

    Each record must have:
      number, sprint, estimated_size,
      coder_started_at, tester_finished_at (or status_changed_at),
      labels: list[str]  (raw; meta-labels are filtered here)

    Returns a history dict ready for save_history().
    """
    events_by_label: dict[str, list] = {}
    now = datetime.now(timezone.utc).isoformat()

    for rec in completed_tickets:
        num = rec.get("number")
        estimated_size = rec.get("estimated_size")
        if not num or not estimated_size or estimated_size not in SIZE_TIERS:
            continue

        start_ts = _parse_ts(rec.get("coder_started_at"))
        end_ts = (
            _parse_ts(rec.get("tester_finished_at"))
            or _parse_ts(rec.get("status_changed_at"))
        )
        if start_ts is None or end_ts is None:
            continue

        elapsed_minutes = max(0.0, end_ts - start_ts) / 60.0
        actual_size = letter_from_minutes(elapsed_minutes)
        tier_diff = abs(SIZE_TIERS[actual_size] - SIZE_TIERS[estimated_size])

        labels = [l for l in (rec.get("labels") or []) if not _is_meta_label(l)]
        if not labels:
            continue

        event = {
            "issue": num,
            "sprint": rec.get("sprint", ""),
            "estimated_size": estimated_size,
            "actual_size": actual_size,
            "actual_elapsed_minutes": round(elapsed_minutes, 1),
            "tier_diff": tier_diff,
            "recorded_at": now,
        }
        for label in labels:
            events_by_label.setdefault(label, []).append(event)

    return {
        "version": 1,
        "events_by_label": events_by_label,
        "last_rebuilt": datetime.now(timezone.utc).isoformat(),
    }


def compute_flags(
    sprint_issues: list[dict],
    history: dict,
    config: dict,
    estimates_dir: Path,
) -> list[dict]:
    """Compute mis-sizing flags for a list of sprint issues.

    sprint_issues: full GitHub issue dicts with 'number', 'title', 'labels'.
    Returns only the issues that should be flagged.
    """
    tier_threshold = config.get("tier_threshold", 2)
    min_events = config.get("min_events", 2)
    events_by_label = history.get("events_by_label", {})
    flags: list[dict] = []

    for iss in sprint_issues:
        issue_num = iss.get("number")
        if issue_num is None:
            continue

        current_size: Optional[str] = None
        est_path = estimates_dir / f"issue-{issue_num}.json"
        if est_path.exists():
            try:
                est_data = json.loads(est_path.read_text(encoding="utf-8"))
                current_size = est_data.get("size") or None
            except (json.JSONDecodeError, OSError):
                pass

        raw_labels = iss.get("labels", [])
        label_names = [
            (lbl["name"] if isinstance(lbl, dict) else lbl)
            for lbl in raw_labels
        ]
        filtered_labels = [l for l in label_names if not _is_meta_label(l)]

        driving_labels: list[str] = []
        all_mis_events: list[dict] = []

        for label in filtered_labels:
            label_events = events_by_label.get(label, [])
            mis_events = [e for e in label_events if e.get("tier_diff", 0) >= tier_threshold]
            if len(mis_events) >= min_events:
                driving_labels.append(label)
                all_mis_events.extend(mis_events)

        if not driving_labels:
            continue

        # Deduplicate by (issue, sprint) to avoid double-counting across labels
        seen: set[tuple] = set()
        deduped: list[dict] = []
        for e in all_mis_events:
            key = (e.get("issue"), e.get("sprint"))
            if key not in seen:
                seen.add(key)
                deduped.append(e)

        actual_minutes_list = [
            e["actual_elapsed_minutes"]
            for e in deduped
            if e.get("actual_elapsed_minutes") is not None
        ]
        hist_avg_minutes: Optional[float] = None
        hist_avg_size: Optional[str] = None
        if actual_minutes_list:
            avg = sum(actual_minutes_list) / len(actual_minutes_list)
            hist_avg_minutes = round(avg, 1)
            hist_avg_size = letter_from_minutes(avg)

        flags.append({
            "issue_number": issue_num,
            "title": iss.get("title", ""),
            "current_estimate": current_size,
            "current_estimate_minutes": CANONICAL_MINUTES.get(current_size) if current_size else None,
            "historical_avg_actual_size": hist_avg_size,
            "historical_avg_actual_minutes": hist_avg_minutes,
            "mis_sizing_event_count": len(deduped),
            "driving_labels": driving_labels,
            "status": "pending",
            "action_taken_at": None,
            "action_note": None,
            "new_size": None,
        })

    return flags


def _flags_path(commander_dir: Path, sprint_label: str) -> Path:
    return commander_dir / f"mis-sizing-flags-{sprint_label}.json"


def load_flags(commander_dir: Path, sprint_label: str) -> dict:
    path = _flags_path(commander_dir, sprint_label)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "sprint_label": sprint_label,
        "flags": [],
        "audit_log": [],
        "generated_at": None,
        "config": {},
    }


def save_flags(commander_dir: Path, sprint_label: str, data: dict) -> None:
    commander_dir.mkdir(parents=True, exist_ok=True)
    _flags_path(commander_dir, sprint_label).write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def generate_and_save_flags(
    commander_dir: Path,
    sprint_label: str,
    sprint_issues: list[dict],
    history: Optional[dict] = None,
    config: Optional[dict] = None,
) -> dict:
    """Generate flags and persist to disk, preserving existing action states.

    Returns the persisted flags data dict.
    """
    if history is None:
        history = load_history(commander_dir)
    if config is None:
        config = load_config(commander_dir)

    estimates_dir = commander_dir / "estimates"
    new_flags = compute_flags(sprint_issues, history, config, estimates_dir)

    existing = load_flags(commander_dir, sprint_label)
    acted: dict[int, dict] = {
        f["issue_number"]: f
        for f in existing.get("flags", [])
        if f.get("status") != "pending"
    }

    merged: list[dict] = []
    for flag in new_flags:
        num = flag["issue_number"]
        if num in acted:
            old = acted[num]
            flag.update({
                "status": old["status"],
                "action_taken_at": old.get("action_taken_at"),
                "action_note": old.get("action_note"),
                "new_size": old.get("new_size"),
            })
        merged.append(flag)

    data = {
        "sprint_label": sprint_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "flags": merged,
        "audit_log": existing.get("audit_log", []),
    }
    save_flags(commander_dir, sprint_label, data)
    return data


def record_action(
    commander_dir: Path,
    sprint_label: str,
    issue_number: int,
    action: str,
    new_size: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Record a reviewer action on a flag. Returns updated flags data."""
    if action not in _VALID_ACTIONS:
        raise ValueError(
            f"Invalid action {action!r}. Must be one of: {sorted(_VALID_ACTIONS)}"
        )
    if action == "reestimated" and (not new_size or new_size not in SIZE_TIERS):
        raise ValueError(f"Invalid new_size {new_size!r} for reestimated action")

    data = load_flags(commander_dir, sprint_label)
    flag = next(
        (f for f in data.get("flags", []) if f["issue_number"] == issue_number),
        None,
    )
    if flag is None:
        raise KeyError(f"No flag for issue #{issue_number} in sprint {sprint_label!r}")

    old_size = flag.get("current_estimate")
    now = datetime.now(timezone.utc).isoformat()

    flag["status"] = action
    flag["action_taken_at"] = now
    flag["action_note"] = note
    if action == "reestimated":
        flag["new_size"] = new_size

    data.setdefault("audit_log", []).append({
        "issue_number": issue_number,
        "action": action,
        "old_size": old_size,
        "new_size": new_size if action == "reestimated" else None,
        "note": note,
        "acted_at": now,
    })

    save_flags(commander_dir, sprint_label, data)
    return data


def has_unresolved_flags(commander_dir: Path, sprint_label: str) -> bool:
    """Return True if any flag for this sprint is still pending."""
    data = load_flags(commander_dir, sprint_label)
    return any(f.get("status") == "pending" for f in data.get("flags", []))
