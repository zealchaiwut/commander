"""backfill_sprint_project.py — Attribute empty sprints.project rows (issue #1460).

Resolves ownership for every sprints / sprint_history row where project is empty
via three ordered strategies:
  1. agent_runs.project for matching sprint_label
  2. Disk walk: <projects_base>/<slug>/.commander/sprints/<label>-plan.json|state.json
  3. UNRESOLVED — logged as warning, row left empty

Usage:
  python3 scripts/backfill_sprint_project.py --dry-run   # show plan, no writes
  python3 scripts/backfill_sprint_project.py --apply     # write changes to DB
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

# Ensure apps/dashboard is importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)
_log = logging.getLogger(__name__)


def _load_repos(projects_file: Path) -> list[str]:
    if not projects_file.exists():
        return []
    try:
        return [p["repo"] for p in json.loads(projects_file.read_text()) if p.get("repo")]
    except Exception:
        return []


def _resolve(
    label: str,
    conn: sqlite3.Connection,
    tables: set[str],
    repos: list[str],
    projects_base: Path,
) -> tuple[str, str]:
    """Return (project, source) for label, or ('', 'UNRESOLVED')."""
    if "agent_runs" in tables:
        row = conn.execute(
            "SELECT project FROM agent_runs "
            "WHERE sprint_label = ? AND project IS NOT NULL AND project != '' LIMIT 1",
            (label,),
        ).fetchone()
        if row and row[0]:
            return row[0], "agent_runs"

    for repo in repos:
        slug = repo.split("/")[-1] if "/" in repo else repo
        sprint_dir = projects_base / slug / ".commander" / "sprints"
        if (sprint_dir / f"{label}-plan.json").exists():
            return repo, "disk"
        if (sprint_dir / f"{label}-state.json").exists():
            return repo, "disk"

    return "", "UNRESOLVED"


def run(
    *,
    dry_run: bool,
    db_path: Path,
    projects_file: Path,
    projects_base: Path,
) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    repos = _load_repos(projects_file)
    total_updated = 0

    for table in ("sprints", "sprint_history"):
        if table not in tables:
            continue
        empty_rows = conn.execute(
            f"SELECT label FROM {table} WHERE project = '' OR project IS NULL"
        ).fetchall()

        for row in empty_rows:
            label = row[0]
            project, source = _resolve(label, conn, tables, repos, projects_base)

            if dry_run:
                print(
                    f"[DRY-RUN] {table} label={label!r}  "
                    f"proposed_project={project or ''}  source={source}"
                )
            else:
                if project:
                    conn.execute(
                        f"UPDATE {table} SET project = ? "
                        f"WHERE label = ? AND (project = '' OR project IS NULL)",
                        (project, label),
                    )
                    total_updated += 1
                    _log.info("Updated %s label=%r → project=%r (source: %s)", table, label, project, source)
                else:
                    _log.warning(
                        "UNRESOLVED: %s label=%r — no agent_runs row and no disk file; "
                        "project left empty",
                        table,
                        label,
                    )

    if not dry_run:
        conn.commit()
        _log.info("Done — %d row(s) updated.", total_updated)

    conn.close()
    return total_updated


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill empty sprints.project / sprint_history.project rows."
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print plan, no writes")
    mode.add_argument("--apply", action="store_true", help="Write changes to DB")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", ""), help="Path to dashboard.db")
    ap.add_argument(
        "--projects-file",
        default=str(_DASHBOARD_DIR / "projects.json"),
        help="Path to projects.json",
    )
    ap.add_argument(
        "--projects-base",
        default=str(Path.home() / "dev"),
        help="Root directory where project slugs live (default: ~/dev)",
    )
    args = ap.parse_args()

    if not args.db:
        ap.error("Set --db or export DB_PATH")

    run(
        dry_run=args.dry_run,
        db_path=Path(args.db),
        projects_file=Path(args.projects_file),
        projects_base=Path(args.projects_base),
    )


if __name__ == "__main__":
    main()
