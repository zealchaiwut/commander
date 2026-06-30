"""LLM provider toggle endpoints (issue #1667).

Routes:
  GET  /api/settings/provider  — return active provider
  POST /api/settings/provider  — switch provider (instructs claude-proxy)
"""
from fastapi import APIRouter
from pydantic import BaseModel

from . import llm_provider_service

router = APIRouter(tags=["settings"])


class _ProviderBody(BaseModel):
    provider: str


@router.get("/api/settings/provider")
def get_provider():
    """Return the currently active LLM provider."""
    return llm_provider_service.get_provider()


@router.post("/api/settings/provider")
def post_provider(body: _ProviderBody):
    """Switch the global LLM provider.

    Instructs the claude-proxy to activate the named profile, then persists
    the selection. Returns HTTP 503 if the proxy is unreachable.
    """
    return llm_provider_service.set_provider(body.provider)
