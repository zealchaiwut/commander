"""runs.py — Run Browser endpoints (issue #783).

Three API endpoints for the forensic Run Browser:
  GET /runs                          — list past sprints + tickets from agent_runs
  GET /runs/{sprint}/{issue}/{agent}/log          — paginated log content
  GET /runs/{sprint}/{issue}/{agent}/log/tail     — last N KB of log

Plus the HTML page:
  GET /run-browser                   — serves run_browser.html (ntfy deep-link target)

All data comes from the local SQLite DB and log files on disk.
Zero GitHub API calls on this surface (AC10 of issue #783).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

# ── Path setup ────────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = _DASHBOARD_ROOT / "static"
for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .runs_service import (  # noqa: E402
    DEFAULT_LOG_LIMIT,
    count_log_lines,
    get_log_path_from_db,
    list_runs,
    read_log_page,
    read_log_tail,
    resolve_log_path,
)

router = APIRouter(tags=["runs"])


# ── HTML page ─────────────────────────────────────────────────────────────────

@router.get("/run-browser")
def get_run_browser() -> FileResponse:
    """Serve the Run Browser HTML page.

    This is the ntfy alert click-through target URL.  Deep-links accept a
    ?sprint=<label> query param handled entirely in the frontend JS.
    """
    html_path = _STATIC_DIR / "run_browser.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="run_browser.html not found")
    return FileResponse(str(html_path), media_type="text/html")


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
    allowed_dir = log_file.parent
    # Walk up to find the .commander/logs/ directory
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
