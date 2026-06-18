"""Sprints-table CSV export/import — PRD→UAT data sync for test tooling.

Lets the operator snapshot one project's ``sprints`` lifecycle rows from one
instance (e.g. PRD) and load them into another (e.g. UAT), so frontend/backend
fixes can be exercised against real sprint states on develop without hotfixing
master. Scoped to a single project; import upserts by sprint label.

New endpoints belong in routers/<area>.py, never in server.py
(COMMANDER_GATE_MONOLITH, issue #761) — these are mounted on the maintenance router.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import db as _db  # noqa: E402  (apps/dashboard on path)

# Columns mirror the sprints DDL (db.py). `label` is the primary key.
SPRINT_COLUMNS = [
    "label", "project", "state", "created_at", "started_at",
    "ended_at", "end_reason", "parent_label",
]

# Allowed lifecycle states (sprints CHECK constraint) — reject anything else on
# import so a malformed CSV can't abort the transaction on a constraint error.
_ALLOWED_STATES = {
    "draft", "planned", "running", "ready_to_merge",
    "needs_rework", "completed", "planning", "cancelled", "failed",
}


def export_sprints_csv(project: str) -> str:
    """Return the project's sprints rows as CSV text (header + rows)."""
    with _db.get_conn() as conn:
        _db._create_sprint_lifecycle_tables(conn)
        rows = conn.execute(
            "SELECT " + ", ".join(SPRINT_COLUMNS)
            + " FROM sprints WHERE project = ? ORDER BY label",
            (project,),
        ).fetchall()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(SPRINT_COLUMNS)
    for r in rows:
        writer.writerow(["" if r[c] is None else r[c] for c in SPRINT_COLUMNS])
    return out.getvalue()


def import_sprints_csv(project: str, csv_text: str, mode: str = "merge") -> dict:
    """Upsert sprints rows from CSV into one project.

    ``mode="merge"`` (default): insert new rows, overwrite rows with a matching
    label. ``mode="replace"``: delete the project's rows first, then load. Every
    imported row is forced to the target ``project`` so a snapshot can't bleed
    into another project. Returns counts + any per-row errors (skipped rows never
    abort the whole import).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    incoming = [r for r in reader]
    if not incoming:
        return {"ok": False, "error": "CSV had no data rows",
                "inserted": 0, "updated": 0, "errors": [], "before": 0, "after": 0}

    inserted = updated = 0
    errors: list[str] = []
    with _db.get_conn() as conn:
        _db._create_sprint_lifecycle_tables(conn)
        before = conn.execute(
            "SELECT COUNT(*) FROM sprints WHERE project = ?", (project,)
        ).fetchone()[0]
        if mode == "replace":
            conn.execute("DELETE FROM sprints WHERE project = ?", (project,))
        for i, row in enumerate(incoming):
            label = (row.get("label") or "").strip()
            if not label:
                errors.append(f"row {i + 1}: missing label — skipped")
                continue
            state = (row.get("state") or "draft").strip()
            if state not in _ALLOWED_STATES:
                errors.append(f"{label}: invalid state {state!r} — skipped")
                continue
            existed = conn.execute(
                "SELECT 1 FROM sprints WHERE label = ?", (label,)
            ).fetchone() is not None
            try:
                conn.execute(
                    "INSERT INTO sprints"
                    " (label, project, state, created_at, started_at, ended_at, end_reason, parent_label)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(label) DO UPDATE SET"
                    "  project=excluded.project, state=excluded.state,"
                    "  created_at=excluded.created_at, started_at=excluded.started_at,"
                    "  ended_at=excluded.ended_at, end_reason=excluded.end_reason,"
                    "  parent_label=excluded.parent_label",
                    (
                        label, project, state,
                        (row.get("created_at") or "").strip() or None,
                        (row.get("started_at") or "").strip() or None,
                        (row.get("ended_at") or "").strip() or None,
                        (row.get("end_reason") or "").strip() or None,
                        (row.get("parent_label") or "").strip() or None,
                    ),
                )
                if existed:
                    updated += 1
                else:
                    inserted += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")
        conn.commit()
        after = conn.execute(
            "SELECT COUNT(*) FROM sprints WHERE project = ?", (project,)
        ).fetchone()[0]
    return {"ok": True, "inserted": inserted, "updated": updated,
            "errors": errors, "before": before, "after": after, "mode": mode}
