#!/usr/bin/env python3
"""Maintenance helper: copy stray estimate JSONs to the canonical project-root location.

Scans all known clone-local .commander/estimates/ directories for a project and
copies any issue-N.json files to <project-root>/.commander/estimates/ when the
canonical file is missing.  Never re-runs estimation (copy only).  Idempotent.

Usage:
    python3 scripts/collect_stray_estimates.py --project <owner/repo>
    python3 scripts/collect_stray_estimates.py --project <owner/repo> --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_PROJECTS_BASE = Path.home() / "dev"


def _project_root(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _clone_dirs(project_root: Path) -> list[Path]:
    """Candidate clone directories that may have a local .commander/."""
    candidates = [
        project_root,
        project_root / "coder",
        project_root / "tester",
        project_root / "uat",
        project_root / "main",
        _PROJECTS_BASE / f"{project_root.name}-coder",
        _PROJECTS_BASE / f"{project_root.name}-tester",
        _PROJECTS_BASE / f"{project_root.name}-uat",
    ]
    return [c for c in candidates if c.is_dir()]


def collect_stray_estimates(repo: str, *, dry_run: bool = False) -> int:
    """Copy stray estimate JSONs to canonical location.  Returns count copied."""
    project_root = _project_root(repo)
    canonical_estimates = project_root / ".commander" / "estimates"

    if dry_run:
        sys.stdout.write(
            f"[dry-run] canonical estimates dir: {canonical_estimates}\n"
        )

    canonical_estimates.mkdir(parents=True, exist_ok=True)

    copied = 0
    for clone in _clone_dirs(project_root):
        local_estimates = clone / ".commander" / "estimates"
        if not local_estimates.is_dir():
            continue
        if local_estimates.resolve() == canonical_estimates.resolve():
            continue
        for src in sorted(local_estimates.glob("issue-*.json")):
            dst = canonical_estimates / src.name
            if dst.exists():
                continue
            if dry_run:
                sys.stdout.write(f"  [dry-run] would copy {src} → {dst}\n")
            else:
                shutil.copy2(src, dst)
                sys.stdout.write(f"  Copied {src.name}: {src} → {dst}\n")
            copied += 1

    if copied == 0:
        sys.stdout.write("No stray estimate files found (or all already canonical).\n")
    else:
        verb = "Would copy" if dry_run else "Copied"
        sys.stdout.write(f"{verb} {copied} estimate file(s) to {canonical_estimates}\n")

    return copied


def main() -> None:
    p = argparse.ArgumentParser(
        description="Copy stray estimate JSONs to the canonical project-root .commander/estimates/ location."
    )
    p.add_argument("--project", required=True, help="owner/repo or repo slug")
    p.add_argument("--dry-run", action="store_true", help="Show what would be copied without writing")
    args = p.parse_args()

    collect_stray_estimates(args.project, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
