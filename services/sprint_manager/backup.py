"""
Backup module: push commander config files to a private GitHub gist, and the
authority DB (commander.db) as a SQL dump to a private GitHub repo.

Public API:
  backup_config_to_gist(config_files, gist_id)  -> str (gist_id)
  restore_config_from_gist(gist_id, target_dir)
  backup_db_to_repo(repo_url, db_path)          -> dict (db_dump manifest entry)
  restore_db(source, target, force=False)       -> dict (restore summary)

The DB dump is taken via SQLite's online-backup API (no torn reads under
concurrent writes), serialised to db_dump.sql, and committed+pushed to the repo
named by the COMMANDER_BACKUP_REPO env var. The cache/clone of that repo lives
under .commander/backup-repo/. When COMMANDER_BACKUP_REPO is unset the DB step
is skipped silently (config/gist backup is unaffected).

Backup triggers are managed by schedule_backup() / start_backup_scheduler().
Status is exposed via get_backup_status() for the GET /api/backup/status endpoint.

CLI usage:
  python -m services.sprint_manager.backup restore --gist-id <id>
  python -m services.sprint_manager.backup restore-db --from <repo|path> \
      --target <db-path> [--force]
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import sqlite3
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

# Env var naming the private repo that holds the authority-DB SQL dump.
_BACKUP_REPO_ENV = "COMMANDER_BACKUP_REPO"

# File names written into the backup repo.
_DB_DUMP_FILENAME = "db_dump.sql"
_DB_MANIFEST_FILENAME = "MANIFEST.json"

# Authority-DB tables whose row counts are recorded in the MANIFEST and verified
# on restore. Tables absent from the live DB are silently ignored.
_KEY_TABLES = [
    "agents",
    "session_events",
    "events",
    "token_usage",
    "project_events",
    "docs_freshness_warnings",
    "ticket_status",
]

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
# Authority-DB backup (SQL dump -> private GitHub repo)
# ---------------------------------------------------------------------------

def _backup_repo_url() -> Optional[str]:
    """Return the backup repo URL from COMMANDER_BACKUP_REPO, or None if unset/blank.

    The repo URL is sourced *solely* from this env var — there is no config-file
    fallback, by design (AC10).
    """
    val = os.environ.get(_BACKUP_REPO_ENV, "").strip()
    return val or None


def _backup_repo_dir() -> Path:
    """Local clone/cache directory for the backup repo: .commander/backup-repo/."""
    return _COMMANDER_DIR / "backup-repo"


def _resolve_db_path() -> Optional[Path]:
    """Resolve the authority DB path.

    Prefers the DB_PATH env var (the same var the dashboard uses); falls back to
    apps/dashboard/commander.db relative to this module. Returns None if nothing
    resolvable exists on disk.
    """
    raw = os.environ.get("DB_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.exists() else None
    # services/sprint_manager/backup.py -> repo root -> apps/dashboard/commander.db
    candidate = Path(__file__).resolve().parent.parent.parent / "apps" / "dashboard" / "commander.db"
    return candidate if candidate.exists() else None


def _snapshot_db(db_path: Path) -> sqlite3.Connection:
    """Take an atomic in-memory snapshot of db_path via the SQLite online-backup API.

    Opens the source read-only and copies it into a fresh in-memory database, so
    the snapshot is consistent even under concurrent writers (no torn reads). The
    caller owns the returned connection and must close it.
    """
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        snap = sqlite3.connect(":memory:")
        src.backup(snap)  # atomic page-by-page online backup
    finally:
        src.close()
    return snap


def _table_row_counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    """Return {table: row_count} for the tables that exist in conn."""
    existing = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    counts: dict[str, int] = {}
    for table in tables:
        if table in existing:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts


def _dump_db_to_sql(db_path: Path) -> str:
    """Return the full SQL text of an atomic snapshot of db_path (iterdump)."""
    snap = _snapshot_db(db_path)
    try:
        return "\n".join(snap.iterdump()) + "\n"
    finally:
        snap.close()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in cwd, raising on failure."""
    return subprocess.run(
        ["git", "-C", str(cwd)] + args,
        capture_output=True,
        text=True,
        check=True,
    )


def _ensure_repo_clone(repo_url: str, cache_dir: Path) -> None:
    """Ensure cache_dir is a working clone of repo_url, pulling latest if it exists."""
    git_dir = cache_dir / ".git"
    if git_dir.is_dir():
        # Best-effort refresh; ignore failures (e.g. empty remote, offline).
        try:
            _git(["fetch", "origin"], cache_dir)
            _git(["reset", "--hard", "origin/HEAD"], cache_dir)
        except subprocess.CalledProcessError:
            pass
    else:
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir)
        subprocess.run(
            ["git", "clone", repo_url, str(cache_dir)],
            capture_output=True, text=True, check=True,
        )
    # Ensure a commit identity exists locally so commits never fail in CI/headless.
    try:
        _git(["config", "user.email", "commander-backup@localhost"], cache_dir)
        _git(["config", "user.name", "Commander Backup"], cache_dir)
    except subprocess.CalledProcessError:
        pass


def backup_db_to_repo(
    repo_url: str,
    db_path: Path,
    *,
    cache_dir: Optional[Path] = None,
) -> dict:
    """Dump db_path to db_dump.sql and commit+push it to repo_url.

    Writes db_dump.sql plus a MANIFEST.json (with a `db_dump` entry holding the
    dump's sha256 and per-key-table row counts) into the repo clone under
    cache_dir (default: .commander/backup-repo/), then commits and pushes.

    Returns the `db_dump` MANIFEST entry dict.
    """
    if cache_dir is None:
        cache_dir = _backup_repo_dir()

    # Take an atomic snapshot once; derive both the SQL text and row counts from it.
    snap = _snapshot_db(db_path)
    try:
        sql = "\n".join(snap.iterdump()) + "\n"
        row_counts = _table_row_counts(snap, _KEY_TABLES)
    finally:
        snap.close()

    sql_bytes = sql.encode("utf-8")
    sha256 = hashlib.sha256(sql_bytes).hexdigest()
    db_dump_entry = {
        "path": _DB_DUMP_FILENAME,
        "sha256": sha256,
        "size": len(sql_bytes),
        "row_counts": row_counts,
    }

    _ensure_repo_clone(repo_url, cache_dir)

    (cache_dir / _DB_DUMP_FILENAME).write_bytes(sql_bytes)

    manifest = {
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "commander_version": _commander_version(),
        "db_dump": db_dump_entry,
    }
    (cache_dir / _DB_MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    _git(["add", _DB_DUMP_FILENAME, _DB_MANIFEST_FILENAME], cache_dir)
    # Commit only if there is something to commit.
    status = _git(["status", "--porcelain"], cache_dir)
    if status.stdout.strip():
        _git(["commit", "-m", "Backup authority DB dump"], cache_dir)
    subprocess.run(
        ["git", "-C", str(cache_dir), "push", "origin", "HEAD"],
        capture_output=True, text=True, check=True,
    )

    log.info(
        "backup: pushed db_dump.sql to %s (sha256=%s, tables=%d)",
        repo_url, sha256[:12], len(row_counts),
    )
    return db_dump_entry


def _run_db_backup_step() -> None:
    """Run the DB-backup step if COMMANDER_BACKUP_REPO is set; else skip silently.

    Never raises — the DB step must not break the config/gist backup.
    """
    repo_url = _backup_repo_url()
    if not repo_url:
        log.info(
            "backup: %s unset — skipping authority-DB backup step",
            _BACKUP_REPO_ENV,
        )
        return
    try:
        db_path = _resolve_db_path()
        if not db_path:
            log.info("backup: no authority DB found — skipping DB backup step")
            return
        backup_db_to_repo(repo_url, db_path)
    except Exception as exc:  # noqa: BLE001 — DB failures must not break gist backup
        log.error("backup: authority-DB backup failed — %s", exc)


# ---------------------------------------------------------------------------
# Authority-DB restore (rebuild commander.db from db_dump.sql)
# ---------------------------------------------------------------------------

def _looks_like_repo_url(source: str) -> bool:
    """Heuristic: does `source` look like a git remote rather than a local path?"""
    s = source.strip()
    if "://" in s or s.startswith("git@"):
        return True
    if s.endswith(".git"):
        return True
    return False


def _resolve_dump_dir(source: str) -> tuple[Path, Optional[Path]]:
    """Resolve `source` to a directory holding db_dump.sql (+ optional MANIFEST.json).

    `source` may be a git repo URL (cloned into a temp dir), a directory, or the
    db_dump.sql file itself. Returns (dump_file, manifest_file_or_None).
    """
    if _looks_like_repo_url(source):
        tmp = Path(tempfile.mkdtemp(prefix="commander-restore-"))
        clone = tmp / "repo"
        subprocess.run(
            ["git", "clone", source, str(clone)],
            capture_output=True, text=True, check=True,
        )
        base = clone
    else:
        p = Path(source).expanduser().resolve()
        if p.is_file():
            dump = p
            manifest = p.parent / _DB_MANIFEST_FILENAME
            return dump, (manifest if manifest.exists() else None)
        base = p

    dump = base / _DB_DUMP_FILENAME
    if not dump.exists():
        raise FileNotFoundError(f"{_DB_DUMP_FILENAME} not found in {base}")
    manifest = base / _DB_MANIFEST_FILENAME
    return dump, (manifest if manifest.exists() else None)


def restore_db(
    source: str,
    target: Path,
    *,
    force: bool = False,
) -> dict:
    """Rebuild a commander.db at `target` from db_dump.sql found at `source`.

    `source` is a git repo URL or a local path (a directory holding db_dump.sql,
    or the db_dump.sql file directly). Refuses to overwrite an existing `target`
    unless force=True. When a MANIFEST.json is available, the restored row counts
    are verified against the recorded `db_dump.row_counts`.

    Returns a summary dict: {"target", "row_counts"}.
    """
    target = Path(target).expanduser()
    if target.exists() and not force:
        raise FileExistsError(
            f"refusing to overwrite existing DB at {target} (pass --force to override)"
        )

    dump_file, manifest_file = _resolve_dump_dir(source)
    sql = dump_file.read_text(encoding="utf-8")

    # Rebuild into the target. Remove any pre-existing file first (force path).
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    conn = sqlite3.connect(target)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()

    # Verify row counts against MANIFEST if present.
    verify_conn = sqlite3.connect(target)
    try:
        restored_counts = _table_row_counts(verify_conn, _KEY_TABLES)
    finally:
        verify_conn.close()

    if manifest_file is not None:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        expected = manifest.get("db_dump", {}).get("row_counts", {})
        mismatches = {
            t: (expected[t], restored_counts.get(t))
            for t in expected
            if restored_counts.get(t) != expected[t]
        }
        if mismatches:
            raise ValueError(
                f"restored row counts do not match MANIFEST: {mismatches}"
            )

    log.info("backup: restored DB into %s (%s)", target, restored_counts)
    return {"target": str(target), "row_counts": restored_counts}


# ---------------------------------------------------------------------------
# High-level backup runner (used by scheduler and startup)
# ---------------------------------------------------------------------------

def run_backup() -> None:
    """Collect files and push to gist. Updates module-level state.

    Never raises — logs errors and updates last_error instead.
    """
    config = _load_backup_config()
    gist_id: Optional[str] = config.get("gist_id")

    # --- Config/gist backup step --------------------------------------------
    try:
        files = _collect_config_files()
        if not files:
            log.info("backup: no config files found, skipping gist step")
            _update_state(last_error="no config files found")
        else:
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

    # --- Authority-DB backup step (runs after the config/gist step) ----------
    # Skipped silently when COMMANDER_BACKUP_REPO is unset; never raises.
    _run_db_backup_step()


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


def _cli_restore_db(args: list[str]) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m services.sprint_manager.backup restore-db",
        description="Rebuild commander.db from a db_dump.sql in the backup repo or a path.",
    )
    parser.add_argument(
        "--from",
        dest="source",
        required=True,
        help="Backup repo URL or local path (dir containing db_dump.sql, or the file).",
    )
    parser.add_argument(
        "--target",
        required=True,
        help="Path to write the rebuilt DB into (e.g. /tmp/commander_test.db).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the target DB if it already exists.",
    )
    parsed = parser.parse_args(args)
    target = Path(parsed.target).expanduser()
    print(f"Restoring DB from {parsed.source} into {target} ...")
    try:
        summary = restore_db(parsed.source, target, force=parsed.force)
    except FileExistsError as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    print(f"Restore complete. Row counts: {summary['row_counts']}")


def main() -> None:
    """CLI entry point: python -m services.sprint_manager.backup <subcommand>."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m services.sprint_manager.backup restore --gist-id <id>")
        print("  python -m services.sprint_manager.backup restore-db --from <repo|path> "
              "--target <db-path> [--force]")
        sys.exit(1)
    sub = sys.argv[1]
    if sub == "restore":
        _cli_restore(sys.argv[2:])
    elif sub == "restore-db":
        _cli_restore_db(sys.argv[2:])
    else:
        print(f"Unknown subcommand: {sub!r}")
        print("Available: restore, restore-db")
        sys.exit(1)


if __name__ == "__main__":
    main()
