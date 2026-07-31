"""failures_service.py — service logic for the unified failures endpoint (issue #2019).

Unions three SQLite sources into a single normalized failure-row list:
  1. events WHERE type='ticket_failed'   (parse detail JSON)
  2. agent_runs WHERE outcome IN ('failed','fail','timed_out')
  3. agents WHERE status='timed_out'

Zero GitHub API calls. All reads through the shared `db` module (get_conn).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402

# ── Vocabulary normalisation ──────────────────────────────────────────────────

_OUTCOME_NORM: dict[str, str] = {
    # Outcome vocabulary (agent_runs / agents sources)
    "fail": "failed",
    "failed": "failed",
    "success": "succeeded",
    "succeeded": "succeeded",
    "pass": "passed",
    "passed": "passed",
    "timed_out": "timed_out",
    "timeout": "timed_out",
    # FailureCategory vocabulary (events source) — normalise the
    # gate_fail / gate_failed variant pair so both spellings match.
    # All other FailureCategory values are already lowercase canonical
    # and pass through .lower() unchanged.
    "gate_fail": "gate_fail",
    "gate_failed": "gate_fail",
}


def normalize_outcome(value: Optional[str]) -> Optional[str]:
    """Collapse outcome/category vocab to canonical form.

    Exported so tests can verify the helper independently (AC5).
    """
    if not value:
        return value
    return _OUTCOME_NORM.get(value.strip().lower(), value.strip().lower())


# ── Lookback-window parser ────────────────────────────────────────────────────

_LOOKBACK_RE = re.compile(r"^(\d+)(h|d|w)$", re.IGNORECASE)


def _parse_since(since: Optional[str]) -> Optional[str]:
    """Convert a ``since`` param to an ISO-8601 UTC string, or None.

    Accepts ISO timestamps directly *or* lookback windows: "24h", "7d", "2w".
    Returns None when ``since`` is absent so the caller applies no filter.
    """
    if not since:
        return None
    m = _LOOKBACK_RE.match(since.strip())
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        delta = {"h": timedelta(hours=amount), "d": timedelta(days=amount), "w": timedelta(weeks=amount)}[unit]
        cutoff = datetime.now(timezone.utc) - delta
        return cutoff.isoformat()
    # Assume caller passed a raw ISO timestamp; pass it through unchanged.
    return since


# ── log_url builder ───────────────────────────────────────────────────────────

def _build_log_url(
    sprint_label: Optional[str],
    issue_number: Optional[int],
    agent: Optional[str],
) -> Optional[str]:
    """Build a log URL from the identifying fields of a failure row.

    Uses the per-agent run-browser route when all three identifiers are present,
    otherwise falls back to the per-issue log route, or None.
    """
    if sprint_label and issue_number is not None and agent:
        return f"/runs/{sprint_label}/{issue_number}/{agent}/log"
    if sprint_label and issue_number is not None:
        return f"/api/sprints/{sprint_label}/issue/{issue_number}/log"
    return None


# ── Source: events.type='ticket_failed' ──────────────────────────────────────

def _rows_from_events(
    conn,
    project_filter: Optional[str],
    since_ts: Optional[str],
) -> list[dict]:
    """Query the events table for ticket_failed rows.

    Malformed or missing detail JSON fields are silently skipped / returned as
    None — this function never raises.
    """
    params: list[Any] = ["ticket_failed"]
    sql = "SELECT project, timestamp, detail FROM events WHERE type = ?"
    if project_filter:
        sql += " AND project = ?"
        params.append(project_filter)
    if since_ts:
        sql += " AND timestamp >= ?"
        params.append(since_ts)
    sql += " ORDER BY timestamp DESC"

    rows: list[dict] = []
    try:
        cursor = conn.execute(sql, params)
        cursor.row_factory = None  # already returns tuples here; dict below
        for project, ts, detail_raw in cursor.fetchall():
            try:
                detail: dict = json.loads(detail_raw) if detail_raw else {}
            except (json.JSONDecodeError, TypeError):
                detail = {}

            issue_num_raw = detail.get("issue_num")
            try:
                issue_number: Optional[int] = int(issue_num_raw) if issue_num_raw is not None else None
            except (TypeError, ValueError):
                issue_number = None

            sprint_label = detail.get("sprint_label") or None
            agent = detail.get("agent") or None
            raw_category = detail.get("category") or None
            category = normalize_outcome(raw_category) or raw_category

            rows.append({
                "source": "events",
                "issue_number": issue_number,
                "sprint_label": sprint_label,
                "project": project or None,
                "agent": agent,
                "category": category,
                "reason": detail.get("reason") or None,
                "failure_class": detail.get("failure_class") or None,
                "message": detail.get("message") or None,
                "attempt_kind": None,
                "branch": detail.get("branch") or None,
                "log_url": _build_log_url(sprint_label, issue_number, agent),
                "ts": ts,
            })
    except Exception:
        pass
    return rows


# ── Source: agent_runs.outcome in {failed, fail, timed_out} ─────────────────

_FAILED_OUTCOMES = ("failed", "fail", "timed_out", "timeout")


def _rows_from_agent_runs(
    conn,
    project_filter: Optional[str],
    since_ts: Optional[str],
) -> list[dict]:
    """Query agent_runs for rows whose outcome signals a failure."""
    placeholders = ",".join("?" * len(_FAILED_OUTCOMES))
    params: list[Any] = list(_FAILED_OUTCOMES)
    sql = (
        "SELECT issue_number, sprint_label, project, agent, outcome, "
        "attempt_kind, log_path, started_at "
        f"FROM agent_runs WHERE outcome IN ({placeholders})"
    )
    if project_filter:
        sql += " AND project = ?"
        params.append(project_filter)
    if since_ts:
        sql += " AND started_at >= ?"
        params.append(since_ts)
    sql += " ORDER BY started_at DESC"

    rows: list[dict] = []
    try:
        # Ensure table exists for fresh DBs.
        _db._create_agent_runs_table(conn)
        cursor = conn.execute(sql, params)
        for row in cursor.fetchall():
            (issue_number, sprint_label, project, agent,
             outcome, attempt_kind, log_path, started_at) = row

            category = normalize_outcome(outcome)
            rows.append({
                "source": "agent_runs",
                "issue_number": issue_number,
                "sprint_label": sprint_label or None,
                "project": project or None,
                "agent": agent or None,
                "category": category,
                "reason": None,
                "failure_class": None,
                "message": None,
                "attempt_kind": attempt_kind or None,
                "branch": None,
                "log_url": _build_log_url(sprint_label, issue_number, agent),
                "ts": started_at,
            })
    except Exception:
        pass
    return rows


# ── Scratchpad / test-harness dir filter (issue #2042) ───────────────────────

# Directories that are exclusively used by test harnesses, pytest scratchpads,
# or the Claude Code CLI's own tmp workspaces.  Agents whose working_dir starts
# with any of these prefixes are excluded from the Failures inbox because they
# are noise produced by test suites, not real sprint failures.
#
# We prefer the structural working_dir signal over agent-name patterns because
# agent names are free-form text (set by the spawning process) and can collide
# with real agent names; working_dir paths are OS-enforced and stable.
_SCRATCHPAD_DIR_PREFIXES: tuple[str, ...] = (
    "/tmp/",
    "/private/tmp/",
    "/var/folders/",
    "/var/tmp/",
)


def _is_scratchpad_working_dir(working_dir: Optional[str]) -> bool:
    """Return True when working_dir is a system temp / pytest-scratchpad path.

    Exported so the test suite can verify the helper directly (issue #2042 AC3).
    """
    if not working_dir:
        return False
    return any(working_dir.startswith(prefix) for prefix in _SCRATCHPAD_DIR_PREFIXES)


# ── Source: agents.status='timed_out' ────────────────────────────────────────

def _rows_from_agents(
    conn,
    since_ts: Optional[str],
) -> list[dict]:
    """Query the agents table for timed-out sessions.

    Project is inferred from working_dir on a best-effort basis;
    issue_number and sprint_label cannot be determined and are returned as None.
    """
    params: list[Any] = ["timed_out"]
    sql = "SELECT session_id, name, working_dir, last_seen FROM agents WHERE status = ?"
    if since_ts:
        sql += " AND last_seen >= ?"
        params.append(since_ts)
    sql += " ORDER BY last_seen DESC"

    rows: list[dict] = []
    try:
        cursor = conn.execute(sql, params)
        for session_id, name, working_dir, last_seen in cursor.fetchall():
            # Skip test-harness / scratchpad agents (issue #2042).
            # These sessions are spawned by pytest inside /tmp or /private/tmp
            # and have project=None — they pollute every project's Failures inbox.
            if _is_scratchpad_working_dir(working_dir):
                continue

            # Best-effort: derive project slug from working directory path.
            project: Optional[str] = None
            try:
                parts = Path(working_dir or "").parts
                # Look for a dev/<project>/<clone> pattern
                dev_idx = next((i for i, p in enumerate(parts) if p == "dev"), None)
                if dev_idx is not None and dev_idx + 1 < len(parts):
                    project = parts[dev_idx + 1]
            except Exception:
                pass

            rows.append({
                "source": "agents",
                "issue_number": None,
                "sprint_label": None,
                "project": project,
                "agent": name or session_id,
                "category": "timed_out",
                "reason": "agent timed out (heartbeat expired)",
                "failure_class": None,
                "message": None,
                "attempt_kind": None,
                "branch": None,
                "log_url": None,
                "ts": last_seen,
            })
    except Exception:
        pass
    return rows


# ── Public entry point ────────────────────────────────────────────────────────

def get_failures(
    project: Optional[str] = None,
    since: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict]:
    """Return a unified list of normalized failure rows from all three sources.

    Query params:
      project  — filter by project (owner/repo or slug); no filter when absent.
      since    — ISO timestamp or lookback window ("24h", "7d", "2w"); no filter when absent.
      category — filter by normalized outcome/category; no filter when absent.

    Never raises; gracefully skips malformed rows.
    """
    since_ts = _parse_since(since)
    # Normalize the category filter so "fail" and "failed" both work.
    norm_category = normalize_outcome(category) if category else None

    results: list[dict] = []
    try:
        with _db.get_conn() as conn:
            results.extend(_rows_from_events(conn, project, since_ts))
            results.extend(_rows_from_agent_runs(conn, project, since_ts))
            results.extend(_rows_from_agents(conn, since_ts))
    except Exception:
        pass

    # Apply project filter post-union for agents rows.
    # Events and agent_runs are already filtered at SQL level (they have a project column).
    # Agents has no project column — project is inferred from working_dir.
    #
    # Issue #2042: when the caller requests a specific project, we EXCLUDE agents
    # whose project cannot be determined (None).  The old behaviour was to include
    # unknown-project rows ("can't determine → possibly relevant"), but that caused
    # every project's Failures inbox to show the same unknowns — cross-project
    # bleed that is worse than occasionally hiding a genuinely ambiguous row.
    # Scratchpad agents are already filtered out in _rows_from_agents above, so
    # the only remaining None-project rows are agents with an unresolvable path
    # (no /dev/ segment).  Excluding them when a project filter is active is
    # the safer default.  Slug matching: "commander" matches "zealchaiwut/commander".
    if project:
        _slug = project.split("/")[-1]

        def _project_matches(row: dict) -> bool:
            if row.get("source") != "agents":
                return True  # already filtered at SQL level
            row_project = row.get("project")
            if row_project is None:
                # Unknown project — exclude when caller requests a specific project.
                # Showing ambiguous rows in every project's inbox causes cross-project
                # bleed (issue #2042 AC2).
                return False
            return row_project == project or row_project == _slug

        results = [r for r in results if _project_matches(r)]

    # Apply category filter post-union (works across all three sources).
    if norm_category:
        results = [r for r in results if normalize_outcome(r.get("category")) == norm_category]

    # Sort newest-first across all sources.
    def _ts_key(r: dict) -> str:
        return r.get("ts") or ""

    results.sort(key=_ts_key, reverse=True)
    return results
