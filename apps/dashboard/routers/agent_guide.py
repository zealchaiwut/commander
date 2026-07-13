"""Agent operate guide endpoint (issue #1861).

Route:
  GET /api/agent-guide  — return {content, version} for docs/agent-guide.md
"""
from fastapi import APIRouter

from . import agent_guide_service

router = APIRouter(tags=["agent-guide"])


@router.get("/api/agent-guide")
def get_agent_guide():
    """Return the agent operate guide as {content, version}.

    content — full markdown text of docs/agent-guide.md
    version — SHA-256 fingerprint of the content (16 hex chars)

    404s with a helpful message if docs/agent-guide.md is absent from the repo.
    """
    return agent_guide_service.load_guide()
