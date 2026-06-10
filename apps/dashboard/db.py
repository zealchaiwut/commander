import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

# Asia/Bangkok is UTC+7 (no DST)
_BKK_OFFSET = timezone(timedelta(hours=7))


def _bkk_midnight_utc() -> str:
    """Return the ISO-8601 UTC timestamp of midnight Asia/Bangkok today."""
    now_bkk  = datetime.now(_BKK_OFFSET)
    midnight  = now_bkk.replace(hour=0, minute=0, second=0, microsecond=0)
    utc_mid   = midnight.astimezone(timezone.utc)
    return utc_mid.strftime("%Y-%m-%dT%H:%M:%S")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
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
        for col, coltype in [("agent_role", "TEXT"), ("model_name", "TEXT")]:
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
        _create_sync_state_table(conn)
        _create_sprint_lifecycle_tables(conn)
        _create_agent_runs_table(conn)
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


# ── Sprint lifecycle + ticket order (issue #757) ──────────────────────────────
#
# The durable home for sprint lifecycle state and ticket execution order. These
# tables replace the ephemeral `{label}-plan.json` / `{label}-pid` files as the
# source of truth, while those files continue to be written as a deprecated
# cache (dual-write) until a later sprint removes them.  `state='failed'` is a
# valid value reserved for the future watchdog recovery sprint (no writer yet).

_SPRINT_STATES = ("planning", "running", "completed", "cancelled", "failed")


def _create_sprint_lifecycle_tables(conn: sqlite3.Connection) -> None:
    """Create the sprints + sprint_ticket_order tables (issue #757).

    Kept in its own helper so the lifecycle writers can ensure the tables exist
    without running the full init_db() migration first.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sprints (
            label        TEXT PRIMARY KEY,
            project      TEXT NOT NULL DEFAULT '',
            state        TEXT NOT NULL DEFAULT 'planning'
                         CHECK(state IN (
                             'planning', 'running', 'completed', 'cancelled', 'failed'
                         )),
            created_at   TEXT,
            started_at   TEXT,
            ended_at     TEXT,
            end_reason   TEXT,
            parent_label TEXT
        )
        """
    )
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


# ── Per-agent run durations (issue #764) ──────────────────────────────────────
#
# `sprint_tickets.actual_elapsed_seconds` stores a single blended coder→tester
# wall-clock span, losing per-agent resolution. `agent_runs` records one row per
# dispatched agent (coder, tester, documenter, reviewer, estimator) with its own
# start/finish timestamps and wall-clock duration. This fills the calibration gap
# for the coder/tester split and surfaces per-agent durations in the UI. The
# blended `sprint_tickets` tracking is unchanged (issue #764 AC8).


def _create_agent_runs_table(conn: sqlite3.Connection) -> None:
    """Create the agent_runs table (issue #764).

    One row per dispatched agent. `started_at`/`finished_at` are ISO-8601 UTC
    strings; `duration_seconds` is the wall-clock duration of the run. Kept in
    its own helper so the recorder can ensure the table exists without running
    the full init_db() migration. Mirrors the Alembic migration
    0009_add_agent_runs so the schema is identical on SQLite and Postgres.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number     INTEGER NOT NULL,
            sprint_label     TEXT NOT NULL,
            agent            TEXT NOT NULL,
            started_at       TEXT NOT NULL,
            finished_at      TEXT,
            duration_seconds INTEGER,
            outcome          TEXT,
            total_tokens     INTEGER
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


def record_agent_start(
    issue_number: int,
    sprint_label: str,
    agent: str,
    started_at: str | None = None,
) -> int | None:
    """Insert an agent_runs row at dispatch time and return its id (issue #764).

    `finished_at`/`duration_seconds`/`outcome` are left NULL until
    record_agent_finish() closes the run. Returns the new row id (used to close
    the exact run) or None on failure — callers treat this as best-effort.
    """
    started_at = started_at or _now_iso()
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        cur = conn.execute(
            "INSERT INTO agent_runs "
            "(issue_number, sprint_label, agent, started_at) "
            "VALUES (?, ?, ?, ?)",
            (int(issue_number), sprint_label, agent, started_at),
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
) -> None:
    """Close the open agent_runs row with finish time, duration and outcome (#764).

    When `run_id` is given the exact row is updated; otherwise the most recent
    still-open run (finished_at IS NULL) matching issue/sprint/agent is closed.
    `duration_seconds` is used as supplied (the dispatcher passes a precise
    monotonic measurement — issue #764 AC3); when omitted it is computed from the
    start/finish timestamps. Best-effort: never raises into the sprint loop.
    """
    finished_at = finished_at or _now_iso()
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        if run_id is not None:
            row = conn.execute(
                "SELECT id, started_at FROM agent_runs WHERE id = ?", (run_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, started_at FROM agent_runs "
                "WHERE issue_number = ? AND sprint_label = ? AND agent = ? "
                "AND finished_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (int(issue_number), sprint_label, agent),
            ).fetchone()
        if row is None:
            return
        if duration_seconds is None:
            duration_seconds = _duration_between(row["started_at"], finished_at)
        conn.execute(
            "UPDATE agent_runs SET finished_at = ?, duration_seconds = ?, "
            "outcome = ?, total_tokens = ? WHERE id = ?",
            (
                finished_at,
                None if duration_seconds is None else int(duration_seconds),
                outcome,
                None if total_tokens is None else int(total_tokens),
                row["id"],
            ),
        )
        conn.commit()


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


def agent_runs_for_sprint(sprint_label: str) -> list[dict]:
    """Return all agent_runs rows for a sprint, ordered by issue then start (#764)."""
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE sprint_label = ? "
            "ORDER BY issue_number, id",
            (sprint_label,),
        ).fetchall()
    return [dict(r) for r in rows]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def record_sprint_start(
    label: str,
    project: str = "",
    started_at: str | None = None,
    parent_label: str | None = None,
) -> None:
    """Write (or move to) a `running` sprints row (issue #757).

    Idempotent on `label`: a second start re-asserts state='running' and
    refreshes started_at without creating a duplicate row.
    """
    started_at = started_at or _now_iso()
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO sprints
                (label, project, state, created_at, started_at, parent_label)
            VALUES (?, ?, 'running', ?, ?, ?)
            ON CONFLICT(label) DO UPDATE SET
                project      = excluded.project,
                state        = 'running',
                started_at   = excluded.started_at,
                created_at   = COALESCE(sprints.created_at, excluded.created_at),
                parent_label = COALESCE(excluded.parent_label, sprints.parent_label)
            """,
            (label, project, started_at, started_at, parent_label),
        )
        conn.commit()


def record_sprint_finish(label: str, ended_at: str | None = None,
                         end_reason: str | None = None) -> None:
    """Move a sprints row to `completed` (issue #757)."""
    _set_sprint_terminal(label, "completed", end_reason, ended_at)


def record_sprint_cancel(label: str, end_reason: str = "cancelled",
                         ended_at: str | None = None) -> None:
    """Move a sprints row to `cancelled` with a reason (issue #757)."""
    _set_sprint_terminal(label, "cancelled", end_reason, ended_at)


def _set_sprint_terminal(label: str, state: str, end_reason: str | None,
                         ended_at: str | None) -> None:
    ended_at = ended_at or _now_iso()
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        # Upsert so a transition can land even if no start row was written
        # (e.g. a legacy sprint cancelled before its first DB write).
        conn.execute(
            """
            INSERT INTO sprints (label, state, created_at, ended_at, end_reason)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(label) DO UPDATE SET
                state      = excluded.state,
                ended_at   = excluded.ended_at,
                end_reason = COALESCE(excluded.end_reason, sprints.end_reason)
            """,
            (label, state, ended_at, ended_at, end_reason),
        )
        conn.commit()


def rename_sprint(old_label: str, new_label: str) -> None:
    """Rename a sprint and its ticket-order rows (issue #758).

    No-op if no row exists for `old_label`. Best-effort: GitHub labels remain the
    source of truth, so a missing DB row must not fail a rename.
    """
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute(
            "UPDATE sprints SET label = ? WHERE label = ?",
            (new_label, old_label),
        )
        conn.execute(
            "UPDATE sprint_ticket_order SET label = ? WHERE label = ?",
            (new_label, old_label),
        )
        conn.commit()


def get_sprint(label: str) -> dict | None:
    """Return the sprints row for `label` as a dict, or None (issue #757)."""
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        row = conn.execute(
            "SELECT * FROM sprints WHERE label = ?", (label,)
        ).fetchone()
    return dict(row) if row else None


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


def get_sync_etag(key: str) -> str | None:
    """Return the stored ETag for *key*, or None."""
    with get_conn() as conn:
        _create_sync_state_table(conn)
        row = conn.execute(
            "SELECT etag FROM sync_state WHERE key = ?", (key,)
        ).fetchone()
    return row["etag"] if row and row["etag"] else None


def set_sync_etag(key: str, etag: str) -> None:
    """Store the ETag for *key* (used as If-None-Match on the next poll)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
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
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
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
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
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
    now = datetime.utcnow().isoformat()
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
    cutoff = (datetime.utcnow() - timedelta(seconds=threshold_seconds)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE agents SET status='timed_out' WHERE status='working' AND last_seen < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount


def add_event(session_id: str, event_type: str, data: dict):
    now = datetime.utcnow().isoformat()
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
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
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
    cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
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
) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO token_usage
               (session_id, project, input_tokens, output_tokens, recorded_at, agent_role, model_name)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, project, input_tokens, output_tokens, now, agent_role, model_name),
        )
        conn.commit()


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
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
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
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE docs_freshness_warnings SET is_cleared=1, cleared_at=? WHERE repo=? AND doc_path=? AND is_cleared=0",
            (now, repo, doc_path),
        )
        conn.commit()
        return cur.rowcount


def clear_docs_warning_by_id(warning_id: int) -> bool:
    """Clear a single warning by id. Returns True if found."""
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
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
