"""SQLite-compatible table-creation helpers for test fixtures.

Place DDL here rather than inline in individual test files (issue #886).
This is the single canonical source for test schema; keep it in sync with
models.py. Column names MUST match the ORM model — type aliases (TEXT for
TIMESTAMP/ENUM/JSON) are acceptable because SQLite has no native equivalents.
"""
from sqlalchemy import text


def make_sprint_db(engine) -> None:
    """Create sprint manager tables in the given SQLite engine for tests.

    Creates sprints, sprint_tickets, and settings with SQLite-compatible DDL
    that mirrors the column structure of the canonical SQLAlchemy models.
    All test fixtures that need these tables should call this function instead
    of writing their own CREATE TABLE statements.
    """
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sprints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT UNIQUE NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                completed_at TEXT,
                cancelled_at TEXT,
                project TEXT NOT NULL
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sprint_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sprint_id INTEGER NOT NULL REFERENCES sprints(id),
                issue_number INTEGER NOT NULL,
                position INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TEXT,
                completed_at TEXT,
                agent_active TEXT,
                actual_elapsed_seconds INTEGER,
                total_tokens INTEGER,
                estimated_size TEXT,
                UNIQUE(sprint_id, issue_number)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                project TEXT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(scope, project, key)
            )
        """))
        conn.commit()
