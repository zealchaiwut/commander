#!/usr/bin/env python3
"""init_project.py — Single command to onboard a new project onto Commander.

Usage:
    python3 scripts/init_project.py <repo-name>
            [--owner <owner>]           # default: default_owner from machine config
            [--visibility public]       # default: public
            [--name "Display Name"]     # default: title-cased repo-name
            [--icon ti-folder]          # default: ti-folder
            [--color blue]              # default: cycled by project count
            [--projects-dir ~/dev]      # default: ~/dev
            [--tester-app-subdir ""]    # default: "" (no subdir)
            [--skip-uat]               # skip UAT clone creation (sets uat.enabled: false)
            [--nested]                 # use nested layout (main/, coder/, tester/ under project root)

    python3 scripts/init_project.py --rollback <repo-name>
            [--confirm-delete]          # required to also delete the GitHub repo
            [--owner <owner>]

Onboards a new project in 11 sequential steps:
  Flat layout (default):
    1. Create ~/dev/<project>/ (git init, README, .gitignore, first commit, gh repo create, push)
    4. Clone repo into ~/dev/<project>-coder and ~/dev/<project>-tester; checkout develop
    5. Write <project>/.commander/sprint.yaml (inside the main clone)

  Nested layout (--nested):
    1. Create ~/dev/<project>/main/ (git init inside nested dir, push)
    4. Clone repo into ~/dev/<project>/coder/ and ~/dev/<project>/tester/
    5. Write ~/dev/<project>/.commander/sprint.yaml (at project root, outside any clone)

  Common steps (both layouts):
    2. Create and push develop branch
    3. Create standard GitHub labels
    4b. Clone repo into <main>/uat/; checkout develop (skipped with --skip-uat)
    6. Register project in dashboard/projects.json
    7. Append repo to TRACKED_REPOS env var if in use
    8. Restart PRD dashboard via scripts/stop_all.sh prd + scripts/start_prd.sh
    9. Verify via GET /api/projects that the new project appears
   10. Print "ready" summary with repo URL and next-step instructions
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

MACHINE_CONFIG_PATH = Path.home() / ".commander" / "config.yaml"
DEFAULT_PROJECTS_DIR = Path.home() / "dev"
DEFAULT_PRD_PORT = 8000
DEFAULT_UAT_PORT = 8001
STANDARD_LABELS = [
    ("backlog",      "#C6D2D9"),
    ("in-progress",  "#C6D2D9"),
    ("SIT",          "#C6D2D9"),
    ("UAT",          "#C6D2D9"),
    ("UAT-approved", "#C6D2D9"),
    ("blocked",      "#C6D2D9"),
    ("needs-rework", "#C6D2D9"),
    ("enhancement",  "#A2EAF6"),
    ("bug",          "#D73A4A"),
    ("sprint-1",     "#1D76DB"),
]
COLOR_CYCLE = ["blue", "purple", "green", "amber", "pink", "cyan"]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, raising on failure by default."""
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        check=check,
        cwd=str(cwd) if cwd else None,
    )


def _try_run(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    """Run a command, returning (success, stdout, stderr)."""
    r = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


def step(n: int, total: int, msg: str) -> None:
    """Print a step prefix."""
    print(f"[{n}/{total}] {msg}")


def warn(msg: str) -> None:
    print(f"  WARNING: {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"  {msg}")


# ── Port detection ────────────────────────────────────────────────────────────

def _is_port_free(port: int) -> bool:
    """Return True if the port can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def _get_free_port() -> int:
    """Ask the OS for a free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _find_free_port(prefer: int, strategy: str = "prefer_default") -> int:
    """Return a free port using the given strategy.

    Args:
        prefer:   The preferred port number.
        strategy: "prefer_default" tries the preferred port first;
                  "always_random" always returns an OS-assigned port.
    """
    if strategy == "always_random":
        return _get_free_port()
    if _is_port_free(prefer):
        return prefer
    return _get_free_port()


# ── Machine config ─────────────────────────────────────────────────────────────

def _load_machine_config() -> dict:
    """Load ~/.commander/config.yaml. Returns empty dict if absent."""
    if not MACHINE_CONFIG_PATH.exists():
        return {}
    try:
        import yaml  # type: ignore
        return yaml.safe_load(MACHINE_CONFIG_PATH.read_text()) or {}
    except ImportError:
        # Parse minimal YAML manually (key: value lines)
        cfg: dict = {}
        for line in MACHINE_CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and ": " in line:
                k, _, v = line.partition(": ")
                cfg[k.strip()] = v.strip()
        return cfg


def _bootstrap_machine_config(commander_home: Path, api_url: str = "http://localhost:8000") -> None:
    """Create ~/.commander/config.yaml with sensible defaults."""
    MACHINE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"commander_home: {commander_home}\n"
        f"api_url: {api_url}\n"
    )
    MACHINE_CONFIG_PATH.write_text(content)
    print(f"Created ~/.commander/config.yaml — review and adjust if needed")


# ── projects.json helpers ──────────────────────────────────────────────────────

def _find_projects_json() -> Path:
    """Locate apps/dashboard/projects.json relative to this script."""
    return Path(__file__).parent.parent / "apps" / "dashboard" / "projects.json"


def _load_projects_json() -> list:
    p = _find_projects_json()
    if p.exists():
        return json.loads(p.read_text())
    return []


def _save_projects_json(projects: list) -> None:
    _find_projects_json().write_text(json.dumps(projects, indent=2) + "\n")


def _project_in_json(full_repo: str) -> bool:
    return any(p.get("repo") == full_repo for p in _load_projects_json())


def _find_all_projects_jsons() -> list[Path]:
    """Return all projects.json paths to keep in sync.

    Walks 4 levels up from this script to the Commander home directory
    (scripts/ -> dashboard/ -> uat|prd/ -> commander_home/) then checks for
    prd/dashboard/projects.json and uat/dashboard/projects.json siblings.
    Writes to each sibling file that already exists.
    Falls back to the script's own dashboard/projects.json if neither sibling
    is found (single-dashboard install or fresh setup).
    """
    own = Path(__file__).parent.parent / "apps" / "dashboard" / "projects.json"
    commander_home = Path(__file__).parent.parent.parent.parent
    found: list[Path] = []
    for variant in ("prd", "uat"):
        candidate = commander_home / variant / "apps" / "dashboard" / "projects.json"
        if candidate.exists():
            found.append(candidate)

    if not found:
        return [own]

    # Deduplicate by resolved path — own equals the uat sibling when running
    # from the uat clone, so both point to the same file.
    seen: set[Path] = set()
    result: list[Path] = []
    for p in found:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            result.append(p)
    return result


def _get_api_url() -> str:
    cfg = _load_machine_config()
    return cfg.get("api_url", "http://localhost:8000").rstrip("/")


# ── Pre-flight ─────────────────────────────────────────────────────────────────

def preflight(owner: str, repo_name: str, projects_dir: Path, nested: bool = False) -> None:
    """Validate environment before creating anything."""
    errors = []

    # gh authenticated?
    ok, _, err = _try_run("gh", "auth", "status")
    if not ok:
        errors.append(f"gh is not authenticated: {err}")

    # projects_dir exists?
    if not projects_dir.exists():
        errors.append(f"Projects directory does not exist: {projects_dir}")

    # local directory already exists?
    local_path = projects_dir / repo_name
    if local_path.exists():
        # This is fine for idempotent re-runs; don't error here.
        pass

    # GitHub repo already exists?
    full_repo = f"{owner}/{repo_name}"
    ok, _, _ = _try_run("gh", "repo", "view", full_repo)
    if ok:
        # Repo exists — idempotent re-run is OK; not an error.
        pass

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


# ── Step implementations ───────────────────────────────────────────────────────

def step1_init_repo(
    owner: str,
    repo_name: str,
    projects_dir: Path,
    visibility: str,
    nested: bool = False,
    from_existing: bool = False,
) -> Path:
    """Create local repo + push to GitHub, or clone an existing one.

    Flat layout:  repo lives at projects_dir/repo_name/
    Nested layout: repo lives at projects_dir/repo_name/main/
    When ``from_existing`` is set, the repo already exists on GitHub (e.g. a
    deleted-then-re-added project): clone it instead of git-init + create, so
    the real history and content come down rather than a divergent fresh init.
    Returns the path of the git working directory (main clone).
    """
    full_repo = f"{owner}/{repo_name}"
    if nested:
        project_root = projects_dir / repo_name
        repo_dir = project_root / "main"
    else:
        repo_dir = projects_dir / repo_name

    if repo_dir.exists():
        info(f"already exists — skipping")
        return repo_dir

    if from_existing:
        # Clone the existing GitHub repo into the (main) working dir.
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        _run("gh", "repo", "clone", full_repo, str(repo_dir))
        info(f"cloned existing repo {full_repo}")
        return repo_dir

    repo_dir.mkdir(parents=True)

    _run("git", "init", cwd=repo_dir)

    # Write minimal README.md
    (repo_dir / "README.md").write_text(f"# {repo_name}\n")

    # Write minimal .gitignore
    (repo_dir / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n.env\n*.log\nvenv/\n.DS_Store\n"
    )

    # Stamp the standard docs structure (shared by every Commander project)
    try:
        from scaffold_docs import scaffold as _scaffold_docs
        _scaffold_docs(repo_dir, repo_name, check=False)
    except Exception as e:  # never block project creation on docs scaffolding
        info(f"docs scaffold skipped: {e}")

    _run("git", "add", ".", cwd=repo_dir)
    _run("git", "commit", "-m", "chore: initial commit", cwd=repo_dir)

    # Create GitHub repo and push
    _run(
        "gh", "repo", "create", full_repo,
        f"--{visibility}",
        "--source=.",
        "--remote=origin",
        "--push",
        cwd=repo_dir,
    )

    return repo_dir


def step2_develop_branch(repo_dir: Path) -> None:
    """AC4: Create and push develop branch."""
    # Check if develop already exists remotely
    ok, _, _ = _try_run("git", "ls-remote", "--exit-code", "origin", "refs/heads/develop", cwd=repo_dir)
    if ok:
        info("develop branch already exists remotely — skipping")
        return

    # Determine default branch
    ok, default_branch, _ = _try_run("git", "symbolic-ref", "--short", "HEAD", cwd=repo_dir)
    if not ok:
        default_branch = "main"

    _run("git", "checkout", "-b", "develop", cwd=repo_dir)
    _run("git", "push", "-u", "origin", "develop", cwd=repo_dir)
    _run("git", "checkout", default_branch, cwd=repo_dir)


def step3_labels(owner: str, repo_name: str) -> None:
    """AC5: Create standard labels. Skip existing ones."""
    full_repo = f"{owner}/{repo_name}"

    # Fetch existing labels
    ok, out, _ = _try_run("gh", "label", "list", "--repo", full_repo, "--json", "name", "--limit", "100")
    existing: set[str] = set()
    if ok and out:
        try:
            existing = {l["name"] for l in json.loads(out)}
        except (json.JSONDecodeError, KeyError):
            pass

    for label_name, color in STANDARD_LABELS:
        if label_name in existing:
            info(f"label '{label_name}' already exists — skipping")
            continue
        ok, _, err = _try_run(
            "gh", "label", "create", label_name,
            "--repo", full_repo,
            "--color", color.lstrip("#"),
        )
        if ok:
            info(f"created label '{label_name}'")
        else:
            # May fail if already exists due to race; non-fatal
            info(f"label '{label_name}' skipped ({err})")


def step4_clones(
    owner: str,
    repo_name: str,
    projects_dir: Path,
    nested: bool = False,
) -> tuple[Path, Path]:
    """Clone repo into coder and tester worktrees.

    Flat layout:   ~/dev/<project>-coder/  and  ~/dev/<project>-tester/
    Nested layout: ~/dev/<project>/coder/  and  ~/dev/<project>/tester/

    Returns (coder_dir, tester_dir).
    """
    full_repo = f"{owner}/{repo_name}"
    repo_url = f"https://github.com/{full_repo}.git"

    if nested:
        project_root = projects_dir / repo_name
        coder_dir  = project_root / "coder"
        tester_dir = project_root / "tester"
    else:
        coder_dir  = projects_dir / f"{repo_name}-coder"
        tester_dir = projects_dir / f"{repo_name}-tester"

    for clone_dir in (coder_dir, tester_dir):
        if clone_dir.exists():
            info(f"{clone_dir} already exists — skipping")
            continue

        _run("git", "clone", repo_url, str(clone_dir))
        ok, _, _ = _try_run("git", "checkout", "develop", cwd=clone_dir)
        if not ok:
            _run("git", "fetch", "origin", cwd=clone_dir)
            _try_run("git", "checkout", "--track", "origin/develop", cwd=clone_dir)

        info(f"cloned into {clone_dir}")

    return coder_dir, tester_dir


def step4b_uat_clone(
    owner: str,
    repo_name: str,
    repo_dir: Path,
    uat_port: int,
    base_dir: Optional[Path] = None,
) -> Path:
    """AC1/AC2/AC3/AC4: Clone repo into <project>/uat/ and checkout develop.

    Creates the UAT clone as a subdirectory of base_dir (defaults to repo_dir).

    Flat layout:   uat/ lives inside the main clone (repo_dir/uat/)
    Nested layout: uat/ lives at the project root (base_dir/uat/) as a sibling
                   of main/, coder/, and tester/ — NOT inside main/.

    Pass base_dir=projects_dir/repo_name when using nested layout so that the
    UAT clone lands at <project_root>/uat/ instead of <main>/uat/.

    Also creates a separate UAT database file (never shared with PRD) and
    writes a .env file so the UAT server uses its own port and DB.
    """
    full_repo = f"{owner}/{repo_name}"
    repo_url = f"https://github.com/{full_repo}.git"
    clone_base = base_dir if base_dir is not None else repo_dir
    uat_dir = clone_base / "uat"

    if uat_dir.exists():
        info(f"{uat_dir} already exists — skipping UAT clone")
        return uat_dir

    info(f"cloning UAT clone into {uat_dir} ...")
    _run("git", "clone", repo_url, str(uat_dir))

    # AC2: checkout develop
    ok, _, _ = _try_run("git", "checkout", "develop", cwd=uat_dir)
    if not ok:
        _run("git", "fetch", "origin", cwd=uat_dir)
        _try_run("git", "checkout", "--track", "origin/develop", cwd=uat_dir)

    # AC3: write .env with separate DB path and UAT port — never shared with PRD
    db_filename = f"{repo_name}-uat.db"
    env_content = (
        f"PORT={uat_port}\n"
        f"ENVIRONMENT=uat\n"
        f"DB_PATH=./{db_filename}\n"
    )
    uat_dashboard = uat_dir / "dashboard"
    if uat_dashboard.exists():
        # If the project has a dashboard/ subdirectory, write .env there
        env_path = uat_dashboard / ".env"
    else:
        env_path = uat_dir / ".env"
    env_path.write_text(env_content)
    info(f"wrote {env_path} (port={uat_port}, db={db_filename})")

    info(f"UAT clone ready at {uat_dir} (branch: develop)")
    return uat_dir


def step5_sprint_config(
    owner: str,
    repo_name: str,
    repo_dir: Path,
    projects_dir: Path,
    tester_app_subdir: str,
    skip_uat: bool = False,
    prd_port: int = DEFAULT_PRD_PORT,
    uat_port: int = DEFAULT_UAT_PORT,
    port_strategy: str = "prefer_default",
    nested: bool = False,
    coder_dir: Optional[Path] = None,
    tester_dir: Optional[Path] = None,
) -> None:
    """Write .commander/sprint.yaml with project-specific fields.

    Flat layout:   .commander/ lives inside the main clone (repo_dir/.commander/)
    Nested layout: .commander/ lives at the project root (projects_dir/repo_name/.commander/)
                   outside any git clone, so it is never accidentally committed.

    Sets uat.enabled: false when skip_uat=True.
    """
    if nested:
        # .commander at project root, outside the main/ clone
        commander_dir = projects_dir / repo_name / ".commander"
    else:
        commander_dir = repo_dir / ".commander"

    sprint_yaml_path = commander_dir / "sprint.yaml"

    if sprint_yaml_path.exists():
        info("sprint.yaml already exists — skipping")
        return

    commander_dir.mkdir(parents=True, exist_ok=True)

    for subdir in ("logs", "sprints", "alerts"):
        (commander_dir / subdir).mkdir(parents=True, exist_ok=True)
        (commander_dir / subdir / ".gitkeep").touch()

    # Resolve worktree paths
    if coder_dir is None:
        coder_dir = projects_dir / f"{repo_name}-coder"
    if tester_dir is None:
        tester_dir = projects_dir / f"{repo_name}-tester"

    if nested:
        # In nested layout, uat/ is a sibling of main/, coder/, tester/ at project root
        uat_dir = projects_dir / repo_name / "uat"
    else:
        uat_dir = repo_dir / "uat"
    db_filename = f"{repo_name}-uat.db"
    uat_enabled = "false" if skip_uat else "true"

    if nested:
        # base_dir for load_config() is the .commander/ directory itself, so
        # relative paths must NOT include the .commander/ prefix — they are
        # resolved relative to .commander/, e.g. "logs/" → .commander/logs/
        logs_path    = "logs/"
        sprints_path = "sprints/"
        alerts_path  = "alerts/"
    else:
        # Flat layout: base_dir is the main clone root, so paths need .commander/ prefix
        logs_path    = ".commander/logs/"
        sprints_path = ".commander/sprints/"
        alerts_path  = ".commander/alerts/"

    sprint_yaml_content = f"""repo_name: {owner}/{repo_name}

worktrees:
  coder:             {coder_dir}
  tester:            {tester_dir}
  tester_app_subdir: "{tester_app_subdir}"

paths:
  logs_dir:     {logs_path}
  sprints_dir:  {sprints_path}
  alerts_dir:   {alerts_path}

app:
  prd_port: {prd_port}
  uat_port: {uat_port}
  port_strategy: {port_strategy}

uat:
  enabled: {uat_enabled}
  auto_sync: false
  db_path: {db_filename}

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
    sprint_yaml_path.write_text(sprint_yaml_content)

    if nested:
        # .commander is at the project root (not inside a git repo), so no git
        # commit is needed here. Just log the location.
        info(f"written {sprint_yaml_path} (at project root, outside git clones)")
    else:
        # Update .gitignore inside the main clone to exclude runtime dirs
        gitignore_path = repo_dir / ".gitignore"
        gitignore_additions = (
            "\n# Commander runtime dirs\n"
            ".commander/logs/*\n!.commander/logs/.gitkeep\n"
            ".commander/sprints/*\n!.commander/sprints/.gitkeep\n"
            ".commander/alerts/*\n!.commander/alerts/.gitkeep\n"
        )
        if gitignore_path.exists():
            existing = gitignore_path.read_text()
            if ".commander/logs/" not in existing:
                gitignore_path.write_text(existing + gitignore_additions)
        else:
            gitignore_path.write_text(gitignore_additions)

        _run("git", "add", ".commander", ".gitignore", cwd=repo_dir)
        _run("git", "commit", "-m", "chore: add .commander sprint config", cwd=repo_dir)
        _run("git", "push", "origin", "HEAD", cwd=repo_dir)
        info(f"written and pushed .commander/sprint.yaml")


def step6_register_project(owner: str, repo_name: str, icon: str, color: str) -> None:
    """AC8: Register project in all discovered dashboard/projects.json files."""
    full_repo = f"{owner}/{repo_name}"
    name = repo_name.replace("-", " ").replace("_", " ").title()
    new_proj = {
        "repo": full_repo,
        "name": name,
        "icon": icon or "ti-folder",
        "color": color or "gray",
        "active_sprints": {},
    }

    targets = _find_all_projects_jsons()
    any_written = False
    for path in targets:
        projects: list = json.loads(path.read_text()) if path.exists() else []
        if any(p.get("repo") == full_repo for p in projects):
            info(f"{full_repo} already in {path} — skipping")
            continue
        projects.append(new_proj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(projects, indent=2) + "\n")
        info(f"registered {full_repo} in {path}")
        any_written = True

    if not any_written:
        info(f"{full_repo} already registered in all target files")


def step7_tracked_repos(owner: str, repo_name: str) -> None:
    """AC (step 7): Append to TRACKED_REPOS env var if in use."""
    tracked = os.environ.get("TRACKED_REPOS", "").strip()
    if not tracked:
        info("TRACKED_REPOS not set — skipping")
        return

    full_repo = f"{owner}/{repo_name}"
    repos = [r.strip() for r in tracked.split(",") if r.strip()]
    if full_repo in repos:
        info(f"{full_repo} already in TRACKED_REPOS — skipping")
        return

    repos.append(full_repo)
    new_val = ",".join(repos)
    info(f"Note: TRACKED_REPOS updated in this process only. Add to your shell profile: export TRACKED_REPOS={new_val}")
    os.environ["TRACKED_REPOS"] = new_val


def step8_restart_dashboard() -> None:
    """AC9: Restart PRD dashboard."""
    scripts_dir = Path(__file__).parent

    stop_script = scripts_dir / "stop_all.sh"
    start_script = scripts_dir / "start_prd.sh"

    if not stop_script.exists() or not start_script.exists():
        warn("stop_all.sh or start_prd.sh not found — skipping dashboard restart")
        return

    ok, _, err = _try_run("bash", str(stop_script), "prd")
    if not ok:
        warn(f"stop_all.sh failed (continuing): {err}")

    time.sleep(1)

    ok, _, err = _try_run("bash", str(start_script))
    if not ok:
        warn(f"start_prd.sh failed (dashboard may not be restarted): {err}")
    else:
        info("dashboard restarted")


def step9_verify(owner: str, repo_name: str) -> None:
    """AC10: Verify project appears in /api/projects."""
    api_url = _get_api_url()
    full_repo = f"{owner}/{repo_name}"
    url = f"{api_url}/api/projects"

    time.sleep(2)  # Give dashboard a moment to come up

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        projects = data.get("projects", [])
        found = any(p.get("repo") == full_repo for p in projects)

        if found:
            info(f"project confirmed at {api_url}/api/projects")
        else:
            warn(f"{full_repo} not found in GET /api/projects response yet (may need a moment)")

    except (urllib.error.URLError, OSError) as e:
        warn(f"API unreachable ({e}) — skipping verification")


def step10_summary(
    owner: str,
    repo_name: str,
    projects_dir: Path,
    repo_dir: Path,
    skip_uat: bool,
    uat_port: int,
    nested: bool = False,
    coder_dir: Optional[Path] = None,
    tester_dir: Optional[Path] = None,
) -> None:
    """Print ready summary."""
    full_repo = f"{owner}/{repo_name}"
    uat_dir = repo_dir / "uat"
    if coder_dir is None:
        coder_dir = projects_dir / f"{repo_name}-coder"
    if tester_dir is None:
        tester_dir = projects_dir / f"{repo_name}-tester"

    print()
    print("=" * 60)
    print(f"  Project '{repo_name}' is ready!")
    print(f"  GitHub:  https://github.com/{full_repo}")
    if nested:
        project_root = projects_dir / repo_name
        print(f"  Layout:  nested ({project_root}/)")
        print(f"  Main:    {repo_dir}")
    print(f"  Coder:   {coder_dir}")
    print(f"  Tester:  {tester_dir}")
    if skip_uat:
        print(f"  UAT:     skipped (--skip-uat)")
    else:
        print(f"  UAT:     {uat_dir}  (port {uat_port}, branch: develop)")
    if nested:
        print(f"  Config:  {projects_dir / repo_name / '.commander' / 'sprint.yaml'}")
    print()
    print("  Next steps:")
    print(f"    1. cd {repo_dir} && open CLAUDE.md (add your conventions)")
    print(f"    2. python3 dashboard/scripts/sprint_init.py --repo {full_repo}")
    print(f"    3. Open dashboard at http://localhost:8000 to see the project card")
    if not skip_uat:
        print(f"    4. Start UAT with: bash dashboard/scripts/start_uat.sh")
    print("=" * 60)


# ── Rollback ───────────────────────────────────────────────────────────────────

def rollback(owner: str, repo_name: str, projects_dir: Path, confirm_delete: bool) -> None:
    """Remove local dirs, projects.json entry, and optionally the GitHub repo.

    Handles both flat and nested layouts automatically.
    """
    full_repo = f"{owner}/{repo_name}"
    removed = []

    project_root = projects_dir / repo_name

    # Detect layout
    nested = (project_root / "main").exists() and (project_root / "main" / ".git").exists()

    if nested:
        # Remove entire project root (contains main/, coder/, tester/, .commander/)
        if project_root.exists():
            shutil.rmtree(project_root)
            removed.append(str(project_root))
            print(f"  Removed {project_root} (nested layout)")
        else:
            print(f"  {project_root} does not exist — skipping")
    else:
        # Flat layout: also remove <project>/uat/ nested inside the project root
        uat_dir = project_root / "uat"
        if uat_dir.exists():
            shutil.rmtree(uat_dir)
            removed.append(str(uat_dir))
            print(f"  Removed {uat_dir}")
        else:
            print(f"  {uat_dir} does not exist — skipping")

        for suffix in ("", "-coder", "-tester"):
            d = projects_dir / f"{repo_name}{suffix}"
            if d.exists():
                shutil.rmtree(d)
                removed.append(str(d))
                print(f"  Removed {d}")
            else:
                print(f"  {d} does not exist — skipping")

    # Remove from all projects.json files
    for path in _find_all_projects_jsons():
        projects = json.loads(path.read_text()) if path.exists() else []
        new_projects = [p for p in projects if p.get("repo") != full_repo]
        if len(new_projects) < len(projects):
            path.write_text(json.dumps(new_projects, indent=2) + "\n")
            removed.append(f"projects.json entry ({path})")
            print(f"  Removed {full_repo} from {path}")
        else:
            print(f"  {full_repo} not found in {path} — skipping")

    # Optionally delete GitHub repo
    if confirm_delete:
        ok, _, err = _try_run("gh", "repo", "delete", full_repo, "--yes")
        if ok:
            removed.append(f"GitHub repo {full_repo}")
            print(f"  Deleted GitHub repo {full_repo}")
        else:
            warn(f"Failed to delete GitHub repo: {err}")

    print()
    print("Rollback summary:")
    for item in removed:
        print(f"  - {item}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Onboard a new project onto Commander"
    )
    parser.add_argument("repo_name", nargs="?", help="Repository name (slug)")
    parser.add_argument("--rollback", metavar="REPO_NAME", help="Remove a previously onboarded project")
    parser.add_argument("--confirm-delete", action="store_true", help="Also delete GitHub repo during rollback")
    parser.add_argument("--owner", default=None, help="GitHub owner (default: from machine config)")
    parser.add_argument("--visibility", default="public", choices=["public", "private"], help="Repo visibility")
    parser.add_argument("--name", default=None, help="Display name for dashboard (default: title-cased repo-name)")
    parser.add_argument("--icon", default="ti-folder", help="Icon class (default: ti-folder)")
    parser.add_argument("--color", default=None, help="Color (default: cycled by project count)")
    parser.add_argument("--projects-dir", default=str(DEFAULT_PROJECTS_DIR), help="Base projects directory (default: ~/dev)")
    parser.add_argument("--from-existing", action="store_true", help="Clone an existing GitHub repo instead of creating a new one (for re-adding a repo)")
    parser.add_argument("--tester-app-subdir", default="", help="Tester app subdirectory (default: '')")
    parser.add_argument(
        "--skip-uat",
        action="store_true",
        help="Skip UAT clone creation and set uat.enabled: false in sprint.yaml",
    )
    parser.add_argument(
        "--prd-port",
        type=int,
        default=DEFAULT_PRD_PORT,
        help=f"Preferred PRD port (default: {DEFAULT_PRD_PORT})",
    )
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
    parser.add_argument(
        "--nested",
        action="store_true",
        help=(
            "Use nested layout: main/, coder/, tester/ under a single project root; "
            ".commander/ lives at the project root outside any git clone. "
            "Default is flat layout (backward compatible)."
        ),
    )

    args = parser.parse_args()

    projects_dir = Path(args.projects_dir).expanduser()

    # ── Rollback mode ─────────────────────────────────────────────────────────
    if args.rollback:
        repo_name = args.rollback

        # Resolve owner
        cfg = _load_machine_config()
        owner = args.owner or cfg.get("default_owner")
        if not owner:
            print("ERROR: --owner is required (or set default_owner in ~/.commander/config.yaml)", file=sys.stderr)
            sys.exit(1)

        print(f"Rolling back project '{repo_name}' for owner '{owner}'...")
        rollback(owner, repo_name, projects_dir, args.confirm_delete)
        return

    # ── Normal onboarding mode ─────────────────────────────────────────────────
    if not args.repo_name:
        parser.print_help()
        sys.exit(1)

    repo_name = args.repo_name

    # ── AC2: Machine config bootstrap ─────────────────────────────────────────
    cfg = _load_machine_config()
    if not MACHINE_CONFIG_PATH.exists():
        commander_home = Path(__file__).parent.parent.parent
        _bootstrap_machine_config(commander_home)
        cfg = _load_machine_config()

    # ── Resolve owner ──────────────────────────────────────────────────────────
    # AC13: use default_owner from config if --owner not passed
    owner = args.owner or cfg.get("default_owner")
    if not owner:
        if MACHINE_CONFIG_PATH.exists() and "default_owner" not in cfg:
            print(
                "ERROR: ~/.commander/config.yaml exists but lacks 'default_owner'. "
                "Pass --owner explicitly.",
                file=sys.stderr,
            )
        else:
            print(
                "ERROR: --owner is required (or set default_owner in ~/.commander/config.yaml)",
                file=sys.stderr,
            )
        sys.exit(1)

    # ── AC14: Color cycling ────────────────────────────────────────────────────
    color = args.color
    if not color:
        projects = _load_projects_json()
        color = COLOR_CYCLE[len(projects) % len(COLOR_CYCLE)]

    # ── AC4: Auto-detect UAT port (distinct from PRD) ─────────────────────────
    prd_port = _find_free_port(prefer=args.prd_port, strategy=args.port_strategy)
    # UAT port must be distinct from PRD port
    uat_port_pref = args.uat_port if args.uat_port != prd_port else args.uat_port + 1
    uat_port = _find_free_port(prefer=uat_port_pref, strategy=args.port_strategy)
    if uat_port == prd_port:
        # Fallback: get any other free port
        uat_port = _get_free_port()

    TOTAL_STEPS = 11
    nested = args.nested

    print(f"Onboarding '{owner}/{repo_name}' onto Commander...")
    print(f"  Layout:    {'nested (--nested)' if nested else 'flat (default)'}")
    if not args.skip_uat:
        print(f"  UAT clone: enabled (port {uat_port})")
    else:
        print(f"  UAT clone: skipped (--skip-uat)")
    print()

    # ── Pre-flight validation ─────────────────────────────────────────────────
    print("[pre-flight] Validating environment...")
    preflight(owner, repo_name, projects_dir, nested=nested)
    info("pre-flight passed")
    print()

    # ── Step 1: Create local repo + GitHub ────────────────────────────────────
    step(1, TOTAL_STEPS, f"Creating local repo and GitHub repo for {owner}/{repo_name}...")
    repo_dir = step1_init_repo(owner, repo_name, projects_dir, args.visibility, nested=nested, from_existing=args.from_existing)
    info("done")

    # ── Step 2: develop branch ────────────────────────────────────────────────
    step(2, TOTAL_STEPS, "Creating develop branch...")
    step2_develop_branch(repo_dir)
    info("done")

    # ── Step 3: Standard labels ───────────────────────────────────────────────
    step(3, TOTAL_STEPS, "Creating standard GitHub labels...")
    step3_labels(owner, repo_name)
    info("done")

    # ── Step 4: Clone coder + tester ──────────────────────────────────────────
    step(4, TOTAL_STEPS, "Cloning coder and tester worktrees...")
    coder_dir, tester_dir = step4_clones(owner, repo_name, projects_dir, nested=nested)
    info("done")

    # ── Step 4b: UAT clone ────────────────────────────────────────────────────
    step(5, TOTAL_STEPS, "Setting up UAT clone...")
    if args.skip_uat:
        info("--skip-uat set — skipping UAT clone creation")
    else:
        # In nested layout, UAT clone must land at <project_root>/uat/ (sibling of
        # main/, coder/, tester/), not inside main/.  Pass base_dir accordingly.
        uat_base_dir = projects_dir / repo_name if nested else None
        step4b_uat_clone(owner, repo_name, repo_dir, uat_port, base_dir=uat_base_dir)
    info("done")

    # ── Step 5: sprint.yaml ───────────────────────────────────────────────────
    step(6, TOTAL_STEPS, "Writing .commander/sprint.yaml...")
    step5_sprint_config(
        owner,
        repo_name,
        repo_dir,
        projects_dir,
        args.tester_app_subdir,
        skip_uat=args.skip_uat,
        prd_port=prd_port,
        uat_port=uat_port,
        port_strategy=args.port_strategy,
        nested=nested,
        coder_dir=coder_dir,
        tester_dir=tester_dir,
    )
    info("done")

    # ── Step 6: Register in projects.json ─────────────────────────────────────
    step(7, TOTAL_STEPS, "Registering project in dashboard/projects.json...")
    step6_register_project(owner, repo_name, args.icon, color)
    info("done")

    # ── Step 7: TRACKED_REPOS ─────────────────────────────────────────────────
    step(8, TOTAL_STEPS, "Checking TRACKED_REPOS environment variable...")
    step7_tracked_repos(owner, repo_name)
    info("done")

    # ── Step 8: Restart dashboard ─────────────────────────────────────────────
    step(9, TOTAL_STEPS, "Restarting PRD dashboard...")
    step8_restart_dashboard()
    info("done")

    # ── Step 9: Verify via API ─────────────────────────────────────────────────
    step(10, TOTAL_STEPS, "Verifying project appears in /api/projects...")
    step9_verify(owner, repo_name)
    info("done")

    # ── Step 10: Print summary ────────────────────────────────────────────────
    step(11, TOTAL_STEPS, "Printing ready summary...")
    step10_summary(
        owner, repo_name, projects_dir, repo_dir, args.skip_uat, uat_port,
        nested=nested, coder_dir=coder_dir, tester_dir=tester_dir,
    )


if __name__ == "__main__":
    main()
