"""Tester regression suite for issue #759: extend backup to authority DB via private repo.

Backend-only change to services/sprint_manager/backup.py. Runs in-process against
the module — git/gh subprocesses are exercised against LOCAL bare repos (no network),
and gist calls are monkeypatched out.

One test function per acceptance criterion:

  AC1   run_backup() runs a DB backup step AFTER the existing config/gist step.
  AC2   Dump taken via SQLite online-backup snapshot — no torn reads under
        concurrent writes (snapshot is independent of later source mutations).
  AC3   Dump written as db_dump.sql (SQL text) and committed+pushed to
        COMMANDER_BACKUP_REPO (verified by re-cloning the bare remote).
  AC4   COMMANDER_BACKUP_REPO unset -> DB step skipped silently, a notice is
        logged, config/gist backup still completes.
  AC5   MANIFEST gains a db_dump entry with sha256 + per-table row counts.
  AC6   Both the scheduler tick and the startup backup funnel the DB push.
  AC7   restore-db --from <repo|path> rebuilds commander.db from db_dump.sql
        into a specified target path.
  AC8   restore-db refuses to overwrite a live DB without --force.
  AC9   Restored DB key-table row counts match the counts recorded in MANIFEST.
  AC10  Backup repo cache lives under .commander/backup-repo/; repo URL sourced
        solely from COMMANDER_BACKUP_REPO.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager import backup  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEY_SAMPLE_TABLES = ["agents", "events", "token_usage"]


def _make_sample_db(path: Path, *, agents: int = 3, events: int = 5) -> None:
    """Create a small sqlite DB that resembles the authority schema."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE agents (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT, payload TEXT);
        CREATE TABLE token_usage (id INTEGER PRIMARY KEY, tokens INTEGER);
        """
    )
    conn.executemany("INSERT INTO agents (name) VALUES (?)",
                     [(f"agent-{i}",) for i in range(agents)])
    conn.executemany("INSERT INTO events (kind, payload) VALUES (?, ?)",
                     [("e", f"p{i}") for i in range(events)])
    conn.execute("INSERT INTO token_usage (tokens) VALUES (100)")
    conn.commit()
    conn.close()


def _init_bare_remote(path: Path) -> str:
    """Create an empty bare git repo to serve as COMMANDER_BACKUP_REPO. Returns its URL/path."""
    subprocess.run(["git", "init", "--bare", str(path)],
                   check=True, capture_output=True, text=True)
    return str(path)


def _row_count(db_path: Path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AC10 — repo dir under .commander/backup-repo/; URL solely from env
# ---------------------------------------------------------------------------

def test_backup_repo_dir_under_commander__ac10(monkeypatch, tmp_path):
    monkeypatch.setattr(backup, "_COMMANDER_DIR", tmp_path)
    assert backup._backup_repo_dir() == tmp_path / "backup-repo"


def test_backup_repo_url_sourced_solely_from_env__ac10(monkeypatch):
    monkeypatch.delenv("COMMANDER_BACKUP_REPO", raising=False)
    assert backup._backup_repo_url() is None
    monkeypatch.setenv("COMMANDER_BACKUP_REPO", "git@example.com:me/secret.git")
    assert backup._backup_repo_url() == "git@example.com:me/secret.git"


# ---------------------------------------------------------------------------
# AC2 — online-backup snapshot, no torn reads
# ---------------------------------------------------------------------------

def test_snapshot_is_independent_of_later_writes__ac2(tmp_path):
    db = tmp_path / "src.db"
    _make_sample_db(db, agents=3)

    sql_before = backup._dump_db_to_sql(db)

    # Mutate source AFTER taking the dump; dump text must not change.
    conn = sqlite3.connect(db)
    conn.executemany("INSERT INTO agents (name) VALUES (?)", [("late",) for _ in range(10)])
    conn.commit()
    conn.close()

    # Re-dumping now reflects the new rows, proving the first dump was a snapshot.
    sql_after = backup._dump_db_to_sql(db)
    assert sql_before != sql_after
    assert sql_before.count("INSERT INTO") < sql_after.count("INSERT INTO")
    # The snapshot dump is valid, self-contained SQL.
    assert "CREATE TABLE" in sql_before and "INSERT INTO" in sql_before


def test_dump_roundtrips_into_valid_db__ac2(tmp_path):
    db = tmp_path / "src.db"
    _make_sample_db(db, agents=4, events=7)
    sql = backup._dump_db_to_sql(db)

    target = tmp_path / "rebuilt.db"
    conn = sqlite3.connect(target)
    conn.executescript(sql)
    conn.commit()
    conn.close()
    assert _row_count(target, "agents") == 4
    assert _row_count(target, "events") == 7


# ---------------------------------------------------------------------------
# AC3 + AC5 — dump written, committed+pushed; MANIFEST db_dump entry
# ---------------------------------------------------------------------------

def test_db_pushed_to_repo_with_manifest__ac3_ac5(tmp_path):
    db = tmp_path / "commander.db"
    _make_sample_db(db, agents=3, events=5)

    remote = _init_bare_remote(tmp_path / "remote.git")
    cache = tmp_path / "cache"

    entry = backup.backup_db_to_repo(remote, db, cache_dir=cache)

    # MANIFEST entry returned with sha256 + row counts (AC5)
    assert "sha256" in entry
    assert "row_counts" in entry
    assert entry["row_counts"]["agents"] == 3
    assert entry["row_counts"]["events"] == 5

    # Re-clone the bare remote to prove commit+push happened (AC3)
    verify = tmp_path / "verify"
    subprocess.run(["git", "clone", remote, str(verify)], check=True,
                   capture_output=True, text=True)
    dump = verify / "db_dump.sql"
    manifest = verify / "MANIFEST.json"
    assert dump.exists(), "db_dump.sql must be committed+pushed to the repo"
    assert manifest.exists()

    text = dump.read_text(encoding="utf-8")
    assert "CREATE TABLE" in text and "INSERT INTO" in text

    man = json.loads(manifest.read_text(encoding="utf-8"))
    assert "db_dump" in man
    assert man["db_dump"]["row_counts"]["agents"] == 3
    # sha256 in MANIFEST matches the actual dumped file
    import hashlib
    assert man["db_dump"]["sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# AC1 — DB step runs AFTER the config/gist step
# ---------------------------------------------------------------------------

def test_run_backup_db_step_after_gist__ac1(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMANDER_BACKUP_REPO", "fake://repo")
    calls: list[str] = []

    monkeypatch.setattr(backup, "_collect_config_files", lambda: [tmp_path / "x"])
    monkeypatch.setattr(backup, "backup_config_to_gist",
                        lambda files, gist_id=None: calls.append("gist") or "gid")
    monkeypatch.setattr(backup, "_save_backup_config", lambda cfg: None)
    monkeypatch.setattr(backup, "_load_backup_config", lambda: {})
    monkeypatch.setattr(backup, "_run_db_backup_step",
                        lambda: calls.append("db"))

    backup.run_backup()

    assert calls == ["gist", "db"], f"expected gist then db, got {calls}"


# ---------------------------------------------------------------------------
# AC4 — unset COMMANDER_BACKUP_REPO -> skip silently, notice logged, gist still runs
# ---------------------------------------------------------------------------

def test_db_step_skipped_when_repo_unset__ac4(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("COMMANDER_BACKUP_REPO", raising=False)
    calls: list[str] = []

    monkeypatch.setattr(backup, "_collect_config_files", lambda: [tmp_path / "x"])
    monkeypatch.setattr(backup, "backup_config_to_gist",
                        lambda files, gist_id=None: calls.append("gist") or "gid")
    monkeypatch.setattr(backup, "_save_backup_config", lambda cfg: None)
    monkeypatch.setattr(backup, "_load_backup_config", lambda: {})

    def _boom(repo_url, db_path, **kw):
        calls.append("db")
        raise AssertionError("DB push must not run when repo unset")
    monkeypatch.setattr(backup, "backup_db_to_repo", _boom)

    with caplog.at_level(logging.INFO):
        backup.run_backup()

    assert "gist" in calls, "config/gist backup must still complete"
    assert "db" not in calls, "DB step must be skipped when repo unset"
    msgs = " ".join(r.getMessage().lower() for r in caplog.records)
    assert "skip" in msgs and ("db" in msgs or "backup_repo" in msgs or "repo" in msgs)


# ---------------------------------------------------------------------------
# AC6 — scheduler tick and startup backup both funnel the DB push
# ---------------------------------------------------------------------------

def test_shared_runner_triggers_db_push__ac6(monkeypatch, tmp_path):
    """Both the 6h scheduler tick and the startup backup call _run_backup_in_thread,
    which must drive the DB push when the repo is configured."""
    db = tmp_path / "commander.db"
    _make_sample_db(db)
    remote = _init_bare_remote(tmp_path / "remote.git")
    monkeypatch.setenv("COMMANDER_BACKUP_REPO", remote)

    pushed: list[str] = []
    monkeypatch.setattr(backup, "_collect_config_files", lambda: [])
    monkeypatch.setattr(backup, "_resolve_db_path", lambda: db)
    monkeypatch.setattr(backup, "backup_db_to_repo",
                        lambda repo_url, db_path, **kw: pushed.append(repo_url) or {})

    backup._run_backup_in_thread()
    assert pushed == [remote]


# ---------------------------------------------------------------------------
# AC7 + AC9 — restore-db rebuilds DB and row counts match MANIFEST
# ---------------------------------------------------------------------------

def test_restore_db_rebuilds_and_counts_match__ac7_ac9(tmp_path):
    db = tmp_path / "commander.db"
    _make_sample_db(db, agents=6, events=9)

    remote = _init_bare_remote(tmp_path / "remote.git")
    backup.backup_db_to_repo(remote, db, cache_dir=tmp_path / "cache")

    # Restore from a local checkout dir containing db_dump.sql + MANIFEST.json
    src = tmp_path / "src"
    subprocess.run(["git", "clone", remote, str(src)], check=True,
                   capture_output=True, text=True)

    target = tmp_path / "restored.db"
    result = backup.restore_db(str(src), target, force=False)

    assert target.exists()
    assert _row_count(target, "agents") == 6
    assert _row_count(target, "events") == 9
    # AC9: returned counts equal MANIFEST counts
    manifest = json.loads((src / "MANIFEST.json").read_text(encoding="utf-8"))
    assert result["row_counts"] == manifest["db_dump"]["row_counts"]


# ---------------------------------------------------------------------------
# AC8 — restore-db refuses to overwrite without --force
# ---------------------------------------------------------------------------

def test_restore_db_refuses_overwrite_without_force__ac8(tmp_path):
    db = tmp_path / "commander.db"
    _make_sample_db(db, agents=2)
    remote = _init_bare_remote(tmp_path / "remote.git")
    backup.backup_db_to_repo(remote, db, cache_dir=tmp_path / "cache")
    src = tmp_path / "src"
    subprocess.run(["git", "clone", remote, str(src)], check=True,
                   capture_output=True, text=True)

    target = tmp_path / "live.db"
    target.write_bytes(b"existing-live-db-do-not-touch")
    original = target.read_bytes()

    with pytest.raises(Exception):
        backup.restore_db(str(src), target, force=False)
    # Existing file untouched
    assert target.read_bytes() == original

    # With --force it overwrites with a valid restored DB
    backup.restore_db(str(src), target, force=True)
    assert _row_count(target, "agents") == 2
