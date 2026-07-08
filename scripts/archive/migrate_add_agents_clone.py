#!/usr/bin/env python3
"""migrate_add_agents_clone.py — Add a dedicated agents clone to an existing project.

The agents clone is the home for non-coding sprint agents (documentor, reviewer,
follow-up BA/estimator) so they never check out feature branches in the
coder/tester worktrees (mid-sprint) or — worse — the serving uat clone. New
projects get it from init_project.py; this script back-fills it for an
already-onboarded project and wires worktrees.agents into sprint.yaml.

Usage:
    python3 scripts/migrate_add_agents_clone.py <repo-name>
            [--owner <owner>]          # default: default_owner from machine config
            [--projects-dir ~/dev]     # default: ~/dev

Handles both layouts:
  - nested: <projects-dir>/<repo>/agents     (sprint.yaml at <repo>/.commander/)
  - flat:   <projects-dir>/<repo>-agents     (sprint.yaml inside the main clone)
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

MACHINE_CONFIG_PATH = Path.home() / ".commander" / "config.yaml"
DEFAULT_PROJECTS_DIR = Path.home() / "dev"


def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=check,
                          cwd=str(cwd) if cwd else None)


def _try_run(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    r = subprocess.run(list(cmd), capture_output=True, text=True,
                       cwd=str(cwd) if cwd else None)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


def info(msg: str) -> None:
    sys.stdout.write(f"  {msg}\n")


def warn(msg: str) -> None:
    sys.stderr.write(f"  WARNING: {msg}\n")


def _load_machine_config() -> dict:
    if not MACHINE_CONFIG_PATH.exists():
        return {}
    try:
        import yaml  # type: ignore
        return yaml.safe_load(MACHINE_CONFIG_PATH.read_text()) or {}
    except ImportError:
        cfg: dict = {}
        for line in MACHINE_CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ": " in line:
                k, _, v = line.partition(": ")
                cfg[k.strip()] = v.strip()
        return cfg


def _find_sprint_yaml(project_root: Path) -> Optional[Path]:
    """Locate .commander/sprint.yaml for nested (project-root) or flat (main clone)."""
    nested = project_root / ".commander" / "sprint.yaml"
    if nested.exists():
        return nested
    # Flat: .commander lives inside the main clone (project_root itself is the clone).
    flat = project_root / ".commander" / "sprint.yaml"
    if flat.exists():
        return flat
    # Flat alt: a main/ subdir clone.
    main = project_root / "main" / ".commander" / "sprint.yaml"
    if main.exists():
        return main
    return None


def _add_agents_to_worktrees(sprint_yaml: Path, agents_dir: Path) -> None:
    """Insert `agents: <path>` into the worktrees: block if not already present."""
    content = sprint_yaml.read_text()
    if "agents:" in content:
        info(f"worktrees.agents already present in {sprint_yaml} — leaving as-is")
        return
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    inserted = False
    in_worktrees = False
    for line in lines:
        out.append(line)
        stripped = line.strip()
        if stripped == "worktrees:":
            in_worktrees = True
            continue
        # Insert right after the `tester:` line inside the worktrees block.
        if in_worktrees and not inserted and stripped.startswith("tester:"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}agents:            {agents_dir}\n")
            inserted = True
    if not inserted:
        warn(f"could not find worktrees.tester in {sprint_yaml} — appending a worktrees note")
        if not content.endswith("\n"):
            out.append("\n")
        out.append(f"# NOTE: add `agents: {agents_dir}` under worktrees: manually\n")
    sprint_yaml.write_text("".join(out))
    info(f"wired worktrees.agents → {agents_dir} into {sprint_yaml}")


def migrate(owner: str, repo_name: str, projects_dir: Path) -> None:
    project_root = projects_dir / repo_name
    if not project_root.exists():
        sys.stderr.write(f"ERROR: project directory not found: {project_root}\n")
        sys.exit(1)

    full_repo = f"{owner}/{repo_name}"
    repo_url = f"https://github.com/{full_repo}.git"

    # Layout: nested if a coder/ subdir exists at the project root, else flat.
    nested = (project_root / "coder").exists()
    agents_dir = (project_root / "agents") if nested else projects_dir / f"{repo_name}-agents"

    sys.stdout.write(f"[1/2] Cloning agents clone into {agents_dir} ...\n")
    if agents_dir.exists():
        info(f"{agents_dir} already exists — skipping clone")
    else:
        _run("git", "clone", repo_url, str(agents_dir))
        ok, _, _ = _try_run("git", "checkout", "develop", cwd=agents_dir)
        if not ok:
            _run("git", "fetch", "origin", cwd=agents_dir)
            _try_run("git", "checkout", "--track", "origin/develop", cwd=agents_dir)
        info("cloned and checked out develop")

    sys.stdout.write("[2/2] Updating .commander/sprint.yaml ...\n")
    sprint_yaml = _find_sprint_yaml(project_root)
    if sprint_yaml is None:
        warn(f"no .commander/sprint.yaml found under {project_root} — clone created, wire worktrees.agents manually")
    else:
        _add_agents_to_worktrees(sprint_yaml, agents_dir)

    sys.stdout.write("\n" + "=" * 60 + "\n")
    sys.stdout.write(f"  Agents clone migration complete for '{repo_name}'!\n")
    sys.stdout.write(f"  Agents clone: {agents_dir} (branch develop)\n")
    sys.stdout.write("  Documentor / reviewer / follow-up agents now run here,\n")
    sys.stdout.write("  not in the coder/tester worktrees or the serving uat clone.\n")
    sys.stdout.write("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a dedicated agents clone to an existing Commander project"
    )
    parser.add_argument("repo_name", help="Repository name (slug)")
    parser.add_argument("--owner", default=None, help="GitHub owner (default: from machine config)")
    parser.add_argument("--projects-dir", default=str(DEFAULT_PROJECTS_DIR),
                        help="Base projects directory (default: ~/dev)")
    args = parser.parse_args()

    cfg = _load_machine_config()
    owner = args.owner or cfg.get("default_owner")
    if not owner:
        sys.stderr.write("ERROR: --owner is required (or set default_owner in ~/.commander/config.yaml)\n")
        sys.exit(1)

    migrate(owner, args.repo_name, Path(args.projects_dir).expanduser())


if __name__ == "__main__":
    main()
