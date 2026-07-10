"""Logs ingestion endpoints (extracted from server.py).

Covers the four endpoints that write agent telemetry into SQLite and broadcast
live updates over SSE:

  POST   /api/agent-event   — ingest an agent lifecycle event
  POST   /api/token-usage   — ingest a token-usage sample
  DELETE /api/events/test   — wipe test/debug events from DB + memory
  GET    /events            — SSE stream for live dashboard updates

Service logic (DB writes, state management) lives in the sibling
``logs_service`` module; this file contains only thin route handlers.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from services.logging import log as _slog

from . import logs_service
from .logs_service import AgentEvent, TokenUsageEvent

router = APIRouter(tags=["logs"])


@router.post("/api/agent-event")
async def receive_event(request: Request, event: AgentEvent):
    request_id = getattr(request.state, "request_id", None)
    _slog.event(
        "route.entry",
        project="dashboard",
        request_id=request_id,
        route="/api/agent-event",
        method="POST",
        event_type=event.event_type,
    )
    broadcast_payload = logs_service.receive_agent_event(event, request_id=request_id)
    await logs_service.broadcast(broadcast_payload)
    return {"ok": True}


@router.post("/api/token-usage")
async def receive_token_usage(event: TokenUsageEvent):
    broadcast_payload = logs_service.receive_token_usage(event)
    if broadcast_payload:
        await logs_service.broadcast(broadcast_payload)
    return {"ok": True}


@router.delete("/api/events/test")
def clear_test_events():
    """Remove test/debug events and agents from the database and clear test alerts from memory."""
    return logs_service.clear_test_events()


@router.get("/events")
async def sse_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    logs_service._subscribers.append(queue)

    async def generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if queue in logs_service._subscribers:
                logs_service._subscribers.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
