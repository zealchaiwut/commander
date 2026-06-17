#!/usr/bin/env python3
"""Force a full GitHub → commander.db issues-mirror resync (all registered repos).

Use when the sprint board shows phantom sprints (stale labels in the local
mirror). GitHub is the source of truth; this re-crawls every issue page and
upserts the mirror. Restart uvicorn afterward so in-memory gh caches clear.

Usage (from any Commander clone):
  cd ~/dev/commander/main
  DB_PATH=./apps/dashboard/commander.db ./venv/bin/python3 scripts/resync_issues_mirror.py

Or with .env loaded:
  cd ~/dev/commander/main/apps/dashboard
  set -a && source .env && set +a
  ../../venv/bin/python3 ../../scripts/resync_issues_mirror.py
"""
from __future__ import annotations

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
    import db

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
    os.chdir(dash)

    from github_events_sync import full_sync_issues_mirror

    repos = _load_repos()
    print(f"DB_PATH={os.environ['DB_PATH']}")
    print(f"Repos: {', '.join(repos)}")
    errors = 0
    for repo in repos:
        print(f"\n→ full sync {repo} …")
        try:
            result = full_sync_issues_mirror(repo)
            print(f"  {result}")
            if result.get("rate_limited"):
                print("  warn: rate limit hit — re-run after reset if counts look wrong")
            counts = _sprint_counts(repo)
            print(f"  open issues by sprint label: {counts or '(none)'}")
        except Exception as exc:
            errors += 1
            print(f"  error: {exc}", file=sys.stderr)

    print("\nDone. Restart the dashboard so in-memory caches clear:")
    print("  launchctl kickstart -k gui/$(id -u)/com.commander.dashboard")
    print("  # or: bash scripts/start_prd.sh")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
