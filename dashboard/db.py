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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                data        TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id    TEXT NOT NULL,
                project       TEXT NOT NULL,
                input_tokens  INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                recorded_at   TEXT NOT NULL
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
            "INSERT INTO events (session_id, event_type, data, created_at) VALUES (?, ?, ?, ?)",
            (session_id, event_type, json.dumps(data), now),
        )
        conn.commit()


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
            FROM events e
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

def record_token_usage(session_id: str, project: str,
                       input_tokens: int, output_tokens: int) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO token_usage
               (session_id, project, input_tokens, output_tokens, recorded_at)
               VALUES (?, ?, ?, ?, ?)""",
            (session_id, project, input_tokens, output_tokens, now),
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
    """Delete events whose session_id or data looks like a test/debug entry.

    Matches patterns: session_id starting with 'test_' or 'Test-', or
    event_type == 'test', or data containing 'Test alert' or 'Test-'.
    Returns the count of rows deleted.
    """
    with get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM events WHERE
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
