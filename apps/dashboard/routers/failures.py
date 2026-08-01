"""failures.py — unified failures endpoint (issue #2019).

GET /api/failures returns a JSON list of normalized failure rows drawn from
three SQLite sources:
  1. events WHERE type='ticket_failed'
  2. agent_runs WHERE outcome IN ('failed', 'fail', 'timed_out')
  3. agents WHERE status='timed_out'

Each row is normalized to a common shape:
  issue_number, sprint_label, project, agent, category, reason,
  failure_class, message, attempt_kind, branch, log_url, ts, source

Query params:
  project   — filter by project (owner/repo string); absent = no filter
  since     — ISO timestamp or lookback window ("24h", "7d"); absent = no filter
  category  — filter by normalized failure category; absent = no filter
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from .failures_service import get_failures
from .hermes_models import FailureRow

router = APIRouter(prefix="/api", tags=["failures"])


@router.get("/failures", response_model=list[FailureRow])
def list_failures(
    project: Optional[str] = Query(default=None, description="Filter by project (owner/repo or slug)"),
    since: Optional[str] = Query(
        default=None,
        description="Earliest timestamp — ISO-8601 string or lookback window (e.g. '24h', '7d', '2w')",
    ),
    category: Optional[str] = Query(default=None, description="Filter by normalized failure category"),
) -> list[FailureRow]:
    """Return all recent failures unified across events, agent_runs, and agents.

    Normalizes outcome/category vocabulary (e.g. 'fail' → 'failed') and
    constructs log_url from available identifying fields.
    Results are ordered newest-first by timestamp.
    """
    return get_failures(project=project, since=since, category=category)
