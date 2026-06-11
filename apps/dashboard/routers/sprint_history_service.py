"""Service logic for the sprint-history router (issue #805).

Builds enriched sprint-history rows for ``GET /api/sprints/history`` from
local sources ONLY — the durable DB tables and on-disk sprint state/summary
files. **No GitHub API call is made at read time** (AC5); that is the whole
point of the endpoint, so the ledger feed renders instantly and offline.

Sources, in priority order per sprint label:

1. ``sprint_history`` table — terminal snapshots (e.g. ``state='deleted'``)
   captured at the moment the event happened. Authoritative when present.
2. ``sprints`` lifecycle table — running/completed/cancelled/failed rows,
   enriched with per-ticket data from the matching ``<label>-state.json``.
3. ``<label>-state.json`` / ``<label>.json`` plan files — fallback for sprints
   that never made it into the lifecycle DB (legacy / file-only sprints).

The delete path calls :func:`record_deleted_sprint` BEFORE stripping labels so
a deleted sprint is queryable here immediately afterwards (AC7/AC8).
"""
from __future__ import annotations

import json
import sys as _sys
from pathlib import Path

# apps/dashboard is on sys.path so ``import db`` resolves (see server bootstrap).
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_DASHBOARD_ROOT))

# The dashboard's own sprint state/summary directory, mirroring server.SPRINTS_DIR.
DEFAULT_SPRINTS_DIR = _DASHBOARD_ROOT / "sprints"


def _db():
    """Deferred import of the db module (honours a patched DB_PATH at call time)."""
    import db  # noqa: PLC0415
    return db


# ── shape helpers ─────────────────────────────────────────────────────────────

# Local ticket dispositions → the merged/closed/open vocabulary the AC mandates.
_MERGED_STATUSES = {"done", "shipped", "merged", "passed", "complete", "completed", "uat"}
_CLOSED_STATUSES = {"skipped", "failed", "cancelled", "closed", "rejected", "blocked"}


def _map_issue_state(raw: str | None) -> str:
    """Map a local ticket status / GitHub issue state to merged|closed|open."""
    s = (raw or "").strip().lower()
    if s in _MERGED_STATUSES:
        return "merged"
    if s in _CLOSED_STATUSES:
        return "closed"
    return "open"


def _seconds_between(start: str | None, end: str | None) -> int | None:
    """Whole seconds between two ISO-8601 timestamps, or None if unusable."""
    if not start or not end:
        return None
    from datetime import datetime
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return round((e - s).total_seconds())


def _issue_time_spent(iss: dict) -> int | None:
    """Per-ticket wall-clock seconds from coder start to the latest finish."""
    start = iss.get("coder_started_at") or iss.get("tester_started_at")
    end = iss.get("tester_finished_at") or iss.get("coder_finished_at")
    return _seconds_between(start, end)


def _normalize_issue(iss: dict) -> dict:
    """Project a state-file ticket dict into the AC3 issue shape."""
    ticket_id = iss.get("number", iss.get("ticket_id", iss.get("issue_number")))
    pr = iss.get("pr_number")
    if pr is None and isinstance(iss.get("pr"), dict):
        pr = iss["pr"].get("number")
    return {
        "ticket_id": ticket_id,
        "state": _map_issue_state(iss.get("status") or iss.get("state")),
        "time_spent": iss.get("time_spent", _issue_time_spent(iss)),
        "pr_number": pr,
    }


def _compute_estimate_accuracy(state: dict) -> float | None:
    """Estimated-vs-actual ratio from a state file, or None when not computable.

    Uses estimator minutes against actual wall-clock; >1.0 means the sprint ran
    longer than estimated, <1.0 means it beat the estimate.
    """
    est_min = state.get("estimator_total_minutes")
    actual_secs = state.get("wall_clock_secs")
    if not est_min or not actual_secs:
        return None
    est_secs = float(est_min) * 60.0
    try:
        return round(float(actual_secs) / est_secs, 3) if est_secs else None
    except (ZeroDivisionError, TypeError, ValueError):
        return None


# ── file readers ──────────────────────────────────────────────────────────────

def _read_state_file(sprints_dir: Path, label: str) -> dict | None:
    """Load ``<label>-state.json`` from the sprints dir, or None."""
    path = sprints_dir / f"{label}-state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _read_plan_file(sprints_dir: Path, label: str) -> dict | None:
    """Load ``<label>.json`` plan file from the sprints dir, or None."""
    path = sprints_dir / f"{label}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _find_summary_path(sprints_dir: Path, label: str) -> str | None:
    """Most recent ``<label>-summary-*.md`` path for a sprint, or None."""
    if not sprints_dir.exists():
        return None
    cands = sorted(
        sprints_dir.glob(f"{label}-summary-*.md"),
        key=lambda p: p.name,
        reverse=True,
    )
    return str(cands[0]) if cands else None


def _enrich_from_state(label: str, sprints_dir: Path) -> dict:
    """Per-ticket issues, tokens, duration, estimate_accuracy from local files."""
    state = _read_state_file(sprints_dir, label)
    out: dict = {
        "issues": [],
        "tokens": None,
        "duration": None,
        "estimate_accuracy": None,
        "summary_path": _find_summary_path(sprints_dir, label),
    }
    if not state:
        return out
    out["issues"] = [_normalize_issue(i) for i in state.get("issues", [])]
    tin = state.get("total_tokens_in") or 0
    tout = state.get("total_tokens_out") or 0
    out["tokens"] = int(tin) + int(tout)
    wc = state.get("wall_clock_secs")
    out["duration"] = round(wc) if isinstance(wc, (int, float)) else None
    out["estimate_accuracy"] = _compute_estimate_accuracy(state)
    return out


# ── lifecycle-state normalization ─────────────────────────────────────────────

_STATE_ALIASES = {
    "complete": "finished",
    "completed": "completed",
    "finished": "finished",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "failed": "failed",
    "running": "running",
    "planning": "planning",
    "deleted": "deleted",
}


def _normalize_state(raw: str | None) -> str:
    return _STATE_ALIASES.get((raw or "").strip().lower(), (raw or "unknown").strip().lower())


# ── record builders ───────────────────────────────────────────────────────────

def _record_from_history(rec: dict) -> dict:
    """Build the response row from a sprint_history snapshot (authoritative)."""
    issues = [_normalize_issue(i) if "ticket_id" not in i else i for i in rec.get("issues", [])]
    return {
        "label": rec.get("label"),
        "project": rec.get("project", ""),
        "lifecycle_state": _normalize_state(rec.get("lifecycle_state")),
        "duration": rec.get("duration"),
        "tokens": rec.get("tokens"),
        "estimate_accuracy": rec.get("estimate_accuracy"),
        "pr_number": rec.get("pr_number"),
        "summary_path": rec.get("summary_path"),
        "issues": issues,
        "_sort_key": rec.get("created_at") or "",
    }


def _record_from_lifecycle(row: dict, sprints_dir: Path) -> dict:
    """Build the response row from a `sprints` lifecycle row + file enrichment."""
    label = row.get("label")
    enrich = _enrich_from_state(label, sprints_dir)
    duration = _seconds_between(row.get("started_at"), row.get("ended_at"))
    if duration is None:
        duration = enrich["duration"]
    return {
        "label": label,
        "project": row.get("project", ""),
        "lifecycle_state": _normalize_state(row.get("state")),
        "duration": duration,
        "tokens": enrich["tokens"],
        "estimate_accuracy": enrich["estimate_accuracy"],
        "pr_number": None,
        "summary_path": enrich["summary_path"],
        "issues": enrich["issues"],
        "_sort_key": row.get("ended_at") or row.get("started_at") or row.get("created_at") or "",
    }


def _record_from_files(label: str, sprints_dir: Path) -> dict:
    """Build a response row purely from on-disk state/plan files (last resort)."""
    enrich = _enrich_from_state(label, sprints_dir)
    plan = _read_plan_file(sprints_dir, label) or {}
    state_raw = plan.get("status")
    return {
        "label": label,
        "project": plan.get("project", ""),
        "lifecycle_state": _normalize_state(state_raw) if state_raw else "unknown",
        "duration": enrich["duration"],
        "tokens": enrich["tokens"],
        "estimate_accuracy": enrich["estimate_accuracy"],
        "pr_number": None,
        "summary_path": enrich["summary_path"],
        "issues": enrich["issues"],
        "_sort_key": label,
    }


def _discover_file_labels(sprints_dir: Path) -> set[str]:
    """Sprint labels that have a state.json or plan.json on disk."""
    labels: set[str] = set()
    if not sprints_dir.exists():
        return labels
    for p in sprints_dir.glob("*-state.json"):
        labels.add(p.name[: -len("-state.json")])
    for p in sprints_dir.glob("sprint-*.json"):
        if p.name.endswith("-state.json"):
            continue
        labels.add(p.stem)
    return labels


# ── public API ────────────────────────────────────────────────────────────────

def get_sprint_history(offset: int = 0, limit: int = 20, sprints_dir: Path | None = None) -> dict:
    """Return paginated, enriched sprint-history rows. No GitHub calls (AC5)."""
    sprints_dir = Path(sprints_dir) if sprints_dir is not None else DEFAULT_SPRINTS_DIR
    offset = max(0, int(offset))
    limit = max(0, int(limit))
    db = _db()

    records: list[dict] = []
    seen_labels: set[str] = set()

    # 1) sprint_history snapshots (deleted etc.) — authoritative, take first.
    for rec in db.list_sprint_history():
        label = rec.get("label")
        if label in seen_labels:
            continue
        seen_labels.add(label)
        records.append(_record_from_history(rec))

    # 2) sprints lifecycle rows not already represented by a snapshot.
    for row in db.list_sprints_lifecycle():
        label = row.get("label")
        if label in seen_labels:
            continue
        seen_labels.add(label)
        records.append(_record_from_lifecycle(row, sprints_dir))

    # 3) file-only sprints with no DB row at all.
    for label in _discover_file_labels(sprints_dir):
        if label in seen_labels:
            continue
        seen_labels.add(label)
        records.append(_record_from_files(label, sprints_dir))

    records.sort(key=lambda r: r.get("_sort_key") or "", reverse=True)
    total = len(records)
    window = records[offset:offset + limit] if limit else records[offset:]
    for r in window:
        r.pop("_sort_key", None)

    return {"sprints": window, "offset": offset, "limit": limit, "total": total}


def record_deleted_sprint(
    label: str,
    project: str,
    issues: list[dict] | None,
    commander_dir: Path | None = None,
    end_reason: str = "deleted via dashboard",
) -> None:
    """Persist a ``state='deleted'`` history snapshot (issue #805, AC7).

    Called from the delete-sprint handler BEFORE any label is stripped, so the
    deleted sprint is queryable via :func:`get_sprint_history` immediately after
    deletion (AC8). ``issues`` is the raw GitHub-issue list for the sprint; it is
    normalized to the AC3 shape. When the sprint's ``<label>-state.json`` is
    still on disk it enriches per-ticket timing, tokens and duration.

    Best-effort: any failure is swallowed so the deletion itself never breaks.
    """
    try:
        db = _db()
        snapshot = [_normalize_issue(i) for i in (issues or [])]

        tokens = duration = estimate_accuracy = None
        summary_path = None
        if commander_dir is not None:
            sprints_dir = Path(commander_dir) / "sprints"
            enrich = _enrich_from_state(label, sprints_dir)
            tokens = enrich["tokens"]
            duration = enrich["duration"]
            estimate_accuracy = enrich["estimate_accuracy"]
            summary_path = enrich["summary_path"]
            # Prefer the richer per-ticket data from the state file when present.
            if enrich["issues"]:
                by_id = {i["ticket_id"]: i for i in enrich["issues"]}
                for snap in snapshot:
                    match = by_id.get(snap["ticket_id"])
                    if match and snap.get("time_spent") is None:
                        snap["time_spent"] = match.get("time_spent")

        db.record_sprint_history(
            label=label,
            project=project or "",
            lifecycle_state="deleted",
            end_reason=end_reason,
            duration=duration,
            tokens=tokens,
            estimate_accuracy=estimate_accuracy,
            pr_number=None,
            summary_path=summary_path,
            issues=snapshot,
        )
    except Exception:
        # Never let a ledger write block a sprint deletion.
        pass
