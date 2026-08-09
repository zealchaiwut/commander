"""Regression test: GET /api/sprints/{label}/live crashed the whole uvicorn
worker with fastapi.exceptions.ResponseValidationError when the only active
agent info came from a stale `sprints/{label}-pid` file (no matching
in-progress issue in `{label}-state.json`).

routers/sprint_live.py's fallback path builds `active_agent` as
{"name": "coder", "model": None, "pid": <pid>} with no "ticket" key, then
reuses that same dict inside the `active_agents` list when no per-issue
coder/tester entries exist. `active_agents` entries are validated against
ActiveAgentEntry (routers/hermes_models.py), which required `ticket` — so
FastAPI's response serialization raised ResponseValidationError for real
traffic, not a mocked/synthetic case.

This test exercises the actual Pydantic validation call that raised, using
the exact payload observed in the crash log (`{'name': 'coder', 'model':
None, 'pid': 9996}`), not a source-text regex.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "dashboard"))

from routers.hermes_models import ActiveAgentEntry  # noqa: E402


def test_active_agent_entry_accepts_missing_ticket():
    """The exact crash payload must now validate instead of raising."""
    entry = ActiveAgentEntry(name="coder", pid=9996)
    assert entry.ticket is None
    assert entry.pid == 9996
    assert entry.name == "coder"


def test_active_agent_entry_still_accepts_full_ticket():
    """Normal per-issue entries (with a real ticket) still validate as before."""
    entry = ActiveAgentEntry(name="tester", ticket={"number": 42, "title": "fix thing"}, pid=123)
    assert entry.ticket.number == 42
    assert entry.ticket.title == "fix thing"
