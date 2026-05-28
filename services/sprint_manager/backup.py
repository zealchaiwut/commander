"""
Backup module: push commander config files to a private GitHub gist.

Public API:
  backup_config_to_gist(config_files, gist_id)  -> str (gist_id)
  restore_config_from_gist(gist_id, target_dir)

Backup triggers are managed by schedule_backup() / start_backup_scheduler().
Status is exposed via get_backup_status() for the GET /api/backup/status endpoint.

CLI usage (restore):
  python -m services.sprint_manager.backup restore --gist-id <id>
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BACKUP_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours

# Patterns whose VALUES should be redacted before uploading
_SECRET_PATTERNS = re.compile(
    r"^([^\S\n]*[A-Za-z0-9_]*(?:_TOKEN|_KEY|_SECRET)[^\S\n]*=)(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Locate .commander directory
# ---------------------------------------------------------------------------

def _find_commander_dir() -> Path:
    """Find the best .commander directory for storing backup_config.json.

    In the nested layout the structure is:
      ~/dev/<project>/coder/services/sprint_manager/backup.py
      ~/dev/<project>/.commander/      <- has sprint.yaml (project root)
      ~/dev/<project>/coder/.commander/  <- may exist but lacks sprint.yaml

    Strategy: walk upward and collect all .commander directories found.
    Prefer the one that contains sprint.yaml; otherwise use the highest-level one
    (closest to filesystem root) since that is the project root in nested layout.
    Falls back to ~/.commander if nothing found.
    """
    found: list[Path] = []
    candidate = Path(__file__).resolve().parent
    for _ in range(15):
        commander = candidate / ".commander"
        if commander.is_dir():
            found.append(commander)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    if not found:
        return Path.home() / ".commander"

    # Prefer the one with sprint.yaml (the "live" config directory)
    for d in found:
        if (d / "sprint.yaml").exists():
            return d

    # Otherwise prefer the outermost (highest-level, last in the list)
    return found[-1]


_COMMANDER_DIR: Path = _find_commander_dir()
_BACKUP_CONFIG_FILE: Path = _COMMANDER_DIR / "backup_config.json"

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def _commander_version() -> str:
    """Return version from a VERSION file if present, else 'unknown'."""
    # Search up from this file for a VERSION file
    candidate = Path(__file__).resolve().parent
    for _ in range(10):
        version_file = candidate / "VERSION"
        if version_file.is_file():
            return version_file.read_text(encoding="utf-8").strip()
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return "unknown"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def _redact_secrets(text: str) -> str:
    """Replace values on lines matching *_TOKEN=*, *_KEY=*, *_SECRET=* with REDACTED."""
    return _SECRET_PATTERNS.sub(r"\1REDACTED", text)


# ---------------------------------------------------------------------------
# Backup config persistence
# ---------------------------------------------------------------------------

def _load_backup_config() -> dict:
    if _BACKUP_CONFIG_FILE.exists():
        try:
            return json.loads(_BACKUP_CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_backup_config(config: dict) -> None:
    _BACKUP_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _BACKUP_CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Module-level backup state (for GET /api/backup/status)
# ---------------------------------------------------------------------------

_backup_state: dict = {
    "last_backup_at": None,
    "gist_id": None,
    "gist_url": None,
    "file_count": 0,
    "last_error": None,
}
_state_lock = threading.Lock()


def get_backup_status() -> dict:
    """Return a copy of the current backup state dict."""
    with _state_lock:
        return dict(_backup_state)


def _update_state(**kwargs: object) -> None:
    with _state_lock:
        _backup_state.update(kwargs)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def _collect_config_files() -> list[Path]:
    """Collect the canonical set of files to back up.

    Returns a list of Paths that exist.
    """
    files: list[Path] = []

    # 1. apps/dashboard/projects.json
    #    Locate it relative to this file: services/sprint_manager/ -> ../../apps/dashboard/
    dashboard_dir = Path(__file__).resolve().parent.parent.parent / "apps" / "dashboard"
    projects_json = dashboard_dir / "projects.json"
    if projects_json.exists():
        files.append(projects_json)

    # 2. .env in dashboard (if present)
    env_file = dashboard_dir / ".env"
    if env_file.exists():
        files.append(env_file)

    # 3. All .commander/sprint.yaml files across registered projects
    try:
        if projects_json.exists():
            registered = json.loads(projects_json.read_text(encoding="utf-8"))
        else:
            registered = []
    except Exception:
        registered = []

    dev_base = Path.home() / "dev"
    for proj in registered:
        repo = proj.get("repo", "")
        slug = repo.split("/")[-1] if repo else ""
        if not slug:
            continue
        # Check nested layout first, then flat layout
        candidates = [
            dev_base / slug / ".commander" / "sprint.yaml",   # nested: slug is project root
            dev_base / slug / "coder" / ".commander" / "sprint.yaml",  # nested coder clone
        ]
        for candidate in candidates:
            if candidate.exists() and candidate not in files:
                files.append(candidate)
                break

    # Also include the local .commander/sprint.yaml (for the commander project itself)
    local_sprint_yaml = _COMMANDER_DIR / "sprint.yaml"
    if local_sprint_yaml.exists() and local_sprint_yaml not in files:
        files.append(local_sprint_yaml)

    return files


# ---------------------------------------------------------------------------
# MANIFEST
# ---------------------------------------------------------------------------

def _build_manifest(files: list[tuple[str, bytes]]) -> str:
    """Build MANIFEST.json content for the gist."""
    entries = []
    for filename, content in files:
        sha256 = hashlib.sha256(content).hexdigest()
        entries.append({
            "path": filename,
            "size": len(content),
            "sha256": sha256,
        })
    manifest = {
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "commander_version": _commander_version(),
        "files": entries,
    }
    return json.dumps(manifest, indent=2)


# ---------------------------------------------------------------------------
# Core gist operations
# ---------------------------------------------------------------------------

def backup_config_to_gist(
    config_files: list[Path],
    gist_id: Optional[str] = None,
) -> str:
    """Push config_files to a private GitHub gist.

    If gist_id is None, creates a new private gist and persists the returned ID.
    If gist_id is given, updates the existing gist.
    Returns the gist ID.

    Secrets are redacted from text files before upload.
    Raises on subprocess failure.
    """
    # Build file name -> content mapping
    file_pairs: list[tuple[str, bytes]] = []
    for path in config_files:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            log.warning("backup: cannot read %s: %s", path, exc)
            continue

        # Redact secrets from text files
        try:
            text = raw.decode("utf-8")
            text = _redact_secrets(text)
            content = text.encode("utf-8")
        except UnicodeDecodeError:
            # Binary file — skip redaction
            content = raw

        # Use the filename as the gist file name (append parent dir for sprint.yaml
        # to avoid collision when multiple projects have sprint.yaml)
        if path.name == "sprint.yaml":
            parent = path.parent.parent.name  # .commander -> project dir name
            gist_filename = f"sprint.yaml.{parent}"
        elif path.name == ".env":
            gist_filename = "dashboard.env"
        else:
            gist_filename = path.name

        file_pairs.append((gist_filename, content))

    if not file_pairs:
        raise ValueError("No files to back up")

    # Add MANIFEST
    manifest_content = _build_manifest(file_pairs).encode("utf-8")
    file_pairs.append(("MANIFEST.json", manifest_content))

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Write all files to temp dir
        for filename, content in file_pairs:
            (tmp / filename).write_bytes(content)

        file_paths = [str(tmp / fn) for fn, _ in file_pairs]

        if gist_id is None:
            # Create new private gist
            description = "Commander config backup (secrets redacted)"
            result = subprocess.run(
                ["gh", "gist", "create", "--private", "--desc", description] + file_paths,
                capture_output=True,
                text=True,
                check=True,
            )
            # gh gist create prints the gist URL on stdout; extract the ID from it
            output = result.stdout.strip()
            # URL form: https://gist.github.com/<user>/<id>
            gist_id = output.rstrip("/").split("/")[-1]
        else:
            # Update existing gist
            subprocess.run(
                ["gh", "gist", "edit", gist_id] + file_paths,
                capture_output=True,
                text=True,
                check=True,
            )

    return gist_id


def restore_config_from_gist(gist_id: str, target_dir: Path) -> None:
    """Fetch all files from the gist and write them to target_dir.

    Each file is written with its gist filename. If MANIFEST.json is present,
    it is written as well so the operator can verify checksums.
    """
    # gh gist view --files lists filenames
    result = subprocess.run(
        ["gh", "gist", "view", gist_id, "--files"],
        capture_output=True,
        text=True,
        check=True,
    )
    filenames = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in filenames:
        file_result = subprocess.run(
            ["gh", "gist", "view", gist_id, "--filename", filename, "--raw"],
            capture_output=True,
            text=True,
            check=True,
        )
        out_path = target_dir / filename
        out_path.write_text(file_result.stdout, encoding="utf-8")
        print(f"  restored: {out_path}")


# ---------------------------------------------------------------------------
# High-level backup runner (used by scheduler and startup)
# ---------------------------------------------------------------------------

def run_backup() -> None:
    """Collect files and push to gist. Updates module-level state.

    Never raises — logs errors and updates last_error instead.
    """
    config = _load_backup_config()
    gist_id: Optional[str] = config.get("gist_id")

    try:
        files = _collect_config_files()
        if not files:
            log.info("backup: no config files found, skipping")
            _update_state(last_error="no config files found")
            return

        new_gist_id = backup_config_to_gist(files, gist_id=gist_id)

        # Persist gist ID if new
        if new_gist_id != gist_id:
            config["gist_id"] = new_gist_id
            _save_backup_config(config)

        gist_url = f"https://gist.github.com/{new_gist_id}"
        now = datetime.now(timezone.utc).isoformat()

        _update_state(
            last_backup_at=now,
            gist_id=new_gist_id,
            gist_url=gist_url,
            file_count=len(files),
            last_error=None,
        )
        log.info("backup: completed — gist %s (%d files)", new_gist_id, len(files))

    except Exception as exc:
        error_msg = str(exc)
        log.error("backup: failed — %s", error_msg)
        _update_state(last_error=error_msg)


def _run_backup_in_thread() -> None:
    """Wrapper that runs run_backup() in the current thread (called from Timer)."""
    try:
        run_backup()
    except Exception as exc:  # belt-and-suspenders: run_backup never raises
        log.error("backup thread unexpected error: %s", exc)


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

_scheduler_lock = threading.Lock()
_scheduler_timer: Optional[threading.Timer] = None
_scheduler_started = False


def _schedule_next() -> None:
    """Self-rescheduling timer tick: run backup then schedule the next tick."""
    global _scheduler_timer
    _run_backup_in_thread()
    with _scheduler_lock:
        _scheduler_timer = threading.Timer(_BACKUP_INTERVAL_SECONDS, _schedule_next)
        _scheduler_timer.daemon = True
        _scheduler_timer.start()


def start_backup_scheduler() -> None:
    """Start the 6-hour self-rescheduling background timer.

    Safe to call multiple times — only one scheduler runs at a time.
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    timer = threading.Timer(_BACKUP_INTERVAL_SECONDS, _schedule_next)
    timer.daemon = True
    timer.start()

    with _scheduler_lock:
        global _scheduler_timer
        _scheduler_timer = timer

    log.info("backup: 6-hour scheduler started")


def schedule_backup() -> None:
    """Trigger a one-shot backup in a background thread.

    Call this after any successful write to projects.json or sprint.yaml.
    Never blocks the caller.
    """
    t = threading.Thread(target=_run_backup_in_thread, daemon=True, name="backup-oneshot")
    t.start()


def schedule_startup_backup(delay_seconds: int = 30) -> None:
    """Fire a one-shot backup after delay_seconds (default 30).

    Called from the FastAPI lifespan on server startup.
    """
    def _delayed():
        import time as _time
        _time.sleep(delay_seconds)
        _run_backup_in_thread()

    t = threading.Thread(target=_delayed, daemon=True, name="backup-startup")
    t.start()
    log.info("backup: startup backup scheduled in %ds", delay_seconds)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_restore(args: list[str]) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m services.sprint_manager.backup restore",
        description="Restore commander config files from a GitHub gist.",
    )
    parser.add_argument("--gist-id", required=True, help="Gist ID to restore from")
    parser.add_argument(
        "--target-dir",
        default=".",
        help="Directory to write restored files into (default: current dir)",
    )
    parsed = parser.parse_args(args)
    target = Path(parsed.target_dir).expanduser().resolve()
    print(f"Restoring from gist {parsed.gist_id} into {target} ...")
    restore_config_from_gist(parsed.gist_id, target)
    print("Restore complete.")


def main() -> None:
    """CLI entry point: python -m services.sprint_manager.backup <subcommand>."""
    if len(sys.argv) < 2:
        print("Usage: python -m services.sprint_manager.backup restore --gist-id <id>")
        sys.exit(1)
    sub = sys.argv[1]
    if sub == "restore":
        _cli_restore(sys.argv[2:])
    else:
        print(f"Unknown subcommand: {sub!r}")
        print("Available: restore")
        sys.exit(1)


if __name__ == "__main__":
    main()
