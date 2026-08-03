"""Brain search endpoints — FTS5 over docs/** (issue #2028).

Routes:
  GET /api/brain/search?q=<query>          — FTS5 keyword search
  GET /api/brain/panels?project=<slug>     — pre-built panel data for the Brain tab
  GET /api/brain/search?q=&project=<slug>  — project-scoped search (optional)

The `project` parameter is optional.  When omitted the service resolves the
docs root from the Commander repo itself (the machine the server runs on).

Auth: GET endpoints are open per CLAUDE.md auth exception — no token required.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from . import brain_service

router = APIRouter(tags=["brain"])

# Resolve the Commander repo root (same logic as server.py)
_SERVER_DIR = Path(__file__).parents[1]   # apps/dashboard/
_REPO_ROOT = Path(__file__).parents[3]   # repo root


def _resolve_docs_root(project: str | None) -> Path:
    """Return the docs root for a given project, or the default repo root when absent.

    When *project* is None the server's own docs are used (intentional default).
    When *project* is provided, it is resolved via docs_service.resolve_clone_root
    which accepts both ``owner/repo`` and bare slug.  An unknown project raises
    HTTPException(404) — resolution failure NEVER falls back to a default project
    (issue #2052 / #2064).
    """
    if project is None:
        # No project specified → serve the Commander repo the server runs from.
        return _REPO_ROOT
    from . import docs_service  # noqa: PLC0415
    return docs_service.resolve_clone_root(project)


@router.get("/api/brain/search")
def brain_search(
    q: str = Query(default="", description="FTS5 keyword query"),
    project: str | None = Query(default=None, description="Project slug (optional)"),
) -> list[dict[str, Any]]:
    """Full-text search over docs/, bulk-create/, decisions/, and committed retros.

    Returns a list of hit objects:
      [{path, source, snippet}, …]

    Malformed FTS5 queries (unbalanced quotes, bare operators) return [] — never 500.
    """
    if not q or not q.strip():
        return []
    docs_root = _resolve_docs_root(project)
    return brain_service.search(q, docs_root)


@router.get("/api/brain/panels")
def brain_panels(
    project: str | None = Query(default=None, description="Project slug (optional)"),
) -> dict[str, Any]:
    """Return pre-built panel data for the Brain tab.

    Returns:
      {
        recent_decisions: [{path, title, decision}],
        open_decisions:   [{path, line}],
        last_learnings:   [str, …],
        backlog_rationale: [{path, title}],
      }
    """
    docs_root = _resolve_docs_root(project)
    return brain_service.get_panels(docs_root)
