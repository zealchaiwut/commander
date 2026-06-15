#!/usr/bin/env python3
"""Prune old pytest files, keeping the N most recently touched in git history.

Scans ``tests/test_*.py`` under a repo root, ranks each file by the timestamp
of its most recent git commit, and removes everything beyond *keep* newest
files. Intended for repos where one-test-per-ticket files accumulate.

Usage:
    python scripts/prune_test_files.py --repo-root /path/to/clone [--keep 100] [--dry-run]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _git_last_commit_ts(repo_root: Path, rel_path: str) -> int:
    """Unix timestamp of the latest commit touching *rel_path*, or 0 if unknown."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", rel_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return 0
        return int(out.stdout.strip())
    except (OSError, ValueError):
        return 0


def list_test_files(repo_root: Path) -> list[Path]:
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(tests_dir.glob("test_*.py"))


def rank_test_files(repo_root: Path, keep: int = 100) -> dict:
    """Return keep/remove lists ranked by git recency (newest first)."""
    repo_root = repo_root.resolve()
    files = list_test_files(repo_root)
    ranked: list[tuple[int, Path]] = []
    for path in files:
        rel = path.relative_to(repo_root).as_posix()
        ranked.append((_git_last_commit_ts(repo_root, rel), path))
    ranked.sort(key=lambda item: (item[0], item[1].name), reverse=True)

    keep_n = max(int(keep), 0)
    kept = [p for _, p in ranked[:keep_n]]
    removed = [p for _, p in ranked[keep_n:]]
    return {
        "repo_root": str(repo_root),
        "keep_limit": keep_n,
        "kept": [p.relative_to(repo_root).as_posix() for p in kept],
        "remove": [p.relative_to(repo_root).as_posix() for p in removed],
        "kept_count": len(kept),
        "remove_count": len(removed),
        "total_count": len(files),
    }


def run_prune(repo_root: Path, *, keep: int = 100, dry_run: bool = True) -> dict:
    plan = rank_test_files(repo_root, keep=keep)
    deleted: list[str] = []
    failed: list[str] = []
    if not dry_run:
        root = repo_root.resolve()
        for rel in plan["remove"]:
            path = root / rel
            try:
                path.unlink(missing_ok=False)
                deleted.append(rel)
            except OSError:
                failed.append(rel)
    return {
        **plan,
        "dry_run": dry_run,
        "deleted": deleted,
        "failed": failed,
    }


def _resolve_repo_root(project_root: Path) -> Path:
    """Pick the clone that actually holds tests/ (nested uat wins over main)."""
    project_root = project_root.resolve()
    for candidate in (project_root / "uat", project_root, Path(__file__).resolve().parents[1]):
        if (candidate / "tests").is_dir() and (candidate / ".git").exists():
            return candidate
    if (project_root / "tests").is_dir():
        return project_root
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None, help="Git repo root (default: repo containing this script)")
    parser.add_argument("--project", type=str, default=None, help="Project slug; resolves ~/dev/<slug> with uat/ fallback")
    parser.add_argument("--keep", type=int, default=100, help="Number of newest test files to keep")
    parser.add_argument("--apply", action="store_true", help="Delete files (default is dry-run preview)")
    args = parser.parse_args(argv)

    if args.repo_root:
        root = args.repo_root.resolve()
    elif args.project:
        slug = args.project.split("/")[-1]
        root = _resolve_repo_root(Path.home() / "dev" / slug)
    else:
        root = _resolve_repo_root(Path(__file__).resolve().parents[1])

    result = run_prune(root, keep=args.keep, dry_run=not args.apply)
    if result["dry_run"]:
        print(f"Would remove {result['remove_count']} of {result['total_count']} test files (keeping {result['kept_count']}).")
        for rel in result["remove"][:20]:
            print(f"  - {rel}")
        if result["remove_count"] > 20:
            print(f"  … and {result['remove_count'] - 20} more")
    else:
        print(f"Removed {len(result['deleted'])} file(s); kept {result['kept_count']}.")
        if result["failed"]:
            print("Failed:", ", ".join(result["failed"]), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
