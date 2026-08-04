"""GET /api/debug/token-usage/by-agent-model — per-agent/model token spend audit (issue #2051).

Exposes the existing db.get_token_usage_by_agent_model() function that was called
internally (startup.py) but never wired to a route.  CLAUDE.md's "Cost visibility"
section advertises this endpoint; without it every request returned 404.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent

for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

router = APIRouter(tags=["debug"])


@router.get("/api/debug/token-usage/by-agent-model")
def get_token_usage_by_agent_model(
    window_start: Optional[str] = Query(
        None,
        description="ISO-8601 UTC timestamp — restrict to rows recorded on or after this time",
    ),
):
    """Return token usage grouped by agent_role and model_name.

    Each row: agent_role, model_name, total_input, total_output, total_tokens.
    Rows without agent_role or model_name are grouped as 'unknown'.

    Query params:
      window_start — optional ISO-8601 UTC lower bound (e.g. 2026-01-01T00:00:00)
    """
    return db.get_token_usage_by_agent_model(window_start_utc=window_start)
