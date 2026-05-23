import asyncio
import json
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, model_validator

import db
import github_client
import projects as projects_module

load_dotenv(Path(__file__).parent / ".env")

STATIC_DIR = Path(__file__).parent / "static"
ENVIRONMENT = os.environ.get("ENVIRONMENT", "prd").lower()

_subscribers: list[asyncio.Queue] = []
_start_time: float = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan)


# ── request models ────────────────────────────────────────────────────────────

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


class RejectBody(BaseModel):
    reason: str


class DatabaseStatus(BaseModel):
    reachable: bool
    path: str


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    database: DatabaseStatus


# ── SSE broadcast ─────────────────────────────────────────────────────────────

async def broadcast(data: dict):
    msg = json.dumps(data)
    for q in _subscribers:
        await q.put(msg)


# ── agent endpoints ───────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    uptime = time.monotonic() - _start_time

    db_reachable = False
    try:
        db.get_conn().execute("SELECT 1")
        db_reachable = True
    except Exception:
        pass

    return HealthResponse(
        status="ok" if db_reachable else "degraded",
        uptime_seconds=uptime,
        database=DatabaseStatus(reachable=db_reachable, path=str(db.DB_PATH)),
    )


@app.get("/api/environment")
def get_environment():
    """Return the current runtime environment (prd or uat)."""
    return {"environment": ENVIRONMENT}


@app.post("/api/agent-event")
async def receive_event(event: AgentEvent):
    db.upsert_agent(event.session_id, event.working_dir, event.status, event.tool_name, event.name)
    db.add_event(event.session_id, event.event_type, event.model_dump())
    await broadcast({"type": "update", "event": event.model_dump()})
    return {"ok": True}


@app.post("/api/token-usage")
async def receive_token_usage(event: TokenUsageEvent):
    if not event.input_tokens and not event.output_tokens:
        return {"ok": True}
    project = Path(event.working_dir).name if event.working_dir != "unknown" else "unknown"
    session_id = event.session_id or "unknown"
    db.record_token_usage(session_id, project, event.input_tokens, event.output_tokens)
    await broadcast({"type": "update", "event": event.model_dump()})
    return {"ok": True}


@app.get("/api/agents")
def list_agents():
    return db.get_agents()


@app.get("/api/events")
def list_events():
    return db.get_recent_events()


@app.get("/events")
async def sse_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.append(queue)

    async def generator():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if queue in _subscribers:
                _subscribers.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── github / sprint endpoints ─────────────────────────────────────────────────

def _gh_error(e: subprocess.CalledProcessError) -> HTTPException:
    detail = e.stderr.strip() if e.stderr else str(e)
    return HTTPException(status_code=502, detail=detail)


@app.get("/api/repo/config")
def get_repo_config():
    try:
        return github_client.repo_config()
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.get("/api/sprints")
def get_sprints():
    try:
        sprints = github_client.list_sprints()
        default = github_client.latest_active_sprint()
        return {"sprints": sprints, "default": default}
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.get("/api/issues")
def get_issues(sprint: int):
    try:
        return github_client.list_issues(sprint)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.post("/api/issues/{issue_id}/approve")
def approve_issue(issue_id: int, repo: Optional[str] = None):
    try:
        github_client.approve_issue(issue_id, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)


@app.post("/api/issues/{issue_id}/reject")
def reject_issue(issue_id: int, body: RejectBody, repo: Optional[str] = None):
    try:
        github_client.reject_issue(issue_id, body.reason, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)


@app.get("/api/issues/{issue_id}/test-report")
def get_test_report(issue_id: int, repo: Optional[str] = None):
    try:
        return github_client.get_test_report(issue_id, repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


# ── project endpoints ─────────────────────────────────────────────────────────

@app.get("/api/projects")
def get_projects():
    try:
        agents = db.get_agents()
        return projects_module.get_all_projects(agents)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.get("/api/project-details")
def get_project_details(repo: str):
    try:
        agents = db.get_agents()
        return projects_module.get_project_details(repo, agents)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
