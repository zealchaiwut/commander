"""repair_sprint_lineage.py — rebuild a sprint lineage's DB rows from GitHub truth.

When a rerun lineage (sprint-N, sprint-N.1, sprint-N.2 …) ran on a clone whose
artifacts were never ingested here, the dashboard DB ends up with missing rows,
empty `project`, and no `parent_label`. The board then can't collapse the
lineage, so superseded/finished members linger as zombie cards one at a time.

This repair, per project + base sprint:
  * discovers lineage members from the issues mirror labels + existing rows,
  * sets each row's project + parent_label (chain: each member's parent is the
    previous member in the lineage; the base has no parent),
  * sets a GitHub-derived state:
      - sprint branch merged (in `gh pr list --state merged`)         → completed
      - else summary issue exists AND 0 open work tickets             → completed
      - else summary issue exists                                     → ready_to_merge
      - else keep the existing state, or needs_rework if none.
Never downgrades a row already `completed`.

With parent links in place, the board groups 9.1–9.N as one lineage; once the
latest member is finished, the whole group drops into History.

Usage:
  python3 scripts/repair_sprint_lineage.py --db DB --project owner/repo --dry-run
  python3 scripts/repair_sprint_lineage.py --db DB --project owner/repo --apply
  python3 scripts/repair_sprint_lineage.py --db DB --project owner/repo --base sprint-9 --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger(__name__)

_NON_WORK = {"sprint-summary", "docs", "documentation"}
_SUMMARY_RE = re.compile(r"Sprint\s+([0-9]+(?:\.[0-9]+)?)\s+Executive Summary", re.I)


def _sub_index(label: str) -> tuple[int, float]:
    """Sort key: base sprint-N → (N, 0); sub sprint-N.M → (N, M)."""
    m = re.match(r"^sprint-(\d+)(?:\.(\d+))?$", label)
    if not m:
        return (10**9, 0.0)
    return (int(m.group(1)), float(m.group(2)) if m.group(2) else 0.0)


def _labels_of(raw: str | None) -> list[str]:
    try:
        return [l.get("name") for l in json.loads(raw or "[]") if isinstance(l, dict) and l.get("name")]
    except (json.JSONDecodeError, TypeError):
        return []


def _has_composite_key(conn) -> bool:
    pk = [r for r in conn.execute("PRAGMA table_info(sprints)").fetchall() if r["pk"]]
    return len(pk) > 1


def _merged_branches(repo: str) -> set[str]:
    """Head refs of merged PRs (sprint/sprint-N). One gh call; empty on error."""
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "merged",
             "--json", "headRefName", "--limit", "300"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return set()
        return {p.get("headRefName") for p in json.loads(out.stdout) if p.get("headRefName")}
    except Exception:
        return set()


def _discover(conn, repo: str, base: str | None) -> dict[str, list[str]]:
    """Map base label → sorted lineage members, from mirror labels + sprints rows."""
    member_re = re.compile(r"^sprint-\d+(?:\.\d+)?$")
    found: set[str] = set()
    for row in conn.execute("SELECT labels FROM issues WHERE repo = ?", (repo,)):
        for n in _labels_of(row["labels"]):
            if member_re.match(n):
                found.add(n)
    for row in conn.execute("SELECT label FROM sprints WHERE project = ? OR project = '' OR project IS NULL", (repo,)):
        if member_re.match(row["label"] or ""):
            found.add(row["label"])

    lineages: dict[str, list[str]] = {}
    for lbl in found:
        n, _m = _sub_index(lbl)
        base_lbl = f"sprint-{n}"
        lineages.setdefault(base_lbl, [])
        if lbl not in lineages[base_lbl]:
            lineages[base_lbl].append(lbl)
    for k in lineages:
        lineages[k].sort(key=_sub_index)
    # Only lineages with at least one sub-sprint (a real rerun chain).
    lineages = {k: v for k, v in lineages.items() if len(v) > 1}
    if base:
        lineages = {k: v for k, v in lineages.items() if k == base}
    return lineages


def _summary_labels(conn, repo: str) -> set[str]:
    """Sprint labels that have an Executive Summary issue in the mirror."""
    out: set[str] = set()
    for row in conn.execute("SELECT title, labels FROM issues WHERE repo = ?", (repo,)):
        names = set(_labels_of(row["labels"]))
        title = row["title"] or ""
        m = _SUMMARY_RE.search(title)
        if "sprint-summary" in names or m:
            for n in names:
                if re.match(r"^sprint-\d+(?:\.\d+)?$", n):
                    out.add(n)
            if m:
                out.add(f"sprint-{m.group(1)}")
    return out


def _open_work_count(conn, repo: str, label: str) -> int:
    n = 0
    for row in conn.execute("SELECT labels FROM issues WHERE repo = ? AND state = 'open'", (repo,)):
        names = set(_labels_of(row["labels"]))
        if label in names and not (names & _NON_WORK):
            n += 1
    return n


def _resolve_state(conn, repo: str, label: str, merged: set[str],
                   summaries: set[str], existing: str | None) -> str:
    if existing and existing == "completed":
        return "completed"
    if f"sprint/{label}" in merged:
        return "completed"
    has_summary = label in summaries
    if has_summary and _open_work_count(conn, repo, label) == 0:
        return "completed"
    if has_summary:
        return "ready_to_merge"
    return existing or "needs_rework"


def _existing_state(conn, label: str, repo: str) -> str | None:
    row = conn.execute(
        "SELECT state FROM sprints WHERE label = ? AND (project = ? OR project = '' OR project IS NULL) LIMIT 1",
        (label, repo),
    ).fetchone()
    return (row["state"] if row else None)


def _upsert(conn, label: str, repo: str, state: str, parent: str | None, composite: bool) -> None:
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    if composite:
        conn.execute(
            """INSERT INTO sprints (label, project, state, created_at, parent_label)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(label, project) DO UPDATE SET
                 state = excluded.state,
                 parent_label = excluded.parent_label,
                 created_at = COALESCE(sprints.created_at, excluded.created_at)""",
            (label, repo, state, created_at, parent),
        )
        # If a stray empty-project row exists for this label, retire it so the
        # scoped row is authoritative (board reads project-scoped).
        conn.execute(
            "UPDATE sprints SET project = ? WHERE label = ? AND (project = '' OR project IS NULL)",
            (repo, label),
        )
    else:
        conn.execute(
            """INSERT INTO sprints (label, project, state, created_at, parent_label)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(label) DO UPDATE SET
                 project = excluded.project,
                 state = excluded.state,
                 parent_label = excluded.parent_label,
                 created_at = COALESCE(sprints.created_at, excluded.created_at)""",
            (label, repo, state, created_at, parent),
        )


def run(*, dry_run: bool, db_path: Path, repo: str, base: str | None) -> int:
    conn = sqlite_connect(db_path)
    conn.row_factory = __import__("sqlite3").Row
    conn.execute("PRAGMA busy_timeout=60000")

    composite = _has_composite_key(conn)
    lineages = _discover(conn, repo, base)
    if not lineages:
        _log.warning("No rerun lineages found for %s%s.", repo, f" base={base}" if base else "")
        conn.close()
        return 0

    merged = _merged_branches(repo)
    summaries = _summary_labels(conn, repo)
    changed = 0

    for base_lbl, members in sorted(lineages.items(), key=lambda kv: _sub_index(kv[0])):
        _log.info("Lineage %s: members %s", base_lbl, members)
        for i, label in enumerate(members):
            parent = members[i - 1] if i > 0 else None
            existing = _existing_state(conn, label, repo)
            state = _resolve_state(conn, repo, label, merged, summaries, existing)
            line = f"  {label}: parent={parent or '-'} state={existing or '<none>'}→{state}"
            if dry_run:
                print("[DRY-RUN]" + line)
                changed += 1
                continue
            _upsert(conn, label, repo, state, parent, composite)
            print("[APPLIED]" + line)
            changed += 1

    if not dry_run:
        conn.commit()
        _log.info("Done — %d member row(s) repaired.", changed)
    else:
        print(f"\n[DRY-RUN] {changed} member row(s) would be repaired.")
    conn.close()
    return changed


def sqlite_connect(db_path: Path):
    import sqlite3
    return sqlite3.connect(str(db_path), timeout=60.0)


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild a sprint lineage's DB rows from GitHub truth.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", ""), help="Path to dashboard.db")
    ap.add_argument("--project", required=True, help="owner/repo, e.g. zealchaiwut/crux")
    ap.add_argument("--base", default=None, help="Only repair this base, e.g. sprint-9 (default: all rerun lineages)")
    args = ap.parse_args()
    if not args.db:
        ap.error("Set --db or export DB_PATH")
    run(dry_run=args.dry_run, db_path=Path(args.db), repo=args.project, base=args.base)


if __name__ == "__main__":
    main()
