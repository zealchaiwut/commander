"""Service logic for the logs ingestion router (extracted from server.py).

Owns the shared SSE broadcast state, agent-event ingestion, token-usage
ingestion, and the test-event cleanup helper. Service functions are thin
wrappers over ``db`` so that route handlers stay free of business logic and
are easy to unit-test.

Also holds the in-memory alert list (``_alerts``) and its test-pattern filter
(``_test_pat``) which are shared with the remaining alert endpoints still in
``server.py`` — those endpoints import the list by name so mutations stay
in sync (Python list identity).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

import db  # noqa: E402
import projects as projects_module  # noqa: E402

from services.logging import log as _slog  # noqa: E402

# ---------------------------------------------------------------------------
# Pydantic models (moved from server.py)
# ---------------------------------------------------------------------------

class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id:  Optional[str] = None
    agent_id:    Optional[str] = None
    event_type:  str
    working_dir: str = "unknown"
    tool_name:   Optional[str] = None
    status:      str = "working"
    name:        Optional[str] = None

    @model_validator(mode="after")
    def resolve_session_id(self) -> "AgentEvent":
        if not self.session_id:
            self.session_id = self.agent_id or "unknown"
        return self


class TokenUsageEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id:    Optional[str] = None
    event_type:    str = "token_usage"
    working_dir:   str = "unknown"
    input_tokens:  int = 0
    output_tokens: int = 0
    agent_role:    Optional[str] = None
    model_name:    Optional[str] = None
    # owner/repo from COMMANDER_PROJECT (sprint-dispatched agents). Optional;
    # interactive sessions fall back to the working-dir basename.
    project:       Optional[str] = None


# ---------------------------------------------------------------------------
# SSE broadcast state (moved from server.py lines 196, 1213)
# ---------------------------------------------------------------------------

_subscribers: list[asyncio.Queue] = []


async def broadcast(data: dict) -> None:
    """Fan-out a JSON-serialised message to all active SSE subscribers."""
    msg = json.dumps(data)
    for q in _subscribers:
        await q.put(msg)


# ---------------------------------------------------------------------------
# In-memory alert state (moved from server.py lines 3800, 3805)
# Shared with the alert endpoints that remain in server.py — those endpoints
# import ``_alerts`` by name so in-place mutations (append/pop) are visible
# across both modules.
# ---------------------------------------------------------------------------

_alerts: list[dict] = []

_test_pat = re.compile(r"(test_|Test-|Test alert|\[CRASH\])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Agent-session bookkeeping (moved from server.py line 1366)
# ---------------------------------------------------------------------------

_seen_agent_sessions: set[str] = set()


# ---------------------------------------------------------------------------
# Agent identity helpers (moved from server.py lines 1369, 1393)
# ---------------------------------------------------------------------------

def _agent_project_from_name(agent_name: str | None) -> str | None:
    """Derive the full owner/repo project key from an agent name string.

    Agent names are formatted as 'role·repo·branch·#short'. We extract the
    repo label (second component) and match it against the loaded projects list.
    Returns None if no match is found.
    """
    if not agent_name:
        return None
    parts = agent_name.split("·")
    if len(parts) < 2:
        return None
    repo_label = parts[1]
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        return None
    matched = next(
        (p["repo"] for p in all_projects if p["repo"].split("/")[-1] == repo_label),
        None,
    )
    return matched


def _parse_agent_identity(agent_name: str | None) -> tuple[str | None, int | None]:
    """Return (role, issue_num) parsed from an agent name string.

    Agent name format: 'role·repo·branch·#short'. The role is the first
    component; the issue number is extracted from the name via a regex.
    """
    role = None
    issue_num = None
    if agent_name and "·" in agent_name:
        role = agent_name.split("·")[0] or None
    if agent_name:
        import re as _re
        m = _re.search(r"issue-(\d+)", agent_name)
        if m:
            issue_num = int(m.group(1))
    return role, issue_num


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------

def receive_agent_event(event: AgentEvent, request_id: Optional[str] = None) -> dict:
    """Persist an agent event and return acknowledgement.

    Pure-sync portion only — caller is responsible for ``await broadcast(...)``.
    Returns the broadcast payload so the route handler can fire it.
    """
    session_id = event.session_id or "unknown"
    project = _agent_project_from_name(event.name)
    actor = event.name or session_id
    role, issue_num = _parse_agent_identity(event.name)

    if project:
        if event.event_type == "tool_use" and session_id not in _seen_agent_sessions:
            _seen_agent_sessions.add(session_id)
            db.record_event(
                project=project,
                source="agent",
                actor=actor,
                type="agent_started",
                target=session_id,
                detail={"role": role, "issue_num": issue_num, "working_dir": event.working_dir},
                action_id=session_id,
            )
        if event.status in ("done", "timed_out", "error") or event.event_type == "agent_stop":
            _seen_agent_sessions.discard(session_id)
            db.record_event(
                project=project,
                source="agent",
                actor=actor,
                type="agent_finished",
                target=session_id,
                detail={"status": event.status, "role": role, "issue_num": issue_num},
                action_id=session_id,
            )

    db.upsert_agent(session_id, event.working_dir, event.status, event.tool_name, event.name)
    db.add_event(session_id, event.event_type, event.model_dump())
    return {"type": "update", "event": event.model_dump()}


def receive_token_usage(event: TokenUsageEvent) -> dict:
    """Persist a token-usage record and return the broadcast payload.

    Returns empty dict when the event carries no tokens (no-op path).
    """
    if not event.input_tokens and not event.output_tokens:
        return {}
    project = event.project or (
        Path(event.working_dir).name if event.working_dir != "unknown" else "unknown"
    )
    session_id = event.session_id or "unknown"
    db.record_token_usage(
        session_id,
        project,
        event.input_tokens,
        event.output_tokens,
        agent_role=event.agent_role,
        model_name=event.model_name,
    )
    return {"type": "update", "event": event.model_dump()}


def clear_test_events() -> dict:
    """Remove test/debug events and agents from the DB; purge test alerts from memory."""
    events_deleted = db.delete_test_events()
    agents_deleted = db.delete_test_agents()
    before = len(_alerts)
    _alerts[:] = [
        a for a in _alerts
        if not (_test_pat.search(a.get("title", "")) or _test_pat.search(a.get("body", "")))
    ]
    alerts_cleared = before - len(_alerts)
    return {
        "ok": True,
        "events_deleted": events_deleted,
        "agents_deleted": agents_deleted,
        "alerts_cleared": alerts_cleared,
    }
