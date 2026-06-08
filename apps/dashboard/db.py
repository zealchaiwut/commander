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
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
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
