#!/usr/bin/env python3
"""sprint_init.py — Bootstrap a new project for use with sprint_manager.py.

Creates .commander/sprint.yaml in the target repo root, the three supporting
subdirectories (logs/, sprints/, alerts/), updates .gitignore, validates that
the gh CLI can access the specified GitHub repo, and prints next-step
instructions.

Usage:
    python3 ~/commander/scripts/sprint_init.py \\
        --repo zealchaiwut/fitness-tracker \\
        --repo-root ~/fitness-tracker \\
        --coder-worktree ~/fitness-tracker-coder \\
        --tester-worktree ~/fitness-tracker-tester \\
        [--tester-app-subdir ""]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent

# ── YAML template ─────────────────────────────────────────────────────────────

_YAML_TEMPLATE = """\
repo_name: {repo_name}

worktrees:
  coder:             {coder_worktree}
  tester:            {tester_worktree}
  tester_app_subdir: "{tester_app_subdir}"

paths:
  scripts_dir:  {scripts_dir}
  logs_dir:     .commander/logs/
  sprints_dir:  .commander/sprints/
  alerts_dir:   .commander/alerts/

dashboard:
  api_url: http://localhost:8000

agents:
  coder_prompt_template: |
    Read the issue at {{issue_url}} and implement it following the
    project's branching workflow. Use the BA/coder/tester workflow defined in CLAUDE.md.
    If the issue body contains an '## Attachments' section, download the
    referenced files from the 'attachments' branch before starting — see
    the 'How to read issue attachments' section in CLAUDE.md for the
    exact `gh api` commands.
    If the issue touches frontend/UI, read PRODUCT.md and DESIGN.md first
    to understand design conventions and anti-patterns to avoid.
    TDD WORKFLOW: before writing implementation code, read the
    '## Acceptance Criteria' (or '## Acceptance') section of the issue
    and translate each criterion into a pytest test in the tests/ directory.
    Write the tests first, then implement until all tests pass.
    You must NOT delete, skip, or weaken any test to make it pass —
    every test must be anchored to a specific acceptance criterion and must
    pass because the implementation satisfies it, not because the test was
    softened.
  tester_prompt_template: |
    Read the issue at {{issue_url}} and verify it as a tester following
    the project's testing workflow. Use the workflow defined in CLAUDE.md.
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _validate_repo_access(repo_name: str) -> None:
    """Run gh repo view to confirm access.  Exits with clear error on failure."""
    result = subprocess.run(
        ["gh", "repo", "view", repo_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip()
        sys.exit(
            f"Cannot access GitHub repo '{repo_name}'.\n"
            f"  gh output: {error_msg}\n"
            f"  Make sure `gh auth login` is complete and the repo exists."
        )


def _append_gitignore(repo_root: Path, entries: list[str]) -> None:
    """Append entries to .gitignore if not already present."""
    gitignore = repo_root / ".gitignore"
    existing  = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines_to_add = [e for e in entries if e not in existing.splitlines()]
    if lines_to_add:
        with gitignore.open("a", encoding="utf-8") as f:
            f.write("\n# commander sprint data\n")
            for line in lines_to_add:
                f.write(line + "\n")
        print(f"  Updated .gitignore: added {lines_to_add}")
    else:
        print("  .gitignore already contains sprint entries; skipping")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Bootstrap a new project for use with sprint_manager.py.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--repo",              required=True, help="owner/repo (e.g. zealchaiwut/fitness-tracker)")
    p.add_argument("--repo-root",         required=True, help="Absolute path to the project's git root")
    p.add_argument("--coder-worktree",    required=True, help="Path to the coder git worktree")
    p.add_argument("--tester-worktree",   required=True, help="Path to the tester git worktree")
    p.add_argument(
        "--tester-app-subdir",
        default="",
        help=(
            "Subdirectory within the tester worktree where tests live "
            "(empty = tester root; 'dashboard' for commander style)"
        ),
    )
    args = p.parse_args()

    repo_name        = args.repo.strip()
    repo_root        = Path(args.repo_root).expanduser().resolve()
    coder_worktree   = Path(args.coder_worktree).expanduser().resolve()
    tester_worktree  = Path(args.tester_worktree).expanduser().resolve()
    tester_app_subdir = args.tester_app_subdir.strip()

    # Step 1: validate gh repo access BEFORE writing anything (AC-9)
    print(f"  Validating access to {repo_name} ...")
    _validate_repo_access(repo_name)
    print(f"  Access confirmed.")

    # Step 2: write .commander/sprint.yaml (AC-8)
    commander_dir = repo_root / ".commander"
    commander_dir.mkdir(parents=True, exist_ok=True)

    yaml_content = _YAML_TEMPLATE.format(
        repo_name        = repo_name,
        coder_worktree   = str(coder_worktree),
        tester_worktree  = str(tester_worktree),
        tester_app_subdir= tester_app_subdir,
        scripts_dir      = str(SCRIPTS_DIR) + "/",
    )

    sprint_yaml = commander_dir / "sprint.yaml"
    sprint_yaml.write_text(yaml_content, encoding="utf-8")
    print(f"  Wrote {sprint_yaml}")

    # Step 3: create subdirectories (AC-8)
    for subdir in ("logs", "sprints", "alerts"):
        d = commander_dir / subdir
        d.mkdir(parents=True, exist_ok=True)
        gitkeep = d / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()
        print(f"  Created {d}/")

    # Step 4: update .gitignore (AC-8)
    _append_gitignore(repo_root, [".commander/logs/", ".commander/sprints/"])

    # Step 5: print next-step instructions (AC-8)
    sprint_yaml_path = sprint_yaml
    print()
    print("Next steps:")
    print(f"  git worktree add {coder_worktree} develop")
    print(f"  git worktree add {tester_worktree} develop")
    print(
        f"  python3 {SCRIPTS_DIR}/sprint_manager.py "
        f"--config {sprint_yaml_path} sprint-1"
    )


if __name__ == "__main__":
    main()
