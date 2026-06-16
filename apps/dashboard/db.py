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

def _create_brief_summaries_table(conn: sqlite3.Connection) -> None:
    """Create the brief_summaries cache table (issue #840)."""
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
# draft / planned / running / ready_to_merge / needs_rework / completed.
# `planning`, `cancelled`, and `failed` are legacy values kept readable for
# pre-redesign rows (forward-only migration, no rewrite) — they are never
# written anew and render through `canonical_lifecycle()`.

_SPRINT_STATES = (
    # unified lifecycle
    "draft", "planned", "running", "ready_to_merge", "needs_rework", "completed",
    # legacy, read-only
    "planning", "cancelled", "failed",
)

# Canonical lifecycle states exposed to the UI. `partial_finished` is derived
# at read time from children's states and never stored; `deleted` lives in the
# sprint_history snapshot table, not in `sprints`.
LIFECYCLE_STATES = (
    "draft", "planned", "running", "ready_to_merge", "needs_rework",
    "partial_finished", "completed", "deleted",
)

# Display mapping for legacy state vocabularies (plan.json, DB rows, summary
# files). Forward-only migration: old rows keep their stored value and render
# through this map. Legacy `completed` stays `completed` (the pre-redesign
# flow auto-merged at end of run, so those sprints are past ready_to_merge).
_LEGACY_LIFECYCLE_MAP = {
    "planning": "draft",
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
    "draft":          frozenset({"planned", "running", "ready_to_merge", "needs_rework", "deleted"}),
    "planned":        frozenset({"running", "ready_to_merge", "needs_rework", "deleted"}),
    "running":        frozenset({"running", "ready_to_merge", "needs_rework", "completed", "deleted"}),
    "ready_to_merge": frozenset({"completed", "needs_rework", "deleted"}),
    "needs_rework":   frozenset({"running", "deleted"}),
    "completed":      frozenset({"deleted"}),
    "deleted":        frozenset(),
}

# Transitions FROM these states TO the guarded targets require actor="manager".
_GUARD_FROM: frozenset[str] = frozenset({"running"})
_GUARD_TO: frozenset[str] = frozenset({"ready_to_merge", "needs_rework", "completed"})

# Reconciler-only promotions not in the general edge table (issue #1137).
_RECONCILE_ONLY_EDGES: frozenset[tuple[str, str]] = frozenset({
    ("needs_rework", "ready_to_merge"),
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
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        row = conn.execute(
            "SELECT state FROM sprints WHERE label = ?", (label,)
        ).fetchone()
    current = canonical_lifecycle(row["state"] if row else "draft")

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
                ON CONFLICT(label) DO UPDATE SET
                    project      = excluded.project,
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
            conn.execute("DELETE FROM sprints WHERE label = ?", (label,))
            conn.commit()
    else:
        _set_sprint_terminal(label, to_state, end_reason, ended_at)

    _logger.info(
        "sprint %r: %r → %r by actor=%r", label, current, to_state, actor
    )
    return TransitionResult(accepted=True)


_SPRINTS_TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS sprints (
            label        TEXT PRIMARY KEY,
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
            parent_label TEXT
        )
"""


def _migrate_sprints_state_check(conn: sqlite3.Connection) -> None:
    """Rebuild the sprints table when its CHECK predates the unified lifecycle.

    SQLite cannot alter a CHECK constraint in place. Existing rows are copied
    verbatim (forward-only migration — legacy state values stay readable).
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sprints'"
    ).fetchone()
    if row is None or "needs_rework" in (row[0] or ""):
        return
    conn.execute("ALTER TABLE sprints RENAME TO sprints_legacy_check")
    conn.execute(_SPRINTS_TABLE_DDL)
    conn.execute(
        """
        INSERT INTO sprints
        SELECT label, project, state, created_at, started_at, ended_at,
               end_reason, parent_label
        FROM sprints_legacy_check
        """
    )
    conn.execute("DROP TABLE sprints_legacy_check")


def _create_sprint_lifecycle_tables(conn: sqlite3.Connection) -> None:
    """Create the sprints + sprint_ticket_order tables (issue #757).

    Kept in its own helper so the lifecycle writers can ensure the tables exist
    without running the full init_db() migration first.
    """
    _migrate_sprints_state_check(conn)
    conn.execute(_SPRINTS_TABLE_DDL)
    _migrate_sprints_run_artifacts(conn)
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
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
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
            project_sql = ""
            if (project or "").strip():
                project_sql = ", project = ?"
                updates.append(project.strip())
            conn.execute(
                f"""
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
                    summary_failure_count = ?{project_sql}
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




def update_sprint_pr_number(label: str, pr_number: int | None) -> None:
    """Persist the sprint merge PR number after Merge Sprint (History links)."""
    if pr_number is None:
        return
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute(
            "UPDATE sprints SET pr_number = ? WHERE label = ?",
            (int(pr_number), label),
        )
        conn.commit()

def update_sprint_reconciliation(label: str, reconciliation: dict) -> None:
    """Refresh the ingested reconciliation block after a background reconcile."""
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        conn.execute(
            "UPDATE sprints SET reconciliation_json = ? WHERE label = ?",
            (json.dumps(reconciliation), label),
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


def _create_agent_runs_table(conn: sqlite3.Connection) -> None:
    """Create the agent_runs table (issue #764).

    One row per dispatched agent. `started_at`/`finished_at` are ISO-8601 UTC
    strings; `duration_seconds` is the wall-clock duration of the run. Kept in
    its own helper so the recorder can ensure the table exists without running
    the full init_db() migration. Mirrors the Alembic migration
    0009_add_agent_runs so the schema is identical on SQLite and Postgres.
    `risk_tier` and `model_used` added by issue #790 (alembic 0010).
    `attempt_kind` added by issue #787 (initial/fix_round/hang_continue).
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
            total_tokens     INTEGER,
            risk_tier        TEXT,
            model_used       TEXT,
            routing_reason   TEXT,
            worktree_sha     TEXT,
            base_sha         TEXT,
            attempt_kind     TEXT,
            log_path         TEXT
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
    # Best-effort ALTER TABLE for existing DBs that predate issue #790/#789/#788/#787/#783.
    for col, typedef in (
        ("risk_tier", "TEXT"),
        ("model_used", "TEXT"),
        ("routing_reason", "TEXT"),
        ("worktree_sha", "TEXT"),
        ("base_sha", "TEXT"),
        ("attempt_kind", "TEXT"),
        ("log_path", "TEXT"),
        ("backend", "TEXT"),
    ):
        try:
            conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {col} {typedef}")
        except Exception:
            pass


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
    Returns the new row id (used to close the exact run) or None on failure.
    """
    started_at = started_at or _now_iso()
    with get_conn() as conn:
        _create_agent_runs_table(conn)
        cur = conn.execute(
            "INSERT INTO agent_runs "
            "(issue_number, sprint_label, agent, started_at, risk_tier, model_used, routing_reason, "
            "worktree_sha, base_sha, attempt_kind, log_path, backend) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(issue_number), sprint_label, agent, started_at, risk_tier, model_used, routing_reason,
             worktree_sha, base_sha, attempt_kind, log_path, backend),
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
    refreshes started_at without creating a duplicate row. Routed through
    transition_sprint_state (issue #1087) so the legal-edge guard applies.
    """
    transition_sprint_state(
        label, "running", actor="manager",
        started_at=started_at, project=project, parent_label=parent_label,
    )


def record_sprint_finish(label: str, ended_at: str | None = None,
                         end_reason: str | None = None,
                         project: str = "") -> None:
    """Move a sprints row to `completed` (issue #757)."""
    transition_sprint_state(
        label, "completed", actor="manager",
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
        end_reason=end_reason, ended_at=ended_at,
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
            ON CONFLICT(label) DO UPDATE SET
                state      = excluded.state,
                ended_at   = excluded.ended_at,
                end_reason = COALESCE(excluded.end_reason, sprints.end_reason),
                project    = CASE
                    WHEN excluded.project IS NOT NULL AND excluded.project != ''
                    THEN excluded.project ELSE sprints.project END
            """,
            (label, project or "", state, ended_at, ended_at, end_reason),
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


def list_sprints_lifecycle() -> list[dict]:
    """Return all rows of the `sprints` lifecycle table as dicts (issue #805)."""
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
        rows = conn.execute("SELECT * FROM sprints").fetchall()
    return [dict(r) for r in rows]


def get_sprint_children(parent_label: str) -> list[dict]:
    """Return sprints rows whose parent_label matches the given label (issue #1093)."""
    with get_conn() as conn:
        _create_sprint_lifecycle_tables(conn)
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
