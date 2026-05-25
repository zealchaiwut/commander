#!/usr/bin/env python3
"""migrate_project_layout.py — Convert an existing flat project layout to nested.

Flat layout (before):
    ~/dev/<project>/        # main clone
    ~/dev/<project>-coder/  # coder agent clone
    ~/dev/<project>-tester/ # tester agent clone

Nested layout (after):
    ~/dev/<project>/
      main/              # primary working clone
      coder/             # coder agent clone
      tester/            # tester agent clone
      .commander/        # sprint config, outside any clone

Usage:
    python3 scripts/migrate_project_layout.py <project-name>
            [--projects-dir ~/dev]   # default: ~/dev
            [--dry-run]              # show what would happen without doing it
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(*cmd, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, cwd=str(cwd) if cwd else None)


def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def warn(msg: str) -> None:
    print(f"  WARNING: {msg}", file=sys.stderr)


def error(msg: str) -> None:
    print(f"  ERROR: {msg}", file=sys.stderr)


# ── Detection ─────────────────────────────────────────────────────────────────

def detect_layout(project_name: str, projects_dir: Path) -> str:
    """Return 'nested', 'flat', or 'unknown'."""
    project_root = projects_dir / project_name
    if not project_root.exists():
        return "unknown"
    main_dir = project_root / "main"
    if main_dir.exists() and (main_dir / ".git").exists():
        return "nested"
    if (project_root / ".git").exists():
        return "flat"
    return "unknown"


# ── Move helpers ──────────────────────────────────────────────────────────────

def _move_contents(src: Path, dst: Path, dry_run: bool) -> None:
    """Move a directory by renaming it (preserves git history)."""
    if dry_run:
        info(f"[dry-run] mv {src} → {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)


# ── Migration steps ───────────────────────────────────────────────────────────

def migrate(project_name: str, projects_dir: Path, dry_run: bool) -> bool:
    """Migrate flat layout to nested. Returns True on success."""
    project_root = projects_dir / project_name
    coder_flat  = projects_dir / f"{project_name}-coder"
    tester_flat = projects_dir / f"{project_name}-tester"

    main_nested   = project_root / "main"
    coder_nested  = project_root / "coder"
    tester_nested = project_root / "tester"
    commander_src = project_root / ".commander"      # currently inside main clone
    commander_dst = project_root / ".commander"       # target: project root (same path if main IS project root)

    # ── Pre-flight checks ──────────────────────────────────────────────────────
    errors = []
    if not project_root.exists():
        errors.append(f"Project root does not exist: {project_root}")
    elif not (project_root / ".git").exists():
        errors.append(f"Expected a git repo at {project_root} but no .git found")

    if not coder_flat.exists():
        warn(f"Coder clone not found at {coder_flat} — will skip moving it")
    if not tester_flat.exists():
        warn(f"Tester clone not found at {tester_flat} — will skip moving it")

    if main_nested.exists():
        errors.append(f"Nested main/ already exists at {main_nested} — already migrated?")

    if errors:
        for e in errors:
            error(e)
        return False

    print(f"\nMigrating '{project_name}' from flat → nested layout...")
    if dry_run:
        print("  (dry-run — no changes will be made)\n")

    # ── Step 1: Create project root as the new project container ──────────────
    # The current project_root IS the main clone. We need to move its contents
    # into a new main/ subdirectory. We do this by:
    #   a) creating a temp directory alongside
    #   b) renaming project_root → temp
    #   c) creating project_root/ fresh
    #   d) renaming temp → project_root/main/
    # This avoids moving a directory into itself.

    temp_dir = projects_dir / f"{project_name}.__migrate_tmp__"
    if temp_dir.exists():
        errors.append(f"Temp directory already exists: {temp_dir} — clean it up and retry")
        for e in errors:
            error(e)
        return False

    info(f"Moving {project_root} → {temp_dir}")
    if not dry_run:
        project_root.rename(temp_dir)
        project_root.mkdir(parents=True)

    info(f"Moving {temp_dir} → {main_nested}")
    if not dry_run:
        temp_dir.rename(main_nested)
    ok("main/ clone in place")

    # ── Step 2: Move coder and tester flat clones ─────────────────────────────
    for src, dst in [(coder_flat, coder_nested), (tester_flat, tester_nested)]:
        if src.exists():
            info(f"Moving {src} → {dst}")
            if not dry_run:
                src.rename(dst)
            ok(f"{dst.name}/ clone in place")
        else:
            info(f"Skipping {src} (not found)")

    # ── Step 3: Move .commander/ out of main/ to project root ─────────────────
    commander_in_main = main_nested / ".commander"
    commander_at_root = project_root / ".commander"

    if commander_in_main.exists():
        if commander_at_root.exists() and not dry_run:
            warn(f".commander/ already exists at {commander_at_root} — skipping move")
        else:
            info(f"Moving {commander_in_main} → {commander_at_root}")
            if not dry_run:
                commander_in_main.rename(commander_at_root)
            ok(".commander/ moved to project root")
    else:
        info(f"No .commander/ inside main/ — skipping (may already be at root or absent)")

    # ── Step 4: Update sprint.yaml worktree paths ─────────────────────────────
    sprint_yaml = commander_at_root / "sprint.yaml"
    if not sprint_yaml.exists() and dry_run:
        sprint_yaml = commander_in_main / "sprint.yaml"  # hasn't moved yet in dry-run

    if sprint_yaml.exists() or dry_run:
        _update_sprint_yaml(sprint_yaml, project_name, projects_dir, coder_nested, tester_nested, dry_run)
    else:
        info("No sprint.yaml found — skipping worktree path update")

    # ── Step 5: Update .commander path inside main clone's .gitignore ─────────
    # After moving .commander out, the main clone no longer owns it; clean up.
    gitignore = main_nested / ".gitignore"
    if gitignore.exists() and not dry_run:
        content = gitignore.read_text()
        if ".commander/logs/" in content:
            # The gitignore entries were for the old location; they now live in
            # the project root (which is not a git repo), so they're harmless to
            # leave. No action needed.
            pass

    print()
    print("Migration complete.")
    print()
    print("Verification commands:")
    print(f"  ls {project_root}/")
    print(f"  ls {main_nested}/")
    if coder_flat.exists() or coder_nested.exists():
        print(f"  ls {coder_nested}/")
    if tester_flat.exists() or tester_nested.exists():
        print(f"  ls {tester_nested}/")
    print(f"  cat {commander_at_root}/sprint.yaml")
    print()
    print("Run sprint manager from project root or any clone:")
    print(f"  cd {project_root} && python3 ~/dev/commander/prd/dashboard/scripts/sprint_manager.py <sprint-label>")
    print(f"  cd {main_nested} && python3 ~/dev/commander/prd/dashboard/scripts/sprint_manager.py <sprint-label>")

    return True


def _update_sprint_yaml(
    sprint_yaml: Path,
    project_name: str,
    projects_dir: Path,
    coder_nested: Path,
    tester_nested: Path,
    dry_run: bool,
) -> None:
    """Rewrite worktree paths in sprint.yaml to point to nested locations."""
    if dry_run:
        info(f"[dry-run] Would update worktree paths in {sprint_yaml}")
        info(f"  coder  → {coder_nested}")
        info(f"  tester → {tester_nested}")
        return

    try:
        text = sprint_yaml.read_text(encoding="utf-8")
    except OSError as e:
        warn(f"Cannot read {sprint_yaml}: {e}")
        return

    # Replace old flat paths with new nested paths
    old_coder  = str(projects_dir / f"{project_name}-coder")
    old_tester = str(projects_dir / f"{project_name}-tester")
    new_coder  = str(coder_nested)
    new_tester = str(tester_nested)

    updated = text.replace(old_coder, new_coder).replace(old_tester, new_tester)

    if updated == text:
        info("sprint.yaml worktree paths already up to date (or not found — check manually)")
    else:
        sprint_yaml.write_text(updated, encoding="utf-8")
        ok(f"sprint.yaml updated: coder → {new_coder}")
        ok(f"sprint.yaml updated: tester → {new_tester}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a flat Commander project layout to nested"
    )
    parser.add_argument("project_name", help="Project name (directory name under projects-dir)")
    parser.add_argument(
        "--projects-dir",
        default=str(Path.home() / "dev"),
        help="Base directory containing projects (default: ~/dev)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making any changes",
    )
    args = parser.parse_args()

    projects_dir = Path(args.projects_dir).expanduser()
    project_name = args.project_name

    layout = detect_layout(project_name, projects_dir)

    if layout == "nested":
        print(f"Project '{project_name}' is already using the nested layout.")
        print(f"  {projects_dir / project_name}/main/   ✓")
        sys.exit(0)

    if layout == "unknown":
        print(f"ERROR: Cannot detect layout for '{project_name}' in {projects_dir}.")
        print("  Expected either a git repo at the project root (flat) or main/ subdirectory (nested).")
        sys.exit(1)

    # layout == 'flat'
    success = migrate(project_name, projects_dir, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
