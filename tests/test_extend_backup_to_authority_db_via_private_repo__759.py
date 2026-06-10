"""Tests for issue #759: Extend backup to authority DB via private repo.

This is a backend/CLI feature (SQLite SQL-dump backup to a private repo + a
`restore-db` CLI). It has no HTTP surface, so tests exercise the module and CLI
directly against temp DBs and *local* git repos used as the remote — no GitHub
access or network required.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import services.sprint_manager.backup as backup


REPO_ROOT = Path(backup.__file__).resolve().parent.parent.parent


# --- Fixtures ---------------------------------------------------------------

def _make_db(path: Path, agents=3, events=5, token_usage=2) -> None:
    """Create a small commander-like DB with a few key tables and rows."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT)")
        conn.execute("CREATE TABLE token_usage (id INTEGER PRIMARY KEY, n INTEGER)")
        conn.executemany("INSERT INTO agents (name) VALUES (?)",
                         [(f"a{i}",) for i in range(agents)])
        conn.executemany("INSERT INTO events (kind) VALUES (?)",
                         [(f"e{i}",) for i in range(events)])
        conn.executemany("INSERT INTO token_usage (n) VALUES (?)",
                         [(i,) for i in range(token_usage)])
        conn.commit()
    finally:
        conn.close()


def _init_bare_remote(path: Path) -> str:
    """Create a bare git repo with one empty commit on a default branch.

    Returns a file:// URL usable as a clone source.
    """
    subprocess.run(["git", "init", "--bare", str(path)],
                   check=True, capture_output=True, text=True)
    # Seed the bare repo with an initial commit so `git clone` yields a checkout
    # with a HEAD (older git versions choke cloning a truly empty bare repo).
    seed = path.parent / "seed"
    subprocess.run(["git", "clone", str(path), str(seed)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(seed), "config", "user.name", "t"], check=True)
    (seed / "README").write_text("seed\n")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-m", "seed"],
                   check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(seed), "push", "origin", "HEAD"],
                   check=True, capture_output=True, text=True)
    return str(path)


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "commander.db"
    _make_db(p)
    return p


@pytest.fixture
def bare_remote(tmp_path):
    return _init_bare_remote(tmp_path / "remote.git")


# --- Acceptance Criteria ----------------------------------------------------

def test_extend_backup__run_backup_includes_db_step_after_gist(monkeypatch, db_path, bare_remote):
    # AC: run_backup() includes a DB backup step that runs after the gist step.
    calls = []

    def fake_gist(files, gist_id=None):
        calls.append("gist")
        return "fakegist"

    def fake_db(repo_url, dbp, **kw):
        calls.append("db")
        return {"path": "db_dump.sql"}

    monkeypatch.setattr(backup, "backup_config_to_gist", fake_gist)
    monkeypatch.setattr(backup, "backup_db_to_repo", fake_db)
    monkeypatch.setattr(backup, "_collect_config_files", lambda: [db_path])
    monkeypatch.setattr(backup, "_resolve_db_path", lambda: db_path)
    monkeypatch.setattr(backup, "_save_backup_config", lambda c: None)
    monkeypatch.setattr(backup, "_load_backup_config", lambda: {})
    monkeypatch.setenv(backup._BACKUP_REPO_ENV, bare_remote)

    backup.run_backup()

    assert calls == ["gist", "db"], f"expected gist then db, got {calls}"


def test_extend_backup__snapshot_no_torn_read(db_path):
    # AC: Dump via SQLite online-backup API — consistent snapshot under writes.
    snap = backup._snapshot_db(db_path)
    try:
        # snapshot is an independent in-memory copy; mutating source after does
        # not change it.
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO agents (name) VALUES ('late')")
        conn.commit()
        conn.close()
        snap_count = snap.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    finally:
        snap.close()
    assert snap_count == 3, "snapshot should be isolated from post-snapshot writes"


def test_extend_backup__dump_written_and_pushed(db_path, bare_remote, tmp_path):
    # AC: db_dump.sql (SQL text) committed+pushed to COMMANDER_BACKUP_REPO.
    cache = tmp_path / "backup-repo"
    backup.backup_db_to_repo(bare_remote, db_path, cache_dir=cache)

    # Clone the bare remote fresh and confirm the pushed dump is there.
    verify = tmp_path / "verify"
    subprocess.run(["git", "clone", bare_remote, str(verify)],
                   check=True, capture_output=True, text=True)
    dump = verify / "db_dump.sql"
    assert dump.exists(), "db_dump.sql not present in pushed remote"
    text = dump.read_text()
    assert "CREATE TABLE agents" in text and "INSERT INTO" in text


def test_extend_backup__unset_repo_skips_db_step_silently(monkeypatch, db_path):
    # AC: COMMANDER_BACKUP_REPO unset -> DB step skipped silently, no crash;
    # config/gist backup still completes.
    monkeypatch.delenv(backup._BACKUP_REPO_ENV, raising=False)
    gist_called = []
    monkeypatch.setattr(backup, "backup_config_to_gist",
                        lambda files, gist_id=None: gist_called.append(1) or "g")
    monkeypatch.setattr(backup, "_collect_config_files", lambda: [db_path])
    monkeypatch.setattr(backup, "_save_backup_config", lambda c: None)
    monkeypatch.setattr(backup, "_load_backup_config", lambda: {})

    def boom(*a, **k):
        raise AssertionError("DB step must not run when repo unset")

    monkeypatch.setattr(backup, "backup_db_to_repo", boom)

    # Must not raise; gist step must still run.
    backup.run_backup()
    assert gist_called == [1]
    # Direct check of the step helper too.
    assert backup._run_db_backup_step() is None


def test_extend_backup__manifest_has_db_dump_sha_and_rowcounts(db_path, bare_remote, tmp_path):
    # AC: MANIFEST gains a db_dump entry with sha256 and row counts.
    cache = tmp_path / "backup-repo"
    entry = backup.backup_db_to_repo(bare_remote, db_path, cache_dir=cache)

    assert entry["path"] == "db_dump.sql"
    assert len(entry["sha256"]) == 64
    assert entry["row_counts"]["agents"] == 3
    assert entry["row_counts"]["events"] == 5
    assert entry["row_counts"]["token_usage"] == 2

    manifest = json.loads((cache / "MANIFEST.json").read_text())
    assert "db_dump" in manifest
    assert manifest["db_dump"]["sha256"] == entry["sha256"]
    # sha256 in manifest matches the actual pushed file bytes.
    actual = hashlib.sha256((cache / "db_dump.sql").read_bytes()).hexdigest()
    assert actual == entry["sha256"]


def test_extend_backup__scheduler_and_startup_trigger_db_push(monkeypatch):
    # AC: 6-hour scheduler and startup backup both trigger the DB push.
    assert backup._BACKUP_INTERVAL_SECONDS == 6 * 60 * 60

    # run_backup() unconditionally calls the DB step after the gist step
    # (capture source before patching run_backup below).
    import inspect
    src = inspect.getsource(backup.run_backup)
    assert "_run_db_backup_step()" in src

    ran = []
    monkeypatch.setattr(backup, "run_backup", lambda: ran.append(1))
    # The single worker both the scheduler tick and the startup path funnel
    # through is _run_backup_in_thread -> run_backup, which invokes the DB step.
    backup._run_backup_in_thread()
    assert ran == [1]


def test_extend_backup__restore_db_cli_rebuilds_target(db_path, bare_remote, tmp_path):
    # AC: restore-db --from <repo> rebuilds commander.db into a target path.
    cache = tmp_path / "backup-repo"
    backup.backup_db_to_repo(bare_remote, db_path, cache_dir=cache)

    target = tmp_path / "restored.db"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "services.sprint_manager.backup", "restore-db",
         "--from", bare_remote, "--target", str(target)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"CLI failed: {proc.stderr}\n{proc.stdout}"
    assert target.exists()
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 3
    finally:
        conn.close()


def test_extend_backup__restore_db_refuses_overwrite_without_force(db_path, bare_remote, tmp_path):
    # AC: restore-db refuses to overwrite a live DB without --force.
    cache = tmp_path / "backup-repo"
    backup.backup_db_to_repo(bare_remote, db_path, cache_dir=cache)

    target = tmp_path / "live.db"
    target.write_text("DO NOT TOUCH")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-m", "services.sprint_manager.backup", "restore-db",
         "--from", bare_remote, "--target", str(target)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, env=env,
    )
    assert proc.returncode != 0, "CLI should fail without --force"
    assert target.read_text() == "DO NOT TOUCH", "existing file must be untouched"


def test_extend_backup__restore_db_force_overwrites(db_path, bare_remote, tmp_path):
    # AC: --force overwrites; restored DB valid (companion to the refuse test).
    cache = tmp_path / "backup-repo"
    backup.backup_db_to_repo(bare_remote, db_path, cache_dir=cache)

    target = tmp_path / "live.db"
    target.write_text("DO NOT TOUCH")
    summary = backup.restore_db(bare_remote, target, force=True)
    assert summary["row_counts"]["agents"] == 3
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5
    finally:
        conn.close()


def test_extend_backup__restored_rowcounts_match_manifest(db_path, bare_remote, tmp_path):
    # AC: restored DB key-table row counts match the MANIFEST counts.
    cache = tmp_path / "backup-repo"
    entry = backup.backup_db_to_repo(bare_remote, db_path, cache_dir=cache)

    target = tmp_path / "restored.db"
    summary = backup.restore_db(bare_remote, target)
    assert summary["row_counts"] == entry["row_counts"]
    # And a mismatch is actually detected: corrupt the manifest, expect failure.
    bad_dir = tmp_path / "baddir"
    bad_dir.mkdir()
    (bad_dir / "db_dump.sql").write_text((cache / "db_dump.sql").read_text())
    bad_manifest = json.loads((cache / "MANIFEST.json").read_text())
    bad_manifest["db_dump"]["row_counts"]["agents"] = 999
    (bad_dir / "MANIFEST.json").write_text(json.dumps(bad_manifest))
    with pytest.raises(ValueError):
        backup.restore_db(str(bad_dir), tmp_path / "restored2.db")


def test_extend_backup__cache_under_commander_and_url_from_env_only(monkeypatch):
    # AC: cache lives under .commander/backup-repo/; URL sourced solely from env.
    assert backup._backup_repo_dir().name == "backup-repo"
    assert backup._backup_repo_dir().parent.name == ".commander"

    monkeypatch.delenv(backup._BACKUP_REPO_ENV, raising=False)
    assert backup._backup_repo_url() is None
    monkeypatch.setenv(backup._BACKUP_REPO_ENV, "https://example.com/repo.git")
    assert backup._backup_repo_url() == "https://example.com/repo.git"
    # Blank/whitespace is treated as unset.
    monkeypatch.setenv(backup._BACKUP_REPO_ENV, "   ")
    assert backup._backup_repo_url() is None
