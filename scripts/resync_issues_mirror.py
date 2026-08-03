#!/usr/bin/env python3
"""Force a full GitHub → commander.db issues-mirror resync (all registered repos).

Use when the sprint board shows phantom sprints (stale labels in the local
mirror). GitHub is the source of truth; this re-crawls every issue page and
upserts the mirror. Restart uvicorn afterward so in-memory gh caches clear.

Without --yes this is a safe dry-run: it prints what would be resynced and
exits 1 without touching GitHub or the database.

Usage (from any Commander clone):
  cd ~/dev/commander/main
  DB_PATH=./apps/dashboard/commander.db \\
    ./venv/bin/python3 scripts/resync_issues_mirror.py --yes

Scope a single repo:
  DB_PATH=./apps/dashboard/commander.db \\
    ./venv/bin/python3 scripts/resync_issues_mirror.py --yes --repo zealchaiwut/commander
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _dashboard_dir() -> Path:
    return _repo_root() / "apps" / "dashboard"


def _load_repos() -> list[str]:
    projects_json = _dashboard_dir() / "projects.json"
    repos: list[str] = []
    if projects_json.is_file():
        try:
            data = json.loads(projects_json.read_text(encoding="utf-8"))
            for proj in data if isinstance(data, list) else []:
                repo = (proj or {}).get("repo")
                if repo:
                    repos.append(repo)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warn: could not read projects.json: {exc}", file=sys.stderr)
    if not repos:
        fallback = os.environ.get("GITHUB_REPO", "zealchaiwut/commander")
        repos = [fallback]
    seen: set[str] = set()
    ordered: list[str] = []
    for r in repos:
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return ordered


def _sprint_counts(repo: str) -> dict[str, int]:
    import db  # noqa: PLC0415

    counts: Counter[str] = Counter()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT labels FROM issues WHERE repo = ? AND state = 'open'",
            (repo,),
        ).fetchall()
    for row in rows:
        labels = json.loads(row["labels"] or "[]")
        for lbl in labels:
            name = lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
            if name.startswith("sprint-") and name not in (
                "sprint-summary",
                "sprint-manager",
                "sprint-sprint-test",
            ):
                counts[name] += 1
    return dict(sorted(counts.items(), key=lambda x: x[0]))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="resync_issues_mirror.py",
        description=(
            "Force a full GitHub → SQLite issues-mirror resync. "
            "Without --yes, prints what would be resynced and exits 1 (safe dry-run)."
        ),
    )
    parser.add_argument(
        "--repo",
        metavar="OWNER/REPO",
        help="Scope resync to a single repository (default: all registered repos).",
    )
    parser.add_argument(
        "--yes",
        "--force",
        action="store_true",
        dest="yes",
        help=(
            "Required to actually perform the resync. "
            "Without this flag only a dry-run summary is printed."
        ),
    )
    args = parser.parse_args()

    repos = [args.repo] if args.repo else _load_repos()

    if not args.yes:
        print(f"Would resync {len(repos)} repo(s):")
        for r in repos:
            print(f"  {r}")
        print("Pass --yes (or --force) to perform the resync.")
        return 1

    # ── Live resync path (--yes required) ─────────────────────────────────────
    dash = _dashboard_dir()
    if not os.environ.get("DB_PATH"):
        default_db = dash / "commander.db"
        if default_db.is_file():
            os.environ["DB_PATH"] = str(default_db.resolve())
        else:
            print("error: set DB_PATH (e.g. apps/dashboard/commander.db)", file=sys.stderr)
            return 1
    else:
        db_path = Path(os.environ["DB_PATH"]).expanduser()
        if not db_path.is_absolute():
            db_path = (Path.cwd() / db_path).resolve()
        os.environ["DB_PATH"] = str(db_path)

    if str(dash) not in sys.path:
        sys.path.insert(0, str(dash))

    from github_events_sync import full_sync_issues_mirror  # noqa: PLC0415

    print(f"Resyncing {len(repos)} repo(s): {', '.join(repos)}")
    print(f"DB_PATH={os.environ['DB_PATH']}")

    errors = 0
    total_rows = 0
    for repo in repos:
        print(f"  → {repo} …", end=" ", flush=True)
        try:
            result = full_sync_issues_mirror(repo)
            rows = result.get("synced", 0) or 0
            total_rows += rows
            rate_note = " (rate-limited — re-run after reset)" if result.get("rate_limited") else ""
            print(f"done ({rows} rows{rate_note})")
            counts = _sprint_counts(repo)
            if counts:
                for sprint, n in counts.items():
                    print(f"    {sprint}: {n} open")
        except Exception as exc:
            errors += 1
            print(f"error: {exc}", file=sys.stderr)

    print(f"\nSummary: {len(repos)} repo(s) touched, {total_rows} rows changed, {errors} error(s)")
    print("Restart the dashboard so in-memory caches clear:")
    print("  launchctl kickstart -k gui/$(id -u)/com.commander.dashboard")
    print("  # or: bash scripts/start_prd.sh")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
