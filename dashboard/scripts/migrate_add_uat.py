#!/usr/bin/env python3
"""migrate_add_uat.py — Add a UAT clone to an existing nested project layout.

AC8: Existing projects can opt-in via this migration script, which adds the
     uat/ clone to an already-onboarded project.

Usage:
    python3 scripts/migrate_add_uat.py <repo-name>
            [--owner <owner>]           # default: default_owner from machine config
            [--projects-dir ~/dev]      # default: ~/dev
            [--uat-port 8001]           # preferred UAT port (auto-detected if occupied)
            [--port-strategy prefer_default]

What this does:
  1. Validates the project root exists (~/dev/<project>/)
  2. Creates the UAT clone at ~/dev/<project>/uat/ (git clone, checkout develop)
  3. Writes .env in the UAT clone with a separate DB path and port
  4. Updates .commander/sprint.yaml to add/update the uat: section
  5. Prints a ready summary
"""

import argparse
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

MACHINE_CONFIG_PATH = Path.home() / ".commander" / "config.yaml"
DEFAULT_PROJECTS_DIR = Path.home() / "dev"
DEFAULT_UAT_PORT = 8001

# ── Helpers ───────────────────────────────────────────────────────────────────


def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        check=check,
        cwd=str(cwd) if cwd else None,
    )


def _try_run(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    r = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


def info(msg: str) -> None:
    print(f"  {msg}")


def warn(msg: str) -> None:
    print(f"  WARNING: {msg}", file=sys.stderr)


# ── Port detection ────────────────────────────────────────────────────────────


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _find_free_port(prefer: int, strategy: str = "prefer_default") -> int:
    if strategy == "always_random":
        return _get_free_port()
    if _is_port_free(prefer):
        return prefer
    return _get_free_port()


# ── Machine config ─────────────────────────────────────────────────────────────


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


# ── Sprint.yaml helpers ────────────────────────────────────────────────────────


def _update_sprint_yaml(sprint_yaml_path: Path, db_filename: str, uat_port: int) -> None:
    """Add or update the uat: section in sprint.yaml.

    Reads existing content and appends/replaces the uat: block.
    Preserves all other content unchanged.
    """
    if not sprint_yaml_path.exists():
        warn(f"sprint.yaml not found at {sprint_yaml_path} — skipping update")
        return

    content = sprint_yaml_path.read_text()

    uat_block = (
        f"\nuat:\n"
        f"  enabled: true\n"
        f"  auto_sync: false\n"
        f"  db_path: {db_filename}\n"
    )

    uat_port_block = (
        f"\napp:\n"
        f"  uat_port: {uat_port}\n"
        f"  port_strategy: prefer_default\n"
    )

    # Remove existing uat: block if present (simple line-based removal)
    lines = content.splitlines(keepends=True)
    filtered: list[str] = []
    skip_until_blank = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("uat:"):
            skip_until_blank = True
            continue
        if skip_until_blank:
            # Skip indented lines belonging to uat: block
            if stripped == "" or not line.startswith(" "):
                skip_until_blank = False
                filtered.append(line)
            continue
        filtered.append(line)

    new_content = "".join(filtered)

    # Also update app.uat_port if app: block exists, else append
    if "app:" in new_content:
        # Inject uat_port under app: if not already there
        if "uat_port:" not in new_content:
            new_lines = []
            for line in new_content.splitlines(keepends=True):
                new_lines.append(line)
                if line.strip() == "app:":
                    new_lines.append(f"  uat_port: {uat_port}\n")
            new_content = "".join(new_lines)
    else:
        new_content += uat_port_block

    # Append uat: block
    if not new_content.endswith("\n"):
        new_content += "\n"
    new_content += uat_block

    sprint_yaml_path.write_text(new_content)
    info(f"updated {sprint_yaml_path} with uat: section")


# ── Migration ──────────────────────────────────────────────────────────────────


def migrate(
    owner: str,
    repo_name: str,
    projects_dir: Path,
    uat_port: int,
    port_strategy: str,
) -> None:
    """Add UAT clone to an existing project."""
    repo_dir = projects_dir / repo_name
    if not repo_dir.exists():
        print(f"ERROR: Project directory not found: {repo_dir}", file=sys.stderr)
        sys.exit(1)

    full_repo = f"{owner}/{repo_name}"
    repo_url = f"https://github.com/{full_repo}.git"
    uat_dir = repo_dir / "uat"
    db_filename = f"{repo_name}-uat.db"

    # ── Step 1: Clone UAT ─────────────────────────────────────────────────────
    print(f"[1/3] Cloning UAT into {uat_dir} ...")
    if uat_dir.exists():
        info(f"{uat_dir} already exists — skipping clone")
    else:
        _run("git", "clone", repo_url, str(uat_dir))
        ok, _, _ = _try_run("git", "checkout", "develop", cwd=uat_dir)
        if not ok:
            _run("git", "fetch", "origin", cwd=uat_dir)
            _try_run("git", "checkout", "--track", "origin/develop", cwd=uat_dir)
        info(f"cloned and checked out develop")

    # ── Step 2: Write .env ────────────────────────────────────────────────────
    print(f"[2/3] Writing UAT .env ...")
    uat_dashboard = uat_dir / "dashboard"
    if uat_dashboard.exists():
        env_path = uat_dashboard / ".env"
    else:
        env_path = uat_dir / ".env"

    if env_path.exists():
        info(f"{env_path} already exists — skipping")
    else:
        env_content = (
            f"PORT={uat_port}\n"
            f"ENVIRONMENT=uat\n"
            f"DB_PATH=./{db_filename}\n"
        )
        env_path.write_text(env_content)
        info(f"wrote {env_path} (port={uat_port}, db={db_filename})")

    # ── Step 3: Update sprint.yaml ────────────────────────────────────────────
    print(f"[3/3] Updating .commander/sprint.yaml ...")
    sprint_yaml_path = repo_dir / ".commander" / "sprint.yaml"
    _update_sprint_yaml(sprint_yaml_path, db_filename, uat_port)

    print()
    print("=" * 60)
    print(f"  UAT migration complete for '{repo_name}'!")
    print(f"  UAT clone: {uat_dir}")
    print(f"  Branch:    develop")
    print(f"  Port:      {uat_port}")
    print(f"  Database:  {db_filename} (separate from PRD)")
    print()
    print("  Next steps:")
    print(f"    bash dashboard/scripts/start_uat.sh")
    print("=" * 60)


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a UAT clone to an existing Commander project"
    )
    parser.add_argument("repo_name", help="Repository name (slug)")
    parser.add_argument("--owner", default=None, help="GitHub owner (default: from machine config)")
    parser.add_argument("--projects-dir", default=str(DEFAULT_PROJECTS_DIR), help="Base projects directory (default: ~/dev)")
    parser.add_argument(
        "--uat-port",
        type=int,
        default=DEFAULT_UAT_PORT,
        help=f"Preferred UAT port (default: {DEFAULT_UAT_PORT}; auto-detected if occupied)",
    )
    parser.add_argument(
        "--port-strategy",
        default="prefer_default",
        choices=["prefer_default", "always_random"],
        help="Port selection strategy (default: prefer_default)",
    )

    args = parser.parse_args()

    cfg = _load_machine_config()
    owner = args.owner or cfg.get("default_owner")
    if not owner:
        print(
            "ERROR: --owner is required (or set default_owner in ~/.commander/config.yaml)",
            file=sys.stderr,
        )
        sys.exit(1)

    projects_dir = Path(args.projects_dir).expanduser()

    # AC4: auto-detect UAT port
    uat_port = _find_free_port(prefer=args.uat_port, strategy=args.port_strategy)

    migrate(owner, args.repo_name, projects_dir, uat_port, args.port_strategy)


if __name__ == "__main__":
    main()
