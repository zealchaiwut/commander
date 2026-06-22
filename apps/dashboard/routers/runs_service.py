"""runs_service.py — service logic for the Run Browser (issue #783).

Provides DB queries and log file I/O for the runs router.  No GitHub API calls.
All log file access is constrained to the .commander/logs/ directory allowlist;
path traversal attempts raise ValueError.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402

_PROJECTS_BASE = Path.home() / "dev"

DEFAULT_LOG_LIMIT = 200  # lines returned per page by default
DEFAULT_TAIL_LINES = 200  # lines returned by the inspector per-issue log tail


def _server():
    """Deferred import of the monolith — safe at request time, avoids the
    import-time circular dependency (server.py mounts this router package)."""
    import server  # noqa: PLC0415 — intentional late import
    return server


def get_issue_log_tail(
    sprint: str,
    issue: int,
    project: str,
    tail_lines: int = DEFAULT_TAIL_LINES,
) -> dict:
    """Tail of ``sprint-issue-<issue>.log`` for the node inspector log tab (issue #804).

    Reuses ``log_source.read_log(kind="issue")`` so the per-issue log surface is
    keyed only by sprint + issue (no agent_runs row required) and reads nothing
    but the on-disk ``sprint-issue-<N>.log`` file — zero GitHub API calls. Lives
    on the Run Browser router so the M5 run-browser ticket absorbs it directly.

    Raises 400 on an invalid sprint label (path-traversal / format guard).
    """
    from fastapi import HTTPException  # noqa: PLC0415 — local keeps import graph flat

    srv = _server()
    if not srv._SPRINT_LABEL_RE.match(sprint):
        raise HTTPException(status_code=400, detail=f"Invalid sprint label: {sprint!r}")

    from log_source import read_log  # noqa: PLC0415 — local keeps startup fast

    project_root = srv._project_root_path(project)
    return read_log(
        "issue",
        project_root,
        label=sprint,
        issue_num=issue,
        tail_lines=tail_lines,
    )

# ── Outcome ordering ──────────────────────────────────────────────────────────
# Lower ordinal = higher priority in sort (failed before passed before unknown).
_OUTCOME_ORDER = {
    "failed": 0,
    "failure": 0,
    "needs-rework": 1,
    "passed": 2,
    "success": 2,
    None: 3,
}


def _outcome_rank(outcome: Optional[str]) -> int:
    if outcome is None:
        return 3
    return _OUTCOME_ORDER.get(outcome.lower(), 3)


# ── Path traversal guard ──────────────────────────────────────────────────────

def resolve_log_path(raw_path: str, allowed_dir: str) -> Path:
    """Resolve *raw_path* and verify it is within *allowed_dir*.

    Raises ValueError if the resolved path escapes the allowed directory.
    This is the single allowlist check — all log file access must go through
    this function (AC2 of issue #783).
    """
    resolved = Path(raw_path).resolve()
    allowed = Path(allowed_dir).resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError:
        raise ValueError(
            f"Path traversal attempt: '{raw_path}' is outside allowed directory '{allowed}'"
        )
    return resolved


def _allowed_dir_for_path(log_path: Path) -> Optional[Path]:
    """Return the .commander/logs/ directory that owns *log_path*, or None."""
    # The .commander/logs dir is always two levels up: logs/ → .commander/ → project root.
    logs_dir = log_path.parent.resolve()
    if logs_dir.name == "logs" and logs_dir.parent.name == ".commander":
        return logs_dir
    return None


# ── DB queries ────────────────────────────────────────────────────────────────

def list_runs() -> list[dict]:
    """Return all sprints with their tickets and agent runs, sorted failed-first.

    Data comes from agent_runs (joined with sprints where available).
    Zero GitHub API calls.
    """
    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        _db._create_sprint_lifecycle_tables(conn)

        rows = conn.execute(
            """
            SELECT
                ar.id,
                ar.sprint_label,
                ar.issue_number,
                ar.agent,
                ar.started_at,
                ar.finished_at,
                ar.duration_seconds,
                ar.outcome,
                ar.log_path,
                s.state      AS sprint_state,
                s.started_at AS sprint_started_at,
                s.ended_at   AS sprint_ended_at
            FROM agent_runs ar
            LEFT JOIN sprints s ON s.label = ar.sprint_label
            ORDER BY ar.sprint_label, ar.issue_number, ar.id
            """
        ).fetchall()

    # ── group by sprint then issue ────────────────────────────────────────────
    sprints: dict[str, dict] = {}
    for r in rows:
        sl = r["sprint_label"]
        if sl not in sprints:
            sprints[sl] = {
                "sprint": sl,
                "state": r["sprint_state"],
                "started_at": r["sprint_started_at"],
                "ended_at": r["sprint_ended_at"],
                "tickets": {},
            }
        inum = r["issue_number"]
        if inum not in sprints[sl]["tickets"]:
            sprints[sl]["tickets"][inum] = {
                "issue_number": inum,
                "agents": [],
                "outcome": None,
            }
        ticket = sprints[sl]["tickets"][inum]
        ticket["agents"].append({
            "agent": r["agent"],
            "outcome": r["outcome"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "duration_seconds": r["duration_seconds"],
            "log_path": r["log_path"],
        })
        # Ticket outcome = worst agent outcome
        if _outcome_rank(r["outcome"]) < _outcome_rank(ticket["outcome"]):
            ticket["outcome"] = r["outcome"]

    # ── build result list ─────────────────────────────────────────────────────
    result = []
    for sl, sp in sprints.items():
        tickets = list(sp["tickets"].values())
        # Sort tickets: failed first
        tickets.sort(key=lambda t: _outcome_rank(t["outcome"]))

        total = len(tickets)
        failed = sum(1 for t in tickets if _outcome_rank(t["outcome"]) == 0)
        result.append({
            "sprint": sl,
            "state": sp["state"],
            "started_at": sp["started_at"],
            "ended_at": sp["ended_at"],
            "total_count": total,
            "failed_count": failed,
            "tickets": tickets,
        })

    # Sort sprints: failed-first (any failed ticket in sprint → failed sprint)
    result.sort(key=lambda s: (0 if s["failed_count"] > 0 else 1, s["sprint"]),
                reverse=False)
    # Reverse sprint order within same failure bucket so newest sprints appear first.
    # Stable sort: failed sprints alphabetically reversed (sprint-59 > sprint-1),
    # passing sprints also reversed.
    result.sort(key=lambda s: (-s["failed_count"], [-ord(c) for c in s["sprint"]]))
    # Simpler: failed > passing, then sprint label descending within each bucket
    result = sorted(result, key=lambda s: (0 if s["failed_count"] > 0 else 1,
                                            s["sprint"]),
                    reverse=False)
    # Re-sort for stable order: failed_count desc, then sprint label desc
    result = sorted(result,
                    key=lambda s: (-(s["failed_count"] > 0), s["sprint"]),
                    reverse=False)
    return result


def get_log_path_from_db(sprint: str, issue: int, agent: str) -> Optional[str]:
    """Return the log_path stored in agent_runs for this sprint/issue/agent combo."""
    with _db.get_conn() as conn:
        _db._create_agent_runs_table(conn)
        row = conn.execute(
            "SELECT log_path FROM agent_runs "
            "WHERE sprint_label = ? AND issue_number = ? AND agent = ? "
            "ORDER BY id DESC LIMIT 1",
            (sprint, issue, agent),
        ).fetchone()
    if row is None:
        return None
    return row["log_path"]


def count_log_lines(log_file: Path) -> int:
    """Count lines in *log_file* efficiently without loading the whole file."""
    count = 0
    with log_file.open("rb") as f:
        buf = bytearray(65536)
        while n := f.readinto(buf):
            count += buf[:n].count(b"\n")
    return count


def read_log_page(
    log_file: Path,
    offset: int = 0,
    limit: int = DEFAULT_LOG_LIMIT,
) -> dict:
    """Return a page of lines from *log_file* starting at line *offset*.

    Uses itertools-style line-counting so the full file is never loaded into
    memory (AC7 of issue #783).  *offset* is 0-based.
    """
    lines: list[str] = []
    total_lines = 0

    with log_file.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f):
            total_lines += 1
            if lineno < offset:
                continue
            if len(lines) < limit:
                lines.append(line.rstrip("\n\r"))

    return {
        "lines": lines,
        "offset": offset,
        "limit": limit,
        "total_lines": total_lines,
    }


def read_log_tail(log_file: Path, kb: int = 32) -> dict:
    """Return the last *kb* kilobytes of *log_file*.

    Seeks from end of file (O(1) for large files) — does not load the full
    file into memory (AC7 of issue #783).
    """
    max_bytes = kb * 1024
    file_size = log_file.stat().st_size

    with log_file.open("rb") as f:
        if file_size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
            raw = f.read(max_bytes)
        else:
            raw = f.read()

    # Decode and strip partial first line (seek may land mid-line)
    text = raw.decode("utf-8", errors="replace")
    if file_size > max_bytes:
        # Drop the (likely partial) first line
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1:]

    return {
        "content": text,
        "file_size": file_size,
        "kb_returned": kb,
    }
