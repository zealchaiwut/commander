#!/usr/bin/env python3
"""export_to_neon.py — one-shot export of local data to Neon (issue #758).

Neon is an OPTIONAL export target, not a live dependency. Normal Commander
operation runs entirely off SQLite (DB_PATH) + local JSON; nothing writes to Neon
mid-flow. Run this script by hand to push a snapshot of local sprint lifecycle
data and projects.json into a Neon (Postgres) instance for external reporting.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db python scripts/export_to_neon.py
    python scripts/export_to_neon.py --db-path /path/to/commander.db
    python scripts/export_to_neon.py --dry-run

Exit codes:
    0  export completed (per-row failures are reported but do not fail the run)
    1  DATABASE_URL is not set
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SM_DIR = _REPO_ROOT / "services" / "sprint_manager"
for _p in (_REPO_ROOT, _SM_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Local SQLite state -> Neon ORM status (the ORM enum is a subset of the local
# lifecycle states). States not listed leave the row at its default 'pending'.
_STATE_TO_STATUS = {
    "running": "running",
    "completed": "complete",
    "cancelled": "cancelled",
    "failed": "cancelled",
}


def _default_db_path() -> Path:
    env = os.environ.get("DB_PATH", "").strip()
    if env:
        return Path(env)
    return _REPO_ROOT / "apps" / "dashboard" / "dashboard.db"


def _read_local_sprints(db_path: Path) -> tuple[list[dict], dict[str, list[int]]]:
    """Return (sprints, ticket_order_by_label) from the local SQLite store.

    Missing tables (a brand-new DB) yield empty results rather than an error.
    """
    sprints: list[dict] = []
    order: dict[str, list[int]] = {}
    if not db_path.exists():
        return sprints, order
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                "SELECT label, project, state FROM sprints"
            ).fetchall()
            sprints = [dict(r) for r in rows]
        except sqlite3.OperationalError:
            sprints = []
        try:
            for r in conn.execute(
                "SELECT label, issue FROM sprint_ticket_order ORDER BY label, position"
            ).fetchall():
                order.setdefault(r["label"], []).append(int(r["issue"]))
        except sqlite3.OperationalError:
            order = {}
    finally:
        conn.close()
    return sprints, order


def export(db_path: Path, dry_run: bool = False) -> dict:
    """Export local sprints + ticket order + projects.json to Neon.

    Writes via portable raw SQL (`ON CONFLICT` upserts) so the same code path runs
    against Postgres (real Neon) and SQLite (tests). Per-row failures are collected
    in ``errors`` but never abort the run, so a partially-migrated Neon schema
    still makes progress.
    """
    from datetime import datetime, timezone

    import sqlalchemy as sa

    from services.sprint_manager import neon_db
    from services.sprint_manager import models  # noqa: F401 — registers ORM tables
    import sync_projects_to_neon as sync_projects

    summary: dict = {
        "sprints_exported": 0,
        "tickets_exported": 0,
        "errors": [],
    }

    sprints, order = _read_local_sprints(db_path)
    sys.stdout.write(str(f"Local store: {db_path}") + "\n")
    sys.stdout.write(str(f"  sprints: {len(sprints)}; ticket-order labels: {len(order)}") + "\n")

    if dry_run:
        sys.stdout.write(str("[dry-run] no writes performed") + "\n")
        summary["dry_run"] = True
        return summary

    engine = neon_db.get_engine()
    # Idempotent: creates any missing target tables; existing ones (created by
    # alembic on a real Neon instance) are left untouched.
    neon_db.Base.metadata.create_all(engine)

    now_iso = datetime.now(timezone.utc).isoformat()
    sprint_upsert = sa.text(
        "INSERT INTO sprints (label, goal, status, created_at, project)"
        " VALUES (:label, :goal, :status, :created_at, :project)"
        " ON CONFLICT (label) DO UPDATE SET"
        "   status = excluded.status, project = excluded.project, goal = excluded.goal"
    )
    ticket_upsert = sa.text(
        "INSERT INTO sprint_tickets (sprint_id, issue_number, position, status)"
        " VALUES (:sid, :issue, :pos, 'pending')"
        " ON CONFLICT (sprint_id, issue_number) DO UPDATE SET position = excluded.position"
    )

    for s in sprints:
        label = s.get("label")
        if not label:
            continue
        project = s.get("project") or ""
        state = (s.get("state") or "").strip()
        status = _STATE_TO_STATUS.get(state, "pending")
        try:
            with engine.begin() as conn:
                conn.execute(sprint_upsert, {
                    "label": label, "goal": label, "status": status,
                    "created_at": now_iso, "project": project,
                })
                sid = conn.execute(
                    sa.text("SELECT id FROM sprints WHERE label = :label"),
                    {"label": label},
                ).scalar()
                summary["sprints_exported"] += 1

                for position, issue in enumerate(order.get(label, [])):
                    conn.execute(ticket_upsert, {"sid": sid, "issue": issue, "pos": position})
                    summary["tickets_exported"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"sprint {label}: {exc}")
            continue

    # projects.json -> Neon (best-effort; returns its own error list).
    proj_summary = sync_projects.sync_projects_to_neon()
    summary["projects"] = proj_summary

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Export local data to Neon (issue #758)")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_default_db_path(),
        help="Path to the local SQLite store (default: $DB_PATH or apps/dashboard/dashboard.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be exported without writing to Neon",
    )
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL", "").strip():
        sys.stderr.write(str("DATABASE_URL is not set. Neon is an optional export target — set "
            "DATABASE_URL to your Neon connection string before running this "
            "script. See the README 'Neon export' section.") + "\n")
        return 1

    summary = export(args.db_path, dry_run=args.dry_run)

    sys.stdout.write(str("\nExport summary:") + "\n")
    sys.stdout.write(str(f"  sprints exported: {summary['sprints_exported']}") + "\n")
    sys.stdout.write(str(f"  tickets exported: {summary['tickets_exported']}") + "\n")
    if summary.get("projects"):
        p = summary["projects"]
        sys.stdout.write(str(f"  projects synced:  {p.get('projects_synced', 0)} "
            f"(skipped {p.get('projects_skipped', 0)})") + "\n")
    if summary["errors"]:
        sys.stdout.write(str(f"  row-level errors: {len(summary['errors'])}") + "\n")
        for err in summary["errors"]:
            sys.stdout.write(str(f"    - {err}") + "\n")
    sys.stdout.write(str("Done.") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
