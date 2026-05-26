#!/usr/bin/env python3
"""migrate_repo_structure.py — Restructure the Commander repo layout.

Moves files from the current dashboard-anchored layout to a clean top-level
layout where each concern has its own home:

  Before (current):
    commander/
    ├── dashboard/          # everything lives here
    │   ├── server.py
    │   ├── db.py
    │   ├── hooks/
    │   ├── scripts/        # CLI tools + sprint_manager.py
    │   ├── static/
    │   ├── tests/
    │   ├── .claude/
    │   └── .github/
    ├── docs/
    ├── scripts/            (empty)
    └── projects/

  After:
    commander/
    ├── apps/dashboard/     # FastAPI server, static, db
    ├── services/
    │   └── sprint_manager/ # sprint_manager.py + helpers
    ├── scripts/            # shared CLI tools (create_ticket.py, etc.)
    ├── hooks/              # Claude hooks
    ├── docs/               # documentation
    ├── projects/           # external project configs
    ├── .claude/            # Claude config (merged from dashboard/.claude/)
    ├── CLAUDE.md
    └── README.md

Usage:
    # Preview every planned move and path rewrite (no changes):
    python3 scripts/migrate_repo_structure.py --dry-run

    # Run the migration (asks for confirmation):
    python3 scripts/migrate_repo_structure.py

    # Skip confirmation prompt (for automation):
    python3 scripts/migrate_repo_structure.py --yes

Run from the repo root (the directory that contains dashboard/).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


# ── Helpers ───────────────────────────────────────────────────────────────────

def _info(msg: str) -> None:
    print(f"  {msg}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _warn(msg: str) -> None:
    print(f"  WARNING: {msg}", file=sys.stderr)


def _error(msg: str) -> None:
    print(f"  ERROR: {msg}", file=sys.stderr)


def _run(*cmd: str, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        check=check,
    )


def _git_mv(src: Path, dst: Path, repo_root: Path, dry_run: bool) -> bool:
    """Move a file or directory using git mv.  Returns True on success."""
    # Make src/dst relative to repo root for git mv
    try:
        rel_src = src.relative_to(repo_root)
        rel_dst = dst.relative_to(repo_root)
    except ValueError:
        _warn(f"Cannot make paths relative: {src} / {dst}")
        return False

    if dry_run:
        _info(f"[dry-run] git mv {rel_src} → {rel_dst}")
        return True

    dst.parent.mkdir(parents=True, exist_ok=True)
    result = _run("git", "mv", str(rel_src), str(rel_dst), cwd=repo_root, check=False)
    if result.returncode != 0:
        _warn(f"git mv failed ({rel_src} → {rel_dst}): {result.stderr.strip()}")
        return False
    _ok(f"git mv {rel_src} → {rel_dst}")
    return True


# ── Detection: is this repo already migrated? ────────────────────────────────

def _is_migrated(repo_root: Path) -> bool:
    """Return True if the repo already has the new layout (apps/dashboard/ exists)."""
    return (repo_root / "apps" / "dashboard").exists()


def _is_pre_migration(repo_root: Path) -> bool:
    """Return True if the repo has the old layout (dashboard/ at root)."""
    return (repo_root / "dashboard").exists()


# ── Build the move plan ───────────────────────────────────────────────────────

def _build_move_plan(repo_root: Path) -> list[tuple[Path, Path]]:
    """Return list of (src, dst) pairs for all git mv operations."""
    dashboard = repo_root / "dashboard"
    moves: list[tuple[Path, Path]] = []

    # 1. dashboard/ → apps/dashboard/
    moves.append((dashboard, repo_root / "apps" / "dashboard"))

    # NOTE: After step 1, sub-paths like dashboard/scripts become
    # apps/dashboard/scripts.  The script handles sub-directory moves
    # as a SEPARATE pass BEFORE the top-level dashboard/ move, since
    # git mv can't rename both parent and child in one shot.

    return moves


def _build_subdir_moves(repo_root: Path) -> list[tuple[Path, Path]]:
    """Return sub-directory moves that happen WITHIN apps/dashboard/ after the top move.

    These are performed after dashboard/ is already at apps/dashboard/.
    """
    apps_dash = repo_root / "apps" / "dashboard"
    moves: list[tuple[Path, Path]] = []

    # hooks/ → repo root hooks/
    if (apps_dash / "hooks").exists():
        moves.append((apps_dash / "hooks", repo_root / "hooks"))

    # scripts/sprint_manager.py → services/sprint_manager/sprint_manager.py
    sm_src = apps_dash / "scripts" / "sprint_manager.py"
    if sm_src.exists():
        moves.append((sm_src, repo_root / "services" / "sprint_manager" / "sprint_manager.py"))

    # dashboard/scripts/ → scripts/ (shared CLI tools)
    # Move each file from apps/dashboard/scripts/ to scripts/
    dash_scripts = apps_dash / "scripts"
    if dash_scripts.exists():
        for f in sorted(dash_scripts.iterdir()):
            if f.is_file():
                moves.append((f, repo_root / "scripts" / f.name))

    # .claude/ contents → repo root .claude/
    dash_claude = apps_dash / ".claude"
    if dash_claude.exists():
        for item in sorted(dash_claude.iterdir()):
            dst_item = repo_root / ".claude" / item.name
            if item.is_dir():
                for sub in sorted(item.rglob("*")):
                    if sub.is_file():
                        rel = sub.relative_to(dash_claude)
                        moves.append((sub, repo_root / ".claude" / rel))
            elif item.is_file():
                moves.append((item, repo_root / ".claude" / item.name))

    # .github/ → repo root .github/
    dash_github = apps_dash / ".github"
    if dash_github.exists():
        for item in sorted(dash_github.rglob("*")):
            if item.is_file():
                rel = item.relative_to(dash_github)
                moves.append((item, repo_root / ".github" / rel))

    # README.md → docs/dashboard-readme.md (preserve old README, don't overwrite root)
    dash_readme = apps_dash / "README.md"
    if dash_readme.exists():
        moves.append((dash_readme, repo_root / "docs" / "dashboard-readme.md"))

    # CLAUDE.md → docs/dashboard-claude.md (preserve, don't overwrite root CLAUDE.md)
    dash_claude_md = apps_dash / "CLAUDE.md"
    if dash_claude_md.exists():
        moves.append((dash_claude_md, repo_root / "docs" / "dashboard-claude.md"))

    return moves


# ── Path rewrite definitions ──────────────────────────────────────────────────

# Map of (old_pattern, new_value) for path rewrites in code and configs.
# These are applied to files AFTER the git mv operations are complete.
_PATH_REWRITES: list[tuple[str, str]] = [
    # dashboard/scripts/ → scripts/
    ("dashboard/scripts/", "scripts/"),
    # dashboard/hooks/ → hooks/
    ("dashboard/hooks/", "hooks/"),
    # dashboard/.claude/ → .claude/
    ("dashboard/.claude/", ".claude/"),
    # ~/commander/dashboard/ → ~/commander/apps/dashboard/
    ("~/commander/dashboard/", "~/commander/apps/dashboard/"),
    # In sprint_manager docstring references
    ("~/commander/dashboard/scripts/sprint_manager.py",
     "~/commander/services/sprint_manager/sprint_manager.py"),
    # sprint_manager path references in scripts
    ("dashboard/scripts/sprint_manager.py",
     "services/sprint_manager/sprint_manager.py"),
]

# Files to rewrite paths in (relative to repo root, after migration)
_FILES_TO_REWRITE: list[str] = [
    "scripts/sprint_manager.py",
    "scripts/start_feature.py",
    "scripts/finish_feature.py",
    "scripts/install_shell_shortcuts.sh",
    "scripts/start_prd.sh",
    "scripts/start_uat.sh",
    "scripts/setup_uat_env.sh",
    "scripts/release.py",
    "scripts/sprint_init.py",
    "scripts/sprint_planner.py",
    ".claude/settings.json",
    ".claude/commands/ba.md",
    ".claude/commands/coder.md",
    ".claude/commands/tester.md",
    "hooks/tool_used.py",
    "hooks/post_tool_used.py",
    "hooks/agent_finished.py",
    "hooks/run_tool_used.sh",
    "hooks/run_post_tool_used.sh",
    "hooks/run_agent_finished.sh",
    "CLAUDE.md",
    "README.md",
]


def _rewrite_paths_in_file(path: Path, dry_run: bool) -> list[str]:
    """Apply all path rewrites to a file. Returns list of changes made."""
    if not path.exists():
        return []

    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return []

    updated = original
    changes: list[str] = []

    for old, new in _PATH_REWRITES:
        if old in updated:
            count = updated.count(old)
            updated = updated.replace(old, new)
            changes.append(f"{old!r} → {new!r} ({count}x)")

    if changes and not dry_run:
        path.write_text(updated, encoding="utf-8")

    return changes


# ── Shell shortcut update (~/.commander.zsh) ──────────────────────────────────

def _update_commander_zsh(repo_root: Path, dry_run: bool) -> None:
    """Update CMDR_SCRIPTS path in ~/.commander.zsh to point to new scripts/ location."""
    zsh_file = Path.home() / ".commander.zsh"
    if not zsh_file.exists():
        _info("~/.commander.zsh not found — skipping shell shortcut update")
        return

    try:
        content = zsh_file.read_text(encoding="utf-8")
    except OSError as e:
        _warn(f"Cannot read ~/.commander.zsh: {e}")
        return

    # Match CMDR_SCRIPTS="<old-path>" and update to new scripts/ path
    # Old path pattern: anything ending in dashboard/scripts
    new_scripts_path = str(repo_root / "scripts")
    old_pattern = re.compile(r'(CMDR_SCRIPTS=")([^"]*dashboard[/\\]scripts)(")')

    if old_pattern.search(content):
        if dry_run:
            _info(f"[dry-run] ~/.commander.zsh: CMDR_SCRIPTS → {new_scripts_path}")
        else:
            updated = old_pattern.sub(lambda m: m.group(1) + new_scripts_path + m.group(3), content)
            zsh_file.write_text(updated, encoding="utf-8")
            _ok(f"~/.commander.zsh: CMDR_SCRIPTS updated to {new_scripts_path}")
    else:
        _info("~/.commander.zsh: CMDR_SCRIPTS already updated or pattern not found — skipping")


# ── run_server.py generation ──────────────────────────────────────────────────

_RUN_SERVER_CONTENT = '''\
#!/usr/bin/env python3
"""run_server.py — Start the Commander dashboard server.

Usage:
    cd apps/dashboard
    python run_server.py          # listens on port 8000 (or PORT env var)

This is a convenience launcher so the server can be started from apps/dashboard/
without remembering the uvicorn invocation.
"""
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    here = Path(__file__).parent
    venv_uvicorn = here / "venv" / "bin" / "uvicorn"
    port = os.environ.get("PORT", "8000")

    if venv_uvicorn.exists():
        uvicorn_cmd = str(venv_uvicorn)
    else:
        uvicorn_cmd = "uvicorn"

    cmd = [uvicorn_cmd, "server:app", "--host", "0.0.0.0", "--port", port]
    print(f"Starting Commander dashboard on port {port} ...")
    os.chdir(here)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
'''


def _create_run_server(apps_dashboard: Path, dry_run: bool) -> None:
    """Create apps/dashboard/run_server.py if it doesn't exist."""
    target = apps_dashboard / "run_server.py"
    if target.exists():
        _info("apps/dashboard/run_server.py already exists — skipping")
        return
    if dry_run:
        _info("[dry-run] Would create apps/dashboard/run_server.py")
        return
    target.write_text(_RUN_SERVER_CONTENT, encoding="utf-8")
    target.chmod(0o755)
    _ok("Created apps/dashboard/run_server.py")


# ── Rollback script generation ────────────────────────────────────────────────

def _write_rollback_script(repo_root: Path, moves: list[tuple[Path, Path]]) -> Path:
    """Write a rollback shell script that reverses all git mv operations."""
    rollback_path = repo_root / "scripts" / "rollback_repo_structure.sh"

    lines = [
        "#!/usr/bin/env bash",
        "# rollback_repo_structure.sh — Undo the repo restructure migration.",
        "# Generated by scripts/migrate_repo_structure.py",
        "# Run from the repo root.",
        "",
        "set -euo pipefail",
        "",
        "echo 'Rolling back repo restructure migration...'",
        "",
    ]

    # Reverse all moves (dst → src)
    for src, dst in reversed(moves):
        try:
            rel_src = dst.relative_to(repo_root)
            rel_dst = src.relative_to(repo_root)
        except ValueError:
            continue
        lines.append(f"git mv {rel_src!s} {rel_dst!s} 2>/dev/null || echo 'SKIP: {rel_src}'")

    lines += [
        "",
        "echo 'Rollback complete. Review git status, then commit.'",
    ]

    rollback_path.parent.mkdir(parents=True, exist_ok=True)
    rollback_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rollback_path.chmod(0o755)
    return rollback_path


# ── Main migration logic ──────────────────────────────────────────────────────

def migrate(repo_root: Path, dry_run: bool) -> bool:
    """Execute (or preview) the full migration. Returns True on success."""

    dashboard = repo_root / "dashboard"

    # ── Pre-flight ────────────────────────────────────────────────────────────
    errors: list[str] = []
    if not (repo_root / ".git").exists():
        errors.append(f"No .git directory found at {repo_root} — run from the repo root")
    if not dashboard.exists():
        errors.append(f"dashboard/ not found at {repo_root} — already migrated or wrong directory?")
    if errors:
        for e in errors:
            _error(e)
        return False

    # ── Phase 1: Move dashboard/ → apps/dashboard/ ────────────────────────────
    print("\nPhase 1: Move dashboard/ → apps/dashboard/")
    apps_dir = repo_root / "apps"
    if not dry_run:
        apps_dir.mkdir(exist_ok=True)

    top_move = (dashboard, repo_root / "apps" / "dashboard")
    if dry_run:
        _info(f"[dry-run] git mv dashboard → apps/dashboard")
    else:
        if not _git_mv(top_move[0], top_move[1], repo_root, dry_run=False):
            _error("Failed to move dashboard/ → apps/dashboard/ — aborting")
            return False

    apps_dashboard = repo_root / "apps" / "dashboard"

    # ── Phase 2: Move sub-directories out of apps/dashboard/ ─────────────────
    print("\nPhase 2: Move sub-directories to their new homes")
    sub_moves: list[tuple[Path, Path]] = []

    # hooks/ → repo root hooks/
    hooks_src = apps_dashboard / "hooks"
    hooks_dst = repo_root / "hooks"
    if (hooks_src if not dry_run else dashboard / "hooks").exists() or dry_run:
        sub_moves.append((hooks_src, hooks_dst))

    # scripts/* → scripts/ at repo root (and sprint_manager.py → services/)
    dash_scripts = apps_dashboard / "scripts"
    # For dry-run mode check old location
    _scripts_check = dashboard / "scripts" if dry_run else dash_scripts
    if _scripts_check.exists() or dry_run:
        # sprint_manager.py → services/sprint_manager/
        sm_src = dash_scripts / "sprint_manager.py"
        sm_dst = repo_root / "services" / "sprint_manager" / "sprint_manager.py"
        sub_moves.append((sm_src, sm_dst))

        # All other scripts → scripts/
        scripts_root = repo_root / "scripts"
        if dry_run:
            # list from original location
            scripts_dir_for_listing = dashboard / "scripts"
        else:
            scripts_dir_for_listing = dash_scripts

        if scripts_dir_for_listing.exists():
            for f in sorted(scripts_dir_for_listing.iterdir()):
                if f.is_file() and f.name != "sprint_manager.py":
                    sub_moves.append((dash_scripts / f.name, scripts_root / f.name))

    # .claude/ contents → repo root .claude/
    dash_claude = apps_dashboard / ".claude"
    _claude_check = dashboard / ".claude" if dry_run else dash_claude
    if _claude_check.exists() or dry_run:
        _claude_dir = dashboard / ".claude" if dry_run else dash_claude
        if _claude_dir.exists():
            for item in sorted(_claude_dir.iterdir()):
                if item.is_dir():
                    for sub in sorted(item.rglob("*")):
                        if sub.is_file():
                            rel = sub.relative_to(_claude_dir)
                            sub_moves.append(
                                (dash_claude / rel, repo_root / ".claude" / rel)
                            )
                elif item.is_file() and item.name != "settings.local.json":
                    sub_moves.append(
                        (dash_claude / item.name, repo_root / ".claude" / item.name)
                    )

    # .github/ → repo root .github/
    dash_github = apps_dashboard / ".github"
    _github_check = dashboard / ".github" if dry_run else dash_github
    if _github_check.exists() or dry_run:
        _github_dir = dashboard / ".github" if dry_run else dash_github
        if _github_dir.exists():
            for item in sorted(_github_dir.rglob("*")):
                if item.is_file():
                    rel = item.relative_to(_github_dir)
                    sub_moves.append(
                        (dash_github / rel, repo_root / ".github" / rel)
                    )

    # README.md → docs/dashboard-readme.md
    dash_readme = apps_dashboard / "README.md"
    _readme_check = dashboard / "README.md" if dry_run else dash_readme
    if _readme_check.exists():
        docs_dir = repo_root / "docs"
        sub_moves.append((dash_readme, docs_dir / "dashboard-readme.md"))

    # CLAUDE.md → docs/dashboard-claude.md
    dash_claude_md = apps_dashboard / "CLAUDE.md"
    _claude_md_check = dashboard / "CLAUDE.md" if dry_run else dash_claude_md
    if _claude_md_check.exists():
        docs_dir = repo_root / "docs"
        sub_moves.append((dash_claude_md, docs_dir / "dashboard-claude.md"))

    # Execute sub-directory moves
    for src, dst in sub_moves:
        _git_mv(src, dst, repo_root, dry_run=dry_run)

    # ── Phase 3: Create run_server.py ─────────────────────────────────────────
    print("\nPhase 3: Create apps/dashboard/run_server.py")
    _create_run_server(apps_dashboard if not dry_run else repo_root / "apps" / "dashboard", dry_run)

    # ── Phase 4: Rewrite hardcoded paths ─────────────────────────────────────
    print("\nPhase 4: Rewrite hardcoded paths")
    for rel_path in _FILES_TO_REWRITE:
        target = repo_root / rel_path
        changes = _rewrite_paths_in_file(target, dry_run=dry_run)
        if changes:
            if dry_run:
                _info(f"[dry-run] {rel_path}:")
                for c in changes:
                    _info(f"    {c}")
            else:
                _info(f"{rel_path}: {len(changes)} rewrite(s)")

    # ── Phase 5: Update ~/.commander.zsh ─────────────────────────────────────
    print("\nPhase 5: Update ~/.commander.zsh shell shortcuts")
    _update_commander_zsh(repo_root, dry_run)

    # ── Phase 6: Write rollback script ───────────────────────────────────────
    all_moves = [top_move] + sub_moves
    if not dry_run:
        print("\nPhase 6: Write rollback script")
        rollback_path = _write_rollback_script(repo_root, all_moves)
        _ok(f"Rollback script written to {rollback_path.relative_to(repo_root)}")
    else:
        print("\nPhase 6: [dry-run] Would write rollback script to scripts/rollback_repo_structure.sh")

    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restructure the Commander repo (dashboard/ → apps/dashboard/ + extracted concerns)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every planned git mv and path rewrite; make no changes on disk.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the confirmation prompt (for automation).",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Path to the repo root (default: CWD). Must contain dashboard/ and .git/.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path.cwd()

    # ── Idempotency check ─────────────────────────────────────────────────────
    if _is_migrated(repo_root):
        print("Repo already migrated (apps/dashboard/ exists) — nothing to do.")
        sys.exit(0)

    if not _is_pre_migration(repo_root):
        print(f"ERROR: Neither dashboard/ nor apps/dashboard/ found in {repo_root}.")
        print("Run this script from the commander repo root.")
        sys.exit(1)

    # ── Print plan header ─────────────────────────────────────────────────────
    print("Commander repo restructure migration")
    print(f"  Repo root : {repo_root}")
    print(f"  Mode      : {'DRY-RUN (no changes)' if args.dry_run else 'LIVE'}")
    print()

    if args.dry_run:
        print("=== Dry-run: planned operations ===")
        migrate(repo_root, dry_run=True)
        print()
        print("Dry-run complete. No changes were made.")
        sys.exit(0)

    # ── Confirmation prompt (skipped with --yes) ──────────────────────────────
    if not args.yes:
        print("This will permanently restructure the repo layout using git mv.")
        print("Make sure you have a backup or are on a feature branch.")
        print()
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            sys.exit(0)

    # ── Run migration ─────────────────────────────────────────────────────────
    success = migrate(repo_root, dry_run=False)

    if success:
        print()
        print("Migration complete.")
        print()
        print("Next steps:")
        print("  1. Review git status to confirm staged renames:")
        print("       git status")
        print("  2. Start the dashboard from its new location:")
        print("       cd apps/dashboard && python run_server.py")
        print("  3. Commit the migration:")
        print("       git commit -m 'refactor: restructure repo — dashboard/ → apps/dashboard/'")
        sys.exit(0)
    else:
        print()
        print("Migration failed. See errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
