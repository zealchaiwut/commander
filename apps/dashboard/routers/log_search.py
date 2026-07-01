"""log_search.py — GET /api/logs/search endpoint (issue #785).

Cross-run log search using ripgrep with DB-indexed pre-filtering.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from .log_search_service import DEFAULT_LIMIT, search_logs
from . import logs_stats_service

router = APIRouter(prefix="/api/logs", tags=["logs"])


class SearchMatch(BaseModel):
    sprint: Optional[str] = None
    issue: Optional[int] = None
    agent: Optional[str] = None
    line_offset: int
    text: str
    file: str


class SearchResponse(BaseModel):
    matches: list[SearchMatch]
    total: int
    capped: bool
    timed_out: bool
    query_ms: int


@router.get("/search", response_model=SearchResponse)
def get_logs_search(
    project: Optional[str] = None,
    sprint: Optional[str] = None,
    issue: Optional[int] = None,
    agent: Optional[str] = None,
    event_type: Optional[str] = None,
    level: Optional[str] = None,
    time_range: Optional[str] = None,
    q: Optional[str] = None,
) -> Any:
    """Search log files across all sprint runs using ripgrep.

    Query params
    ------------
    project     owner/repo or slug; omit to search all projects
    sprint      sprint label, e.g. sprint-59
    issue       GitHub issue number
    agent       coder | tester | dispatch
    event_type  structured-log event type substring
    level       log level substring (INFO, FAIL, WARN, ERROR)
    time_range  relative range: 24h, 7d, 30d (applied to structured logs)
    q           substring to search within log lines

    Returns up to 500 matches (configurable server-side via DEFAULT_LIMIT).
    Each match includes sprint, issue, agent, line_offset for deep-linking.
    """
    result = search_logs(
        project=project,
        sprint=sprint,
        issue=issue,
        agent=agent,
        event_type=event_type,
        level=level,
        time_range=time_range,
        q=q,
        limit=DEFAULT_LIMIT,
    )
    return result


@router.get("/runs/{sprint_label}/ticket-stats")
def get_logs_ticket_stats(sprint_label: str, project: Optional[str] = None) -> Any:
    """Per-ticket timing + token + failure detail for a Logs-tab run (issue #858).

    One row per ticket in the sprint, each carrying the coder/tester duration,
    the combined token total (any of which may be null → dash on the frontend),
    and the failure class + message for failed tickets. Aggregated locally from
    the durable ``agent_runs`` rows; logic lives in ``logs_stats_service``.
    """
    return logs_stats_service.ticket_stats(sprint_label, project=project)


@router.get("/runs/{sprint_label}/ica-cost")
def get_logs_ica_cost(sprint_label: str, project: Optional[str] = None) -> Any:
    """Per-sprint ICA cost summary for the Logs-tab expanded run (issue #1672 AC5).

    Returns pre-computed ICA token totals and USD cost for a sprint, read from
    the ``agent_runs`` table. Only includes successful ICA runs with a positive
    cost (AC7). Returns ``{is_ica, run_count, total_tokens, cost_usd, sprint_label}``.
    """
    return logs_stats_service.ica_cost_summary(sprint_label, project=project)
