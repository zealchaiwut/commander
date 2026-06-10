#!/usr/bin/env python3
"""Archive stale per-sprint runtime files to reduce startup noise (issue #735).

Over time ``.commander/sprints/`` accumulates hundreds of per-sprint JSON files.
The transient runtime files for *finished* sprints — the plan, the zero-issue
placeholder, and the state — add noise to startup scans without carrying data
that analytics or the dashboard still need. This script moves those files into a
reversible ``.commander/sprints/archive/`` subfolder.

What it archives (per finished sprint N):
    sprint-N-plan.json      execution plan
    sprint-N.json           ONLY when it is a zero-issue placeholder
    sprint-N-state.json     runtime state snapshot

What it NEVER touches:
    sprint-N-status.json    read by analytics / live status
    sprint-N-estimate.json  read by analytics / sizing
    sprint-N-summary-*.md   the durable summary (also a finish signal)
    anything for a non-finished sprint

A sprint is "finished" only when it has a posted summary issue OR a summary
markdown file AND no live process is currently running it.

Nothing is ever deleted — every move is reversible by moving the file back.

Usage:
    python scripts/clean_sprint_files.py --project <id> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

ARCHIVE_DIRNAME = "archive"

# sprint-N-... where N is an integer (sub-sprints like sprint-N.M are ignored —
# they carry a '.' before the next token and are out of scope per the ticket).
_SPRINT_NUM_RE = re.compile(r"^sprint-(\d+)(?:[-.]|$)")


# ── liveness ──────────────────────────────────────────────────────────────────

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def is_sprint_running(sprints_dir: Path, n: int) -> bool:
    """Return True when sprint N has a live PID file (or a pending claim).

    Mirrors the dashboard's PID-file convention: ``sprint-N-pid`` /
    ``sprint-N-pid.pending``. An empty/zero PID is a pending claim (still
    starting up) and counts as running.
    """
    label = f"sprint-{n}"
    for name in (f"{label}-pid", f"{label}-pid.pending"):
        p = sprints_dir / name
        if not p.exists():
            continue
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in ("", "0"):
            return True
        try:
            pid = int(raw)
        except ValueError:
            continue
        if _pid_alive(pid):
            return True
    return False


# ── finish detection ──────────────────────────────────────────────────────────

def _summary_md_exists(sprints_dir: Path, n: int) -> bool:
    return any(sprints_dir.glob(f"sprint-{n}-summary-*.md"))


def is_sprint_finished(
    sprints_dir: Path,
    n: int,
    *,
    has_summary_issue: Callable[[int], bool] = lambda n: False,
    running_check: Optional[Callable[[Path, int], bool]] = None,
) -> bool:
    """A sprint is finished when it has a summary (issue OR markdown) and is not running."""
    run_check = running_check or is_sprint_running
    has_summary = _summary_md_exists(sprints_dir, n) or has_summary_issue(n)
    if not has_summary:
        return False
    if run_check(sprints_dir, n):
        return False
    return True


# ── placeholder check ─────────────────────────────────────────────────────────

def _is_zero_issue_placeholder(path: Path) -> bool:
    """True when sprint-N.json is a placeholder with no tickets."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    tickets = data.get("tickets")
    return isinstance(tickets, list) and len(tickets) == 0


# ── discovery ─────────────────────────────────────────────────────────────────

def discover_sprint_numbers(sprints_dir: Path) -> set[int]:
    """Integer sprint numbers seen among the top-level files (archive/ excluded)."""
    nums: set[int] = set()
    if not sprints_dir.exists():
        return nums
    for p in sprints_dir.iterdir():
        if p.is_dir():
            continue  # skips archive/ and any sprint-N/ screenshot dirs
        m = _SPRINT_NUM_RE.match(p.name)
        if m:
            nums.add(int(m.group(1)))
    return nums


# ── planning + execution ──────────────────────────────────────────────────────

def plan_cleanup(
    sprints_dir: Path,
    *,
    has_summary_issue: Callable[[int], bool] = lambda n: False,
    running_check: Optional[Callable[[Path, int], bool]] = None,
) -> list[Path]:
    """Return the list of files that would be archived (sorted, stable)."""
    plan: list[Path] = []
    for n in sorted(discover_sprint_numbers(sprints_dir)):
        if not is_sprint_finished(
            sprints_dir, n,
            has_summary_issue=has_summary_issue,
            running_check=running_check,
        ):
            continue
        plan_file = sprints_dir / f"sprint-{n}-plan.json"
        if plan_file.exists():
            plan.append(plan_file)
        placeholder = sprints_dir / f"sprint-{n}.json"
        if placeholder.exists() and _is_zero_issue_placeholder(placeholder):
            plan.append(placeholder)
        state_file = sprints_dir / f"sprint-{n}-state.json"
        if state_file.exists():
            plan.append(state_file)
    return plan


def _count_kept(sprints_dir: Path) -> int:
    """Count top-level files remaining in sprints_dir (archive/ contents excluded)."""
    if not sprints_dir.exists():
        return 0
    return sum(1 for p in sprints_dir.iterdir() if p.is_file())


def run_cleanup(
    sprints_dir: Path,
    *,
    dry_run: bool = False,
    has_summary_issue: Callable[[int], bool] = lambda n: False,
    running_check: Optional[Callable[[Path, int], bool]] = None,
) -> dict:
    """Archive finished-sprint runtime files. Returns {archived, kept_count, dry_run}.

    Idempotent: a second run finds the files already moved and archives nothing.
    """
    sprints_dir = Path(sprints_dir)
    plan = plan_cleanup(
        sprints_dir,
        has_summary_issue=has_summary_issue,
        running_check=running_check,
    )

    if dry_run:
        archived = [p.name for p in plan]
        # Files that *would* remain after the planned moves.
        kept = max(_count_kept(sprints_dir) - len(archived), 0)
        return {"archived": archived, "kept_count": kept, "dry_run": True}

    archive_dir = sprints_dir / ARCHIVE_DIRNAME
    archived: list[str] = []
    if plan:
        archive_dir.mkdir(parents=True, exist_ok=True)
    for src in plan:
        dst = archive_dir / src.name
        shutil.move(str(src), str(dst))
        archived.append(src.name)

    return {
        "archived": archived,
        "kept_count": _count_kept(sprints_dir),
        "dry_run": False,
    }


# ── CLI helpers ───────────────────────────────────────────────────────────────

def _projects_base() -> Path:
    return Path(os.environ.get("COMMANDER_PROJECTS_BASE", str(Path.home() / "dev")))


def resolve_sprints_dir(project: str) -> Path:
    """Resolve a project identifier to its .commander/sprints directory.

    Accepts ``owner/repo`` or a bare slug; supports nested and flat layouts
    (both keep .commander at the project root).
    """
    slug = project.split("/")[-1] if "/" in project else project
    return _projects_base() / slug / ".commander" / "sprints"


def _github_summary_issue_checker(project: str) -> Callable[[int], bool]:
    """Best-effort set of sprint numbers with a posted summary issue (via gh).

    Returns a predicate that is always safe — on any failure it reports no
    summary issues, so finish detection falls back to the local summary md.
    """
    repo = project if "/" in project else None
    if repo is None:
        return lambda n: False
    nums: set[int] = set()
    title_re = re.compile(r"^Sprint\s+(\d+)\b", re.IGNORECASE)
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "--repo", repo,
             "--label", "sprint-summary", "--state", "all",
             "--json", "title", "--limit", "200"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            for iss in json.loads(r.stdout or "[]"):
                m = title_re.match(iss.get("title", "") or "")
                if m:
                    nums.add(int(m.group(1)))
    except Exception:
        pass
    return lambda n: n in nums


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Archive stale per-sprint runtime files for finished sprints.")
    ap.add_argument("--project", required=True,
                    help="Project identifier (owner/repo or slug).")
    ap.add_argument("--dry-run", action="store_true",
                    help="List files that would be archived without moving anything.")
    args = ap.parse_args(argv)

    sprints_dir = resolve_sprints_dir(args.project)
    if not sprints_dir.exists():
        sys.stdout.write(str(f"No sprints directory found at {sprints_dir}") + "\n")
        return 1

    has_summary_issue = _github_summary_issue_checker(args.project)
    result = run_cleanup(
        sprints_dir,
        dry_run=args.dry_run,
        has_summary_issue=has_summary_issue,
    )

    verb = "Would archive" if args.dry_run else "Archived"
    if result["archived"]:
        sys.stdout.write(str(f"{verb} {len(result['archived'])} file(s) "
              f"-> {sprints_dir / ARCHIVE_DIRNAME}:") + "\n")
        for name in result["archived"]:
            sys.stdout.write(str(f"  {name}") + "\n")
    else:
        sys.stdout.write(str(f"{verb} 0 files — nothing to do.") + "\n")
    sys.stdout.write(str(f"Kept {result['kept_count']} sprint file(s) in place.") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
