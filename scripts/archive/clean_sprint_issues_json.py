"""clean_sprint_issues_json.py — Prune cross-project tickets from sprints.issues_json.

Before agent_runs reads were project-scoped, a reconcile pass merged a
same-numbered sprint from ANOTHER repo into a sprint row's ``issues_json`` as
title-less agent-run entries (e.g. commander #1045-1056 baked into perf-coach
``sprint-80``). The board outcome band renders ``issues_json`` directly, so those
foreign tickets show on the wrong project's card. The code fix
(fix/agent-runs-project-scope) stops new pollution; this cleans what is stored.

Foreign = an ``issues_json`` entry whose issue number is NOT owned by the row's
project in the ``issues`` mirror (e.g. perf-coach has no #1045). To avoid ever
dropping a legitimate ticket, an entry is pruned ONLY when it is BOTH not-owned
AND title-less (the baked agent-run rows have no title; real tickets carry one).
A not-owned but titled entry is logged for manual review, never dropped.

After pruning, the denormalized count columns are recomputed from the kept list.

Usage:
  python3 scripts/clean_sprint_issues_json.py --dry-run   # show plan, no writes
  python3 scripts/clean_sprint_issues_json.py --apply     # write changes to DB
  python3 scripts/clean_sprint_issues_json.py --apply --label sprint-80   # one sprint
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger(__name__)


def _entry_number(entry: dict) -> int | None:
    raw = entry.get("number")
    if raw is None:
        raw = entry.get("ticket_id")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _has_title(entry: dict) -> bool:
    return bool((entry.get("title") or "").strip())


def _owned_by_project(conn: sqlite3.Connection, repo: str, number: int) -> bool:
    """True when the issues mirror has this number under *repo*."""
    row = conn.execute(
        "SELECT 1 FROM issues WHERE repo = ? AND issue_number = ? LIMIT 1",
        (repo, number),
    ).fetchone()
    return row is not None


def _recompute_counts(issues: list[dict]) -> tuple[int, int, int]:
    settled_done = sum(
        1 for i in issues
        if (i.get("state") or "").lower() == "merged"
        or (i.get("agent_status") or "").lower() in ("completed", "done")
    )
    failure_count = sum(
        1 for i in issues
        if (i.get("agent_status") or "").lower() == "failed"
        or bool(i.get("failure_reason"))
    )
    uat_count = sum(1 for i in issues if (i.get("status") or "").lower() == "uat")
    return settled_done, uat_count, failure_count


def run(*, dry_run: bool, db_path: Path, only_label: str | None) -> int:
    conn = sqlite3.connect(str(db_path), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")

    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "sprints" not in tables or "issues" not in tables:
        _log.warning("Need both 'sprints' and 'issues' tables in %s — nothing to do.", db_path)
        conn.close()
        return 0

    sql = "SELECT label, project, issues_json FROM sprints WHERE issues_json IS NOT NULL AND issues_json != '' AND issues_json != '[]'"
    params: list = []
    if only_label:
        sql += " AND label = ?"
        params.append(only_label)

    rows = conn.execute(sql, params).fetchall()
    total_rows_changed = 0
    total_dropped = 0

    for row in rows:
        label = row["label"]
        project = (row["project"] or "").strip()
        if not project:
            continue  # can't determine ownership without a project
        try:
            issues = json.loads(row["issues_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(issues, list):
            continue

        kept: list[dict] = []
        dropped: list[int] = []
        for entry in issues:
            num = _entry_number(entry)
            if num is None:
                kept.append(entry)
                continue
            if _owned_by_project(conn, project, num):
                kept.append(entry)
                continue
            # Not owned by this project.
            if _has_title(entry):
                _log.warning(
                    "REVIEW: %s [%s] #%s not owned by project but HAS a title — kept (manual review)",
                    label, project, num,
                )
                kept.append(entry)
                continue
            dropped.append(num)

        if not dropped:
            continue

        total_rows_changed += 1
        total_dropped += len(dropped)
        if dry_run:
            print(f"[DRY-RUN] {label} [{project}] would drop {len(dropped)} foreign: {dropped}")
            continue

        settled_done, uat_count, failure_count = _recompute_counts(kept)
        conn.execute(
            "UPDATE sprints SET issues_json = ?, summary_settled_done = ?, "
            "summary_uat_count = ?, summary_failure_count = ? "
            "WHERE label = ? AND project = ?",
            (json.dumps(kept), settled_done, uat_count, failure_count, label, project),
        )
        _log.info(
            "Cleaned %s [%s]: dropped %d foreign %s — counts now done=%d uat=%d failed=%d",
            label, project, len(dropped), dropped, settled_done, uat_count, failure_count,
        )

    if not dry_run:
        conn.commit()
        _log.info("Done — %d row(s) cleaned, %d foreign entries dropped.", total_rows_changed, total_dropped)
    else:
        print(f"\n[DRY-RUN] {total_rows_changed} row(s) would change, {total_dropped} foreign entries would drop.")

    conn.close()
    return total_dropped


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Prune cross-project tickets from sprints.issues_json."
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Print plan, no writes")
    mode.add_argument("--apply", action="store_true", help="Write changes to DB")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", ""), help="Path to dashboard.db")
    ap.add_argument("--label", default=None, help="Only clean this sprint label (default: all)")
    args = ap.parse_args()

    if not args.db:
        ap.error("Set --db or export DB_PATH")

    run(dry_run=args.dry_run, db_path=Path(args.db), only_label=args.label)


if __name__ == "__main__":
    main()
