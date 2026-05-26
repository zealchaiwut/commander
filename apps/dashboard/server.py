import asyncio
import json
import os
import re
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, model_validator

# Load .env before importing local modules so that DB_PATH and other env vars
# are available when db.py executes its module-level startup checks.
load_dotenv(Path(__file__).parent / ".env")

import db
import github_client
import projects as projects_module

STATIC_DIR = Path(__file__).parent / "static"
ENVIRONMENT = os.environ.get("ENVIRONMENT", "prd").lower()

# Configurable via .env: how long (seconds) a 'working' agent can be silent before
# it is marked 'timed_out'.  Default: 300 s (5 minutes).
AGENT_IDLE_TIMEOUT_SECONDS: int = int(os.environ.get("AGENT_IDLE_TIMEOUT_SECONDS", "300"))
_TIMEOUT_CHECK_INTERVAL: int = 60  # run the check every 60 seconds

_subscribers: list[asyncio.Queue] = []
_start_time: float = 0.0


async def _cache_refresh_loop():
    """Periodically re-fetch GitHub data and broadcast an update so clients refresh."""
    while True:
        await asyncio.sleep(30)
        try:
            await broadcast({"type": "update", "event": {"event_type": "cache_refresh"}})
        except Exception:
            pass


async def _timeout_loop() -> None:
    """Background task: mark stale 'working' agents as 'timed_out' every 60 s."""
    while True:
        await asyncio.sleep(_TIMEOUT_CHECK_INTERVAL)
        try:
            count = db.timeout_idle_agents(AGENT_IDLE_TIMEOUT_SECONDS)
            if count:
                await broadcast({"type": "update", "event": {"event_type": "agent_timeout", "count": count}})
        except Exception:
            pass  # never crash the background task


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    db.init_db()
    task1 = asyncio.create_task(_cache_refresh_loop())
    task2 = asyncio.create_task(_timeout_loop())
    yield
    task1.cancel()
    task2.cancel()
    for t in (task1, task2):
        try:
            await t
        except asyncio.CancelledError:
            pass


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
    agent_role:    Optional[str] = None
    model_name:    Optional[str] = None


class RejectBody(BaseModel):
    reason: str


class NewProjectBody(BaseModel):
    repo_url: str
    icon: Optional[str] = "ti-folder"
    color: Optional[str] = "gray"


class InitProjectBody(BaseModel):
    repo_name: str
    projects_dir: str = "~/dev"
    nested: bool = False
    skip_uat: bool = False


class RemoveProjectBody(BaseModel):
    delete_local_folders: bool = False
    delete_github_repo: bool = False


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


@app.get("/projects/{path:path}")
async def spa_project_route(path: str):
    if path.endswith("/plan-sprint"):
        new_path = path[: -len("plan-sprint")] + "sprint-mgmt"
        return RedirectResponse(url=f"/projects/{new_path}", status_code=308)
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
    db.record_token_usage(
        session_id,
        project,
        event.input_tokens,
        event.output_tokens,
        agent_role=event.agent_role,
        model_name=event.model_name,
    )
    await broadcast({"type": "update", "event": event.model_dump()})
    return {"ok": True}


@app.get("/api/debug/token-usage/by-agent-model")
def debug_token_usage_by_agent_model(since: Optional[str] = None):
    """Return token usage grouped by agent_role and model_name.

    Useful for auditing which agents/models are consuming tokens.
    Optional query param: since=<ISO-8601> to restrict to a time window.
    """
    return db.get_token_usage_by_agent_model(window_start_utc=since)


@app.get("/api/debug/token-usage")
def debug_token_usage():
    """AC-2: Diagnostic endpoint for the token_usage pipeline.

    Returns row_count (int), latest_recorded_at (ISO-8601 or null),
    and tokens_today (int) so operators can confirm pipeline health
    without querying SQLite directly.
    """
    return db.get_debug_token_usage()


@app.get("/api/agents")
def list_agents():
    return db.get_agents()


@app.get("/api/events")
def list_events():
    return db.get_recent_events()


@app.delete("/api/events/test")
def clear_test_events():
    """Remove test/debug events and agents from the database and clear test alerts from memory."""
    events_deleted = db.delete_test_events()
    agents_deleted = db.delete_test_agents()
    # Also purge in-memory test alerts (patterns: 'test_', 'Test-', 'Test alert')
    import re as _re
    _test_pat = _re.compile(r"(test_|Test-|Test alert|\[CRASH\])", _re.IGNORECASE)
    before = len(_alerts)
    _alerts[:] = [
        a for a in _alerts
        if not (_test_pat.search(a.get("title", "")) or _test_pat.search(a.get("body", "")))
    ]
    alerts_cleared = before - len(_alerts)
    return {"ok": True, "events_deleted": events_deleted, "agents_deleted": agents_deleted, "alerts_cleared": alerts_cleared}


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
def get_issues(sprint: Optional[int] = None):
    try:
        if sprint is None:
            return github_client.list_all_open_issues()
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


@app.post("/api/projects", status_code=201)
def add_project(body: NewProjectBody):
    try:
        new_proj = projects_module.add_project(
            repo=body.repo_url,
            icon=body.icon or "ti-folder",
            color=body.color or "gray",
        )
        return new_proj
    except FileExistsError as e:
        raise HTTPException(409, detail=str(e))
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)


@app.delete("/api/projects/{owner}/{repo_name}")
async def remove_project(owner: str, repo_name: str, body: RemoveProjectBody):
    import shutil

    repo = f"{owner}/{repo_name}"

    if not any(p["repo"] == repo for p in projects_module.load_projects()):
        raise HTTPException(404, detail="Project not found")

    removed: list[str] = []

    # Remove from all projects.json copies first (not rolled back on subsequent errors)
    removed.extend(projects_module.remove_project(repo))

    if body.delete_local_folders:
        projects_dir = Path.home() / "dev"
        project_root = projects_dir / repo_name
        nested = (project_root / "main").exists() and (project_root / "main" / ".git").exists()
        if nested:
            if project_root.exists():
                shutil.rmtree(project_root)
                removed.append(str(project_root))
        else:
            uat_dir = project_root / "uat"
            if uat_dir.exists():
                shutil.rmtree(uat_dir)
                removed.append(str(uat_dir))
            for suffix in ("", "-coder", "-tester"):
                d = projects_dir / f"{repo_name}{suffix}"
                if d.exists():
                    shutil.rmtree(d)
                    removed.append(str(d))

    if body.delete_github_repo:
        result = subprocess.run(
            ["gh", "repo", "delete", repo, "--yes"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise HTTPException(502, detail=f"Failed to delete GitHub repository: {err}")
        removed.append(f"GitHub repo {repo}")

    return {"ok": True, "removed": removed}


@app.post("/api/projects/{owner}/{repo_name}/approve-batch")
def approve_batch(owner: str, repo_name: str):
    repo = f"{owner}/{repo_name}"
    try:
        issues = github_client.list_open_uat_issues(repo_name=repo)
        approved = []
        for issue in issues:
            github_client.approve_issue(issue["number"], repo_name=repo)
            approved.append(issue["number"])
        return {"approved": approved, "count": len(approved)}
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)


@app.post("/api/projects/init")
async def init_project(body: InitProjectBody):
    """Spawn init_project.py and stream its stdout back as SSE (text/event-stream).

    AC1  — accepts repo_name, projects_dir, nested, skip_uat
    AC2  — spawns init_project.py as subprocess, streams output line by line
    AC3  — resolves ~ in projects_dir via Path.expanduser()
    AC4  — HTTP 400 if repo_name is empty or contains / or \\
    AC5  — HTTP 409 if repo_name already exists in projects.json
    AC6  — streams live log lines as SSE events
    AC7  — sends 'done' SSE event on success (exit code 0)
    AC8  — sends 'error' SSE event on failure (non-zero exit code)
    """
    repo_name = (body.repo_name or "").strip()
    if not repo_name or "/" in repo_name or "\\" in repo_name:
        raise HTTPException(
            status_code=400,
            detail="repo_name must be non-empty and must not contain path separators (/ or \\).",
        )

    # AC5: check projects.json for existing entry
    existing = projects_module.load_projects()
    for p in existing:
        slug = p.get("repo", "").split("/")[-1]
        if slug.lower() == repo_name.lower():
            raise HTTPException(
                status_code=409,
                detail=f"A project named '{repo_name}' already exists in projects.json.",
            )

    # AC3: expand ~ in projects_dir
    projects_dir = Path(body.projects_dir or "~/dev").expanduser()

    # Build subprocess command
    script_path = Path(__file__).parent / "scripts" / "init_project.py"
    cmd = [
        "python3",
        str(script_path),
        repo_name,
        "--projects-dir", str(projects_dir),
    ]
    if body.nested:
        cmd.append("--nested")
    if body.skip_uat:
        cmd.append("--skip-uat")

    async def _stream():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        last_line = ""
        try:
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                last_line = line
                yield f"event: log\ndata: {json.dumps(line)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps(str(exc))}\n\n"
            return

        await proc.wait()
        if proc.returncode == 0:
            yield f"event: done\ndata: {json.dumps('ok')}\n\n"
        else:
            yield f"event: error\ndata: {json.dumps(last_line or 'init_project.py failed')}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/project-details")
def get_project_details(repo: str):
    try:
        agents = db.get_agents()
        return projects_module.get_project_details(repo, agents)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


# ── plan usage endpoint ───────────────────────────────────────────────────────

def _plan_config() -> tuple[int | None, int | None, float]:
    """Return (window_token_limit, weekly_token_limit, window_hours) from env.

    Returns (None, None, 5.0) when WINDOW_TOKEN_LIMIT or PLAN_TYPE is unset.
    """
    plan_type = os.environ.get("PLAN_TYPE", "").strip()
    window_limit_raw = os.environ.get("WINDOW_TOKEN_LIMIT", "").strip()
    if not plan_type or not window_limit_raw:
        return None, None, 5.0

    try:
        window_limit = int(window_limit_raw)
    except ValueError:
        return None, None, 5.0

    weekly_raw = os.environ.get("WEEKLY_TOKEN_LIMIT", "").strip()
    weekly_limit: int | None = None
    if weekly_raw:
        try:
            weekly_limit = int(weekly_raw)
        except ValueError:
            pass

    window_hours_raw = os.environ.get("WINDOW_DURATION_HOURS", "5").strip()
    try:
        window_hours = float(window_hours_raw)
    except ValueError:
        window_hours = 5.0

    return window_limit, weekly_limit, window_hours


@app.get("/api/plan-usage")
def get_plan_usage():
    """Return Plan Usage data for the rolling token window.

    AC-1: Hidden (404 with detail) when WINDOW_TOKEN_LIMIT or PLAN_TYPE is unset.
    AC-3: Returns window_tokens, window_limit, window_pct, window_start,
           window_resets_at, seconds_remaining, weekly_tokens, weekly_limit, status.
    AC-4: Window start = earliest token_usage row after previous window expiry.
    """
    window_limit, weekly_limit, window_hours = _plan_config()
    if window_limit is None:
        raise HTTPException(status_code=404, detail="Plan usage not configured")

    window_duration = timedelta(hours=window_hours)
    now_utc = datetime.now(timezone.utc)

    # --- AC-4: Determine window start via rolling-window logic ---
    # Start from the absolute earliest row ever and walk forward through
    # expired windows until we find the active one or conclude there's none.
    earliest_ts_str = db.get_earliest_token_row_after(None)

    if earliest_ts_str is None:
        # No rows at all → no_activity
        return {
            "window_tokens":    0,
            "window_limit":     window_limit,
            "window_pct":       0.0,
            "window_start":     None,
            "window_resets_at": None,
            "seconds_remaining": 0,
            "weekly_tokens":    None,
            "weekly_limit":     weekly_limit,
            "status":           "no_activity",
        }

    # Parse earliest timestamp (stored without timezone → treat as UTC)
    def _parse_utc(ts: str) -> datetime:
        if ts.endswith("Z"):
            ts = ts[:-1]
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

    window_start = _parse_utc(earliest_ts_str)

    # Walk forward: find the current window by advancing past expired ones
    while True:
        window_end = window_start + window_duration
        if window_end > now_utc:
            # This window is still active
            break
        # Window expired — look for the next activity after this window ended
        window_end_str = window_end.strftime("%Y-%m-%dT%H:%M:%S")
        next_ts_str = db.get_earliest_token_row_after(window_end_str)
        if next_ts_str is None:
            # No activity after last window expiry → expired status
            window_tokens_str = window_start.strftime("%Y-%m-%dT%H:%M:%S")
            window_tokens = db.get_window_usage(window_tokens_str)
            window_pct = round(min(window_tokens / window_limit * 100, 100.0), 2)
            weekly_tokens: int | None = None
            if weekly_limit is not None:
                # Sum last 7 days
                week_start = (now_utc - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
                weekly_tokens = db.get_window_usage(week_start)
            return {
                "window_tokens":    window_tokens,
                "window_limit":     window_limit,
                "window_pct":       window_pct,
                "window_start":     window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_resets_at": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "seconds_remaining": 0,
                "weekly_tokens":    weekly_tokens,
                "weekly_limit":     weekly_limit,
                "status":           "expired",
            }
        window_start = _parse_utc(next_ts_str)

    # Active window found
    window_end = window_start + window_duration
    window_start_str = window_start.strftime("%Y-%m-%dT%H:%M:%S")
    window_tokens = db.get_window_usage(window_start_str)
    window_pct = round(min(window_tokens / window_limit * 100, 100.0), 2)
    seconds_remaining = max(0, int((window_end - now_utc).total_seconds()))

    weekly_tokens = None
    if weekly_limit is not None:
        week_start = (now_utc - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
        weekly_tokens = db.get_window_usage(week_start)

    return {
        "window_tokens":    window_tokens,
        "window_limit":     window_limit,
        "window_pct":       window_pct,
        "window_start":     window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_resets_at": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds_remaining": seconds_remaining,
        "weekly_tokens":    weekly_tokens,
        "weekly_limit":     weekly_limit,
        "status":           "active",
    }


# ── alert banner endpoints (AC-3a from #24) ──────────────────────────────────

_alerts: list[dict] = []


class AlertPayload(BaseModel):
    title: str = ""
    body: str = ""
    issue_num: Optional[int] = None
    category: Optional[str] = None
    repo: Optional[str] = None  # AC-5 (issue #82): owner/repo for per-project prefix in banners


@app.post("/api/alerts", status_code=201)
def receive_alert(payload: AlertPayload):
    _alerts.append(payload.model_dump())
    return {"ok": True, "count": len(_alerts)}


@app.get("/api/alerts")
def get_alerts():
    return _alerts


@app.delete("/api/alerts/{idx}")
def dismiss_alert(idx: int):
    if 0 <= idx < len(_alerts):
        _alerts.pop(idx)
    return {"ok": True, "count": len(_alerts)}


# ── sprint status endpoint (AC-6 from #24) ───────────────────────────────────

# Keyed by project ("owner/repo"). Legacy fallback key is "" for older sprint
# managers that do not send a project field.
_sprint_statuses: dict[str, dict] = {}


class SprintStatusPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    project: str = ""
    sprint_label: str = ""
    sprint_number: Optional[int] = None
    issues: list[dict] = []
    start_timestamp: Optional[str] = None
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    wall_clock_secs: float = 0.0


@app.post("/api/sprint-status")
def set_sprint_status(payload: SprintStatusPayload):
    global _sprint_statuses
    data = payload.model_dump()
    key = data.get("project") or ""
    _sprint_statuses[key] = data
    return {"ok": True}


@app.get("/api/sprint-status")
def get_sprint_status(project: Optional[str] = None):
    """Return sprint status.

    Without ?project=: returns all active statuses as a list under key "statuses".
    With ?project=owner/repo: returns single status dict (or {"active": False}).

    For backwards compatibility the response always includes "active" key.
    """
    if project is not None:
        status = _sprint_statuses.get(project)
        if status is None:
            return {"active": False}
        return {**status, "active": True}
    # Return all stored statuses
    statuses = [
        {**v, "active": True}
        for v in _sprint_statuses.values()
        if v.get("sprint_label")
    ]
    if not statuses:
        return {"active": False, "statuses": []}
    return {"active": True, "statuses": statuses}


# ── sprint summary / history endpoints (AC-4 / AC-6 from #24) ────────────────

SPRINTS_DIR = Path(__file__).parent / "sprints"


@app.get("/api/sprint-summary")
def get_sprint_summary():
    """Return the path and markdown content of the most recent summary file."""
    if not SPRINTS_DIR.exists():
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    summaries = sorted(
        SPRINTS_DIR.glob("*-summary-*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summaries:
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    latest = summaries[0]
    content = latest.read_text(encoding="utf-8")
    return {"path": str(latest), "content": content}


def _parse_summary_file(path: Path) -> dict:
    """Parse metadata from a sprint summary markdown file.

    Extracts: sprint_num, date, status, shipped_count, skipped_count, total_tokens.
    """
    # Filename: sprint-<N>-summary-<YYYY-MM-DD>.md
    name = path.stem  # sprint-3-summary-2026-05-24
    m = re.match(r"sprint-(\d+)-summary-(\d{4}-\d{2}-\d{2})", name)
    sprint_num = int(m.group(1)) if m else None
    date       = m.group(2)      if m else ""

    content = path.read_text(encoding="utf-8")

    # Status from header: ## Sprint N — <status>
    status_m = re.search(r"^## Sprint \S+ — (\S+)", content, re.MULTILINE)
    status   = status_m.group(1) if status_m else "unknown"

    # Count shipped rows (rows in Pending UAT Review table, skip header + empty rows).
    # Also accepts the legacy "What Shipped" heading for old summary files.
    shipped_count = 0
    in_shipped = False
    for line in content.splitlines():
        if line.startswith("## Pending UAT Review") or line.startswith("## What Shipped"):
            in_shipped = True
            continue
        if in_shipped and line.startswith("## "):
            break
        if in_shipped and line.startswith("|") and not line.startswith("| Issue") and "|---|" not in line:
            cell = line.split("|")[1].strip()
            if cell and cell != "—":
                shipped_count += 1

    # Count didn't-ship rows
    skipped_count = 0
    in_skipped = False
    for line in content.splitlines():
        if line.startswith("## What Didn't Ship"):
            in_skipped = True
            continue
        if in_skipped and line.startswith("## "):
            break
        if in_skipped and line.startswith("|") and not line.startswith("| Issue") and "|---|" not in line:
            cell = line.split("|")[1].strip()
            if cell and cell != "—":
                skipped_count += 1

    # Total tokens from Stats table
    total_tokens = 0
    tok_m = re.search(r"\|\s*Total tokens\s*\|\s*(\d+)\s*\|", content)
    if tok_m:
        total_tokens = int(tok_m.group(1))

    return {
        "sprint_num":    sprint_num,
        "date":          date,
        "status":        status,
        "shipped_count": shipped_count,
        "skipped_count": skipped_count,
        "total_tokens":  total_tokens,
    }


@app.get("/api/sprint-history")
def get_sprint_history():
    """AC-4: Return a JSON array of all past summaries, newest first.

    Each entry:
      sprint_num, date, status, file_path, github_issue_url,
      shipped_count, skipped_count, total_tokens
    """
    if not SPRINTS_DIR.exists():
        return []

    summary_files = sorted(
        SPRINTS_DIR.glob("*-summary-*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summary_files:
        return []

    results: list[dict] = []
    for path in summary_files:
        try:
            meta = _parse_summary_file(path)
        except Exception:
            meta = {"sprint_num": None, "date": "", "status": "unknown",
                    "shipped_count": 0, "skipped_count": 0, "total_tokens": 0}

        # Look for matching state file to get summary_issue_url
        sprint_num   = meta.get("sprint_num")
        issue_url    = None
        if sprint_num is not None:
            state_file = SPRINTS_DIR / f"sprint-{sprint_num}-state.json"
            if state_file.exists():
                try:
                    state_data = json.loads(state_file.read_text())
                    issue_url  = state_data.get("summary_issue_url")
                except Exception:
                    pass

        results.append({
            "sprint_num":       meta["sprint_num"],
            "date":             meta["date"],
            "status":           meta["status"],
            "file_path":        str(path),
            "github_issue_url": issue_url,
            "shipped_count":    meta["shipped_count"],
            "skipped_count":    meta["skipped_count"],
            "total_tokens":     meta["total_tokens"],
        })

    return results


@app.get("/api/sprint-history-content")
def get_sprint_history_content(sprint_num: Optional[int] = None, idx: Optional[int] = None):
    """Return markdown content of a specific sprint summary file.

    Looks up by sprint_num first; falls back to position idx in sorted list.
    """
    if not SPRINTS_DIR.exists():
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    summary_files = sorted(
        SPRINTS_DIR.glob("*-summary-*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summary_files:
        raise HTTPException(status_code=404, detail="No sprint summaries found")

    target = None
    if sprint_num is not None:
        for path in summary_files:
            if re.match(rf"sprint-{sprint_num}-summary-", path.stem):
                target = path
                break
    if target is None and idx is not None and 0 <= idx < len(summary_files):
        target = summary_files[idx]

    if target is None:
        raise HTTPException(status_code=404, detail="Sprint summary not found")

    return {"path": str(target), "content": target.read_text(encoding="utf-8")}


# ── sprint planning endpoints (issue #26) ────────────────────────────────────

def _sprint_estimate_size(issue: dict) -> str:
    """Estimate issue size from body content and labels (same heuristic as sprint_planner.py).

    Sizing table:
    | AC + UAT | File mentions | Base size |
    |----------|---------------|-----------|
    | <= 3     | <= 1          | S         |
    | 4-7      | 2-3           | M         |
    | 8-12     | 4-6           | L         |
    | > 12     | > 6           | XL        |

    Label modifier: bug -> -1 level; enhancement -> +1 level
    """
    import re as _re
    body = issue.get("body") or ""
    labels = {lbl["name"] for lbl in issue.get("labels", [])}

    ac_count = 0
    in_ac = False
    for line in body.splitlines():
        if _re.match(r"^#+\s+Acceptance Criteria", line, _re.IGNORECASE):
            in_ac = True
            continue
        if in_ac and _re.match(r"^#+\s+", line):
            in_ac = False
        if in_ac and _re.match(r"^\s*-\s+\[[ x]\]", line):
            ac_count += 1

    uat_count = 0
    in_uat = False
    for line in body.splitlines():
        if _re.match(r"^#+\s+UAT Test Steps", line, _re.IGNORECASE):
            in_uat = True
            continue
        if in_uat and _re.match(r"^#+\s+", line):
            in_uat = False
        if in_uat and _re.match(r"^\s*\d+\.\s+", line):
            uat_count += 1

    file_pattern = _re.compile(r"\b[\w_-]+\.(?:py|js|ts|html|css|sh|json|md|yaml|yml|txt|env)\b")
    file_mentions = len(set(file_pattern.findall(body)))

    total = ac_count + uat_count
    _sizes = ["S", "M", "L", "XL"]
    _size_idx = {s: i for i, s in enumerate(_sizes)}

    if total <= 3:
        _size_by_total = "S"
    elif total <= 7:
        _size_by_total = "M"
    elif total <= 12:
        _size_by_total = "L"
    else:
        _size_by_total = "XL"

    if file_mentions <= 1:
        _size_by_files = "S"
    elif file_mentions <= 3:
        _size_by_files = "M"
    elif file_mentions <= 6:
        _size_by_files = "L"
    else:
        _size_by_files = "XL"

    base = _sizes[min(_size_idx[_size_by_total], _size_idx[_size_by_files])]

    idx = _size_idx[base]
    if "bug" in labels:
        idx = max(0, idx - 1)
    if "enhancement" in labels:
        idx = min(len(_sizes) - 1, idx + 1)

    return _sizes[idx]


class SprintAssignBody(BaseModel):
    issue: int
    sprint: Optional[int] = None  # None = remove all sprint labels


@app.get("/api/sprint-planning/issues")
def get_sprint_planning_issues():
    """Return all open issues with sprint assignment and size estimate.

    Cache TTL: 30s. Cache is invalidated after label mutations via POST /assign.
    """
    try:
        issues = github_client.list_open_issues_with_body(limit=200)
        sprints = github_client.list_sprints()
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    sprint_re_local = re.compile(r"^sprint-(\d+)$")

    result_issues = []
    for iss in issues:
        sprint_num = None
        for lbl in iss.get("labels", []):
            m = sprint_re_local.match(lbl["name"])
            if m:
                sprint_num = int(m.group(1))
                break

        size = _sprint_estimate_size(iss)
        status = github_client.classify_issue(iss)

        result_issues.append({
            "number": iss["number"],
            "title": iss["title"],
            "labels": iss.get("labels", []),
            "sprint": sprint_num,
            "size": size,
            "status": status,
            "url": iss.get("url", ""),
        })

    return {
        "sprints": sprints,
        "issues": result_issues,
    }


@app.post("/api/sprint-planning/assign")
async def assign_sprint_label(body: SprintAssignBody):
    """Assign or remove a sprint label on an issue.

    Body: {"issue": 21, "sprint": 3} — assigns sprint-3, removes other sprint-* labels
    Body: {"issue": 21, "sprint": null} — removes all sprint-* labels

    On success: invalidates cache, broadcasts SSE sprint_plan_update, returns {"ok": true}.
    Creates sprint-N label if it doesn't exist.
    """
    try:
        github_client.assign_sprint(body.issue, body.sprint)
        # Invalidate open_issues_body cache so next GET reflects the change
        github_client.invalidate("open_issues_body:")
        github_client.invalidate("open_issues:")
        github_client.invalidate("sprints:")
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    await broadcast({"type": "update", "event": {"event_type": "sprint_plan_update"}})
    return {"ok": True}


# ── Plan-sprint endpoints (AC-14, AC-15, AC-16) ───────────────────────────────


@app.get("/api/open-issues")
def get_open_issues():
    """Return all open issues including body for conflict detection.  Cached 30 s."""
    try:
        issues = github_client.list_open_issues_with_body(limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    return [
        {
            "number": iss["number"],
            "title": iss["title"],
            "labels": iss.get("labels", []),
            "body": iss.get("body") or "",
            "url": iss.get("url", ""),
            "state": iss.get("state", "open"),
        }
        for iss in issues
    ]


class SprintLabelBody(BaseModel):
    sprint: int


@app.post("/api/issues/{issue_id}/sprint-label")
async def add_sprint_label(issue_id: int, body: SprintLabelBody):
    """Add sprint-N label to an issue without removing existing labels."""
    sprint_label = f"sprint-{body.sprint}"
    try:
        github_client.ensure_sprint_label(body.sprint)
        github_client.update_labels(issue_id, add=[sprint_label], remove=[])
        github_client.invalidate("open_issues_body:")
        github_client.invalidate("open_issues:")
        github_client.invalidate("sprints:")
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return {"ok": True}


_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+$")

_REPO_ROOT = Path(__file__).parent.parent.parent
SPRINT_MANAGER_PATH = _REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
SPRINT_LOG_PATH = Path(__file__).parent / "sprints" / "sprint_run.log"


class SprintRunBody(BaseModel):
    label: str
    goal: str


@app.post("/api/sprint-run")
def run_sprint(body: SprintRunBody):
    """Spawn sprint_manager.py as a detached background process."""
    if not _SPRINT_LABEL_RE.match(body.label):
        raise HTTPException(400, detail=f"Invalid sprint label: {body.label!r}")
    if not SPRINT_MANAGER_PATH.exists():
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    SPRINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(SPRINT_LOG_PATH, "a")

    subprocess.Popen(
        ["python3", str(SPRINT_MANAGER_PATH), body.label],
        env={**os.environ, "SPRINT_GOAL": body.goal},
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    return {"ok": True, "label": body.label}


# ── Sprint Management endpoints (issue #95) ──────────────────────────────────

_PROJECTS_BASE = Path.home() / "dev"


def _project_root_path(repo: str) -> Path:
    """Return the project root directory for a given repo (owner/repo).

    Supports both nested layout (~/dev/<slug>/) and flat layout
    (~/dev/<slug>/ as the main clone). Uses ~/dev as base.
    """
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _coder_clone_path(project_root: Path) -> Path:
    """Return the coder clone path for a project root.

    Nested: <project_root>/coder/
    Flat:   <dev>/<slug>-coder/
    Falls back to project_root itself.
    """
    nested = project_root / "coder"
    if nested.exists():
        return nested
    flat = project_root.parent / f"{project_root.name}-coder"
    if flat.exists():
        return flat
    return project_root


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _sprint_order_path(project_root: Path) -> Path:
    return _commander_dir(project_root) / "sprint-order.json"


def _sprint_goal_path(project_root: Path, sprint_label: str) -> Path:
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}-goal.txt"


def _load_sprint_order(project_root: Path, all_sprints: list[int]) -> list[str]:
    """Load sprint order from file; fill missing/new sprints in ascending order."""
    order_path = _sprint_order_path(project_root)
    saved: list[str] = []
    if order_path.exists():
        try:
            saved = json.loads(order_path.read_text(encoding="utf-8"))
        except Exception:
            saved = []

    all_labels = {f"sprint-{n}" for n in all_sprints}
    saved_set = set(saved)

    # Start with known order, filter out sprints that no longer exist
    result = [s for s in saved if s in all_labels]
    # Append any new sprints not in saved order (ascending)
    new_sprints = sorted(all_labels - saved_set, key=lambda s: int(s.split("-")[1]))
    result.extend(new_sprints)
    return result


def _is_sprint_running(project_root: Path, sprint_label: str) -> bool:
    """Check if a sprint process is running by reading its PID file."""
    pid_file = _commander_dir(project_root) / "sprints" / f"{sprint_label}-pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)  # signal 0 = check if process exists
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        # Process not running — clean up stale PID file
        try:
            pid_file.unlink()
        except OSError:
            pass
        return False


def _any_sprint_running() -> Optional[dict]:
    """Scan all projects for a running sprint. Returns {project, sprint_label} or None."""
    projects = projects_module.load_projects()
    for proj in projects:
        root = _project_root_path(proj["repo"])
        sprints_dir = _commander_dir(root) / "sprints"
        if not sprints_dir.exists():
            continue
        for pid_file in sprints_dir.glob("*-pid"):
            label = pid_file.stem  # e.g. "sprint-2"
            if _is_sprint_running(root, label):
                return {"project": proj["repo"], "sprint_label": label}
    return None


def _all_sprints_running() -> list[dict]:
    """Scan all projects for running sprints. Returns list of {project, sprint_label}."""
    result: list[dict] = []
    projects = projects_module.load_projects()
    for proj in projects:
        root = _project_root_path(proj["repo"])
        sprints_dir = _commander_dir(root) / "sprints"
        if not sprints_dir.exists():
            continue
        for pid_file in sprints_dir.glob("*-pid"):
            label = pid_file.stem  # e.g. "sprint-2"
            if _is_sprint_running(root, label):
                result.append({"project": proj["repo"], "sprint_label": label})
    return result


class SprintMgmtRunBody(BaseModel):
    project: str
    sprint_label: str


class SprintOrderBody(BaseModel):
    order: list[str]


class SprintCreateBody(BaseModel):
    project: str


class SprintGoalBody(BaseModel):
    project: str
    sprint_label: str
    goal: str


@app.get("/api/sprints/goal")
def get_sprint_goal(project: str, sprint: str):
    """Return the persisted sprint goal for a project/sprint."""
    project_root = _project_root_path(project)
    goal_path = _sprint_goal_path(project_root, sprint)
    if goal_path.exists():
        return {"goal": goal_path.read_text(encoding="utf-8").strip()}
    return {"goal": ""}


@app.post("/api/sprints/goal")
def save_sprint_goal(body: SprintGoalBody):
    """Persist sprint goal to .commander/sprints/<label>-goal.txt."""
    if not _SPRINT_LABEL_RE.match(body.sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {body.sprint_label!r}")
    project_root = _project_root_path(body.project)
    goal_path = _sprint_goal_path(project_root, body.sprint_label)
    goal_path.parent.mkdir(parents=True, exist_ok=True)
    goal_path.write_text(body.goal, encoding="utf-8")
    return {"ok": True}


@app.get("/api/sprint-management/issues")
def get_sprint_management_issues(repo: str):
    """Return all open issues + sprint list + display order for a project.

    Also returns:
    - empty_sprint_labels: sprint labels that have 0 open tickets (stale/ghost sprints)
    - placeholder_sprint: the next sprint number to show as a drop target (max+1)
    """
    try:
        issues = github_client.list_open_issues_with_body(repo_name=repo, limit=200)
        sprints = github_client.list_sprints(repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    sprint_re_local = re.compile(r"^sprint-(\d+)$")
    result_issues = []
    # Count open tickets per sprint label
    sprint_ticket_counts: dict[int, int] = {n: 0 for n in sprints}
    for iss in issues:
        sprint_num = None
        for lbl in iss.get("labels", []):
            m = sprint_re_local.match(lbl["name"])
            if m:
                sprint_num = int(m.group(1))
                break
        result_issues.append({
            "number": iss["number"],
            "title": iss["title"],
            "labels": iss.get("labels", []),
            "sprint": sprint_num,
            "status": github_client.classify_issue(iss),
            "url": iss.get("url", ""),
        })
        if sprint_num is not None and sprint_num in sprint_ticket_counts:
            sprint_ticket_counts[sprint_num] += 1

    # Sprint labels with 0 tickets are "empty" — these are stale/ghost labels
    empty_sprint_labels = [
        f"sprint-{n}" for n in sorted(sprint_ticket_counts.keys())
        if sprint_ticket_counts[n] == 0
    ]

    # Build order only from sprints that have tickets (non-empty)
    non_empty_sprints = [n for n in sprints if sprint_ticket_counts.get(n, 0) > 0]
    project_root = _project_root_path(repo)
    order = _load_sprint_order(project_root, non_empty_sprints)

    # Placeholder sprint = max existing sprint + 1 (or 1 if no sprints)
    placeholder_sprint = (max(sprints) if sprints else 0) + 1

    return {
        "sprints": sprints,
        "order": order,
        "issues": result_issues,
        "empty_sprint_labels": empty_sprint_labels,
        "placeholder_sprint": placeholder_sprint,
    }


@app.get("/api/sprints/order")
def get_sprint_order(project: str):
    """Return the persisted sprint display order for a project slug."""
    project_root = _project_root_path(project)
    try:
        sprints = github_client.list_sprints(repo_name=None)
    except Exception:
        sprints = []
    order = _load_sprint_order(project_root, sprints)
    return {"order": order}


@app.post("/api/sprints/order")
def save_sprint_order(project: str, body: SprintOrderBody):
    """Persist sprint display order for a project slug."""
    project_root = _project_root_path(project)
    order_path = _sprint_order_path(project_root)
    order_path.parent.mkdir(parents=True, exist_ok=True)
    order_path.write_text(json.dumps(body.order), encoding="utf-8")
    return {"ok": True}


@app.post("/api/sprints/run", status_code=202)
def run_sprint_managed(body: SprintMgmtRunBody):
    """Spawn sprint_manager.py for the given project + sprint.

    - cwd = project's coder clone
    - ANTHROPIC_API_KEY stripped from subprocess env
    - stdout/stderr → .commander/logs/sprint-run-<label>-<ts>.log
    - PID → .commander/sprints/<label>-pid
    """
    if not _SPRINT_LABEL_RE.match(body.sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {body.sprint_label!r}")
    if not SPRINT_MANAGER_PATH.exists():
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    # Per-issue-#123: only block if this specific (project, sprint_label) is already running.
    # Different projects and different sprint labels within the same project can run concurrently.
    project_root_check = _project_root_path(body.project)
    if _is_sprint_running(project_root_check, body.sprint_label):
        raise HTTPException(
            409,
            detail=f"Sprint {body.sprint_label} is already running for {body.project}",
        )

    project_root = _project_root_path(body.project)
    coder_path   = _coder_clone_path(project_root)
    commander    = _commander_dir(project_root)

    log_dir = commander / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = log_dir / f"sprint-run-{body.sprint_label}-{ts}.log"

    pid_dir = commander / "sprints"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path = pid_dir / f"{body.sprint_label}-pid"

    stripped_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    goal_path = _sprint_goal_path(project_root, body.sprint_label)
    if goal_path.exists():
        stripped_env["SPRINT_GOAL"] = goal_path.read_text(encoding="utf-8").strip()

    log_fh = open(log_path, "w")
    proc = subprocess.Popen(
        ["python3", str(SPRINT_MANAGER_PATH), body.sprint_label, "--skip-gates"],
        env=stripped_env,
        cwd=str(coder_path),
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )

    pid_path.write_text(str(proc.pid), encoding="utf-8")
    return {"ok": True, "sprint_label": body.sprint_label, "pid": proc.pid, "log": str(log_path)}


@app.get("/api/sprints/running")
def get_running_sprints():
    """Return all currently running sprints across all projects (checks PID files).

    Returns {"sprints": [...], "count": N} where each item is {project, sprint_label}.
    """
    sprints = _all_sprints_running()
    return {"sprints": sprints, "count": len(sprints)}


@app.get("/api/sprints/running-all")
def get_all_running_sprints():
    """Return ALL currently running sprints across all projects (per-project PID files).

    Returns: {"running": [{"project": ..., "sprint_label": ...}, ...]}
    Empty list means no sprints are running.
    """
    all_running = _all_sprints_running()
    return {"running": all_running}


@app.delete("/api/sprints/run/{sprint_label}", status_code=200)
def kill_sprint(sprint_label: str, project: str):
    """SIGTERM then SIGKILL the running sprint process for the given project/label."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    pid_file = _commander_dir(project_root) / "sprints" / f"{sprint_label}-pid"

    if not pid_file.exists():
        raise HTTPException(404, detail=f"No running sprint found for {sprint_label}")

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        try:
            pid_file.unlink()
        except OSError:
            pass
        raise HTTPException(404, detail=f"Invalid PID file for {sprint_label}")

    # SIGTERM first, then wait up to 5 s for graceful exit, then SIGKILL
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    else:
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                break
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    try:
        pid_file.unlink()
    except OSError:
        pass

    return {"ok": True}


@app.post("/api/sprints/create")
async def create_sprint_label(body: SprintCreateBody):
    """Create the next sprint-N label for a project (idempotent)."""
    try:
        sprints = github_client.list_sprints(repo_name=body.project)
        next_num = (max(sprints) if sprints else 0) + 1
        github_client.ensure_sprint_label(next_num, repo_name=body.project)
        github_client.invalidate("sprints:")
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return {"ok": True, "sprint_label": f"sprint-{next_num}"}


class SprintDeleteBody(BaseModel):
    labels: list[str]  # list of sprint-N label names to delete
    project: str


@app.post("/api/sprints/delete-empty")
async def delete_empty_sprints(body: SprintDeleteBody):
    """Delete empty sprint labels from GitHub. Only allows deleting sprint-N labels with 0 tickets."""
    # Validate all labels are sprint-N pattern
    for label in body.labels:
        if not _SPRINT_LABEL_RE.match(label):
            raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    # Verify each label has 0 tickets before deleting
    try:
        issues = github_client.list_open_issues_with_body(repo_name=body.project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    label_set = set(body.labels)
    for iss in issues:
        for lbl in iss.get("labels", []):
            if lbl["name"] in label_set:
                raise HTTPException(
                    400,
                    detail=f"Label {lbl['name']!r} still has open tickets — cannot delete",
                )

    deleted = []
    errors = []
    for label in body.labels:
        try:
            github_client.delete_label(label, repo_name=body.project)
            deleted.append(label)
        except subprocess.CalledProcessError as e:
            errors.append(f"{label}: {e.stderr.strip() if e.stderr else str(e)}")

    github_client.invalidate("sprints:")
    result: dict = {"ok": True, "deleted": deleted}
    if errors:
        result["errors"] = errors
    return result


_RERUN_STRIP_LABELS = {"UAT", "UAT-approved", "released", "SIT", "in-progress", "needs-rework"}


class SprintRerunBody(BaseModel):
    confirm: bool


@app.post("/api/sprints/{sprint_label}/rerun")
def rerun_sprint(sprint_label: str, project: str, body: SprintRerunBody):
    """Strip status labels from all completed tickets in a sprint and delete the state file.

    Does NOT spawn any sprint subprocess — user clicks Run sprint afterward.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not body.confirm:
        raise HTTPException(400, detail="confirm must be true")

    if _is_sprint_running(_project_root_path(project), sprint_label):
        raise HTTPException(409, detail="Cannot reset a sprint that is currently running")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    log_dir = commander / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = log_dir / f"sprint-rerun-{sprint_label}-{ts}.log"
    log_lines: list[str] = []

    try:
        issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_issues = [
        iss for iss in issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    affected: list[dict] = []
    errors: list[str] = []

    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        to_remove = list(current_labels & _RERUN_STRIP_LABELS)
        if not to_remove:
            continue
        try:
            github_client.update_labels(iss["number"], add=[], remove=to_remove, repo_name=project)
            affected.append({"number": iss["number"], "removed_labels": to_remove})
            log_lines.append(f"#{iss['number']} {iss['title']}: removed {to_remove}")
        except subprocess.CalledProcessError as e:
            msg = f"#{iss['number']} failed: {e.stderr.strip()}"
            errors.append(msg)
            log_lines.append(f"ERROR {msg}")

    state_file = commander / "sprints" / f"{sprint_label}-state.json"
    state_file.unlink(missing_ok=True)
    log_lines.append(f"Deleted state file: {state_file}")

    log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    github_client.invalidate(f"open_issues_body:")
    github_client.invalidate(f"open_issues:")
    github_client.invalidate(f"issues:")

    result: dict = {"reset_count": len(affected), "affected_issues": affected}
    if errors:
        result["errors"] = errors
    return result


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
