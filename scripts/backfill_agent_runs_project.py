"""backfill_agent_runs_project.py — Attribute empty agent_runs.project rows.

Why this exists
---------------
``agent_runs_for_sprint(label, project)`` scopes by project, but FALLS BACK to
an unscoped label-only read when the scoped query finds zero rows. Legacy
agent_runs rows (written before the project column) have empty/NULL project, so
the scoped read returns nothing and the fallback fires — mixing a same-numbered
sprint from another repo into the board/outcome (e.g. commander sprint-80 runs
bleeding into perf-coach's sprint-80). ``backfill_sprint_project.py`` fixes the
``sprints`` / ``sprint_history`` tables but NOT ``agent_runs`` — this closes
that gap.

Resolution (per agent_runs row, keyed on sprint_label + issue_number)
---------------------------------------------------------------------
Sprint labels collide across repos and so do issue numbers, but a ticket's
sprint label lives only in its own repo's issue mirror, so the PAIR resolves
the owner:
  1. issues mirror: repo whose issue ``issue_number`` carries the exact label
     ``sprint_label`` — if exactly one repo matches, attribute it.
  2. issues mirror, base label: same, but match the base label (sprint-80) for
     a sub-sprint row (sprint-80.1) whose tickets carry only the base label.
  3. issues mirror, number only: repo that is the sole owner of that issue
     number (ignoring labels) — last mirror-backed resort.
  4. Disk: the sole project whose
     ``<base>/<slug>/.commander/sprints/<label|base>-plan.json|state.json``
     exists — label-only, used when the mirror can't decide.
  5. UNRESOLVED — logged, row left empty.

Usage:
  python3 scripts/backfill_agent_runs_project.py --dry-run   # show plan, no writes
  python3 scripts/backfill_agent_runs_project.py --apply     # write changes to DB
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

# Ensure apps/dashboard is importable (parity with backfill_sprint_project.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)
_log = logging.getLogger(__name__)

_SUB_LABEL_RE = re.compile(r"^(sprint-\d+)\.\d+$")


def _load_repos(projects_file: Path) -> list[str]:
    if not projects_file.exists():
        return []
    try:
        return [p["repo"] for p in json.loads(projects_file.read_text()) if p.get("repo")]
    except Exception:
        return []


def _base_label(label: str) -> str:
    m = _SUB_LABEL_RE.match(label)
    return m.group(1) if m else label


def _issue_carries_label(labels_json: str, label: str) -> bool:
    try:
        labels = json.loads(labels_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return False
    return any(isinstance(l, dict) and l.get("name") == label for l in labels)


def _repos_for_issue(
    conn: sqlite3.Connection, issue_number: int, label: str | None
) -> list[str]:
    """Repos whose mirror has this issue number, optionally filtered to those
    whose issue carries *label*. Returns distinct repos (order not significant)."""
    rows = conn.execute(
        "SELECT repo, labels FROM issues WHERE issue_number = ?",
        (issue_number,),
    ).fetchall()
    if label is None:
        return sorted({r[0] for r in rows})
    return sorted({r[0] for r in rows if _issue_carries_label(r[1], label)})


def _resolve(
    label: str,
    issue_number: int | None,
    conn: sqlite3.Connection,
    tables: set[str],
    repos: list[str],
    projects_base: Path,
) -> tuple[str, str]:
    """Return (project, source) for one (label, issue_number), or ('', 'UNRESOLVED')."""
    base = _base_label(label)

    if "issues" in tables and issue_number is not None:
        # 1. exact label on that issue number
        hits = _repos_for_issue(conn, issue_number, label)
        if len(hits) == 1:
            return hits[0], "issues:label"
        # 2. base label on that issue number (sub-sprint row whose ticket carries base)
        if base != label:
            hits = _repos_for_issue(conn, issue_number, base)
            if len(hits) == 1:
                return hits[0], "issues:base-label"
        # 3. sole owner of the issue number (labels ignored)
        hits = _repos_for_issue(conn, issue_number, None)
        if len(hits) == 1:
            return hits[0], "issues:number"

    # 4. disk fallback — sole project with a sprint file for this label or its base
    disk_hits: list[str] = []
    for repo in repos:
        slug = repo.split("/")[-1] if "/" in repo else repo
        sprint_dir = projects_base / slug / ".commander" / "sprints"
        if any(
            (sprint_dir / f"{lbl}{suffix}").exists()
            for lbl in {label, base}
            for suffix in ("-plan.json", "-state.json")
        ):
            disk_hits.append(repo)
    if len(set(disk_hits)) == 1:
        return disk_hits[0], "disk"

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
    if "agent_runs" not in tables:
        _log.warning("No agent_runs table in %s — nothing to do.", db_path)
        conn.close()
        return 0

    repos = _load_repos(projects_file)

    empty_rows = conn.execute(
        "SELECT DISTINCT sprint_label, issue_number FROM agent_runs "
        "WHERE project = '' OR project IS NULL"
    ).fetchall()

    total_updated = 0
    unresolved = 0
    for row in empty_rows:
        label = row["sprint_label"]
        issue_number = row["issue_number"]
        project, source = _resolve(
            label, issue_number, conn, tables, repos, projects_base
        )

        if dry_run:
            print(
                f"[DRY-RUN] agent_runs sprint_label={label!r} issue=#{issue_number}  "
                f"proposed_project={project or ''}  source={source}"
            )
            if not project:
                unresolved += 1
            continue

        if project:
            cur = conn.execute(
                "UPDATE agent_runs SET project = ? "
                "WHERE sprint_label = ? AND issue_number = ? "
                "AND (project = '' OR project IS NULL)",
                (project, label, issue_number),
            )
            total_updated += cur.rowcount
            _log.info(
                "Updated agent_runs sprint_label=%r issue=#%s → project=%r (%d row(s), source: %s)",
                label, issue_number, project, cur.rowcount, source,
            )
        else:
            unresolved += 1
            _log.warning(
                "UNRESOLVED: agent_runs sprint_label=%r issue=#%s — project left empty",
                label, issue_number,
            )

    if not dry_run:
        conn.commit()
        _log.info("Done — %d row(s) updated, %d pair(s) unresolved.", total_updated, unresolved)
    else:
        print(f"\n[DRY-RUN] {unresolved} (label, issue) pair(s) would remain unresolved.")

    conn.close()
    return total_updated


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Backfill empty agent_runs.project rows (project attribution)."
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
