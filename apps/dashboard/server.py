import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
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


def _sweep_orphan_pid_files() -> None:
    """On startup: scan all projects' PID files and remove orphans.

    A PID file is orphaned when:
    - The process no longer exists (ProcessLookupError from os.kill(pid, 0))
    - The process exists but its argv doesn't contain sprint_manager.py followed
      by the expected sprint label (PID reuse by an unrelated process)

    Live sprint_manager.py processes for the correct label are left untouched.
    """
    sweep_start = time.monotonic()
    try:
        projects = projects_module.load_projects()
    except Exception as exc:
        print(f"[startup-sweep] could not load projects: {exc}")
        return

    for proj in projects:
        try:
            project_root = _project_root_path(proj["repo"])
            sprints_dir = _commander_dir(project_root) / "sprints"
            if not sprints_dir.exists():
                continue
            for pid_file in sprints_dir.glob("*-pid"):
                sprint_label = pid_file.name.removesuffix("-pid")  # e.g. "sprint-9"
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                except (ValueError, OSError):
                    # Unreadable/corrupt PID file — remove it.
                    try:
                        pid_file.unlink()
                    except OSError:
                        pass
                    print(f"[startup-sweep] cleaned unreadable PID file {pid_file}")
                    continue

                # Check if the process exists.
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    try:
                        pid_file.unlink()
                    except OSError:
                        pass
                    print(
                        f"[startup-sweep] cleaned orphan PID file {pid_file}"
                        f" (PID {pid} not running)"
                    )
                    continue
                except PermissionError:
                    # Process exists but we can't signal it (different user).
                    # Leave it; it may be legitimate.
                    continue
                except OSError:
                    continue

                # Process is alive — check its argv to guard against PID reuse.
                if _psutil is None:
                    # psutil not installed — skip argv check, leave PID file.
                    continue
                try:
                    proc_info = _psutil.Process(pid)
                    argv = proc_info.cmdline()
                except Exception:
                    # Process already gone or permission error — skip argv check.
                    continue

                # argv must contain "sprint_manager.py" followed by sprint_label.
                try:
                    sm_idx = next(
                        i for i, arg in enumerate(argv) if "sprint_manager.py" in arg
                    )
                    label_present = (
                        len(argv) > sm_idx + 1 and argv[sm_idx + 1] == sprint_label
                    )
                except StopIteration:
                    label_present = False

                if not label_present:
                    try:
                        pid_file.unlink()
                    except OSError:
                        pass
                    print(
                        f"[startup-sweep] cleaned orphan PID file {pid_file}"
                        f" (PID {pid} reused by unrelated process)"
                    )
        except Exception as exc:
            print(f"[startup-sweep] error scanning project {proj.get('repo')}: {exc}")

    elapsed_ms = (time.monotonic() - sweep_start) * 1000
    print(f"[startup-sweep] completed in {elapsed_ms:.1f}ms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    db.init_db()
    _sweep_orphan_pid_files()
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
    # Also purge in-memory test alerts using the module-level _test_pat pattern.
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


@app.get("/api/github/labels")
def get_github_labels(repo: Optional[str] = None):
    """Return all GitHub labels for the repo (cached 30 s)."""
    try:
        return github_client.list_labels(repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
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


@app.post("/api/tickets/{issue_id}/approve")
async def approve_ticket(issue_id: int, repo: Optional[str] = None):
    """Close a UAT-labelled ticket on GitHub and remove the UAT label."""
    try:
        github_client.approve_issue(issue_id, repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    await broadcast({"type": "update", "event": {"event_type": "ticket_approved", "issue": issue_id}})
    return {"ok": True}


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
async def approve_batch(owner: str, repo_name: str):
    repo = f"{owner}/{repo_name}"
    try:
        issues = github_client.list_open_uat_issues(repo_name=repo)
        approved = []
        for issue in issues:
            github_client.approve_issue(issue["number"], repo_name=repo)
            approved.append(issue["number"])
        for issue_id in approved:
            await broadcast({"type": "update", "event": {"event_type": "ticket_approved", "issue": issue_id}})
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

# Pattern used to identify test/debug alerts — applied both when purging via
# DELETE /api/events/test and when serving GET /api/alerts so that test noise
# is never surfaced on PRD.
_test_pat = re.compile(r"(test_|Test-|Test alert|\[CRASH\])", re.IGNORECASE)


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
    # Silently exclude test/debug alerts so they are never shown on PRD.
    return [
        a for a in _alerts
        if not (_test_pat.search(a.get("title", "")) or _test_pat.search(a.get("body", "")))
    ]


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
    token_budget: int = 0
    paused: bool = False


@app.post("/api/sprint-status")
async def set_sprint_status(payload: SprintStatusPayload):
    global _sprint_statuses
    data = payload.model_dump()
    key = data.get("project") or ""
    _sprint_statuses[key] = data
    await broadcast({"type": "sprint_update", **data})
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


@app.post("/api/sprint-pause")
async def sprint_pause():
    """AC-4: Create pause file, set paused=True in state, broadcast SSE."""
    global _sprint_status
    if _sprint_status is None:
        raise HTTPException(status_code=404, detail="No active sprint")
    n = _sprint_status.get("sprint_number")
    if n is not None:
        pause_file = SPRINTS_DIR / f"sprint-{n}.pause"
        pause_file.parent.mkdir(parents=True, exist_ok=True)
        pause_file.touch()
    _sprint_status["paused"] = True
    await broadcast({"type": "sprint_update", **_sprint_status})
    return {"ok": True, "state": "paused"}


@app.post("/api/sprint-resume")
async def sprint_resume():
    """AC-5: Remove pause file, set paused=False in state, broadcast SSE."""
    global _sprint_status
    if _sprint_status is None:
        raise HTTPException(status_code=404, detail="No active sprint")
    n = _sprint_status.get("sprint_number")
    if n is not None:
        pause_file = SPRINTS_DIR / f"sprint-{n}.pause"
        pause_file.unlink(missing_ok=True)
    _sprint_status["paused"] = False
    await broadcast({"type": "sprint_update", **_sprint_status})
    return {"ok": True, "state": "running"}


@app.post("/api/sprint-stop")
async def sprint_stop():
    """AC-6: Read PID from sprint-N.pid, SIGTERM the process, clear state, broadcast sprint_stopped."""
    global _sprint_status
    if _sprint_status is None:
        raise HTTPException(status_code=404, detail="No active sprint")
    n = _sprint_status.get("sprint_number")
    if n is None:
        raise HTTPException(status_code=404, detail="Sprint number unknown")
    pid_file = SPRINTS_DIR / f"sprint-{n}.pid"
    if not pid_file.exists():
        raise HTTPException(status_code=404, detail="PID file missing — sprint may have already stopped")
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except ValueError:
        pid_file.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail="Invalid PID file")
    except (ProcessLookupError, PermissionError) as e:
        raise HTTPException(status_code=502, detail=f"Cannot signal process: {e}")
    pid_file.unlink(missing_ok=True)
    _sprint_status = None
    await broadcast({"type": "sprint_stopped"})
    return {"ok": True}


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

        # Look for matching state file to get summary_issue_url and reviewer data
        sprint_num           = meta.get("sprint_num")
        issue_url            = None
        reviewer_status      = None
        reviewer_comment_url = None
        reviewer_findings    = None
        if sprint_num is not None:
            state_file = SPRINTS_DIR / f"sprint-{sprint_num}-state.json"
            if state_file.exists():
                try:
                    state_data           = json.loads(state_file.read_text())
                    issue_url            = state_data.get("summary_issue_url")
                    reviewer_status      = state_data.get("reviewer_status")
                    reviewer_comment_url = state_data.get("reviewer_comment_url")
                    reviewer_findings    = state_data.get("reviewer_findings")
                except Exception:
                    pass

        results.append({
            "sprint_num":            meta["sprint_num"],
            "date":                  meta["date"],
            "status":                meta["status"],
            "file_path":             str(path),
            "github_issue_url":      issue_url,
            "shipped_count":         meta["shipped_count"],
            "skipped_count":         meta["skipped_count"],
            "total_tokens":          meta["total_tokens"],
            "reviewer_status":       reviewer_status,
            "reviewer_comment_url":  reviewer_comment_url,
            "reviewer_findings":     reviewer_findings,
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
    budget: Optional[int] = None


@app.post("/api/sprint-run")
def run_sprint(body: SprintRunBody):
    """Spawn sprint_manager.py as a detached background process."""
    if not _SPRINT_LABEL_RE.match(body.label):
        raise HTTPException(400, detail=f"Invalid sprint label: {body.label!r}")
    if not SPRINT_MANAGER_PATH.exists():
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    SPRINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(SPRINT_LOG_PATH, "a")

    cmd = ["python3", str(SPRINT_MANAGER_PATH), body.label]
    if body.budget is not None:
        cmd += [f"--budget={body.budget}"]

    subprocess.Popen(
        cmd,
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
    migrate_from: list[int] = []


class SprintOrderBody(BaseModel):
    order: list[str]


class SprintCreateBody(BaseModel):
    project: str
    sprint_number: int | None = None


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

    # Compute the minimum sprint number that has >= 1 open ticket (lowest active sprint)
    active_sprint_nums = [n for n, count in sprint_ticket_counts.items() if count > 0]
    min_active_sprint = min(active_sprint_nums) if active_sprint_nums else None

    # Sprint labels with 0 tickets are "empty" — only include those strictly below the
    # lowest active sprint (sprints at or above the threshold are not offered for cleanup).
    # If there are no active sprints at all, no sprints are offered for cleanup.
    empty_sprint_labels = [
        f"sprint-{n}" for n in sorted(sprint_ticket_counts.keys())
        if sprint_ticket_counts[n] == 0
        and min_active_sprint is not None
        and n < min_active_sprint
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


_MIGRATION_STATUS_LABELS = {"UAT", "UAT-approved", "SIT", "in-progress", "need-rework"}


@app.post("/api/sprints/run", status_code=202)
def run_sprint_managed(body: SprintMgmtRunBody):
    """Spawn sprint_manager.py for the given project + sprint.

    - cwd = project's coder clone
    - ANTHROPIC_API_KEY stripped from subprocess env
    - stdout/stderr → .commander/logs/sprint-run-<label>-<ts>.log
    - PID → .commander/sprints/<label>-pid
    - migrate_from: list of sprint numbers whose open tickets are moved to target sprint
      before dispatch; rollback on any failure.
    """
    if not _SPRINT_LABEL_RE.match(body.sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {body.sprint_label!r}")
    if not SPRINT_MANAGER_PATH.exists():
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    # Per-issue-#123: only block if this specific (project, sprint_label) is already running.
    # Different projects and different sprint labels within the same project can run concurrently.
    project_root = _project_root_path(body.project)
    if _is_sprint_running(project_root, body.sprint_label):
        pid_file = _commander_dir(project_root) / "sprints" / f"{body.sprint_label}-pid"
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            pid_str = str(pid)
        except (ValueError, OSError):
            pid_str = "unknown"
        raise HTTPException(
            409,
            detail=f"Sprint {body.sprint_label} is already running on {body.project} (PID {pid_str})",
        )
    coder_path   = _coder_clone_path(project_root)
    commander    = _commander_dir(project_root)

    # ── Migration: move open tickets from earlier sprints to target ───────────
    migration_log_lines: list[str] = []
    migrated_count = 0
    if body.migrate_from:
        target_label = body.sprint_label
        ts_mig = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        mig_log_dir = commander / "logs"
        mig_log_dir.mkdir(parents=True, exist_ok=True)
        mig_log_path = mig_log_dir / f"sprint-migration-{ts_mig}.log"

        try:
            all_issues = github_client.list_open_issues_with_body(repo_name=body.project, limit=200)
        except subprocess.CalledProcessError as e:
            raise _gh_error(e)

        # Collect pending changes: (issue_num, from_label, labels_to_remove)
        pending: list[tuple[int, str, list[str]]] = []
        for src_num in body.migrate_from:
            src_label = f"sprint-{src_num}"
            src_issues = [
                iss for iss in all_issues
                if any(lbl["name"] == src_label for lbl in iss.get("labels", []))
            ]
            for iss in src_issues:
                current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
                status_to_remove = list(current_labels & _MIGRATION_STATUS_LABELS)
                pending.append((iss["number"], src_label, status_to_remove))

        # Apply changes with rollback on failure
        applied: list[tuple[int, str, list[str]]] = []
        try:
            for issue_num, src_label, status_to_remove in pending:
                remove_labels = [src_label] + status_to_remove
                add_labels = [target_label]
                github_client.update_labels(
                    issue_num,
                    add=add_labels,
                    remove=remove_labels,
                    repo_name=body.project,
                )
                applied.append((issue_num, src_label, status_to_remove))
                migration_log_lines.append(
                    f"Moved #{issue_num} from {src_label} to {target_label}"
                    + (f"; stripped status: {status_to_remove}" if status_to_remove else "")
                )
                migrated_count += 1
        except subprocess.CalledProcessError as rollback_err:
            # Rollback applied changes
            rollback_errors: list[str] = []
            for issue_num, src_label, status_to_remove in reversed(applied):
                try:
                    github_client.update_labels(
                        issue_num,
                        add=[src_label] + status_to_remove,
                        remove=[target_label],
                        repo_name=body.project,
                    )
                    migration_log_lines.append(f"ROLLBACK #{issue_num}: restored {src_label}")
                except subprocess.CalledProcessError as rb_e:
                    rollback_errors.append(f"#{issue_num}: {rb_e.stderr.strip()}")
                    migration_log_lines.append(f"ROLLBACK FAILED #{issue_num}")

            mig_log_path.write_text("\n".join(migration_log_lines) + "\n", encoding="utf-8")
            detail = f"Migration failed on #{pending[len(applied)][0]}: {rollback_err.stderr.strip()}"
            if rollback_errors:
                detail += f"; rollback errors: {'; '.join(rollback_errors)}"
            raise HTTPException(500, detail=detail)

        mig_log_path.write_text("\n".join(migration_log_lines) + "\n", encoding="utf-8")

        # Invalidate caches after migration
        github_client.invalidate("open_issues_body:")
        github_client.invalidate("open_issues:")
        github_client.invalidate("issues:")

    log_dir = commander / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = log_dir / f"sprint-run-{body.sprint_label}-{ts}.log"

    pid_dir = commander / "sprints"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_path = pid_dir / f"{body.sprint_label}-pid"

    stripped_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    stripped_env["COMMANDER_DISPATCHED_BY_SERVER"] = "1"
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

    # Write PID file immediately so we can clean it up if the process crashes fast.
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    # Poll for ~2 seconds to detect fast crashes before returning 202.
    exit_code: Optional[int] = None
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        exit_code = proc.poll()
        if exit_code is not None:
            break
        time.sleep(0.1)

    if exit_code is not None:
        # Subprocess crashed — flush the log, clean up PID file, return 502 with log tail.
        log_fh.flush()
        log_fh.close()
        try:
            pid_path.unlink()
        except OSError:
            pass
        try:
            log_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(log_lines[-30:])
        except OSError:
            log_tail = "(log file not readable)"
        detail = f"Sprint process exited with code {exit_code}.\n\n{log_tail}"
        if len(detail) > 1500:
            detail = detail[-1500:]
        raise HTTPException(502, detail=detail)

    return {
        "ok": True,
        "sprint_label": body.sprint_label,
        "pid": proc.pid,
        "log": str(log_path),
        "migrated_count": migrated_count,
        "migrate_from": body.migrate_from,
    }


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
    """Create a sprint-N label for a project. Uses sprint_number if provided, else auto-increments."""
    try:
        sprints = github_client.list_sprints(repo_name=body.project)
        if body.sprint_number is not None:
            if body.sprint_number in sprints:
                raise HTTPException(409, detail=f"Sprint {body.sprint_number} already exists")
            target_num = body.sprint_number
        else:
            target_num = (max(sprints) if sprints else 0) + 1
        github_client.ensure_sprint_label(target_num, repo_name=body.project)
        github_client.invalidate("sprints:")
    except HTTPException:
        raise
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return {"ok": True, "sprint_label": f"sprint-{target_num}"}


class SprintDeleteBody(BaseModel):
    labels: list[str]  # list of sprint-N label names to delete
    project: str


@app.post("/api/sprints/delete-empty")
async def delete_empty_sprints(body: SprintDeleteBody):
    """Delete empty sprint labels from GitHub. Only allows deleting sprint-N labels with 0 tickets
    that are strictly below the lowest active sprint number."""
    # Validate all labels are sprint-N pattern
    for label in body.labels:
        if not _SPRINT_LABEL_RE.match(label):
            raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    # Verify each label has 0 tickets before deleting, and compute lowest active sprint
    try:
        issues = github_client.list_open_issues_with_body(repo_name=body.project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    # Compute sprint ticket counts to determine min_active_sprint
    sprint_re_local = re.compile(r"^sprint-(\d+)$")
    issue_sprint_counts: dict[int, int] = {}
    label_set = set(body.labels)
    for iss in issues:
        for lbl in iss.get("labels", []):
            m = sprint_re_local.match(lbl["name"])
            if m:
                n = int(m.group(1))
                issue_sprint_counts[n] = issue_sprint_counts.get(n, 0) + 1
            if lbl["name"] in label_set:
                raise HTTPException(
                    400,
                    detail=f"Label {lbl['name']!r} still has open tickets — cannot delete",
                )

    active_sprint_nums = [n for n, count in issue_sprint_counts.items() if count > 0]
    min_active_sprint = min(active_sprint_nums) if active_sprint_nums else None

    # Reject any label whose sprint number >= min_active_sprint (or if no active sprints exist)
    for label in body.labels:
        m = sprint_re_local.match(label)
        if m:
            label_num = int(m.group(1))
            if min_active_sprint is None:
                raise HTTPException(
                    422,
                    detail=f"Cannot delete {label}: no active sprints exist, nothing is eligible for cleanup",
                )
            if label_num >= min_active_sprint:
                raise HTTPException(
                    422,
                    detail=f"Cannot delete {label}: sprint number {label_num} is not below the lowest active sprint ({min_active_sprint})",
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


_RERUN_STRIP_LABELS = {"UAT", "UAT-approved", "released", "SIT", "in-progress", "need-rework"}


@app.get("/api/sprints/{sprint_label}/estimate")
def get_sprint_estimate(sprint_label: str, project: str):
    """Return the sprint estimate JSON file content for sprint_label.

    Returns the parsed JSON from <sprints_dir>/sprint-<N>-estimate.json,
    or 404 if the file has not been generated yet.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    # Extract sprint number from label like "sprint-9" → "9"
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    estimate_path = commander / "sprints" / f"sprint-{n}-estimate.json"

    if not estimate_path.exists():
        raise HTTPException(
            404,
            detail=f"Estimate not found for {sprint_label!r}. Run the estimator first.",
        )

    try:
        data = json.loads(estimate_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read estimate file: {e}")

    return data


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


@app.delete("/api/sprints/{sprint_label}")
def delete_sprint(sprint_label: str, project: str):
    """Remove a sprint label from GitHub and unlabel all attached tickets.

    Does NOT delete the issues themselves — only the sprint label is removed.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    if _is_sprint_running(_project_root_path(project), sprint_label):
        raise HTTPException(409, detail="Cannot delete a sprint that is currently running")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    try:
        issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_issues = [
        iss for iss in issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    errors: list[str] = []
    unlabelled_count = 0

    for iss in sprint_issues:
        try:
            github_client.update_labels(iss["number"], add=[], remove=[sprint_label], repo_name=project)
            unlabelled_count += 1
        except subprocess.CalledProcessError as e:
            errors.append(f"#{iss['number']} failed: {e.stderr.strip()}")

    try:
        github_client.delete_label(sprint_label, repo_name=project)
    except subprocess.CalledProcessError as e:
        errors.append(f"Label deletion failed: {e.stderr.strip()}")

    (commander / "sprints" / f"{sprint_label}-state.json").unlink(missing_ok=True)
    (commander / "sprints" / f"{sprint_label}-goal.txt").unlink(missing_ok=True)

    github_client.invalidate(f"open_issues_body:")
    github_client.invalidate(f"open_issues:")
    github_client.invalidate(f"issues:")
    github_client.invalidate(f"sprints:")

    result: dict = {"deleted_label": sprint_label, "unlabelled_count": unlabelled_count}
    if errors:
        result["errors"] = errors
    return result


# ── Finish Sprint endpoint (issue #195) ──────────────────────────────────────

_FINISH_SPRINT_REMOVE_LABELS = {"in-progress", "sit", "need-rework"}


@app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/finish")
async def finish_sprint(owner: str, repo_name: str, label: str):
    """Bulk-close all open issues for a sprint, moving them to UAT first.

    AC: iterates all open issues with the sprint label, adds 'UAT' label
    (removing 'in-progress', 'sit', 'need-rework' if present), then closes each
    issue via gh issue close.

    Returns: { "closed": N, "errors": [] }
      - HTTP 200 on full success (including zero-issue case)
      - HTTP 207 if any individual issue operation failed
    """
    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    project_root = _project_root_path(repo)

    # Block if this sprint is currently running
    if _is_sprint_running(project_root, label):
        raise HTTPException(409, detail=f"Sprint {label} is currently running — finish it after the run completes")

    # Fetch all open issues for the repo
    try:
        all_issues = github_client.list_open_issues_with_body(repo_name=repo, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    # Filter to issues belonging to this sprint label
    sprint_issues = [
        iss for iss in all_issues
        if any(lbl["name"] == label for lbl in iss.get("labels", []))
    ]

    closed = 0
    errors: list[str] = []

    for iss in sprint_issues:
        issue_num = iss["number"]
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        to_remove = list(current_labels & _FINISH_SPRINT_REMOVE_LABELS)
        try:
            # Add UAT and strip workflow labels in one gh call
            github_client.update_labels(issue_num, add=["UAT"], remove=to_remove, repo_name=repo)
            github_client.close_issue(issue_num, repo_name=repo)
            closed += 1
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() if e.stderr else str(e)
            errors.append(f"#{issue_num}: {err_msg}")

    # Invalidate caches so the board refreshes
    github_client.invalidate(f"open_issues_body:")
    github_client.invalidate(f"open_issues:")
    github_client.invalidate(f"issues:")
    github_client.invalidate(f"recent_closed:")

    await broadcast({"type": "update", "event": {"event_type": "sprint_finished", "sprint_label": label}})

    result: dict = {"closed": closed, "errors": errors}
    status_code = 207 if errors else 200
    return JSONResponse(content=result, status_code=status_code)


# ── Draft Ticket endpoints (issue #94) ───────────────────────────────────────

_DRAFT_UPLOAD_DIR = Path(__file__).parent / "runtime" / "draft-uploads"

# ── Attachment branch constants (issue #188) ──────────────────────────────────
_ATTACHMENTS_BRANCH = "attachments"
_ATTACHMENTS_CACHE_DIR = Path(__file__).parent / "runtime" / "attachments-cache"

# Server-side allow-list for upload extensions.
_ALLOWED_UPLOAD_EXTS = {
    ".html", ".htm", ".md", ".txt", ".csv", ".json", ".yaml", ".yml",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf",
    ".py", ".js", ".ts", ".tsx", ".css", ".sh", ".log",
    ".drawio", ".xlsx", ".pptx", ".docx", ".zip",
}
# Per-file size limit (bytes)
_MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
# Per-batch total size limit (bytes)
_MAX_BATCH_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


def _repo_root() -> Path:
    """Return the git repository root (the checkout that contains this file)."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent),
    )
    if result.returncode != 0:
        raise RuntimeError("Could not determine git repo root")
    return Path(result.stdout.strip())


def _sanitize_filename(filename: str) -> str:
    """Sanitize an uploaded filename for storage on the attachments branch.

    - Strip path components (basename only)
    - Lowercase
    - Replace any char not in [a-z0-9._-] with _
    - Preserve extension
    """
    name = Path(filename).name  # strip path components
    name = name.lower()
    stem = Path(name).stem
    suffix = Path(name).suffix  # includes the dot
    # Replace non-safe chars
    safe_stem = re.sub(r"[^a-z0-9._-]", "_", stem)
    safe_suffix = re.sub(r"[^a-z0-9._-]", "_", suffix)
    return safe_stem + safe_suffix


def _resolve_collision(desired: str, existing: set[str]) -> str:
    """Return desired if not in existing, else append a numeric suffix before extension."""
    if desired not in existing:
        return desired
    stem = Path(desired).stem
    suffix = Path(desired).suffix
    counter = 1
    while True:
        candidate = f"{stem}-{counter}{suffix}"
        if candidate not in existing:
            return candidate
        counter += 1


def _get_attachment_cache_dir(repo: str) -> Path:
    """Return the bare-clone cache path for the given repo (owner/name)."""
    safe = repo.replace("/", "-")
    return _ATTACHMENTS_CACHE_DIR / safe


def _ensure_attachments_branch(repo: str) -> None:
    """Ensure the `attachments` orphan branch exists on the remote repo.

    Uses `gh api` to check; if missing, clones the repo into a temp dir,
    creates the orphan branch with an empty commit, and pushes it.
    """
    # Check if branch exists
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/branches/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return  # branch already exists

    # Branch missing — create orphan branch via a temp clone
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Get remote URL
        url_result = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "sshUrl,url", "-q", ".url"],
            capture_output=True, text=True,
        )
        remote_url = url_result.stdout.strip()
        if not remote_url:
            remote_url = f"https://github.com/{repo}.git"

        # Clone just enough to create the branch
        subprocess.run(
            ["git", "clone", "--depth=1", remote_url, tmpdir],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "checkout", "--orphan", _ATTACHMENTS_BRANCH],
            capture_output=True, text=True, cwd=tmpdir,
        )
        subprocess.run(
            ["git", "rm", "-rf", "."],
            capture_output=True, text=True, cwd=tmpdir,
        )
        # Create empty initial commit
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init attachments branch"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        subprocess.run(
            ["git", "push", "origin", _ATTACHMENTS_BRANCH],
            capture_output=True, text=True, cwd=tmpdir, check=True,
        )


def _init_attachment_cache(repo: str) -> Path:
    """Initialize or return the bare-clone cache for the attachments branch.

    Returns path to the bare clone directory.
    """
    cache_dir = _get_attachment_cache_dir(repo)
    if cache_dir.exists():
        # Fetch latest from origin
        subprocess.run(
            ["git", "fetch", "origin", _ATTACHMENTS_BRANCH],
            capture_output=True, text=True, cwd=str(cache_dir),
        )
        return cache_dir

    # First use — bare-clone
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    url_result = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "url", "-q", ".url"],
        capture_output=True, text=True,
    )
    remote_url = url_result.stdout.strip()
    if not remote_url:
        remote_url = f"https://github.com/{repo}.git"

    subprocess.run(
        ["git", "clone", "--bare", "--branch", _ATTACHMENTS_BRANCH,
         remote_url, str(cache_dir)],
        capture_output=True, text=True, check=True,
    )
    return cache_dir


def _list_existing_attachments(cache_dir: Path, issue_number: int) -> set[str]:
    """Return set of filenames already on the attachments branch for this issue."""
    path_prefix = f"references/issue-{issue_number}/"
    result = subprocess.run(
        ["git", "ls-tree", "--name-only", f"refs/heads/{_ATTACHMENTS_BRANCH}", path_prefix],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if result.returncode != 0:
        return set()
    files = set()
    for line in result.stdout.splitlines():
        name = line.strip()
        if "/" in name:
            name = name.rsplit("/", 1)[-1]
        if name:
            files.add(name)
    return files


def _commit_attachments_to_branch(
    cache_dir: Path,
    issue_number: int,
    file_data: list[tuple[str, bytes]],  # (sanitized_filename, content)
) -> None:
    """Commit files to the attachments branch in the bare cache and push.

    Uses git hash-object + update-index + write-tree + commit-tree + update-ref
    to write directly to the bare repo without a worktree checkout.

    On push failure: retries once with fresh fetch+rebase.
    Raises RuntimeError on persistent failure.
    """
    import tempfile

    def _do_commit():
        # Read existing tree for attachments branch
        parent_result = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{_ATTACHMENTS_BRANCH}"],
            capture_output=True, text=True, cwd=str(cache_dir),
        )
        parent_sha = parent_result.stdout.strip() if parent_result.returncode == 0 else None

        # Get the current tree of the parent commit (or start empty)
        if parent_sha:
            tree_result = subprocess.run(
                ["git", "cat-file", "-p", parent_sha],
                capture_output=True, text=True, cwd=str(cache_dir),
            )
            parent_tree_sha = None
            for line in tree_result.stdout.splitlines():
                if line.startswith("tree "):
                    parent_tree_sha = line.split()[1]
                    break
        else:
            parent_tree_sha = None

        # For each file: hash-object it, then add via update-index
        # We use a temporary index file to build the new tree on top of the parent
        import tempfile as _tf
        idx_file = _tf.NamedTemporaryFile(delete=False, suffix=".idx")
        idx_file.close()
        idx_path = idx_file.name
        try:
            env = {"GIT_INDEX_FILE": idx_path, "HOME": str(Path.home())}
            # Initialize index from parent tree if available
            if parent_tree_sha:
                subprocess.run(
                    ["git", "read-tree", parent_tree_sha],
                    capture_output=True, text=True, cwd=str(cache_dir),
                    env={**env, "GIT_DIR": str(cache_dir)},
                )

            for fname, content in file_data:
                dest_path = f"references/issue-{issue_number}/{fname}"
                # Hash the object
                hash_proc = subprocess.run(
                    ["git", "hash-object", "-w", "--stdin"],
                    input=content, capture_output=True,
                    cwd=str(cache_dir),
                )
                if hash_proc.returncode != 0:
                    raise RuntimeError(f"hash-object failed for {fname}")
                blob_sha = hash_proc.stdout.strip().decode()

                # Add to index
                subprocess.run(
                    ["git", "update-index", "--add", "--cacheinfo",
                     f"100644,{blob_sha},{dest_path}"],
                    capture_output=True, text=True, cwd=str(cache_dir),
                    env={**env, "GIT_DIR": str(cache_dir)},
                    check=True,
                )

            # Write the tree
            write_result = subprocess.run(
                ["git", "write-tree"],
                capture_output=True, text=True, cwd=str(cache_dir),
                env={**env, "GIT_DIR": str(cache_dir)},
            )
            new_tree_sha = write_result.stdout.strip()

            # Create the commit object
            commit_cmd = ["git", "commit-tree", new_tree_sha,
                          "-m", f"chore(attachments): add file for issue #{issue_number}"]
            if parent_sha:
                commit_cmd += ["-p", parent_sha]
            commit_result = subprocess.run(
                commit_cmd,
                capture_output=True, text=True, cwd=str(cache_dir),
            )
            new_commit_sha = commit_result.stdout.strip()

            # Update the ref
            subprocess.run(
                ["git", "update-ref", f"refs/heads/{_ATTACHMENTS_BRANCH}", new_commit_sha],
                capture_output=True, text=True, cwd=str(cache_dir), check=True,
            )
        finally:
            try:
                Path(idx_path).unlink()
            except Exception:
                pass

        return new_commit_sha

    new_sha = _do_commit()

    # Push to remote
    push_result = subprocess.run(
        ["git", "push", "origin", f"refs/heads/{_ATTACHMENTS_BRANCH}:refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if push_result.returncode == 0:
        return

    # Push failed — retry once with fresh fetch + rebase
    subprocess.run(
        ["git", "fetch", "origin", _ATTACHMENTS_BRANCH],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    # Update FETCH_HEAD into the local branch and redo commit on top
    # Reset local branch to remote
    subprocess.run(
        ["git", "update-ref", f"refs/heads/{_ATTACHMENTS_BRANCH}",
         f"refs/remotes/origin/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    # Redo the commit on the new base
    _do_commit()
    retry_push = subprocess.run(
        ["git", "push", "origin", f"refs/heads/{_ATTACHMENTS_BRANCH}:refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if retry_push.returncode != 0:
        raise RuntimeError(
            f"Push to attachments branch failed after retry: {retry_push.stderr.strip()}"
        )


def _parse_ba_draft(output: str) -> tuple[str, str]:
    """Extract title and body from BA agent JSON output."""
    # Strip markdown code fence if present
    clean = re.sub(r"^```(?:json)?\s*", "", output.strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s*```\s*$", "", clean.strip(), flags=re.MULTILINE)
    clean = clean.strip()

    try:
        data = json.loads(clean)
        return str(data.get("title", "")), str(data.get("body", ""))
    except json.JSONDecodeError:
        pass

    # Find outermost {...} block
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(clean[start : end + 1])
            return str(data.get("title", "")), str(data.get("body", ""))
        except json.JSONDecodeError:
            pass

    first_line = output.split("\n")[0].strip()[:80]
    return first_line or "Draft Ticket", output


@app.post("/api/tickets/draft")
async def create_ticket_draft(
    description: str = Form(...),
    project: str = Form(default=""),
    sprint_label: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
):
    description = description.strip()
    if not description:
        raise HTTPException(400, detail="Description is required")

    draft_id = str(uuid.uuid4())
    upload_dir = _DRAFT_UPLOAD_DIR / draft_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in _ALLOWED_UPLOAD_EXTS:
            continue
        dest = upload_dir / f.filename
        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))

    file_list = (
        "\n".join(f"  - {p}" for p in saved_paths) if saved_paths else "  (none)"
    )
    prompt = (
        "You are a BA (Business Analyst) agent writing a GitHub issue.\n\n"
        f"User description: {description}\n\n"
        "Write a complete GitHub issue with these sections:\n"
        "  - Title (short, imperative, 5-10 words)\n"
        "  - ## What & Why (1-3 sentences)\n"
        "  - ## Acceptance Criteria (checkbox list, specific and testable)\n"
        "  - ## UAT Test Steps (numbered, each with Expected: line)\n"
        "  - ## Out of Scope (brief list)\n\n"
        f"Reference files — read them and incorporate relevant details:\n{file_list}\n\n"
        'Output ONLY valid JSON with exactly two string fields: "title" and "body".\n'
        "The body field must be GitHub-flavored markdown. No text outside the JSON."
    )

    cmd = [
        "claude",
        "--model", "claude-sonnet-4-6",
        "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(upload_dir),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, detail="BA agent timed out after 180s")
    except FileNotFoundError:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(503, detail="claude CLI not found")

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise HTTPException(502, detail=f"BA agent failed: {err[:300]}")

    output = stdout.decode("utf-8", errors="replace").strip()
    title, body = _parse_ba_draft(output)
    return {"draft_id": draft_id, "title": title, "body": body}


class CreateTicketBody(BaseModel):
    draft_id: str = ""
    title: str
    body: str = ""
    project: str = ""
    sprint_label: str = ""
    extra_labels: list[str] = []


@app.post("/api/tickets/create", status_code=201)
async def create_ticket_from_draft(
    draft_id: str = Form(default=""),
    title: str = Form(...),
    body: str = Form(default=""),
    project: str = Form(default=""),
    sprint_label: str = Form(default=""),
    extra_labels: list[str] = Form(default=[]),
    files: list[UploadFile] = File(default=[]),
):
    title = title.strip()
    if not title:
        raise HTTPException(400, detail="Title is required")

    labels: list[str] = ["backlog"]
    if sprint_label:
        labels.append(sprint_label)
    for lbl in extra_labels:
        lbl = lbl.strip()
        if lbl and lbl not in labels:
            labels.append(lbl)

    try:
        number, url = github_client.create_issue(
            title=title,
            body=body,
            labels=labels,
            repo_name=project or None,
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(502, detail=f"gh CLI failed: {e.stderr.strip()[:300]}")

    # Handle attachments via the dedicated `attachments` branch (issue #188).
    valid_files = [f for f in files if f.filename]
    if valid_files:
        repo_name = github_client.get_repo_for_operation(project or None)

        # --- Validate extensions and sizes before reading content ---
        batch_size = 0
        file_data_raw: list[tuple[str, bytes]] = []  # (original_filename, content)
        for upload in valid_files:
            ext = Path(upload.filename).suffix.lower()
            if ext not in _ALLOWED_UPLOAD_EXTS:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' has disallowed extension '{ext}'. "
                           f"Allowed: {', '.join(sorted(_ALLOWED_UPLOAD_EXTS))}",
                )
            content = await upload.read()
            if len(content) > _MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' exceeds the 25 MB per-file limit "
                           f"({len(content) // (1024*1024)} MB).",
                )
            batch_size += len(content)
            if batch_size > _MAX_BATCH_SIZE_BYTES:
                raise HTTPException(
                    422,
                    detail=f"Upload batch exceeds the 50 MB total limit.",
                )
            file_data_raw.append((upload.filename, content))

        # --- Ensure attachments branch exists on remote ---
        try:
            _ensure_attachments_branch(repo_name)
        except Exception as e:
            # Non-fatal: log and skip attachments
            import logging
            logging.warning(f"Could not ensure attachments branch: {e}")
            file_data_raw = []

        if file_data_raw:
            # --- Initialize or refresh the bare-clone cache ---
            try:
                cache_dir = _init_attachment_cache(repo_name)
            except Exception as e:
                import logging
                logging.warning(f"Could not initialize attachment cache: {e}")
                cache_dir = None

            if cache_dir:
                # --- Sanitize filenames and resolve collisions ---
                existing = _list_existing_attachments(cache_dir, number)
                file_data: list[tuple[str, bytes]] = []
                used_names: set[str] = set(existing)
                for orig_name, content in file_data_raw:
                    sanitized = _sanitize_filename(orig_name)
                    final_name = _resolve_collision(sanitized, used_names)
                    used_names.add(final_name)
                    file_data.append((final_name, content))

                # --- Commit ONE batch, then append Attachments section ---
                push_error: str | None = None
                try:
                    _commit_attachments_to_branch(cache_dir, number, file_data)
                except RuntimeError as e:
                    push_error = str(e)

                if push_error is None:
                    # Build raw.githubusercontent.com links
                    owner_repo = repo_name  # e.g. "zealchaiwut/commander"
                    links = "\n".join(
                        f"- [{fname}](https://raw.githubusercontent.com/{owner_repo}/"
                        f"{_ATTACHMENTS_BRANCH}/references/issue-{number}/{fname})"
                        for fname, _ in file_data
                    )
                    # Only append if not already present (idempotent retry)
                    if "## Attachments" not in body:
                        updated_body = body + f"\n\n## Attachments\n\n{links}"
                    else:
                        updated_body = body
                    github_client.update_issue_body(number, updated_body, repo_name=repo_name)
                # If push_error is set, skip the Attachments section — issue already created.

    # Clean up temp draft upload directory.
    if draft_id:
        upload_dir = _DRAFT_UPLOAD_DIR / draft_id
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)

    github_client.invalidate(f"open_issues_body:")
    github_client.invalidate(f"open_issues:")
    github_client.invalidate(f"issues:")
    return {"number": number, "url": url}


# ── Bulk Ticket Creation (issue #189) ─────────────────────────────────────────

# Configurable BA timeout per ticket (seconds)
_BULK_BA_TIMEOUT = int(os.environ.get("BULK_CREATE_BA_TIMEOUT_SEC", "90"))

# Job storage: in-memory + persisted snapshots
_bulk_jobs: dict[str, dict] = {}  # job_id -> job state
_bulk_job_queues: dict[str, asyncio.Queue] = {}  # job_id -> SSE event queue list
_BULK_JOBS_DIR = Path(__file__).parent / "runtime" / "bulk-jobs"

_ALLOWED_CONCURRENCY = {1, 3, 5}
_MAX_BULK_PROMPTS = 25


def _bulk_jobs_dir() -> Path:
    """Return the .commander/bulk-jobs directory, creating it if needed."""
    # Try to find .commander relative to repo root
    try:
        root = _repo_root()
        commander_dir = root / ".commander" / "bulk-jobs"
    except RuntimeError:
        commander_dir = _BULK_JOBS_DIR
    commander_dir.mkdir(parents=True, exist_ok=True)
    return commander_dir


def _persist_bulk_job(job: dict) -> None:
    """Persist job state snapshot to disk."""
    try:
        jobs_dir = _bulk_jobs_dir()
        path = jobs_dir / f"{job['job_id']}.json"
        path.write_text(json.dumps(job, default=str))
    except Exception:
        pass


def _bulk_job_created_at(job: dict) -> float:
    """Return created_at as epoch float for age comparison."""
    try:
        ts = job.get("created_at", "")
        if ts:
            from datetime import timezone as _tz
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    return time.time()


def _prune_old_bulk_jobs() -> None:
    """Remove job snapshots older than 24 hours from disk and memory."""
    cutoff = time.time() - 86400
    try:
        jobs_dir = _bulk_jobs_dir()
        for p in jobs_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                age = _bulk_job_created_at(data)
                if age < cutoff:
                    p.unlink(missing_ok=True)
                    _bulk_jobs.pop(data.get("job_id", ""), None)
            except Exception:
                pass
    except Exception:
        pass
    # Also prune from memory
    stale = [jid for jid, j in _bulk_jobs.items() if _bulk_job_created_at(j) < cutoff]
    for jid in stale:
        _bulk_jobs.pop(jid, None)


async def _broadcast_bulk_event(job_id: str, event: dict) -> None:
    """Send a job update event to all SSE subscribers for this job."""
    queues = _bulk_job_queues.get(job_id, [])
    payload = json.dumps(event)
    for q in list(queues):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


async def _run_single_ba_ticket(
    job_id: str,
    index: int,
    prompt: str,
    repo: str,
    default_labels: list[str],
) -> None:
    """Run BA polish for a single ticket and update job state. Does NOT create the issue."""
    job = _bulk_jobs.get(job_id)
    if not job:
        return

    ticket = job["tickets"][index]
    # Check if skipped before starting
    if ticket["state"] == "skipped":
        return

    # Transition to drafting
    ticket["state"] = "drafting"
    ticket["started_at"] = datetime.now(timezone.utc).isoformat()
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    prompt_text = (
        "You are a BA (Business Analyst) agent writing a GitHub issue.\n\n"
        f"User description: {prompt}\n\n"
        "Write a complete GitHub issue with these sections:\n"
        "  - Title (short, imperative, 5-10 words)\n"
        "  - ## What & Why (1-3 sentences)\n"
        "  - ## Acceptance Criteria (checkbox list, specific and testable)\n"
        "  - ## UAT Test Steps (numbered, each with Expected: line)\n"
        "  - ## Out of Scope (brief list)\n\n"
        'Output ONLY valid JSON with exactly two string fields: "title" and "body".\n'
        "The body field must be GitHub-flavored markdown. No text outside the JSON."
    )

    cmd = [
        "claude",
        "--model", "claude-sonnet-4-6",
        "--dangerously-skip-permissions",
        "-p", prompt_text,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=float(_BULK_BA_TIMEOUT)
            )
        except asyncio.TimeoutError:
            proc.kill()
            ticket["state"] = "failed"
            ticket["error"] = f"BA polish timed out after {_BULK_BA_TIMEOUT}s"
            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            return
    except FileNotFoundError:
        ticket["state"] = "failed"
        ticket["error"] = "claude CLI not found on server"
        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()[:300]
        ticket["state"] = "failed"
        ticket["error"] = f"BA agent failed: {err}"
        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    output = stdout.decode("utf-8", errors="replace").strip()
    title, body = _parse_ba_draft(output)

    # Store draft result — issue creation happens in-order via the flusher
    ticket["title"] = title
    ticket["body"] = body
    ticket["state"] = "draft_ready"  # internal state — flusher picks up from here
    ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
    ticket["_default_labels"] = default_labels
    ticket["_repo"] = repo
    _persist_bulk_job(job)
    # Don't broadcast yet — flusher will broadcast after creating the issue


async def _bulk_flusher(job_id: str) -> None:
    """Flush completed drafts to GitHub in original index order."""
    job = _bulk_jobs.get(job_id)
    if not job:
        return

    tickets = job["tickets"]
    n = len(tickets)
    flush_idx = 0

    while flush_idx < n:
        job = _bulk_jobs.get(job_id)
        if not job:
            return

        # Check if job is stopped
        if job.get("stop_requested") and job.get("status") == "stopped":
            break

        ticket = job["tickets"][flush_idx]

        if ticket["state"] in ("skipped", "failed"):
            flush_idx += 1
            continue

        if ticket["state"] == "draft_ready":
            # Create the issue
            try:
                labels = ["backlog"] + ticket.get("_default_labels", [])
                number, url = github_client.create_issue(
                    title=ticket["title"],
                    body=ticket["body"],
                    labels=labels,
                    repo_name=ticket.get("_repo") or None,
                )
                ticket["state"] = "created"
                ticket["issue_num"] = number
                ticket["issue_url"] = url
                ticket["body_preview"] = ticket["body"][:200]
                ticket["label_pills"] = labels
            except Exception as e:
                ticket["state"] = "failed"
                ticket["error"] = f"GitHub issue creation failed: {str(e)[:200]}"

            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            # Remove internal fields
            ticket.pop("_default_labels", None)
            ticket.pop("_repo", None)
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            flush_idx += 1

        elif ticket["state"] in ("pending", "drafting"):
            # Not ready yet — wait a bit
            await asyncio.sleep(0.5)

        else:
            flush_idx += 1

    # Check if all done
    job = _bulk_jobs.get(job_id)
    if job:
        all_done = all(
            t["state"] in ("created", "failed", "skipped") for t in job["tickets"]
        )
        if all_done:
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})


async def _bulk_worker(
    job_id: str,
    semaphore: asyncio.Semaphore,
    queue: asyncio.Queue,
    repo: str,
    default_labels: list[str],
) -> None:
    """Worker coroutine: pull tickets from queue and run BA on each."""
    while True:
        try:
            index = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        job = _bulk_jobs.get(job_id)
        if not job:
            break

        ticket = job["tickets"][index]
        if ticket["state"] == "skipped":
            queue.task_done()
            continue

        # Check if stop was requested
        if job.get("stop_requested"):
            ticket["state"] = "skipped"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            queue.task_done()
            continue

        async with semaphore:
            await _run_single_ba_ticket(job_id, index, ticket["prompt"], repo, default_labels)
        queue.task_done()


async def _run_bulk_job(job_id: str) -> None:
    """Main bulk job orchestrator: runs workers + flusher concurrently."""
    job = _bulk_jobs.get(job_id)
    if not job:
        return

    concurrency = job["concurrency"]
    repo = job["repo"]
    default_labels = job["default_labels"]
    tickets = job["tickets"]

    semaphore = asyncio.Semaphore(concurrency)
    work_queue: asyncio.Queue = asyncio.Queue()

    for t in tickets:
        if t["state"] == "pending":
            work_queue.put_nowait(t["index"])

    # Launch workers
    workers = [
        asyncio.create_task(
            _bulk_worker(job_id, semaphore, work_queue, repo, default_labels)
        )
        for _ in range(min(concurrency, len(tickets)))
    ]

    # Launch flusher
    flusher = asyncio.create_task(_bulk_flusher(job_id))

    # Wait for all workers to finish
    await asyncio.gather(*workers, return_exceptions=True)

    # Signal stop if needed — mark remaining pending as skipped
    job = _bulk_jobs.get(job_id)
    if job and job.get("stop_requested"):
        for t in job["tickets"]:
            if t["state"] in ("pending", "draft_ready"):
                t["state"] = "skipped"
                await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
        job["status"] = "stopped"
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})

    # Wait for flusher to finish
    await flusher

    # Final status update
    job = _bulk_jobs.get(job_id)
    if job and job.get("status") not in ("done", "stopped"):
        job["status"] = "done"
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})


class BulkCreateBody(BaseModel):
    repo: str
    default_labels: list[str] = []
    prompts: list[str]
    concurrency: int = 3


@app.post("/api/tickets/bulk", status_code=202)
async def bulk_create_start(body: BulkCreateBody):
    """Start a bulk ticket creation job.

    Returns {job_id} immediately; use the SSE stream endpoint to track progress.
    """
    _prune_old_bulk_jobs()

    # Validate repo
    projects = projects_module.load_projects()
    if not any(p["repo"] == body.repo for p in projects):
        raise HTTPException(422, detail=f"Repo '{body.repo}' is not a configured project")

    # Validate concurrency
    if body.concurrency not in _ALLOWED_CONCURRENCY:
        raise HTTPException(422, detail=f"Concurrency must be one of {sorted(_ALLOWED_CONCURRENCY)}")

    # Filter blank prompts
    clean_prompts = [p.strip() for p in body.prompts if p.strip()]
    if not clean_prompts:
        raise HTTPException(422, detail="Batch must contain at least one non-blank prompt")
    if len(clean_prompts) > _MAX_BULK_PROMPTS:
        raise HTTPException(
            422,
            detail=f"Batch limit is {_MAX_BULK_PROMPTS} prompts (got {len(clean_prompts)})"
        )

    # Validate default_labels — each must already exist in the repo
    if body.default_labels:
        existing_labels = {lbl["name"] for lbl in github_client.list_labels(repo_name=body.repo)}
        bad = [lbl for lbl in body.default_labels if lbl not in existing_labels]
        if bad:
            raise HTTPException(
                422,
                detail=f"Unknown labels (not in repo): {', '.join(bad)}"
            )

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    tickets = [
        {
            "index": i,
            "prompt": prompt,
            "state": "pending",
            "title": None,
            "body": None,
            "body_preview": None,
            "issue_num": None,
            "issue_url": None,
            "label_pills": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }
        for i, prompt in enumerate(clean_prompts)
    ]

    job = {
        "job_id": job_id,
        "status": "running",
        "repo": body.repo,
        "default_labels": body.default_labels,
        "concurrency": body.concurrency,
        "created_at": now,
        "stop_requested": False,
        "tickets": tickets,
    }
    _bulk_jobs[job_id] = job
    _bulk_job_queues[job_id] = []
    _persist_bulk_job(job)

    # Fire off the job in the background
    asyncio.create_task(_run_bulk_job(job_id))

    return {"job_id": job_id}


@app.get("/api/tickets/bulk/{job_id}")
async def bulk_get_job(job_id: str):
    """Return the current state of a bulk job."""
    job = _bulk_jobs.get(job_id)
    if not job:
        # Try to load from disk
        try:
            path = _bulk_jobs_dir() / f"{job_id}.json"
            if path.exists():
                job = json.loads(path.read_text())
                _bulk_jobs[job_id] = job
        except Exception:
            pass
    if not job:
        raise HTTPException(404, detail="Job not found")
    # Strip internal fields from response
    tickets = [
        {k: v for k, v in t.items() if not k.startswith("_")}
        for t in job["tickets"]
    ]
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "concurrency": job["concurrency"],
        "tickets": tickets,
    }


@app.get("/api/tickets/bulk/{job_id}/stream")
async def bulk_job_stream(job_id: str, request: Request):
    """SSE stream of state-change events for a bulk job."""
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    # Register this subscriber
    if job_id not in _bulk_job_queues:
        _bulk_job_queues[job_id] = []
    _bulk_job_queues[job_id].append(queue)

    async def generator():
        try:
            # On connect: send full current state first
            state_snapshot = await bulk_get_job(job_id)
            yield f"event: snapshot\ndata: {json.dumps(state_snapshot)}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: update\ndata: {data}\n\n"
                    # Stop streaming if job is done
                    parsed = json.loads(data)
                    if parsed.get("type") == "job_done":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                except Exception:
                    break
        finally:
            qlist = _bulk_job_queues.get(job_id, [])
            if queue in qlist:
                qlist.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class BulkStopBody(BaseModel):
    pass


@app.post("/api/tickets/bulk/{job_id}/stop")
async def bulk_stop_job(job_id: str):
    """Graceful stop: finish in-flight BA calls, mark remaining pending as skipped."""
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    job["stop_requested"] = True
    _persist_bulk_job(job)
    return {"ok": True}


class BulkSkipBody(BaseModel):
    index: int


@app.post("/api/tickets/bulk/{job_id}/skip")
async def bulk_skip_ticket(job_id: str, body: BulkSkipBody):
    """Mark a pending ticket as skipped (no-op if already past pending)."""
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] == "pending":
        ticket["state"] = "skipped"
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
    return {"ok": True, "state": ticket["state"]}


class BulkRetryBody(BaseModel):
    index: int


@app.post("/api/tickets/bulk/{job_id}/retry")
async def bulk_retry_ticket(job_id: str, body: BulkRetryBody):
    """Re-queue a failed ticket for retry."""
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] != "failed":
        return {"ok": True, "state": ticket["state"]}

    # Reset to pending and re-run as a single task
    ticket["state"] = "pending"
    ticket["error"] = None
    ticket["started_at"] = None
    ticket["finished_at"] = None
    job["status"] = "running"
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _retry_task():
        await _run_single_ba_ticket(
            job_id, body.index, ticket["prompt"],
            job["repo"], job["default_labels"]
        )
        # After BA, trigger flusher to create the issue (best-effort, appended order)
        t = job["tickets"][body.index]
        if t.get("state") == "draft_ready":
            try:
                labels = ["backlog"] + t.get("_default_labels", [])
                number, url = github_client.create_issue(
                    title=t["title"],
                    body=t["body"],
                    labels=labels,
                    repo_name=t.get("_repo") or None,
                )
                t["state"] = "created"
                t["issue_num"] = number
                t["issue_url"] = url
                t["body_preview"] = t["body"][:200]
                t["label_pills"] = labels
            except Exception as e:
                t["state"] = "failed"
                t["error"] = f"GitHub issue creation failed: {str(e)[:200]}"
            t["finished_at"] = datetime.now(timezone.utc).isoformat()
            t.pop("_default_labels", None)
            t.pop("_repo", None)
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

        # Update job status
        all_done = all(
            tt["state"] in ("created", "failed", "skipped") for tt in job["tickets"]
        )
        if all_done:
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})

    asyncio.create_task(_retry_task())
    return {"ok": True}


# ── Startup: mark any in-flight jobs as failed (best-effort) ─────────────────

@app.on_event("startup")
async def _mark_inflight_jobs_failed():
    """On restart, mark any previously-running jobs as failed (state lost)."""
    try:
        jobs_dir = _bulk_jobs_dir()
        for p in jobs_dir.glob("*.json"):
            try:
                job = json.loads(p.read_text())
                if job.get("status") == "running":
                    job["status"] = "failed"
                    for t in job.get("tickets", []):
                        if t["state"] in ("pending", "drafting", "draft_ready"):
                            t["state"] = "failed"
                            t["error"] = "Server restarted — job state lost"
                    p.write_text(json.dumps(job, default=str))
            except Exception:
                pass
    except Exception:
        pass


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
