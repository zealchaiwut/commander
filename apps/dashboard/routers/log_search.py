"""log_search.py — GET /api/logs/search endpoint (issue #785).

Cross-run log search using ripgrep with DB-indexed pre-filtering.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from .log_search_service import DEFAULT_LIMIT, search_logs

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
