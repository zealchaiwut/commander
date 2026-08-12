#!/usr/bin/env python3
"""One-shot backfill: migrate .commander/sprints/*.json files into Neon.

Scans .commander/sprints/ for sprint state JSON files and inserts any that are
not already present in Neon. Safe to re-run — existing sprints are skipped.

Usage:
    python scripts/migrate_sprints_to_neon.py
    python scripts/migrate_sprints_to_neon.py --dry-run
    python scripts/migrate_sprints_to_neon.py --project zealchaiwut/commander
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT = SCRIPTS_DIR.parent.parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        # Try canonical locations in precedence order.
        for candidate in [
            REPO_ROOT / ".env",
            REPO_ROOT / "apps" / "dashboard" / ".env",
        ]:
            if candidate.exists():
                load_dotenv(candidate)
                break
    except ImportError:
        pass


def _find_sprints_dir() -> Path:
    """Walk up from CWD to find .commander/sprints/."""
    here = Path.cwd()
    for p in [here, *here.parents]:
        candidate = p / ".commander" / "sprints"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not find .commander/sprints/ — run from inside the project directory."
    )


def _read_goal(sprints_dir: Path, sprint_label: str) -> str:
    """Return goal text from a companion -goal.txt file, or fall back to label."""
    goal_file = sprints_dir / f"{sprint_label}-goal.txt"
    if goal_file.exists():
        try:
            text = goal_file.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    return sprint_label


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill .commander/sprints/*.json files into Neon."
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        help="Only process sprints belonging to this project.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be inserted without writing to the database.",
    )
    args = parser.parse_args()

    _load_dotenv()

    # Ensure repo root is on sys.path so services.sprint_manager.* can be imported.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    # In dry-run mode we never touch the database, so we can skip the import.
    sprint_repo = None
    if not args.dry_run:
        try:
            import services.sprint_manager.sprint_repo as sprint_repo  # type: ignore[assignment]
        except ImportError as exc:
            sys.stderr.write(str(f"Error: could not import sprint_repo: {exc}") + "\n")
            sys.exit(1)

    try:
        sprints_dir = _find_sprints_dir()
    except FileNotFoundError as exc:
        sys.stderr.write(str(f"Error: {exc}") + "\n")
        sys.exit(1)

    json_files = sorted(sprints_dir.glob("*.json"))
    if not json_files:
        sys.stdout.write(str(f"No JSON files found in {sprints_dir}") + "\n")
        sys.exit(0)

    created = 0
    skipped = 0
    errored = 0

    for json_path in json_files:
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            sys.stdout.write(str(f"  ERROR  {json_path.name}: {exc}") + "\n")
            errored += 1
            continue

        sprint_label = data.get("sprint_label")
        if not sprint_label:
            # Not a sprint state file — skip silently.
            continue

        project = (data.get("project") or "").strip()
        if not project:
            # Infer from path: <project>/.commander/sprints/
            project = sprints_dir.parent.parent.name

        if args.project and project != args.project:
            continue

        issues = data.get("issues") or []

        if args.dry_run:
            sys.stdout.write(str(f"  WOULD CREATE  {sprint_label} ({len(issues)} tickets) [{project}]") + "\n")
            continue

        goal = _read_goal(sprints_dir, sprint_label)

        # Check if this sprint already exists in Neon.
        try:
            existing = sprint_repo.get_sprint_by_label(sprint_label)  # type: ignore[union-attr]
        except Exception as exc:
            sys.stdout.write(str(f"  ERROR  {sprint_label}: DB error checking existence: {exc}") + "\n")
            errored += 1
            continue

        if existing is not None:
            sys.stdout.write(str(f"  SKIP   {sprint_label} ({len(issues)} tickets) — already in Neon") + "\n")
            skipped += 1
            continue

        try:
            sprint_repo.create_sprint(label=sprint_label, goal=goal, project=project)  # type: ignore[union-attr]
            for position, issue in enumerate(issues):
                issue_number = issue.get("number")
                if issue_number is None:
                    continue
                sprint_repo.add_ticket(  # type: ignore[union-attr]
                    sprint_label=sprint_label,
                    issue_number=issue_number,
                    position=position,
                )
            sys.stdout.write(str(f"  CREATE {sprint_label} ({len(issues)} tickets) [{project}]") + "\n")
            created += 1
        except Exception as exc:
            sys.stdout.write(str(f"  ERROR  {sprint_label}: {exc}") + "\n")
            errored += 1

    sys.stdout.write("\n")
    sys.stdout.write(str(f"{created} created, {skipped} skipped (already in Neon), {errored} errored") + "\n")


if __name__ == "__main__":
    main()
