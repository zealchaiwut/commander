import contextlib
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

# ── DB_PATH resolution ────────────────────────────────────────────────────────
_db_path_raw = os.environ.get("DB_PATH", "").strip()
if not _db_path_raw:
    logging.basicConfig(level=logging.ERROR)
    logging.error(
        "DB_PATH environment variable is not set or is blank. "
        "Set DB_PATH in your .env file (e.g. DB_PATH=./commander.db) before starting the server."
    )
    sys.exit(1)

DB_PATH = Path(_db_path_raw)

# ── Relative-path guard (issue #2047) ─────────────────────────────────────────
# A relative DB_PATH is dangerous: its resolved location is determined by the
# CWD at the time the process starts, so a pytest run from the repo root would
# write to <repo-root>/commander.db instead of apps/dashboard/commander.db.
# Resolve it to absolute (relative to this file's directory = apps/dashboard/)
# and log a loud warning so operators know to switch to an absolute path.
if not DB_PATH.is_absolute():
    _resolved_db = (Path(__file__).parent / DB_PATH).resolve()
    logging.warning(
        "DB_PATH %r is relative — resolved to absolute %r. "
        "Relative paths are unsafe: the CWD at process startup determines which "
        "database file gets written.  Set DB_PATH to an absolute path in your "
        ".env to eliminate CWD-dependent behaviour and suppress this warning.",
        _db_path_raw,
        str(_resolved_db),
    )
    DB_PATH = _resolved_db

# Default local-backup directory — sibling .commander/db-backups/ of the DB.
# Tests can monkeypatch this directly.
_LOCAL_BACKUP_DIR = DB_PATH.parent / ".commander" / "db-backups"

# Asia/Bangkok is UTC+7 (no DST)
_BKK_OFFSET = timezone(timedelta(hours=7))

_log = logging.getLogger(__name__)


def set_local_backup_dir(path: Path) -> None:
    """Called by backup.py to register where local rolling backups live."""
    global _LOCAL_BACKUP_DIR
    _LOCAL_BACKUP_DIR = path


def _find_best_backup(backup_dir: Path) -> "Path | None":
    """Return the most recent verified non-empty .bak file, or None.

    Iterates candidates newest-first, skipping zero-byte files and any that
    fail PRAGMA integrity_check.  Returns the first that passes both checks.
    If none pass, returns None so the caller can emit a safe "no valid backup"
    message instead of naming a corrupt file in a restore hint (issue #2036).
    """
    baks = sorted(backup_dir.glob("*.bak"), key=lambda p: p.name, reverse=True)
    for bak in baks:
        try:
            if bak.stat().st_size == 0:
                continue
        except OSError:
            continue
        if check_db_integrity(bak) == "ok":
            return bak
    return None


def _build_restore_hint() -> str:
    """Build the operator restore hint string from the current local backup dir."""
    backup_dir = _LOCAL_BACKUP_DIR
    if backup_dir is not None and backup_dir.is_dir():
        best = _find_best_backup(backup_dir)
        if best is not None:
            return (
                f"\n  Most recent verified backup: {best}"
                f"\n  Restore: cp {best} {DB_PATH}"
                f"\n  Verify:  sqlite3 {DB_PATH} 'PRAGMA integrity_check'"
            )
        all_baks = list(backup_dir.glob("*.bak"))
        if all_baks:
            return (
                f"\n  WARNING: No verified non-empty backup found in {backup_dir}."
                f"\n  {len(all_baks)} backup file(s) exist but all are empty or"
                f" failed integrity_check — do NOT restore from them."
                f"\n  Check off-site backups or attempt: sqlite3 commander.db .recover"
            )
        return f"\n  Local backup dir is empty: {backup_dir}"
    return f"\n  Check {DB_PATH.parent / '.commander' / 'db-backups'} for local backups."


def _startup_integrity_check() -> None:
    """Run integrity_check at startup; raise RuntimeError with restore hint if corrupt.

    The restore hint names the most recent VERIFIED non-empty backup (issue #2036).
    If the backup dir contains only empty or corrupt files, the message says so
    explicitly rather than printing a cp command that would destroy data.
    """
    if not DB_PATH.exists():
        return
    status = check_db_integrity(DB_PATH)
    if status == "ok":
        return
    restore_hint = _build_restore_hint()
    _log.critical("FATAL: commander.db corrupt at startup: %s%s", status, restore_hint)
    raise RuntimeError(
        f"commander.db is corrupt (PRAGMA integrity_check: {status})."
        f" The database cannot be used.{restore_hint}"
    )


_DISK_IO_STR = "disk i/o error"


def handle_runtime_disk_io_error(exc: Exception) -> None:
    """Run integrity_check when a sqlite3.OperationalError('disk I/O error') is caught.

    Called from the mirror-sync loop when a disk-level error surfaces at runtime
    (issue #2012). Runs PRAGMA integrity_check, logs CRITICAL with the restore hint,
    then raises RuntimeError to abort the caller instead of retrying indefinitely.

    No-op for any exception that is not a disk I/O OperationalError so existing
    broad except-and-continue handlers can call this unconditionally.
    """
    if not (isinstance(exc, sqlite3.OperationalError)
            and _DISK_IO_STR in str(exc).lower()):
        return
    status = check_db_integrity(DB_PATH)
    restore_hint = _build_restore_hint()
    _log.critical(
        "FATAL: runtime disk I/O error — integrity: %s%s", status, restore_hint
    )
    raise RuntimeError(
        f"commander.db disk I/O error at runtime (integrity: {status}).{restore_hint}"
    )


def utc_now() -> str:
    """Return the current UTC instant as YYYY-MM-DDTHH:MM:SSZ (no microseconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc_ts(s: str) -> datetime:
    """Parse any of the three legacy timestamp shapes plus the new Z form.

    Handles:
      - "2026-01-01T12:00:00Z"          (new canonical)
      - "2026-01-01T12:00:00"           (legacy, no offset)
      - "2026-01-01T12:00:00.123456"    (legacy isoformat with microseconds)
      - "2026-01-01T12:00:00+00:00"     (explicit UTC offset)
    Returns a timezone-aware datetime in UTC.
    """
    normalized = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(f"Cannot parse timestamp: {s!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _bkk_midnight_utc() -> str:
    """Return the ISO-8601 UTC timestamp of midnight Asia/Bangkok today."""
    now_bkk  = datetime.now(_BKK_OFFSET)
    midnight  = now_bkk.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_mid   = midnight.astimezone(timezone.utc)
    return utc_mid.strftime("%Y-%m-%dT%H:%M:%SZ")


def check_db_integrity(db_path: Path) -> str:
    """Run PRAGMA integrity_check on db_path. Returns 'ok' or an error string."""
    if not db_path.exists():
        return f"error: file not found: {db_path}"
    if db_path.stat().st_size == 0:
        return "error: database file is empty"
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] if row else "error: no result from integrity_check"
    except sqlite3.DatabaseError as exc:
        return f"error: {exc}"
    finally:
        conn.close()


def check_db_quick(db_path: Path) -> str:
    """Run PRAGMA quick_check on db_path (faster than integrity_check; no sort-order check).

    Called by the periodic integrity loop (issue #2037 AC5) so corruption detected
    while the process is running is cheaper to check than the full integrity_check.
    Returns 'ok' or an error string.
    """
    if not db_path.exists():
        return f"error: file not found: {db_path}"
    if db_path.stat().st_size == 0:
        return "error: database file is empty"
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        return row[0] if row else "error: no result from quick_check"
    except sqlite3.DatabaseError as exc:
        return f"error: {exc}"
    finally:
        conn.close()


def alert_if_corrupt(db_path: "Path | None" = None) -> str:
    """Run quick_check and log CRITICAL if corrupt; return the status string.

    Separated from the async periodic loop so it can be tested synchronously
    without async infrastructure (issue #2037 AC5).
    """
    path = db_path if db_path is not None else DB_PATH
    status = check_db_quick(path)
    if status != "ok":
        _log.critical(
            "DB CORRUPTION DETECTED by periodic quick_check: %s — "
            "service is still running but writes may be unreliable; "
            "restart required to trigger full startup integrity check",
            status,
        )
    return status


def run_wal_checkpoint(db_path: "Path | None" = None) -> tuple:
    """Run PRAGMA wal_checkpoint(PASSIVE) and return (busy, log, checkpointed)."""
    path = db_path if db_path is not None else DB_PATH
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return tuple(row) if row else (0, 0, 0)
    finally:
        conn.close()


@contextlib.contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    # WAL + an explicit busy_timeout (issue #1688): the server and the sprint
    # manager subprocess both write this file concurrently. The rollback
    # journal (sqlite's default) serializes all writers and blocks readers
    # during a write; WAL lets readers proceed during a writer's transaction
    # and cuts lock-contention errors between the two processes. busy_timeout
    # makes SQLite retry internally for up to 5s on SQLITE_BUSY before
    # raising, on top of the sqlite3 module's own 5s connect(timeout=...)
    # retry loop — belt and suspenders since some call sites may not go
    # through this helper.
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    # Integrity guard: detect corruption before DDL so we never silently
    # 500-loop on a truncated/corrupt DB (issue #1901).
    _startup_integrity_check()

    # Write-access guard: verify the path is writable before attempting DDL.
    try:
        with open(DB_PATH, "a"):
            pass
    except OSError as exc:
        raise RuntimeError(
            f"DB_PATH '{DB_PATH.resolve()}' is not writable: {exc}"
        ) from exc

    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                session_id  TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                working_dir TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'working',
                last_tool   TEXT,
                last_seen   TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        # Migrate old session-events table if it has the legacy schema.
        old_cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "session_id" in old_cols:
            conn.execute("ALTER TABLE events RENAME TO session_events")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_events (
                id          INTEGER PRIMARY KEY,
                session_id  TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                data        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY,
                project   TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                source    TEXT NOT NULL CHECK(source IN ('agent', 'dashboard', 'github')),
                actor     TEXT NOT NULL,
                type      TEXT NOT NULL,
                target    TEXT NOT NULL,
                action_id TEXT,
                detail    TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_project_ts "
            "ON events (project, timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_project_target "
            "ON events (project, target)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_action_id "
            "ON events (action_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT NOT NULL,
                project       TEXT NOT NULL,
                input_tokens  INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                recorded_at   TEXT NOT NULL,
                agent_role    TEXT,
                model_name    TEXT
            )
        """)
        # Migrate existing token_usage tables that lack the new columns (backward compat)
        for col, coltype in [
            ("agent_role", "TEXT"),
            ("model_name", "TEXT"),
            # ICA metered-path columns (issue #1672)
            ("cache_read_tokens", "INTEGER DEFAULT 0"),
            ("cache_write_tokens", "INTEGER DEFAULT 0"),
            ("ccproxy_profile", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE token_usage ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # column already exists — ignore
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                source      TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                target      TEXT,
                actor       TEXT,
                action_id   TEXT,
                data        TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_project_events_project_created "
            "ON project_events (project, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_project_events_target "
            "ON project_events (project, target)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_project_events_action "
            "ON project_events (action_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS docs_freshness_warnings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                repo         TEXT NOT NULL,
                doc_path     TEXT NOT NULL,
                trigger_ref  TEXT NOT NULL,
                trigger_type TEXT NOT NULL DEFAULT 'push',
                trigger_url  TEXT,
                flagged_at   TEXT NOT NULL,
                is_cleared   INTEGER NOT NULL DEFAULT 0,
                cleared_at   TEXT
            )
        """)
        _create_ticket_status_table(conn)
        _create_issues_table(conn)
        _create_milestones_table(conn)
        _create_sync_state_table(conn)
        _create_sprint_lifecycle_tables(conn)
        _create_sprint_history_table(conn)
        _create_agent_runs_table(conn)
        _create_advisor_suggestions_table(conn)
        _create_advisor_look_ahead_table(conn)
        _create_advisor_dismissed_table(conn)
        conn.commit()


def _create_ticket_status_table(conn: sqlite3.Connection) -> None:
    """Create the ticket_status write-through table (issue #755).

    Records the state written by state_machine.transition() after a successful
    GitHub label edit, so the dashboard read-path no longer depends on a
    post-edit verify re-fetch.  Kept in its own helper so record_ticket_status()
    can ensure the table exists without running the full init_db() migration.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_status (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            issue   TEXT NOT NULL,
            status  TEXT NOT NULL,
            actor   TEXT NOT NULL,
            note    TEXT,
            ts      TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_ticket_status_issue_ts "
        "ON ticket_status (issue, ts DESC)"
    )


# ── Issues mirror (issue #756) ────────────────────────────────────────────────
#
# The dashboard read model is the local DB, not GitHub.  The `issues` table is a
# mirror of repo issues kept fresh by github_events_sync.sync_issues_mirror() via
# ETag-conditional polling.  Read endpoints (project view, sprint cards, running
# view, finish/rerun previews) serve from this table so renders consume zero
# GitHub rate-limit quota.  The `sync_state` table holds the per-repo ETag used
# for If-None-Match conditional requests.


def _create_issues_table(conn: sqlite3.Connection) -> None:
    """Create the issues mirror table (issue #756).

    `labels` and `raw` hold JSON.  `raw` is the full gh-CLI-shaped issue dict so
    readers can reconstruct fields (assignees, url, body, …) without a live call.
    Primary key is (repo, issue_number) so multiple repos can be mirrored.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            repo         TEXT NOT NULL DEFAULT '',
            issue_number INTEGER NOT NULL,
            title        TEXT,
            state        TEXT,
            labels       TEXT NOT NULL DEFAULT '[]',
            updated_at   TEXT,
            raw          TEXT,
            PRIMARY KEY (repo, issue_number)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_issues_repo_state "
        "ON issues (repo, state)"
    )


def _create_sync_state_table(conn: sqlite3.Connection) -> None:
    """Create the sync_state table holding per-key ETags (issue #756)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sync_state (
            key        TEXT PRIMARY KEY,
            etag       TEXT,
            updated_at TEXT
        )
    """)


# ── Milestones mirror (issue #877) ────────────────────────────────────────────
#
# A local mirror of repo milestones, kept fresh by
# github_milestones.sync_milestones_mirror() via ETag-conditional polling — the
# same read-from-DB-not-GitHub model as the issues mirror. The GET milestones
# endpoint serves from this table so reads consume zero GitHub rate-limit quota;
# before the first sync it falls back to a live GitHub fetch. Write operations
# (create/edit/close) upsert here too so the change is visible immediately.


def _create_milestones_table(conn: sqlite3.Connection) -> None:
    """Create the milestones mirror table (issue #877).

    `raw` holds the full GitHub-shaped milestone dict so readers can reconstruct
    every field without a live call. Primary key is (repo, number) so multiple
    repos can be mirrored.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS milestones (
            repo        TEXT NOT NULL DEFAULT '',
            number      INTEGER NOT NULL,
            title       TEXT,
            description TEXT,
            state       TEXT,
            due_on      TEXT,
            updated_at  TEXT,
            raw         TEXT,
            PRIMARY KEY (repo, number)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_milestones_repo_state "
        "ON milestones (repo, state)"
    )


def upsert_milestones(repo: str, milestones: list[dict]) -> int:
    """Upsert a batch of GitHub-shaped milestone dicts into the mirror.

    Each dict carries: number, title, description, state, due_on, plus any extra
    fields preserved in the `raw` column. Returns the number written.
    """
    if not milestones:
        return 0
    with get_conn() as conn:
        _create_milestones_table(conn)
        for ms in milestones:
            number = ms.get("number")
            if number is None:
                continue
            conn.execute(
                """INSERT INTO milestones
                       (repo, number, title, description, state, due_on,
                        updated_at, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repo, number) DO UPDATE SET
                       title       = excluded.title,
                       description = excluded.description,
                       state       = excluded.state,
                       due_on      = excluded.due_on,
                       updated_at  = excluded.updated_at,
                       raw         = excluded.raw""",
                (
                    repo,
                    int(number),
                    ms.get("title", ""),
                    ms.get("description", ""),
                    ms.get("state", ""),
                    ms.get("due_on", "") or "",
                    ms.get("updated_at", "") or ms.get("updatedAt", ""),
                    json.dumps(ms),
                ),
            )
        conn.commit()
    return len(milestones)


def _row_to_milestone(row: sqlite3.Row) -> dict:
    """Reconstruct a GitHub-shaped milestone dict from a mirror row."""
    if row["raw"]:
        try:
            return json.loads(row["raw"])
        except (ValueError, TypeError):
            pass
    return {
        "number": row["number"],
        "title": row["title"],
        "description": row["description"],
        "state": row["state"],
        "due_on": row["due_on"],
    }


def get_mirrored_milestones(repo: str, state: str | None = None) -> list[dict]:
    """Return mirrored milestones for a repo as GitHub-shaped dicts.

    Optionally filter by state ('open' / 'closed'). Returns an empty list when
    nothing is mirrored yet (callers may then fall back to a live fetch).
    """
    sql = "SELECT * FROM milestones WHERE repo = ?"
    params: list = [repo]
    if state is not None:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY number ASC"
    with get_conn() as conn:
        _create_milestones_table(conn)
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_milestone(r) for r in rows]


def get_mirrored_milestone(repo: str, number: int) -> dict | None:
    """Return a single mirrored milestone, or None if not present."""
    with get_conn() as conn:
        _create_milestones_table(conn)
        row = conn.execute(
            "SELECT * FROM milestones WHERE repo = ? AND number = ?",
            (repo, int(number)),
        ).fetchone()
    return _row_to_milestone(row) if row else None


# ── Brief summary cache (issue #840) ──────────────────────────────────────────
#
# Per-(scope, project, date) cache of the LLM-generated brief summary so the
# model is invoked at most once per key. `scope` is 'project' (keyed by slug) or
# 'home' (project=''). Only model-generated summaries are persisted here; the
# deterministic templated fallback is recomputed on the fly so a transient model
# failure never poisons the cache.

# ORPHANED (#2257): brief_summary.py was deleted; these helpers and the
# brief_summaries table are no longer written to. Kept to avoid a schema
# migration — existing rows remain in the DB but nothing reads or writes them.
def _create_brief_summaries_table(conn: sqlite3.Connection) -> None:
    """Create the brief_summaries cache table (issue #840 — orphaned #2257)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brief_summaries (
            scope      TEXT NOT NULL,
            project    TEXT NOT NULL DEFAULT '',
            date       TEXT NOT NULL,
            summary    TEXT NOT NULL,
            source     TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (scope, project, date)
        )
    """)


def get_brief_summary(scope: str, project: str, date: str) -> dict | None:
    """Return the cached summary for (scope, project, date), or None."""
    with get_conn() as conn:
        _create_brief_summaries_table(conn)
        row = conn.execute(
            "SELECT summary, source, created_at FROM brief_summaries "
            "WHERE scope = ? AND project = ? AND date = ?",
            (scope, project or "", date),
        ).fetchone()
    if row is None:
        return None
    return {"summary": row["summary"], "source": row["source"],
            "created_at": row["created_at"]}


def set_brief_summary(scope: str, project: str, date: str, summary: str,
                      source: str, created_at: str | None = None) -> None:
    """Upsert the cached summary for (scope, project, date)."""
    ts = created_at or _now_iso()
    with get_conn() as conn:
        _create_brief_summaries_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO brief_summaries "
            "(scope, project, date, summary, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scope, project or "", date, summary, source, ts),
        )
        conn.commit()


def delete_brief_summary(scope: str, project: str, date: str) -> None:
    """Clear the cached summary for (scope, project, date) — used by Regenerate."""
    with get_conn() as conn:
        _create_brief_summaries_table(conn)
        conn.execute(
            "DELETE FROM brief_summaries "
            "WHERE scope = ? AND project = ? AND date = ?",
            (scope, project or "", date),
        )
        conn.commit()


# ── Daily brief artifact store (issue #841) ───────────────────────────────────
#
# Persists the full daily brief as an artifact keyed by (scope, project, date) so
# a given day's brief is generated once and served instantly thereafter. `scope`
# is 'project' (keyed by slug) or 'home' (project=''). The `payload` column holds
# the complete brief object (window metadata + structured brief + summary) as
# JSON; `generated_at` records when it was last (re)generated, which the
# Regenerate action advances.

def _create_brief_artifacts_table(conn: sqlite3.Connection) -> None:
    """Create the brief_artifacts store table (issue #841)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brief_artifacts (
            scope        TEXT NOT NULL,
            project      TEXT NOT NULL DEFAULT '',
            date         TEXT NOT NULL,
            payload      TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (scope, project, date)
        )
    """)


def get_brief_artifact(scope: str, project: str, date: str) -> dict | None:
    """Return the stored artifact for (scope, project, date), or None.

    The returned dict is ``{"payload": <decoded dict>, "generated_at": <iso>}``.
    """
    with get_conn() as conn:
        _create_brief_artifacts_table(conn)
        row = conn.execute(
            "SELECT payload, generated_at FROM brief_artifacts "
            "WHERE scope = ? AND project = ? AND date = ?",
            (scope, project or "", date),
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"])
    except (ValueError, TypeError):
        payload = None
    return {"payload": payload, "generated_at": row["generated_at"]}


def set_brief_artifact(scope: str, project: str, date: str, payload: dict,
                       generated_at: str | None = None) -> str:
    """Upsert the stored artifact for (scope, project, date).

    Returns the ``generated_at`` timestamp written (a fresh one is stamped when
    not supplied), so the caller can surface the new generation time.
    """
    ts = generated_at or _now_iso()
    with get_conn() as conn:
        _create_brief_artifacts_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO brief_artifacts "
            "(scope, project, date, payload, generated_at) VALUES (?, ?, ?, ?, ?)",
            (scope, project or "", date, json.dumps(payload), ts),
        )
        conn.commit()
    return ts


def delete_brief_artifact(scope: str, project: str, date: str) -> None:
    """Clear the stored artifact for (scope, project, date)."""
    with get_conn() as conn:
        _create_brief_artifacts_table(conn)
        conn.execute(
            "DELETE FROM brief_artifacts "
            "WHERE scope = ? AND project = ? AND date = ?",
            (scope, project or "", date),
        )
        conn.commit()


# ── Sprint lifecycle + ticket order (issue #757) ──────────────────────────────
#
# The durable home for sprint lifecycle state and ticket execution order. These
# tables replace the ephemeral `{label}-plan.json` / `{label}-pid` files as the
# source of truth, while those files continue to be written as a deprecated
# cache (dual-write) until a later sprint removes them.
#
# Unified lifecycle (docs/architecture/sprint-lifecycle.md): new writes use
# draft / running / ready_to_merge / needs_rework / completed.
# `planning`, `planned`, `cancelled`, and `failed` are legacy values kept
# readable for pre-redesign rows (forward-only migration, no rewrite) — they
# are never written anew and render through `canonical_lifecycle()`.
#
# `planned` was deprecated in issue #1686 alongside the plan.json `signoff`
# gate — the preflight-confirmation step it modeled was never stabilized.
# Nothing writes it anymore; any legacy row that carries it displays as
# `draft`.

_SPRINT_STATES = (
    # unified lifecycle
    "draft", "running", "ready_to_merge", "needs_rework", "completed",
    # legacy, read-only
    "planning", "planned", "cancelled", "failed",
)

# Canonical lifecycle states exposed to the UI. `partial_finished` is derived
# at read time from children's states and never stored; `deleted` lives in the
# sprint_history snapshot table, not in `sprints`.
LIFECYCLE_STATES = (
    "draft", "running", "ready_to_merge", "needs_rework",
    "partial_finished", "completed", "deleted",
)

# Display mapping for legacy state vocabularies (plan.json, DB rows, summary
# files). Forward-only migration: old rows keep their stored value and render
# through this map. Legacy `completed` stays `completed` (the pre-redesign
# flow auto-merged at end of run, so those sprints are past ready_to_merge).
_LEGACY_LIFECYCLE_MAP = {
    "planning": "draft",
    "planned": "draft",
    "complete": "completed",
    "finished": "completed",
    "cancelled": "needs_rework",
    "canceled": "needs_rework",
    "failed": "needs_rework",
    "has_rework": "needs_rework",
    "stopped": "needs_rework",
}


def canonical_lifecycle(raw: str | None) -> str:
    """Map any stored sprint state (current or legacy) to the unified enum."""
    s = (raw or "").strip().lower()
    if s in LIFECYCLE_STATES:
        return s
    return _LEGACY_LIFECYCLE_MAP.get(s, s or "unknown")


# ── Sprint state machine guard ────────────────────────────────────────────────

_logger = logging.getLogger("db.sprint_state")

# Legal state-to-state edges per the sprint-lifecycle contract diagram.
# Non-running states allow direct terminal transitions to handle sprint
# cancellations, legacy data ingest, and orphan-PID reconciliation without
# requiring a full running start.  The critical guard — requiring actor="manager"
# for running→terminal — is enforced separately below.
_LEGAL_SPRINT_EDGES: dict[str, frozenset[str]] = {
    # `current` (below) is always run through canonical_lifecycle() before
    # this table is consulted, and `planned` canonicalizes to `draft` (#1686
    # deprecation) — so a `"planned"` key here would be unreachable dead code.
    "draft":          frozenset({"running", "ready_to_merge", "needs_rework", "deleted"}),
    "running":        frozenset({"running", "ready_to_merge", "needs_rework", "completed", "deleted"}),
    "ready_to_merge": frozenset({"completed", "needs_rework", "deleted"}),
    # Derived-only in normal flow; if a legacy row stored this value, allow
    # settlement after Merge Sprint / bulk complete (same as ready_to_merge).
    "partial_finished": frozenset({"ready_to_merge", "completed", "needs_rework", "deleted"}),
    "needs_rework":   frozenset({"running", "deleted"}),
    "completed":      frozenset({"deleted"}),
    "deleted":        frozenset(),
}

# Transitions FROM these states TO the guarded targets require actor="manager".
_GUARD_FROM: frozenset[str] = frozenset({"running"})
_GUARD_TO: frozenset[str] = frozenset({"ready_to_merge", "needs_rework", "completed"})

# Reconciler-only promotions not in the general edge table (issue #1137).
# needs_rework→completed (B2): a superseded ancestor whose entire lineage has
# merged into develop is completed even though its own tickets show rework — the
# rework moved to a child that has since merged up. Guarded by a real
# merge-to-develop check in the reconciler (sprint_reconcile_service requires a
# strictly-later completed lineage member AND the lineage base branch in the
# merged-PR-head set), so this edge can never auto-complete a sprint whose work
# is not actually in develop.
_RECONCILE_ONLY_EDGES: frozenset[tuple[str, str]] = frozenset({
    ("needs_rework", "ready_to_merge"),
    ("needs_rework", "completed"),
})


class TransitionResult:
    """Returned by transition_sprint_state to signal acceptance or rejection."""

    __slots__ = ("accepted", "reason")

    def __init__(self, accepted: bool, reason: str = "") -> None:
        self.accepted = accepted
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"TransitionResult(accepted={self.accepted!r}, reason={self.reason!r})"


def transition_sprint_state(
    label: str,
    to_state: str,
    actor: str,
    end_reason: str | None = None,
    ended_at: str | None = None,
    started_at: str | None = None,
    project: str = "",
    parent_label: str | None = None,
) -> TransitionResult:
    """Single authoritative writer for sprint lifecycle state transitions.

    Enforces the legal edge set from docs/architecture/sprint-lifecycle.md and
    rejects any running→terminal transition where actor != "manager". Returns a
    TransitionResult; never raises on a guard failure.
    """
    # B1: a child sprint (``sprint-N.M``) must persist parent_label = base
    # ``sprint-N`` so children_of()'s DB-primary lookup resolves the whole
    # lineage. The running-state writer was historically called without
    # parent_label, leaving it NULL — which forced a disk-glob fallback that
    # silently dropped descendants once any sibling row existed. Derive the base
    # here so every transition self-heals, regardless of the caller.
    if parent_label is None and label and "." in label and label.startswith("sprint-"):
        _base = label.split(".", 1)[0]
        if _base[len("sprint-"):].isdigit():
            parent_label = _base
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        # Scope by project: sprint labels are unique only per repo, so an unscoped
        # read picked up another project's same-numbered sprint and reported a
        # bogus 'completed→running' illegal edge (e.g. commander sprint-65 blocking
        # perf-coach sprint-65). When this project has no row yet, current falls to
        # 'draft' so the fresh run's →running is legal.
        # NB: the sprints table still has label as its sole PRIMARY KEY, so the
        # WRITE below can overwrite another project's same-labelled row — the real
        # fix is a composite (label, project) key (tracked separately).
        if project:
            row = conn.execute(
                "SELECT state FROM sprints WHERE label = ? AND project = ?",
                (label, project),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT state FROM sprints WHERE label = ?", (label,)
            ).fetchone()
    current = canonical_lifecycle(row["state"] if row else "draft")

    # Idempotent no-op: re-issuing a transition to the state the sprint is
    # already in is a success, not an illegal edge. Without this, resuming a
    # partially-done bulk-complete re-attempts `completed→completed` on members a
    # prior run already finished; the edge table has no completed→completed entry,
    # so the state machine rejected it ("illegal edge — lifecycle left unchanged")
    # and wedged the whole resume. Same-state is semantically a no-op — accept it
    # and skip the write so the original timestamps/end_reason are preserved.
    if current == to_state:
        return TransitionResult(accepted=True, reason=f"no-op: already {to_state!r}")

    allowed = _LEGAL_SPRINT_EDGES.get(current, frozenset())
    reconcile_promote = (
        actor == "reconcile"
        and (current, to_state) in _RECONCILE_ONLY_EDGES
    )
    if to_state not in allowed and not reconcile_promote:
        msg = (
            f"sprint {label!r}: illegal edge {current!r}→{to_state!r} "
            f"by actor={actor!r} (allowed: {sorted(allowed)})"
        )
        _logger.warning(msg)
        return TransitionResult(accepted=False, reason=msg)

    if current in _GUARD_FROM and to_state in _GUARD_TO and actor != "manager":
        msg = (
            f"sprint {label!r}: {current!r}→{to_state!r} rejected for "
            f"actor={actor!r}; only actor='manager' may make this transition"
        )
        _logger.warning(msg)
        return TransitionResult(accepted=False, reason=msg)

    if to_state == "running":
        _ts = started_at or _now_iso()
        with get_conn() as conn:
            _create_sprint_lifecycle_tables(conn)
            conn.execute(
                """
                INSERT INTO sprints
                    (label, project, state, created_at, started_at, parent_label)
                VALUES (?, ?, 'running', ?, ?, ?)
                ON CONFLICT(label, project) DO UPDATE SET
                    state        = 'running',
                    started_at   = excluded.started_at,
                    created_at   = COALESCE(sprints.created_at, excluded.created_at),
                    parent_label = COALESCE(excluded.parent_label, sprints.parent_label),
                    ended_at     = NULL,
                    end_reason   = NULL
                """,
                (label, project, _ts, _ts, parent_label),
            )
            conn.commit()
    elif to_state == "deleted":
        # `deleted` is not a storable state in the sprints CHECK constraint
        # (it lives in sprint_history per the lifecycle spec); remove the row.
        with get_conn() as conn:
            _create_sprint_lifecycle_tables(conn)
            conn.execute(
                "DELETE FROM sprints WHERE label = ? AND project = ?",
                (label, project),
            )
            conn.commit()
    else:
        _set_sprint_terminal(label, to_state, end_reason, ended_at, project=project)

    _logger.info(
        "sprint %r: %r → %r by actor=%r", label, current, to_state, actor
    )
    return TransitionResult(accepted=True)


_SPRINTS_TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS sprints (
            label        TEXT NOT NULL,
            project      TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL DEFAULT 'draft'
                         CHECK(state IN (
                             'draft', 'planned', 'running', 'ready_to_merge',
                             'needs_rework', 'completed',
                             'planning', 'cancelled', 'failed'
                         )),
            created_at   TEXT,
            started_at   TEXT,
            ended_at     TEXT,
            end_reason   TEXT,
            parent_label TEXT,
            PRIMARY KEY (label, project)
        )
"""

# Schema version written to _sprint_schema_migrations when composite PK rebuild runs.
_SPRINTS_COMPOSITE_PK_VERSION = 1


def _migrate_sprints_state_check(conn: sqlite3.Connection) -> None:
    """Rebuild the sprints table when its CHECK predates the unified lifecycle.

    SQLite cannot alter a CHECK constraint in place. Existing rows are copied
    verbatim (forward-only migration — legacy state values stay readable).
    """
    # Salvage pass: a prior run of this migration can strand rows in
    # sprints_legacy_check — the RENAME/CREATE (DDL, autocommitted) survived
    # while the row copy + DROP (DML, transactional) rolled back when the
    # caller's get_conn() closed without commit (#1749 made close a rollback).
    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sprints_legacy_check'"
    ).fetchone()
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sprints'"
    ).fetchone()
    if row is not None and "needs_rework" in (row[0] or ""):
        if leftover is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO sprints
                    (label, project, state, created_at, started_at, ended_at,
                     end_reason, parent_label)
                SELECT label, project, state, created_at, started_at, ended_at,
                       end_reason, parent_label
                FROM sprints_legacy_check
                """
            )
            conn.execute("DROP TABLE sprints_legacy_check")
            conn.commit()
        return
    if row is None:
        return
    conn.execute("ALTER TABLE sprints RENAME TO sprints_legacy_check")
    conn.execute(_SPRINTS_TABLE_DDL)
    conn.execute(
        """
        INSERT OR IGNORE INTO sprints
        SELECT label, project, state, created_at, started_at, ended_at,
               end_reason, parent_label
        FROM sprints_legacy_check
        """
    )
    conn.execute("DROP TABLE sprints_legacy_check")
    # Commit immediately: this helper runs inside read-only get_conn() blocks
    # whose close() otherwise rolls the row copy back while the DDL persists,
    # silently emptying the sprints table.
    conn.commit()


def _migrate_sprints_to_composite_pk(conn: sqlite3.Connection) -> None:
    """Rebuild sprints with composite PRIMARY KEY (label, project) (issue #1462).

    Gated on _sprint_schema_migrations version 1 — runs exactly once and is a
    no-op on subsequent calls. Creates 'sprints_new' with composite PK and all
    run-artifact columns, copies rows from 'sprints' deduplicating on
    (label, project) by keeping the highest-rowid row, drops 'sprints',
    renames 'sprints_new' → 'sprints', and recreates any explicit indexes.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sprint_schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
    """)
    if conn.execute(
        "SELECT 1 FROM _sprint_schema_migrations WHERE version = ?",
        (_SPRINTS_COMPOSITE_PK_VERSION,),
    ).fetchone() is not None:
        return

    sprints_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sprints'"
    ).fetchone()

    # Remove any sprints_new left behind by a prior crashed run
    conn.execute("DROP TABLE IF EXISTS sprints_new")

    # Create sprints_new with composite PK (core columns)
    new_ddl = _SPRINTS_TABLE_DDL.replace(
        "CREATE TABLE IF NOT EXISTS sprints",
        "CREATE TABLE sprints_new",
    )
    conn.execute(new_ddl)
    # Add run-artifact columns to sprints_new so the copy is complete
    new_cols = {r[1] for r in conn.execute("PRAGMA table_info(sprints_new)").fetchall()}
    for col, typedef in _RUN_ARTIFACT_COLUMNS:
        if col not in new_cols:
            conn.execute(f"ALTER TABLE sprints_new ADD COLUMN {col} {typedef}")

    if sprints_exists is not None:
        existing_cols = [
            r[1] for r in conn.execute("PRAGMA table_info(sprints)").fetchall()
        ]
        dest_cols = {r[1] for r in conn.execute("PRAGMA table_info(sprints_new)").fetchall()}
        copy_cols = [c for c in existing_cols if c in dest_cols]
        cols_sql = ", ".join(copy_cols)
        # Deduplicate: keep the highest-rowid row per (label, project)
        conn.execute(
            f"INSERT OR IGNORE INTO sprints_new ({cols_sql}) "
            f"SELECT {cols_sql} FROM sprints "
            f"WHERE rowid IN ("
            f"  SELECT MAX(rowid) FROM sprints GROUP BY label, project"
            f")"
        )
        old_indexes = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name='sprints' AND sql IS NOT NULL"
        ).fetchall()
        conn.execute("DROP TABLE sprints")
        conn.execute("ALTER TABLE sprints_new RENAME TO sprints")
        for (idx_sql,) in old_indexes:
            conn.execute(idx_sql)
    else:
        conn.execute("ALTER TABLE sprints_new RENAME TO sprints")

    conn.execute(
        "INSERT INTO _sprint_schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
        (_SPRINTS_COMPOSITE_PK_VERSION,),
    )
    # Commit immediately — same rollback hazard as _migrate_sprints_state_check:
    # callers routinely run this inside read-only get_conn() blocks that close
    # (= rollback) without committing, which would undo the row copy + version
    # marker while the interleaved DDL persists.
    conn.commit()


def _create_sprint_lifecycle_tables(conn: sqlite3.Connection) -> None:
    """Create the sprints + sprint_ticket_order tables (issue #757).

    Kept in its own helper so the lifecycle writers can ensure the tables exist
    without running the full init_db() migration first.
    """
    _migrate_sprints_state_check(conn)
    _migrate_sprints_to_composite_pk(conn)
    conn.execute(_SPRINTS_TABLE_DDL)
    _migrate_sprints_run_artifacts(conn)
    _backfill_child_parent_labels(conn)
    _backfill_sprint_project(conn)
    _backfill_immediate_parent_labels(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sprint_ticket_order (
            label    TEXT NOT NULL,
            issue    INTEGER NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (label, issue)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_sprint_ticket_order_label_pos "
        "ON sprint_ticket_order (label, position)"
    )


_RUN_ARTIFACT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("issues_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("tokens", "INTEGER"),
    ("wall_clock_secs", "INTEGER"),
    ("reconciliation_json", "TEXT"),
    ("summary_issue_url", "TEXT"),
    ("summary_path", "TEXT"),
    ("pr_number", "INTEGER"),
    ("post_sprint_json", "TEXT"),
    ("estimate_accuracy", "REAL"),
    ("run_ingested_at", "TEXT"),
    ("summary_settled_done", "INTEGER NOT NULL DEFAULT 0"),
    ("summary_uat_count", "INTEGER NOT NULL DEFAULT 0"),
    ("summary_failure_count", "INTEGER NOT NULL DEFAULT 0"),
    # issue #1691: `parent_label` (above, in the core DDL) always holds the
    # BASE label of a rerun chain (self-healed to the sprint-N root — drives
    # History lineage grouping). Re-run's actual merge topology is child ->
    # IMMEDIATE parent (e.g. 94.2's immediate parent is 94.1, not 94), and
    # until now that only lived in plan.json's `parent` field. This column
    # gives the DB the same information so merge-topology resolution can
    # prefer it over a disk read.
    ("immediate_parent", "TEXT"),
)


def _migrate_sprints_run_artifacts(conn: sqlite3.Connection) -> None:
    """Add end-of-run artifact columns to sprints (lifecycle P3)."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sprints'"
    ).fetchone()
    if row is None:
        return
    existing = {r[1] for r in conn.execute("PRAGMA table_info(sprints)").fetchall()}
    for col, typedef in _RUN_ARTIFACT_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE sprints ADD COLUMN {col} {typedef}")


def _backfill_child_parent_labels(conn: sqlite3.Connection) -> None:
    """One-time heal: set parent_label = base for child sprints left NULL.

    Child-sprint rows written before parent_label was persisted (the running
    writer was called without it) keep NULL, which makes children_of()'s
    DB-primary lookup miss the lineage. Derive base ``sprint-N`` from the label
    via the first dot. Idempotent: only touches NULL rows matching sprint-N.M.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sprints'"
    ).fetchone()
    if row is None:
        return
    conn.execute(
        """
        UPDATE sprints
           SET parent_label = substr(label, 1, instr(label, '.') - 1)
         WHERE parent_label IS NULL
           AND label GLOB 'sprint-[0-9]*.[0-9]*'
        """
    )


def _backfill_immediate_parent_labels(
    conn: sqlite3.Connection,
    *,
    projects_file: "Path | None" = None,
    projects_base: "Path | None" = None,
    _sprints_dir_override: "Path | None" = None,
) -> None:
    """One-time heal: set immediate_parent for child sprints left NULL (issue #2048).

    For each child sprint row with NULL immediate_parent, find its plan.json and
    copy the ``parent`` field.  Idempotent: only touches NULL rows.

    AC5 topology-change report: logs the count of affected rows before writing,
    so operators know how many sprints had their merge topology fixed.
    """
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sprints'"
    ).fetchone()
    if row is None:
        return

    null_rows = conn.execute(
        """
        SELECT label, project FROM sprints
         WHERE immediate_parent IS NULL
           AND label GLOB 'sprint-[0-9]*.[0-9]*'
        """
    ).fetchall()

    if not null_rows:
        return

    # AC5: report count before writing so operators see the topology-change scope.
    logging.info(
        "backfill_immediate_parent: %d child sprint row(s) have NULL immediate_parent "
        "(merge topology may be incorrect — applying plan.json backfill, issue #2048)",
        len(null_rows),
    )

    # Build set of known plan.json directories to search.
    sprints_dirs: list[Path] = []
    if _sprints_dir_override is not None:
        sprints_dirs.append(_sprints_dir_override)
    else:
        if projects_base is None:
            projects_base = Path.home() / "dev"
        if projects_file is None:
            projects_file = Path(__file__).parent / "projects.json"
        try:
            if projects_file.exists():
                for p in json.loads(projects_file.read_text()):
                    repo = p.get("repo", "")
                    slug = repo.split("/")[-1] if "/" in repo else repo
                    if slug:
                        sprints_dirs.append(projects_base / slug / ".commander" / "sprints")
        except Exception:
            pass

    updated = 0
    for label, project in null_rows:
        parent_val: str | None = None

        # Search project-specific dir first (derived from the stored project value).
        dirs_to_try = list(sprints_dirs)
        if project and _sprints_dir_override is None:
            slug = project.split("/")[-1] if "/" in project else project
            if projects_base is not None:
                proj_dir = projects_base / slug / ".commander" / "sprints"
                dirs_to_try.insert(0, proj_dir)

        for sd in dirs_to_try:
            plan_path = sd / f"{label}-plan.json"
            if plan_path.exists():
                try:
                    raw = json.loads(plan_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        parent_val = (raw.get("parent") or "").strip() or None
                except Exception:
                    pass
                break

        if parent_val:
            conn.execute(
                "UPDATE sprints SET immediate_parent = ? "
                " WHERE label = ? AND project = ? AND immediate_parent IS NULL",
                (parent_val, label, project),
            )
            updated += 1
        else:
            logging.warning(
                "backfill_immediate_parent: label=%r project=%r — "
                "no plan.json parent found; immediate_parent left NULL "
                "(merge topology falls back to base sprint, issue #2048)",
                label, project,
            )

    if updated:
        logging.info(
            "backfill_immediate_parent: updated %d/%d rows with immediate_parent from plan.json",
            updated, len(null_rows),
        )


def _backfill_sprint_project(
    conn: sqlite3.Connection,
    *,
    projects_file: "Path | None" = None,
    projects_base: "Path | None" = None,
) -> None:
    """Backfill empty sprints.project (and sprint_history.project) rows (issue #1460).

    Resolution order per label:
      1. agent_runs.project WHERE sprint_label matches — fastest, most reliable.
      2. Walk project roots derived from projects.json; look for
         .commander/sprints/<label>-plan.json or <label>-state.json.
      3. Leave empty and emit logging.warning with the label.

    Idempotent: only touches rows where project = '' or IS NULL.
    """
    _tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "sprints" not in _tables:
        return

    # Resolve projects_file / projects_base defaults.
    if projects_file is None:
        projects_file = Path(__file__).parent / "projects.json"
    if projects_base is None:
        projects_base = Path.home() / "dev"

    # Load project list for disk-walk strategy.
    repos: list[str] = []
    try:
        if projects_file.exists():
            for p in json.loads(projects_file.read_text()):
                repo = p.get("repo", "")
                if repo:
                    repos.append(repo)
    except Exception:
        pass

    def _resolve_label(label: str) -> tuple[str, str]:
        """Return (project, source) for one label, or ('', 'UNRESOLVED')."""
        # Strategy 1: agent_runs
        if "agent_runs" in _tables:
            row = conn.execute(
                "SELECT project FROM agent_runs "
                "WHERE sprint_label = ? AND project IS NOT NULL AND project != '' "
                "LIMIT 1",
                (label,),
            ).fetchone()
            if row and row[0]:
                return row[0], "agent_runs"

        # Strategy 2: disk walk
        for repo in repos:
            slug = repo.split("/")[-1] if "/" in repo else repo
            sprint_dir = projects_base / slug / ".commander" / "sprints"
            if (sprint_dir / f"{label}-plan.json").exists():
                return repo, "disk"
            if (sprint_dir / f"{label}-state.json").exists():
                return repo, "disk"

        return "", "UNRESOLVED"

    def _backfill_table(table: str) -> None:
        if table not in _tables:
            return
        empty_rows = conn.execute(
            f"SELECT label FROM {table} WHERE project = '' OR project IS NULL"
        ).fetchall()
        for row in empty_rows:
            label = row[0]
            project, source = _resolve_label(label)
            if project:
                conn.execute(
                    f"UPDATE {table} SET project = ? "
                    f"WHERE label = ? AND (project = '' OR project IS NULL)",
                    (project, label),
                )
            else:
                logging.warning(
                    "backfill_sprint_project: label=%r is UNRESOLVED — "
                    "no agent_runs row and no disk file found; leaving project empty",
                    label,
                )

    _backfill_table("sprints")
    _backfill_table("sprint_history")


def ingest_sprint_run_artifact(
    label: str,
    state: dict,
    *,
    project: str = "",
    summary_path: str | None = None,
) -> None:
    """Persist end-of-run state into the sprints row (lifecycle P3)."""
    from routers import sprint_artifact_service  # noqa: PLC0415

    fields = sprint_artifact_service.artifact_fields_from_state(
        state, summary_path=summary_path,
    )
    ingested_at = _now_iso()
    _project = (project or "").strip()
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        if _project:
            existing = conn.execute(
                "SELECT label FROM sprints WHERE label = ? AND project = ?",
                (label, _project),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT label FROM sprints WHERE label = ?", (label,)
            ).fetchone()
        if existing:
            updates = [
                fields["issues_json"],
                fields["tokens"],
                fields["wall_clock_secs"],
                fields["reconciliation_json"],
                fields["summary_issue_url"],
                fields["summary_path"],
                fields["pr_number"],
                fields["post_sprint_json"],
                fields["estimate_accuracy"],
                ingested_at,
                fields["summary_settled_done"],
                fields["summary_uat_count"],
                fields["summary_failure_count"],
            ]
            if _project:
                conn.execute(
                    """
                    UPDATE sprints SET
                        issues_json = ?,
                        tokens = ?,
                        wall_clock_secs = ?,
                        reconciliation_json = ?,
                        summary_issue_url = ?,
                        summary_path = ?,
                        pr_number = ?,
                        post_sprint_json = ?,
                        estimate_accuracy = ?,
                        run_ingested_at = ?,
                        summary_settled_done = ?,
                        summary_uat_count = ?,
                        summary_failure_count = ?
                    WHERE label = ? AND project = ?
                    """,
                    tuple(updates) + (label, _project),
                )
            else:
                conn.execute(
                    """
                    UPDATE sprints SET
                        issues_json = ?,
                        tokens = ?,
                        wall_clock_secs = ?,
                        reconciliation_json = ?,
                        summary_issue_url = ?,
                        summary_path = ?,
                        pr_number = ?,
                        post_sprint_json = ?,
                        estimate_accuracy = ?,
                        run_ingested_at = ?,
                        summary_settled_done = ?,
                        summary_uat_count = ?,
                        summary_failure_count = ?
                    WHERE label = ?
                    """,
                    tuple(updates) + (label,),
                )
        else:
            conn.execute(
                """
                INSERT INTO sprints (label, project, state, created_at, run_ingested_at,
                                     issues_json, tokens, wall_clock_secs,
                                     reconciliation_json, summary_issue_url, summary_path,
                                     pr_number, post_sprint_json, estimate_accuracy,
                                     summary_settled_done, summary_uat_count,
                                     summary_failure_count)
                VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    label,
                    project,
                    ingested_at,
                    ingested_at,
                    fields["issues_json"],
                    fields["tokens"],
                    fields["wall_clock_secs"],
                    fields["reconciliation_json"],
                    fields["summary_issue_url"],
                    fields["summary_path"],
                    fields["pr_number"],
                    fields["post_sprint_json"],
                    fields["estimate_accuracy"],
                    fields["summary_settled_done"],
                    fields["summary_uat_count"],
                    fields["summary_failure_count"],
                ),
            )
        conn.commit()




def update_sprint_pr_number(label: str, pr_number: int | None, project: str | None = None) -> None:
    """Persist the sprint merge PR number after Merge Sprint (History links).

    Scope by project: sprint labels are unique only per repo, so an unscoped
    UPDATE ... WHERE label=? would clobber another project's same-numbered sprint.
    """
    if pr_number is None:
        return
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        if project:
            conn.execute(
                "UPDATE sprints SET pr_number = ? WHERE label = ? AND project = ?",
                (int(pr_number), label, project),
            )
        else:
            conn.execute(
                "UPDATE sprints SET pr_number = ? WHERE label = ?",
                (int(pr_number), label),
            )
        conn.commit()

def update_sprint_reconciliation(label: str, reconciliation: dict, project: str | None = None) -> None:
    """Refresh the ingested reconciliation block after a background reconcile.

    Scope by project (see update_sprint_pr_number) to avoid a cross-project clobber.
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        if project:
            conn.execute(
                "UPDATE sprints SET reconciliation_json = ? WHERE label = ? AND project = ?",
                (json.dumps(reconciliation), label, project),
            )
        else:
            conn.execute(
                "UPDATE sprints SET reconciliation_json = ? WHERE label = ?",
                (json.dumps(reconciliation), label),
            )
        conn.commit()


def update_sprint_run_counts(
    label: str,
    issues_json: str,
    summary_settled_done: int,
    summary_uat_count: int,
    summary_failure_count: int,
    project: str = "",
) -> None:
    """Overwrite issues_json and denormalized count columns (reconcile path)."""
    if not (project or "").strip():
        return
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute(
            """
            UPDATE sprints SET
                issues_json = ?,
                summary_settled_done = ?,
                summary_uat_count = ?,
                summary_failure_count = ?
            WHERE label = ? AND project = ?
            """,
            (
                issues_json,
                int(summary_settled_done),
                int(summary_uat_count),
                int(summary_failure_count),
                label,
                project.strip(),
            ),
        )
        conn.commit()


# ── Sprint history records (issue #805) ───────────────────────────────────────
#
# A queryable, append-only ledger of terminal sprint events for the history
# feed (GET /api/sprints/history). The `sprints` lifecycle table cannot hold a
# `deleted` state — its CHECK constraint forbids it, and the row is meant to be
# gone once the label is stripped. So sprint deletion writes a self-contained
# snapshot here (state='deleted', end_reason, full ticket list) BEFORE label
# stripping, keeping deleted sprints visible in the feed. `issues_json` is a
# JSON array of {ticket_id, state, time_spent, pr_number} captured at write time.


def _create_sprint_history_table(conn: sqlite3.Connection) -> None:
    """Create the sprint_history ledger table (issue #805).

    Kept in its own helper so the delete path can ensure the table exists
    without running the full init_db() migration first.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sprint_history (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            label             TEXT NOT NULL,
            project           TEXT NOT NULL DEFAULT '',
            lifecycle_state   TEXT NOT NULL,
            end_reason        TEXT,
            duration          INTEGER,
            tokens            INTEGER,
            estimate_accuracy REAL,
            pr_number         INTEGER,
            summary_path      TEXT,
            issues_json       TEXT NOT NULL DEFAULT '[]',
            created_at        TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_sprint_history_created "
        "ON sprint_history (created_at DESC)"
    )


def record_sprint_history(
    label: str,
    project: str = "",
    lifecycle_state: str = "deleted",
    end_reason: str | None = None,
    duration: int | None = None,
    tokens: int | None = None,
    estimate_accuracy: float | None = None,
    pr_number: int | None = None,
    summary_path: str | None = None,
    issues: list[dict] | None = None,
    created_at: str | None = None,
) -> None:
    """Append one sprint-history snapshot row (issue #805).

    `issues` is serialized to JSON. Best-effort callers (the delete path) should
    wrap this in try/except so a ledger write never blocks the deletion itself.
    """
    created_at = created_at or _now_iso()
    issues_json = json.dumps(issues or [])
    with get_conn() as conn:
        _create_sprint_history_table(conn)
        conn.execute(
            """
            INSERT INTO sprint_history
                (label, project, lifecycle_state, end_reason, duration, tokens,
                 estimate_accuracy, pr_number, summary_path, issues_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (label, project, lifecycle_state, end_reason, duration, tokens,
             estimate_accuracy, pr_number, summary_path, issues_json, created_at),
        )
        conn.commit()


def list_sprint_history() -> list[dict]:
    """Return all sprint_history rows, newest first, with issues_json parsed (issue #805)."""
    with get_conn() as conn:
        _create_sprint_history_table(conn)
        rows = conn.execute(
            "SELECT * FROM sprint_history ORDER BY created_at DESC, id DESC"
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        rec = dict(r)
        try:
            rec["issues"] = json.loads(rec.pop("issues_json") or "[]")
        except (ValueError, TypeError):
            rec["issues"] = []
        out.append(rec)
    return out


# ── Per-agent run durations (issue #764) ──────────────────────────────────────
#
# `sprint_tickets.actual_elapsed_seconds` stores a single blended coder→tester
# wall-clock span, losing per-agent resolution. `agent_runs` records one row per
# dispatched agent (coder, tester, documenter, reviewer, estimator) with its own
# start/finish timestamps and wall-clock duration. This fills the calibration gap
# for the coder/tester split and surfaces per-agent durations in the UI. The
# blended `sprint_tickets` tracking is unchanged (issue #764 AC8).


def _maybe_make_sprint_label_nullable(conn: sqlite3.Connection) -> None:
    """Drop the NOT NULL constraint on sprint_label via table recreation (issue #2246).

    SQLite cannot remove NOT NULL with ALTER TABLE; the table must be recreated.
    Called at the end of _create_agent_runs_table after ALTER TABLE migrations
    have run, so the existing column list is final when we copy data.
    Best-effort: any exception is silently swallowed so a migration failure
    never crashes the dashboard or blocks an agent session.
    """
    try:
        info = conn.execute("PRAGMA table_info(agent_runs)").fetchall()
        sprint_col = next((r for r in info if r["name"] == "sprint_label"), None)
        if sprint_col is None or sprint_col["notnull"] == 0:
            return  # already nullable — nothing to do

        # Columns we know about across all migrations (safe intersection set).
        _known = {
            "id", "issue_number", "sprint_label", "agent", "started_at",
            "finished_at", "duration_seconds", "outcome", "total_tokens",
            "risk_tier", "model_used", "routing_reason", "worktree_sha",
            "base_sha", "attempt_kind", "log_path", "provider", "is_ica",
            "cost_usd", "final_message", "transcript_path", "backend",
            "project", "session_id",
        }
        copy_cols = [r["name"] for r in info if r["name"] in _known]
        col_list = ", ".join(copy_cols)

        conn.execute("ALTER TABLE agent_runs RENAME TO _agent_runs_pre2246")
        try:
            conn.execute("""
                CREATE TABLE agent_runs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_number     INTEGER NOT NULL,
                    sprint_label     TEXT,
                    agent            TEXT NOT NULL,
                    started_at       TEXT NOT NULL,
                    finished_at      TEXT,
                    duration_seconds INTEGER,
                    outcome          TEXT,
                    total_tokens     INTEGER,
                    risk_tier        TEXT,
                    model_used       TEXT,
                    routing_reason   TEXT,
                    worktree_sha     TEXT,
                    base_sha         TEXT,
                    attempt_kind     TEXT,
                    log_path         TEXT,
                    provider         TEXT,
                    is_ica           INTEGER DEFAULT 0,
                    cost_usd         REAL,
                    final_message    TEXT,
                    transcript_path  TEXT,
                    backend          TEXT,
                    project          TEXT,
                    session_id       TEXT
                )
            """)
            conn.execute(
                f"INSERT INTO agent_runs ({col_list}) "
                f"SELECT {col_list} FROM _agent_runs_pre2246"
            )
            conn.execute("DROP TABLE _agent_runs_pre2246")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_agent_runs_issue_agent "
                "ON agent_runs (issue_number, agent)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_agent_runs_sprint "
                "ON agent_runs (sprint_label)"
            )
        except Exception:
            try:
                conn.execute("ALTER TABLE _agent_runs_pre2246 RENAME TO agent_runs")
            except Exception:
                pass
    except Exception:
        pass


def _create_agent_runs_table(conn: sqlite3.Connection) -> None:
    """Create the agent_runs table (issue #764).

    One row per dispatched agent. `started_at`/`finished_at` are ISO-8601 UTC
    strings; `duration_seconds` is the wall-clock duration of the run. Kept in
    its own helper so the recorder can ensure the table exists without running
    the full init_db() migration. Mirrors the Alembic migration
    0009_add_agent_runs so the schema is identical on SQLite and Postgres.
    `risk_tier` and `model_used` added by issue #790 (alembic 0010).
    `attempt_kind` added by issue #787 (initial/fix_round/hang_continue).
    `session_id` added by issue #2246 (manual-run recorder).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number     INTEGER NOT NULL,
            sprint_label     TEXT,
            agent            TEXT NOT NULL,
            started_at       TEXT NOT NULL,
            finished_at      TEXT,
            duration_seconds INTEGER,
            outcome          TEXT,
            total_tokens     INTEGER,
            risk_tier        TEXT,
            model_used       TEXT,
            routing_reason   TEXT,
            worktree_sha     TEXT,
            base_sha         TEXT,
            attempt_kind     TEXT,
            log_path         TEXT,
            provider         TEXT,
            is_ica           INTEGER DEFAULT 0,
            cost_usd         REAL,
            final_message    TEXT,
            transcript_path  TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_issue_agent "
        "ON agent_runs (issue_number, agent)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_runs_sprint "
        "ON agent_runs (sprint_label)"
    )
    # Best-effort ALTER TABLE for existing DBs that predate issue #790/#789/#788/#787/#783/#2246.
    for col, typedef in (
        ("risk_tier", "TEXT"),
        ("model_used", "TEXT"),
        ("routing_reason", "TEXT"),
        ("worktree_sha", "TEXT"),
        ("base_sha", "TEXT"),
        ("attempt_kind", "TEXT"),
        ("log_path", "TEXT"),
        ("backend", "TEXT"),
        # Owning project (owner/repo). Sprint labels are unique only per repo, so
        # without this a same-numbered sprint in another project mixes in here.
        ("project", "TEXT"),
        # LLM provider used for this run, e.g. 'ICA' (issue #1673).
        ("provider", "TEXT"),
        # ICA metered-path tracking (issue #1672).
        # is_ica=1 when CCPROXY_PROFILE was set at dispatch time (IBM Cloud metered path).
        # cost_usd is the pre-computed USD estimate stored atomically at run close.
        ("is_ica", "INTEGER DEFAULT 0"),
        ("cost_usd", "REAL"),
        # Durable agent narrative tail + Claude Code transcript path (issue #2021).
        # final_message: last ~4 KB of the run log, populated at run close.
        # transcript_path: absolute path to the Claude Code session JSONL, when known.
        ("final_message", "TEXT"),
        ("transcript_path", "TEXT"),
        # Hook-driven session key for manual runs (issue #2246).
        ("session_id", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    # Make sprint_label nullable on existing DBs that created it as NOT NULL (issue #2246).
    _maybe_make_sprint_label_nullable(conn)


def _create_advisor_suggestions_table(conn: sqlite3.Connection) -> None:
    """Create the advisor_suggestions draft store (issue #881).

    One row per suggestion from the most recent advisor run. Replaced wholesale
    on every new run (scheduled or on-demand) so there is no suggestion history
    beyond the current draft set per project.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS advisor_suggestions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            project   TEXT NOT NULL,
            run_at    TEXT NOT NULL,
            on_demand INTEGER NOT NULL DEFAULT 0,
            pitch     TEXT NOT NULL,
            rationale TEXT NOT NULL,
            milestone TEXT NOT NULL,
            scope     TEXT NOT NULL CHECK(scope IN ('S', 'M', 'L'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_advisor_suggestions_project "
        "ON advisor_suggestions (project)"
    )


def _create_advisor_look_ahead_table(conn: sqlite3.Connection) -> None:
    """Create the advisor_look_ahead store (issue #883 / brief #884).

    Ordered entries from the most recent advisor look-ahead run. Replaced
    wholesale on every new run — no history beyond the current set per project.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS advisor_look_ahead (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            project   TEXT NOT NULL,
            run_at    TEXT NOT NULL,
            position  INTEGER NOT NULL,
            entry     TEXT NOT NULL,
            on_demand INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_advisor_look_ahead_project "
        "ON advisor_look_ahead (project)"
    )


def _create_advisor_dismissed_table(conn: sqlite3.Connection) -> None:
    """Durable store for pitches the user has dismissed (issue #882)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS advisor_dismissed (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project      TEXT NOT NULL,
            pitch        TEXT NOT NULL,
            dismissed_at TEXT NOT NULL,
            UNIQUE(project, pitch)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_advisor_dismissed_project "
        "ON advisor_dismissed (project)"
    )


def _duration_between(started_at: str | None, finished_at: str | None) -> int | None:
    """Whole seconds between two ISO timestamps, or None if either is unusable."""
    if not started_at or not finished_at:
        return None
    try:
        s = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        f = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return round((f - s).total_seconds())


def _read_log_tail(log_path: str | None, max_bytes: int = 4096) -> str | None:
    """Return the last ``max_bytes`` of a log file as a decoded UTF-8 string.

    Called at run-close to populate ``final_message`` in ``agent_runs``
    (issue #2021 AC2). The bound (default 4 KB ≈ ~100 lines) prevents the
    87 MB DB from bloating over many runs.

    Best-effort: returns ``None`` when ``log_path`` is absent, unreadable,
    or the file is empty — never raises, so a missing log can never break
    run recording or the sprint loop.

    Testable entry-point (AC4): import and call directly with a fake log path
    and a temp ``agent_runs`` DB — no full sprint required.
    """
    if not log_path:
        return None
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, 2)  # seek to end
            size = fh.tell()
            if size == 0:
                return None
            seek_to = max(0, size - max_bytes)
            fh.seek(seek_to)
            raw = fh.read(max_bytes)
        text = raw.decode("utf-8", errors="replace")
        # If we seeked mid-file, drop the likely-truncated first line.
        if seek_to > 0 and "\n" in text:
            text = text[text.index("\n") + 1:]
        return text.strip() or None
    except Exception:
        return None


def record_agent_start(
    issue_number: int,
    sprint_label: str,
    agent: str,
    started_at: str | None = None,
    risk_tier: str | None = None,
    model_used: str | None = None,
    routing_reason: str | None = None,
    worktree_sha: str | None = None,
    base_sha: str | None = None,
    attempt_kind: str | None = None,
    log_path: str | None = None,
    backend: str | None = None,
    project: str | None = None,
    provider: str | None = None,
    is_ica: bool = False,
) -> int | None:
    """Insert an agent_runs row at dispatch time and return its id (issue #764).

    `finished_at`/`duration_seconds`/`outcome` are left NULL until
    record_agent_finish() closes the run. `risk_tier` and `model_used` are
    optional for tester risk-tier routing (issue #790). `routing_reason` is
    optional for coder size-tier routing (issue #789). `worktree_sha` and
    `base_sha` are optional forensic fields from worktree hygiene (issue #788).
    `attempt_kind` is one of 'initial', 'fix_round', or 'hang_continue' (issue #787).
    `log_path` is the absolute path to the issue log file (issue #783).
    `backend` is 'cline' or 'claude-code' (issue #920).
    `provider` is the LLM provider identifier, e.g. 'ICA' (issue #1673).
    `is_ica` is True when CCPROXY_PROFILE is set (IBM Cloud metered path) (issue #1672).
    Returns the new row id (used to close the exact run) or None on failure.
    """
    started_at = started_at or _now_iso()
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        cur = conn.execute(
            "INSERT INTO agent_runs "
            "(issue_number, sprint_label, agent, started_at, risk_tier, model_used, routing_reason, "
            "worktree_sha, base_sha, attempt_kind, log_path, backend, project, provider, is_ica) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(issue_number), sprint_label, agent, started_at, risk_tier, model_used, routing_reason,
             worktree_sha, base_sha, attempt_kind, log_path, backend, project, provider, 1 if is_ica else 0),
        )
        conn.commit()
        return cur.lastrowid


def record_agent_finish(
    issue_number: int,
    sprint_label: str,
    agent: str,
    finished_at: str | None = None,
    duration_seconds: int | None = None,
    outcome: str | None = None,
    total_tokens: int | None = None,
    run_id: int | None = None,
    is_ica: bool = False,
    cost_usd: float | None = None,
    transcript_path: str | None = None,
) -> None:
    """Close the open agent_runs row with finish time, duration and outcome (#764).

    When `run_id` is given the exact row is updated; otherwise the most recent
    still-open run (finished_at IS NULL) matching issue/sprint/agent is closed.
    `duration_seconds` is used as supplied (the dispatcher passes a precise
    monotonic measurement — issue #764 AC3); when omitted it is computed from the
    start/finish timestamps. `is_ica` and `cost_usd` are written atomically with
    the finish record for ICA metered-path runs (issue #1672 AC4).
    `final_message` is populated from the tail of the stored `log_path` via
    `_read_log_tail` (issue #2021 AC2). `transcript_path` is stored when
    supplied by the caller (issue #2021 AC2).
    Best-effort: never raises into the sprint loop.
    """
    finished_at = finished_at or _now_iso()
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        if run_id is not None:
            row = conn.execute(
                "SELECT id, started_at, log_path FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, started_at, log_path FROM agent_runs "
                "WHERE issue_number = ? AND sprint_label = ? AND agent = ? "
                "AND finished_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (int(issue_number), sprint_label, agent),
            ).fetchone()
        if row is None:
            return
        if duration_seconds is None:
            duration_seconds = _duration_between(row["started_at"], finished_at)
        final_message = _read_log_tail(row["log_path"])
        conn.execute(
            "UPDATE agent_runs SET finished_at = ?, duration_seconds = ?, "
            "outcome = ?, total_tokens = ?, is_ica = ?, cost_usd = ?, "
            "final_message = ?, transcript_path = ? WHERE id = ?",
            (
                finished_at,
                None if duration_seconds is None else int(duration_seconds),
                outcome,
                None if total_tokens is None else int(total_tokens),
                1 if is_ica else 0,
                cost_usd,
                final_message,
                transcript_path,
                row["id"],
            ),
        )
        conn.commit()


def record_manual_run_open(
    session_id: str,
    issue_number: int,
    agent: str,
    project: str,
    sprint_label: str | None = None,
    started_at: str | None = None,
) -> None:
    """Insert an agent_runs row at hook session-start for manual runs (issue #2246).

    Called from logs_service when the first tool_use fires for a new session.
    sprint_label is NULL for non-sprint sessions.  Best-effort: any exception
    is silently swallowed so a DB failure never blocks or fails the agent.
    """
    try:
        started_at = started_at or _now_iso()
        with get_conn() as conn:
            _create_agent_runs_table(conn)
            conn.execute(
                "INSERT INTO agent_runs "
                "(issue_number, sprint_label, agent, started_at, project, session_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(issue_number), sprint_label, agent, started_at, project, session_id),
            )
            conn.commit()
    except Exception:
        pass


def record_manual_run_close(
    session_id: str,
    finished_at: str | None = None,
) -> None:
    """Close the open agent_runs row keyed by session_id (issue #2246).

    Called from logs_service when agent_stop fires.  Best-effort: any exception
    is silently swallowed so a DB failure never blocks the session stop path.
    """
    try:
        finished_at = finished_at or _now_iso()
        with get_conn() as conn:
            _create_agent_runs_table(conn)
            row = conn.execute(
                "SELECT id, started_at FROM agent_runs "
                "WHERE session_id = ? AND finished_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if row is None:
                return
            duration = _duration_between(row["started_at"], finished_at)
            conn.execute(
                "UPDATE agent_runs SET finished_at = ?, duration_seconds = ? WHERE id = ?",
                (finished_at, duration, row["id"]),
            )
            conn.commit()
    except Exception:
        pass


def agent_runs_for_issue(issue_number: int, sprint_label: str | None = None) -> list[dict]:
    """Return agent_runs rows for an issue (optionally scoped to a sprint) (#764)."""
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        if sprint_label is None:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE issue_number = ? ORDER BY id",
                (int(issue_number),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE issue_number = ? AND sprint_label = ? "
                "ORDER BY id",
                (int(issue_number), sprint_label),
            ).fetchall()
    return [dict(r) for r in rows]


def update_worktree_shas(
    issue_number: int,
    sprint_label: str,
    agent: str,
    worktree_sha: "str | None",
    base_sha: "str | None",
) -> None:
    """Update the most recent open agent_runs row with worktree forensic SHAs (issue #788).

    Best-effort: silently returns if no matching open row is found.
    """
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        conn.execute(
            "UPDATE agent_runs SET worktree_sha = ?, base_sha = ? "
            "WHERE id = (SELECT MAX(id) FROM agent_runs "
            "WHERE issue_number = ? AND sprint_label = ? AND agent = ? AND finished_at IS NULL)",
            (worktree_sha, base_sha, int(issue_number), sprint_label, agent),
        )
        conn.commit()


def _manual_runs_for_sprint(
    conn,
    sprint_label: str,
    project: str,
) -> list[dict]:
    """Return NULL-sprint agent_runs rows for issues that carry *sprint_label* in the
    issues mirror (issue #2247).

    Joins against the issues table (JSON labels column) to find which issue numbers
    belong to the sprint, then returns manual (sprint_label IS NULL) runs for those
    issues scoped to *project*.
    """
    _create_issues_table(conn)
    issue_rows = conn.execute(
        "SELECT issue_number FROM issues "
        "WHERE repo = ? AND EXISTS ("
        "  SELECT 1 FROM json_each(labels) WHERE value = ?"
        ")",
        (project, sprint_label),
    ).fetchall()
    if not issue_rows:
        return []
    issue_nums = [r["issue_number"] for r in issue_rows]
    placeholders = ", ".join("?" * len(issue_nums))
    rows = conn.execute(
        f"SELECT * FROM agent_runs "
        f"WHERE sprint_label IS NULL AND project = ? "
        f"AND issue_number IN ({placeholders}) "
        f"ORDER BY issue_number, id",
        [project, *issue_nums],
    ).fetchall()
    return [dict(r) for r in rows]


def agent_runs_for_sprint(sprint_label: str, project: str | None = None) -> list[dict]:
    """Return all agent_runs rows for a sprint, ordered by issue then start (#764).

    When *project* is given, rows are scoped to it — sprint labels are unique only
    per repo, so an unscoped read mixes a same-numbered sprint from another
    project. Legacy rows written before the project column existed have NULL
    project; if the project filter finds none, fall back to a read restricted to
    those blank/NULL-project rows so pre-migration sprints still render. Rows
    owned by a *different* project are never returned (issue #1881 — a draft
    sprint that never ran was rendering another project's same-labelled runs).

    Also includes manual (sprint_label IS NULL) runs for issues labelled with
    *sprint_label* in the issues mirror so sprint-finish grouping captures work
    done outside a formal sprint run (issue #2247).
    """
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        if project:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE sprint_label = ? AND project = ? "
                "ORDER BY issue_number, id",
                (sprint_label, project),
            ).fetchall()
            if rows:
                labeled = [dict(r) for r in rows]
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_runs WHERE sprint_label = ? "
                    "AND (project = '' OR project IS NULL) "
                    "ORDER BY issue_number, id",
                    (sprint_label,),
                ).fetchall()
                labeled = [dict(r) for r in rows]

            manual = _manual_runs_for_sprint(conn, sprint_label, project)
            seen_ids = {r["id"] for r in labeled}
            combined = labeled + [r for r in manual if r["id"] not in seen_ids]
            combined.sort(key=lambda r: (r.get("issue_number") or 0, r["id"]))
            return combined

        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE sprint_label = ? "
            "ORDER BY issue_number, id",
            (sprint_label,),
        ).fetchall()
    return [dict(r) for r in rows]


def _now_iso() -> str:
    return utc_now()


def record_sprint_start(
    label: str,
    project: str = "",
    started_at: str | None = None,
    parent_label: str | None = None,
) -> None:
    """Write (or move to) a `running` sprints row (issue #757).

    Idempotent on `label`: a second start re-asserts state='running' and
    refreshes started_at without creating a duplicate row. Routed through
    transition_sprint_state (issue #1087) so the legal-edge guard applies.
    """
    transition_sprint_state(
        label, "running", actor="manager",
        started_at=started_at, project=project, parent_label=parent_label,
    )


def record_sprint_finish(label: str, ended_at: str | None = None,
                         end_reason: str | None = None,
                         project: str = "",
                         actor: str = "manager") -> "TransitionResult":
    """Move a sprints row to `completed` (issue #757).

    actor defaults to "manager"; pass "reconcile" to complete a superseded
    ancestor still in `needs_rework` whose whole lineage has merged to develop
    (the B2 edge is reconcile-only). Returns the TransitionResult so callers can
    detect a silent rejection instead of assuming success.
    """
    return transition_sprint_state(
        label, "completed", actor=actor,
        end_reason=end_reason, ended_at=ended_at, project=project,
    )


def record_sprint_needs_rework(label: str, end_reason: str | None = None,
                               ended_at: str | None = None,
                               project: str = "") -> None:
    """Move a sprints row to `needs_rework` with a reason.

    Unified-lifecycle terminal for every bad ending: ticket failure, crash,
    orphan PID, or user cancel. The distinction lives in `end_reason`
    ("stopped by user", "process lost", "ticket-failures", …).
    """
    transition_sprint_state(
        label, "needs_rework", actor="manager",
        end_reason=end_reason, ended_at=ended_at, project=project,
    )


def record_sprint_ready_to_merge(label: str, end_reason: str | None = None,
                                 ended_at: str | None = None,
                                 project: str = "") -> None:
    """Move a sprints row to `ready_to_merge` (run ended, all tickets passed)."""
    transition_sprint_state(
        label, "ready_to_merge", actor="manager",
        end_reason=end_reason, ended_at=ended_at, project=project,
    )


def record_sprint_cancel(label: str, end_reason: str = "stopped by user",
                         ended_at: str | None = None) -> None:
    """Deprecated alias — `cancelled` no longer exists; writes `needs_rework`."""
    record_sprint_needs_rework(label, end_reason=end_reason, ended_at=ended_at)


def record_sprint_fail(label: str, end_reason: str | None = None,
                       ended_at: str | None = None) -> None:
    """Deprecated alias — writes `needs_rework` under the unified lifecycle."""
    record_sprint_needs_rework(label, end_reason=end_reason, ended_at=ended_at)


def _set_sprint_terminal(label: str, state: str, end_reason: str | None,
                         ended_at: str | None, project: str = "") -> None:
    ended_at = ended_at or _now_iso()
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        # Upsert so a transition can land even if no start row was written
        # (e.g. a legacy sprint cancelled before its first DB write).
        conn.execute(
            """
            INSERT INTO sprints (label, project, state, created_at, ended_at, end_reason)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(label, project) DO UPDATE SET
                state      = excluded.state,
                ended_at   = excluded.ended_at,
                end_reason = COALESCE(excluded.end_reason, sprints.end_reason)
            """,
            (label, project or "", state, ended_at, ended_at, end_reason),
        )
        conn.commit()


def rename_sprint(old_label: str, new_label: str, project: str | None = None) -> None:
    """Rename a sprint and its ticket-order rows (issue #758).

    No-op if no row exists for `old_label`. Best-effort: GitHub labels remain the
    source of truth, so a missing DB row must not fail a rename.

    Scope the sprints row by project so a rename in one repo doesn't rename another
    repo's same-numbered sprint. (sprint_ticket_order has no project column yet —
    a separate schema migration is needed to scope it.)
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        if project:
            conn.execute(
                "UPDATE sprints SET label = ? WHERE label = ? AND project = ?",
                (new_label, old_label, project),
            )
        else:
            conn.execute(
                "UPDATE sprints SET label = ? WHERE label = ?",
                (new_label, old_label),
            )
        conn.execute(
            "UPDATE sprint_ticket_order SET label = ? WHERE label = ?",
            (new_label, old_label),
        )
        conn.commit()


def ensure_sprint_draft_row(label: str, project: str) -> None:
    """Create a `draft` row for *label* if none exists yet (issue #1693).

    Sprint creation and rerun-queue (auto_run=false) previously wrote no DB
    row at all — a never-run sprint was invisible to `sprint_state.current()`
    and read only as an *implicit* draft (missing row -> "draft" inside
    `transition_sprint_state`). That implicit fallback still holds for
    legacy/pre-#1693 sprints, but new sprints now get a real row from the
    moment they're queued, so `sprint_state.current()` is a complete picture
    without relying on the missing-row special case.

    Idempotent and non-destructive: `INSERT OR IGNORE` — if a row already
    exists in ANY state (e.g. a fast dispatch raced ahead of this call),
    it is left untouched.
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO sprints (label, project, state, created_at)
            VALUES (?, ?, 'draft', ?)
            """,
            (label, project, _now_iso()),
        )
        conn.commit()


def set_sprint_immediate_parent(label: str, project: str, immediate_parent: str) -> None:
    """Record the immediate-parent lineage link for a rerun child (issue #1691).

    Distinct from `parent_label` (always the BASE label, self-healed on every
    transition — drives History grouping): `immediate_parent` is whichever
    label was actually rerun to create this one (e.g. 94.2's immediate parent
    is 94.1, not 94) and is what merge-topology resolution should use.
    Creates a placeholder `draft` row when the child doesn't have one yet
    (rerun with auto_run=false queues the child before any dispatch) so the
    lineage link isn't lost while queued; a later `running` transition
    upgrades that row in place via the existing ON CONFLICT path.
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO sprints (label, project, state, created_at, immediate_parent)
            VALUES (?, ?, 'draft', ?, ?)
            ON CONFLICT(label, project) DO UPDATE SET
                immediate_parent = excluded.immediate_parent
            """,
            (label, project, _now_iso(), immediate_parent),
        )
        conn.commit()


def get_sprint(label: str, project: str | None = None) -> dict | None:
    """Return the sprints row for `label` as a dict, or None (issue #757).

    When ``project`` is set, never return a row owned by a different project
  (labels like ``sprint-64`` are only unique per repo, not globally).
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        if project is not None:
            prow = (project or "").strip()
            row = conn.execute(
                "SELECT * FROM sprints WHERE label = ? AND project = ?",
                (label, prow),
            ).fetchone()
            if row:
                return dict(row)
            row = conn.execute(
                "SELECT * FROM sprints WHERE label = ?", (label,)
            ).fetchone()
            if not row:
                return None
            stored = (row["project"] or "").strip()
            if stored and stored != prow:
                return None
            return dict(row)
        row = conn.execute(
            "SELECT * FROM sprints WHERE label = ?", (label,)
        ).fetchone()
    return dict(row) if row else None


def list_sprints_lifecycle() -> list[dict]:
    """Return all rows of the `sprints` lifecycle table as dicts (issue #805)."""
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        rows = conn.execute("SELECT * FROM sprints").fetchall()
    return [dict(r) for r in rows]


def get_sprint_children(parent_label: str, project: str | None = None) -> list[dict]:
    """Return sprints rows whose parent_label matches the given label (issue #1093).

    When project is provided, results are scoped to that project (issue #1464).
    Without project, all children are returned with a warning (label-only fallback).
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        if project:
            rows = conn.execute(
                "SELECT * FROM sprints WHERE parent_label = ? AND project = ? ORDER BY label",
                (parent_label, project),
            ).fetchall()
        else:
            _logger.warning(
                "get_sprint_children called without project for parent %r — label-only fallback",
                parent_label,
            )
            rows = conn.execute(
                "SELECT * FROM sprints WHERE parent_label = ? ORDER BY label",
                (parent_label,),
            ).fetchall()
    return [dict(r) for r in rows]


def is_sprint_running(label: str, pid_alive: bool) -> bool:
    """Authoritative "is this sprint running?" check (issue #757).

    True only when the DB state is `running` AND the sprint process is alive.
    A PID-dead + DB-running row does NOT report running.
    """
    row = get_sprint(label)
    return bool(row and row["state"] == "running" and pid_alive)


def set_sprint_ticket_order(label: str, issue_numbers: list[int]) -> None:
    """Persist the ticket execution order for a sprint (issue #757).

    Replaces any existing order for `label` so positions reflect exactly the
    supplied sequence (position 0 dispatched first).
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute("DELETE FROM sprint_ticket_order WHERE label = ?", (label,))
        conn.executemany(
            "INSERT INTO sprint_ticket_order (label, issue, position) "
            "VALUES (?, ?, ?)",
            [(label, int(n), pos) for pos, n in enumerate(issue_numbers)],
        )
        conn.commit()


def get_sprint_ticket_order(label: str) -> list[int]:
    """Return persisted issue numbers for `label` in position order (issue #757)."""
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        rows = conn.execute(
            "SELECT issue FROM sprint_ticket_order WHERE label = ? "
            "ORDER BY position",
            (label,),
        ).fetchall()
    return [r["issue"] for r in rows]


def upsert_issues(repo: str, issues: list[dict]) -> int:
    """Upsert a batch of gh-CLI-shaped issue dicts into the mirror.

    Each issue dict is expected to carry: number, title, state, labels (list of
    {name, color}), updatedAt, plus any extra fields (assignees, url, body) which
    are preserved in the `raw` column.  Returns the number of issues written.
    """
    if not issues:
        return 0
    with get_conn() as conn:
        _create_issues_table(conn)
        for issue in issues:
            number = issue.get("number")
            if number is None:
                continue
            labels = issue.get("labels") or []
            conn.execute(
                """INSERT INTO issues
                       (repo, issue_number, title, state, labels, updated_at, raw)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repo, issue_number) DO UPDATE SET
                       title      = excluded.title,
                       state      = excluded.state,
                       labels     = excluded.labels,
                       updated_at = excluded.updated_at,
                       raw        = excluded.raw""",
                (
                    repo,
                    int(number),
                    issue.get("title", ""),
                    issue.get("state", ""),
                    json.dumps(labels),
                    issue.get("updatedAt", "") or issue.get("updated_at", ""),
                    json.dumps(issue),
                ),
            )
        conn.commit()
    return len(issues)


def mark_issues_closed(repo: str, numbers: list[int]) -> int:
    """Flip mirrored issues to closed by number (issue-mirror reconcile).

    Mirror reads reconstruct from the `raw` JSON (see _row_to_issue), so the
    state column alone is not enough — the raw blob's `$.state` is patched too,
    via json_set, preserving every other field (title, labels, body). Used by
    the open-set reconcile to correct rows that the incremental poll missed
    closing (an issue can fall off the recently-updated window before its close
    is observed, leaving a permanently-stale `open` row that keeps a finished
    sprint on the board). Returns the number of rows updated.
    """
    if not numbers:
        return 0
    updated = 0
    with get_conn() as conn:
        _create_issues_table(conn)
        for number in numbers:
            cur = conn.execute(
                """UPDATE issues
                       SET state = 'closed',
                           raw   = json_set(raw, '$.state', 'closed')
                   WHERE repo = ? AND issue_number = ? AND state = 'open'""",
                (repo, int(number)),
            )
            updated += cur.rowcount
        conn.commit()
    return updated


def _row_to_issue(row: sqlite3.Row) -> dict:
    """Reconstruct a gh-CLI-shaped issue dict from a mirror row."""
    if row["raw"]:
        try:
            return json.loads(row["raw"])
        except (ValueError, TypeError):
            pass
    return {
        "number": row["issue_number"],
        "title": row["title"],
        "state": row["state"],
        "labels": json.loads(row["labels"]) if row["labels"] else [],
        "updatedAt": row["updated_at"],
    }


def get_mirrored_issues(repo: str, state: str | None = None) -> list[dict]:
    """Return mirrored issues for a repo as gh-CLI-shaped dicts.

    Optionally filter by state ('open' / 'closed').  Returns an empty list when
    nothing is mirrored yet (callers may then fall back to a live fetch).
    """
    sql = "SELECT * FROM issues WHERE repo = ?"
    params: list = [repo]
    if state is not None:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY issue_number DESC"
    with get_conn() as conn:
        _create_issues_table(conn)
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_issue(r) for r in rows]


def get_mirrored_issue(repo: str, issue_number: int) -> dict | None:
    """Return a single mirrored issue, or None if not present."""
    with get_conn() as conn:
        _create_issues_table(conn)
        row = conn.execute(
            "SELECT * FROM issues WHERE repo = ? AND issue_number = ?",
            (repo, int(issue_number)),
        ).fetchone()
    return _row_to_issue(row) if row else None


def invalidate_mirrored_issue(repo: str, issue_number: int) -> None:
    """Delete one issue's mirror row so the next read falls back to REST.

    Called after a label write (issue #1814) so _get_issue_labels cannot
    return a stale label set that predates the write.  The row is
    repopulated by the next ~60 s ETag sync.  Best-effort: exceptions are
    swallowed so a missing DB_PATH or locked DB never breaks a transition.
    """
    try:
        with get_conn() as conn:
            _create_issues_table(conn)
            conn.execute(
                "DELETE FROM issues WHERE repo = ? AND issue_number = ?",
                (repo, int(issue_number)),
            )
            conn.commit()
    except Exception:
        pass


def get_sync_etag(key: str) -> str | None:
    """Return the stored ETag for *key*, or None."""
    with get_conn() as conn:
        _create_sync_state_table(conn)
        row = conn.execute(
            "SELECT etag FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
    return row["etag"] if row and row["etag"] else None


def get_sync_updated_at(key: str) -> str | None:
    """Return the updated_at timestamp for *key* from sync_state, or None.

    Records the wall-clock time at which set_sync_etag last ran for this key
    (i.e., when the mirror last performed a successful ETag sync). Used by
    dispatch to get the mirror's last-sync timestamp so the stale-hit guard
    compares issue updatedAt against when the mirror actually synced, not
    wall-clock now (issue #1915).
    """
    with get_conn() as conn:
        _create_sync_state_table(conn)
        row = conn.execute(
            "SELECT updated_at FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
    return row["updated_at"] if row and row["updated_at"] else None


def set_sync_etag(key: str, etag: str) -> None:
    """Store the ETag for *key* (used as If-None-Match on the next poll)."""
    now = utc_now()
    with get_conn() as conn:
        _create_sync_state_table(conn)
        conn.execute(
            """INSERT INTO sync_state (key, etag, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   etag = excluded.etag, updated_at = excluded.updated_at""",
            (key, etag, now),
        )
        conn.commit()


# ── Bootstrap schema marker (issue #760) ──────────────────────────────────────
#
# A fresh clone starts with no commander.db, so the mirror is empty until a sync
# runs. The server detects this on startup via the *absence* of a schema-marker
# row and runs a one-time full GitHub sync before handing off to the ETag loop.
# The marker is stored in the sync_state table under a reserved key so a second
# start can detect it and skip the bootstrap.

# v2: the original bootstrap synced only one page (~100 newest issues), so the
# mirror was partial. Bumping the marker key makes existing installs re-run the
# (now paginated) full crawl on next start; the old key is left behind, inert.
BOOTSTRAP_MARKER_KEY = "bootstrap:complete:v2"


def is_bootstrap_complete() -> bool:
    """Return True if the bootstrap schema-marker row is present (issue #760)."""
    with get_conn() as conn:
        _create_sync_state_table(conn)
        row = conn.execute(
            "SELECT 1 FROM sync_state WHERE key = ?", (BOOTSTRAP_MARKER_KEY,)
        ).fetchone()
    return row is not None


def mark_bootstrap_complete() -> None:
    """Write the bootstrap schema-marker row on a successful full sync (issue #760)."""
    now = utc_now()
    with get_conn() as conn:
        _create_sync_state_table(conn)
        conn.execute(
            """INSERT INTO sync_state (key, etag, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET updated_at = excluded.updated_at""",
            (BOOTSTRAP_MARKER_KEY, "1", now),
        )
        conn.commit()


def record_ticket_status(
    issue: str | int,
    status: str,
    actor: str,
    note: str | None = None,
    ts: str | None = None,
) -> None:
    """Write a ticket_status row for a successful transition (issue #755).

    `ts` defaults to the current UTC timestamp in the same ISO-8601 format used
    by the other tables.  Ensures the table exists before inserting, so callers
    don't need to have run init_db() first.
    """
    if ts is None:
        ts = utc_now()
    with get_conn() as conn:
        _create_ticket_status_table(conn)
        conn.execute(
            "INSERT INTO ticket_status (issue, status, actor, note, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(issue), status, actor, note, ts),
        )
        conn.commit()


def upsert_agent(session_id: str, working_dir: str, status: str,
                 last_tool: str | None = None, name: str | None = None):
    name = name or Path(working_dir).name or working_dir
    now = utc_now()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO agents (session_id, name, working_dir, status, last_tool, last_seen, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                status    = CASE WHEN agents.status = 'archived' THEN 'archived' ELSE excluded.status END,
                last_tool = COALESCE(excluded.last_tool, agents.last_tool),
                last_seen = excluded.last_seen
        """, (session_id, name, working_dir, status, last_tool, now, now))
        conn.commit()


def timeout_idle_agents(threshold_seconds: int) -> int:
    """Mark 'working' agents with last_seen older than threshold as 'timed_out'.

    Returns the count of agents updated.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE agents SET status='timed_out' WHERE status='working' AND last_seen < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount


def add_event(session_id: str, event_type: str, data: dict):
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO session_events (session_id, event_type, data, created_at) VALUES (?, ?, ?, ?)",
            (session_id, event_type, json.dumps(data), now),
        )
        conn.commit()


def record_event(
    project: str,
    source: str,
    actor: str,
    type: str,
    target: str,
    detail: dict,
    action_id: str | None = None,
) -> None:
    """Insert one structured log event into the events table."""
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO events
               (project, timestamp, source, actor, type, target, action_id, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (project, now, source, actor, type, target, action_id, json.dumps(detail)),
        )
        conn.commit()


def record_project_event(
    project: str,
    source: str,
    event_type: str,
    target: str | None = None,
    actor: str | None = None,
    data: dict | None = None,
    action_id: str | None = None,
) -> None:
    """Insert one row into project_events."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_events
               (project, created_at, source, event_type, target, actor, action_id, data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (project, now, source, event_type, target, actor, action_id,
             json.dumps(data) if data is not None else None),
        )
        conn.commit()


def get_project_events(
    project: str,
    source: str | None = None,
    target: str | None = None,
    since: str | None = None,
    until: str | None = None,
    action_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return project_events rows newest-first with optional filters."""
    clauses = ["project = ?"]
    params: list = [project]
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if target is not None:
        clauses.append("target = ?")
        params.append(target)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since.replace("Z", ""))
    if until is not None:
        clauses.append("created_at <= ?")
        params.append(until.replace("Z", ""))
    if action_id is not None:
        clauses.append("action_id = ?")
        params.append(action_id)
    params.append(limit)
    sql = (
        "SELECT * FROM project_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC, id DESC LIMIT ?"
    )
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_agents() -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_conn() as conn:
        conn.execute(
            "UPDATE agents SET status = 'archived' WHERE status = 'done' AND last_seen < ?",
            (cutoff,),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM agents WHERE status != 'archived' ORDER BY last_seen DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_events(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT e.*, a.name AS agent_name
            FROM session_events e
            LEFT JOIN agents a ON e.session_id = a.session_id
            ORDER BY e.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["data"] = json.loads(d["data"])
        result.append(d)
    return result


# ── Token usage ───────────────────────────────────────────────────────────────

def record_token_usage(
    session_id: str,
    project: str,
    input_tokens: int,
    output_tokens: int,
    agent_role: str | None = None,
    model_name: str | None = None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    ccproxy_profile: str | None = None,
) -> None:
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO token_usage
               (session_id, project, input_tokens, output_tokens, recorded_at,
                agent_role, model_name, cache_read_tokens, cache_write_tokens, ccproxy_profile)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, project, input_tokens, output_tokens, now,
             agent_role, model_name, cache_read_tokens or 0, cache_write_tokens or 0, ccproxy_profile),
        )
        conn.commit()


def sum_token_usage_window(agent_role: str, since_utc: str, until_utc: str) -> tuple[int, int]:
    """Return (input_sum, output_sum) for one agent role within a UTC window.

    Used by sprint_manager to attribute a dispatch's token spend: rows are
    written by the PostToolUse hook with the CLAUDE_AGENT_ROLE tag, so scoping
    by role + the dispatch's start/finish window yields that dispatch's usage
    (correct while at most one agent per role runs at a time).
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(input_tokens), 0)  AS tin,
                      COALESCE(SUM(output_tokens), 0) AS tout
               FROM token_usage
               WHERE agent_role = ? AND recorded_at >= ? AND recorded_at <= ?""",
            (agent_role, since_utc, until_utc),
        ).fetchone()
    return (int(row["tin"]), int(row["tout"])) if row else (0, 0)


def sum_token_usage_window_full(
    agent_role: str, since_utc: str, until_utc: str
) -> tuple[int, int, int, int, bool]:
    """Return (raw_input, output, cache_read, cache_write, is_ica) for an agent role window.

    Extends sum_token_usage_window with the ICA-specific breakdown (issue #1672).
    `raw_input` = input_tokens - cache_read_tokens (uncached input only).
    `is_ica` is True when any row in the window has ccproxy_profile set (i.e. CCPROXY_PROFILE
    was active during the dispatch). Falls back gracefully when cache columns are absent.
    """
    try:
        with get_conn() as conn:
            row = conn.execute(
                """SELECT
                       COALESCE(SUM(input_tokens), 0)       AS tin,
                       COALESCE(SUM(output_tokens), 0)      AS tout,
                       COALESCE(SUM(cache_read_tokens), 0)  AS tcr,
                       COALESCE(SUM(cache_write_tokens), 0) AS tcw,
                       MAX(CASE WHEN ccproxy_profile IS NOT NULL THEN 1 ELSE 0 END) AS has_ica
                   FROM token_usage
                   WHERE agent_role = ? AND recorded_at >= ? AND recorded_at <= ?""",
                (agent_role, since_utc, until_utc),
            ).fetchone()
        if row is None:
            return (0, 0, 0, 0, False)
        tin = int(row["tin"])
        tout = int(row["tout"])
        tcr = int(row["tcr"])
        tcw = int(row["tcw"])
        is_ica = bool(row["has_ica"])
        # raw_input = total input stored (raw + cache_read as sent by hook) minus cache_read
        raw_input = max(0, tin - tcr)
        return (raw_input, tout, tcr, tcw, is_ica)
    except Exception:
        # Graceful fallback: cache columns may not exist on old DBs
        tin, tout = sum_token_usage_window(agent_role, since_utc, until_utc)
        return (tin, tout, 0, 0, False)


def _compute_ica_cost_usd(
    raw_input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    model_name: str | None,
    price_map: dict | None,
) -> float | None:
    """Compute ICA USD cost from token breakdown and a price_map (issue #1672 AC2).

    Uses the model's `in` and `out` rates from price_map ($/million tokens).
    Cache read costs 10% of the input rate; cache write costs 125% of the input rate
    (standard Anthropic cache pricing ratios, applicable on the ICA metered path).
    Returns None when price_map is absent or the model is not found in it.
    """
    if not price_map or not model_name:
        return None
    model_key = (model_name or "").lower()
    entry = price_map.get(model_key) or price_map.get(model_key.split("-20")[0])
    if not entry:
        return None
    rate_in = float(entry.get("in", 0)) / 1_000_000
    rate_out = float(entry.get("out", 0)) / 1_000_000
    rate_cr = rate_in * 0.10   # cache read: 10% of input rate
    rate_cw = rate_in * 1.25   # cache write: 125% of input rate
    cost = (
        raw_input_tokens * rate_in
        + output_tokens * rate_out
        + cache_read_tokens * rate_cr
        + cache_write_tokens * rate_cw
    )
    return cost


def ica_sprint_cost_summary(
    sprint_label: str, project: str | None = None
) -> dict:
    """Return ICA cost totals for a sprint, read from agent_runs (issue #1672 AC5/AC6).

    Only includes runs where is_ica=1, outcome in ('success','pass'), and cost_usd
    is not NULL and > 0 (AC7). Returns a dict:
      {is_ica, run_count, total_tokens, cost_usd}
    All values are read from the DB — no re-computation at call time (AC6).
    """
    # Outcomes that represent successful/completed runs (exclude failed/crashed)
    _SUCCESS_OUTCOMES = ("success", "pass")
    try:
        with get_conn() as conn:
            _create_agent_runs_table(conn)
            if project:
                rows = conn.execute(
                    """SELECT total_tokens, cost_usd
                       FROM agent_runs
                       WHERE sprint_label = ?
                         AND project = ?
                         AND is_ica = 1
                         AND outcome IN ('success', 'pass')
                         AND cost_usd IS NOT NULL
                         AND cost_usd > 0""",
                    (sprint_label, project),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT total_tokens, cost_usd
                       FROM agent_runs
                       WHERE sprint_label = ?
                         AND is_ica = 1
                         AND outcome IN ('success', 'pass')
                         AND cost_usd IS NOT NULL
                         AND cost_usd > 0""",
                    (sprint_label,),
                ).fetchall()
    except Exception:
        rows = []

    run_count = len(rows)
    total_tokens = sum(int(r["total_tokens"] or 0) for r in rows)
    cost_usd = sum(float(r["cost_usd"] or 0.0) for r in rows)
    return {
        "is_ica": run_count > 0,
        "run_count": run_count,
        "total_tokens": total_tokens,
        "cost_usd": round(cost_usd, 6),
    }


def get_earliest_token_row_after(after_utc: str | None = None) -> str | None:
    """Return the recorded_at of the earliest token_usage row after *after_utc*.

    If *after_utc* is None, returns the earliest row ever.
    Returns None if no rows exist.
    """
    with get_conn() as conn:
        if after_utc is None:
            row = conn.execute(
                "SELECT MIN(recorded_at) AS ts FROM token_usage"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT MIN(recorded_at) AS ts FROM token_usage WHERE recorded_at > ?",
                (after_utc,),
            ).fetchone()
    return row["ts"] if row and row["ts"] else None


def get_window_usage(window_start_utc: str) -> int:
    """Sum input_tokens + output_tokens for all rows with recorded_at >= window_start_utc.

    Covers all sessions and projects.
    Returns the total token count as an integer.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS total
               FROM token_usage
               WHERE recorded_at >= ?""",
            (window_start_utc,),
        ).fetchone()
    return int(row["total"])


def delete_test_events() -> int:
    """Delete session_events whose session_id or data looks like a test/debug entry.

    Matches patterns: session_id starting with 'test_' or 'Test-', or
    event_type == 'test', or data containing 'Test alert' or 'Test-'.
    Returns the count of rows deleted.
    """
    with get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM session_events WHERE
               session_id LIKE 'test_%'
               OR session_id LIKE 'Test-%'
               OR event_type = 'test'
               OR data LIKE '%Test alert%'
               OR data LIKE '%Test-%'
               OR data LIKE '%-test-%'""",
        )
        conn.commit()
        return cur.rowcount


def delete_test_agents() -> int:
    """Delete agents whose session_id or name looks like a test/debug entry.

    Returns the count of rows deleted.
    """
    with get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM agents WHERE
               session_id LIKE 'test_%'
               OR session_id LIKE 'Test-%'
               OR name LIKE 'test_%'
               OR name LIKE 'Test-%'""",
        )
        conn.commit()
        return cur.rowcount


def get_tokens_today(project: str | None = None) -> dict:
    """Return total input_tokens, output_tokens since midnight Asia/Bangkok.

    If *project* is given, filter to that project only.
    Returns {"input_tokens": int, "output_tokens": int}.
    """
    cutoff = _bkk_midnight_utc()
    with get_conn() as conn:
        if project:
            row = conn.execute(
                """SELECT COALESCE(SUM(input_tokens),0)  AS inp,
                          COALESCE(SUM(output_tokens),0) AS out
                   FROM token_usage
                   WHERE project = ? AND recorded_at >= ?""",
                (project, cutoff),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT COALESCE(SUM(input_tokens),0)  AS inp,
                          COALESCE(SUM(output_tokens),0) AS out
                   FROM token_usage
                   WHERE recorded_at >= ?""",
                (cutoff,),
            ).fetchone()
    return {"input_tokens": row["inp"], "output_tokens": row["out"]}


def get_token_usage_by_agent_model(window_start_utc: str | None = None) -> list[dict]:
    """Return token usage grouped by agent_role and model_name.

    Each row contains agent_role, model_name, total_input, total_output, total_tokens.
    If window_start_utc is provided, restricts to rows recorded on or after that timestamp.
    Rows without agent_role or model_name are grouped as 'unknown'.
    """
    cutoff_clause = "WHERE recorded_at >= ?" if window_start_utc else ""
    params = (window_start_utc,) if window_start_utc else ()
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT
                  COALESCE(agent_role, 'unknown') AS agent_role,
                  COALESCE(model_name, 'unknown') AS model_name,
                  COALESCE(SUM(input_tokens), 0)  AS total_input,
                  COALESCE(SUM(output_tokens), 0) AS total_output,
                  COALESCE(SUM(input_tokens + output_tokens), 0) AS total_tokens
               FROM token_usage
               {cutoff_clause}
               GROUP BY agent_role, model_name
               ORDER BY total_tokens DESC""",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ── Docs freshness warnings ───────────────────────────────────────────────────

def upsert_docs_warning(
    repo: str,
    doc_path: str,
    trigger_ref: str,
    trigger_type: str,
    trigger_url: str | None = None,
) -> int:
    """Insert or re-open a docs freshness warning. Returns the row id."""
    now = utc_now()
    with get_conn() as conn:
        # Check if an active (non-cleared) warning already exists for this repo+doc.
        existing = conn.execute(
            "SELECT id FROM docs_freshness_warnings WHERE repo=? AND doc_path=? AND is_cleared=0",
            (repo, doc_path),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE docs_freshness_warnings SET trigger_ref=?, trigger_type=?, trigger_url=?, flagged_at=? WHERE id=?",
                (trigger_ref, trigger_type, trigger_url, now, existing["id"]),
            )
            conn.commit()
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO docs_freshness_warnings
               (repo, doc_path, trigger_ref, trigger_type, trigger_url, flagged_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (repo, doc_path, trigger_ref, trigger_type, trigger_url, now),
        )
        conn.commit()
        return cur.lastrowid


def clear_docs_warning(repo: str, doc_path: str) -> int:
    """Clear all active warnings for repo+doc_path. Returns count cleared."""
    now = utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE docs_freshness_warnings SET is_cleared=1, cleared_at=? WHERE repo=? AND doc_path=? AND is_cleared=0",
            (now, repo, doc_path),
        )
        conn.commit()
        return cur.rowcount


def clear_docs_warning_by_id(warning_id: int) -> bool:
    """Clear a single warning by id. Returns True if found."""
    now = utc_now()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE docs_freshness_warnings SET is_cleared=1, cleared_at=? WHERE id=? AND is_cleared=0",
            (now, warning_id),
        )
        conn.commit()
        return cur.rowcount > 0


def get_active_docs_warnings(repo: str | None = None) -> list[dict]:
    """Return all non-cleared docs freshness warnings, optionally filtered by repo."""
    with get_conn() as conn:
        if repo:
            rows = conn.execute(
                "SELECT * FROM docs_freshness_warnings WHERE is_cleared=0 AND repo=? ORDER BY flagged_at DESC",
                (repo,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM docs_freshness_warnings WHERE is_cleared=0 ORDER BY flagged_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_debug_token_usage() -> dict:
    """Return diagnostic info about the token_usage table.

    Returns:
        row_count        — total number of rows in token_usage
        latest_recorded_at — ISO-8601 string of the most recent recorded_at, or None
        tokens_today     — total tokens (input + output) since Bangkok midnight
    """
    cutoff = _bkk_midnight_utc()
    with get_conn() as conn:
        count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM token_usage"
        ).fetchone()
        latest_row = conn.execute(
            "SELECT MAX(recorded_at) AS ts FROM token_usage"
        ).fetchone()
        today_row = conn.execute(
            """SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS total
               FROM token_usage
               WHERE recorded_at >= ?""",
            (cutoff,),
        ).fetchone()

    return {
        "row_count":          int(count_row["cnt"]),
        "latest_recorded_at": latest_row["ts"] if latest_row and latest_row["ts"] else None,
        "tokens_today":       int(today_row["total"]),
    }
