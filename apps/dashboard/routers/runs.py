"""runs.py — Run Browser endpoints (issue #783).

Three API endpoints for the forensic Run Browser:
  GET /runs                          — list past sprints + tickets from agent_runs
  GET /runs/{sprint}/{issue}/{agent}/log          — paginated log content
  GET /runs/{sprint}/{issue}/{agent}/log/tail     — last N KB of log

All data comes from the local SQLite DB and log files on disk.
Zero GitHub API calls on this surface (AC10 of issue #783).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

# ── Path setup ────────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = _DASHBOARD_ROOT / "static"
for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .runs_service import (  # noqa: E402
    DEFAULT_LOG_LIMIT,
    DEFAULT_TAIL_LINES,
    get_issue_log_tail,
    get_log_path_from_db,
    get_run_reasoning,
    list_runs,
    read_log_page,
    read_log_tail,
)

router = APIRouter(tags=["runs"])


# ── Sprint/ticket list ────────────────────────────────────────────────────────

@router.get("/runs")
def get_runs() -> Any:
    """List all past sprints with their tickets and per-agent run metadata.

    Data source: agent_runs table (joined with sprints table).
    Failed sprints/tickets sort before passing ones.
    Zero GitHub API calls.
    """
    return list_runs()


# ── Log endpoints ─────────────────────────────────────────────────────────────

def _resolve_agent_log(sprint: str, issue: int, agent: str) -> Path:
    """Look up and validate the log file for sprint/issue/agent.

    Raises:
        400  — path traversal attempt detected
        404  — no agent_runs row found, or log file does not exist
    """
    # Reject traversal in URL segments before touching the DB
    for segment in (sprint, agent):
        if ".." in segment or "/" in segment or "\\" in segment:
            raise HTTPException(status_code=400, detail="Invalid path segment")

    raw_path = get_log_path_from_db(sprint, issue, agent)
    if raw_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"No run found for sprint={sprint} issue={issue} agent={agent}",
        )

    log_file = Path(raw_path).resolve()
    # Constrain to .commander/logs/ allowlist (AC2)
    # The allowlist check: log_path must be inside a .commander/logs/ directory
    if not _is_within_commander_logs(log_file):
        raise HTTPException(
            status_code=400,
            detail="Log path is outside the .commander/logs/ allowlist",
        )

    if not log_file.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_file}")

    return log_file


def _is_within_commander_logs(path: Path) -> bool:
    """Return True if *path* is inside a .commander/logs/ directory."""
    parent = path.parent.resolve()
    return parent.name == "logs" and parent.parent.name == ".commander"


@router.get("/runs/{sprint}/{issue}/{agent}/log")
def get_log_page(
    sprint: str,
    issue: int,
    agent: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_LOG_LIMIT, ge=1, le=5000),
) -> Any:
    """Return a paginated page of log lines.

    Parameters
    ----------
    sprint    sprint label, e.g. sprint-59.3
    issue     GitHub issue number
    agent     coder | tester | documenter | reviewer | estimator
    offset    0-based line offset (default 0)
    limit     max lines to return (default 200, max 5000)

    Returns
    -------
    {lines: [...], offset: N, limit: N, total_lines: N}
    """
    log_file = _resolve_agent_log(sprint, issue, agent)
    return read_log_page(log_file, offset=offset, limit=limit)


@router.get("/logs/tail")
def get_issue_log_tail_endpoint(
    sprint: str = Query(..., description="sprint label, e.g. sprint-61"),
    issue: int = Query(..., description="GitHub issue number"),
    project: str = Query(..., description="owner/repo or project slug"),
    tail_lines: int = Query(default=DEFAULT_TAIL_LINES, ge=1, le=2000),
) -> Any:
    """Per-issue log tail for the node inspector (issue #804).

    Streams the last *tail_lines* of ``sprint-issue-<issue>.log`` for the given
    sprint/issue. Keyed by sprint + issue only (no agent segment), so the
    inspector's per-issue log tab can poll it without an agent_runs row. Reuses
    ``read_log(kind="issue")`` and is structured to be absorbed by the M5
    run-browser surface this module owns.

    Returns
    -------
    {found: bool, path: str, tail: str, mtime: str} when the log exists, else
    {found: false, candidate_paths: [...], tail: ""}.
    """
    return get_issue_log_tail(sprint, issue, project, tail_lines)


@router.get("/api/runs/{agent_run_id}/reasoning")
def get_run_reasoning_endpoint(agent_run_id: int) -> Any:
    """Return the persisted reasoning narrative for a completed agent run (issue #2022).

    Reads ``final_message``, ``transcript_path``, and a fresh ``log_tail`` from
    the ``agent_runs`` row keyed by ``agent_run_id``.

    Returns
    -------
    {
      "final_message": str | null,
      "transcript_path": str | null,
      "log_tail": str | null
    }

    Status codes:
      200  — run found; fields may be null when the run closed before #2021 shipped
      404  — no agent_runs row with this id
    """
    result = get_run_reasoning(agent_run_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Run {agent_run_id} not found",
        )
    return result


@router.get("/runs/{sprint}/{issue}/{agent}/log/tail")
def get_log_tail(
    sprint: str,
    issue: int,
    agent: str,
    kb: int = Query(default=32, ge=1, le=1024),
) -> Any:
    """Return the last *kb* kilobytes of the log file.

    Parameters
    ----------
    sprint    sprint label
    issue     GitHub issue number
    agent     coder | tester | etc.
    kb        kilobytes from end to return (default 32, max 1024)

    Returns
    -------
    {content: "...", file_size: N, kb_returned: N}
    """
    log_file = _resolve_agent_log(sprint, issue, agent)
    return read_log_tail(log_file, kb=kb)
