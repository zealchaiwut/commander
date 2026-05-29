import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
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
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, model_validator

# Load .env before importing local modules so that DB_PATH and other env vars
# are available when db.py executes its module-level startup checks.
load_dotenv(Path(__file__).parent / ".env")

import db
import github_client
import projects as projects_module

# Backup module lives in services/sprint_manager/ — add it to sys.path
import sys as _sys
_SERVICES_DIR = Path(__file__).parent.parent.parent / "services" / "sprint_manager"
if str(_SERVICES_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SERVICES_DIR))
try:
    import backup as _backup_module
    _BACKUP_AVAILABLE = True
except ImportError:
    _backup_module = None  # type: ignore[assignment]
    _BACKUP_AVAILABLE = False

try:
    import sprint_repo as _sprint_repo
    _SPRINT_REPO_AVAILABLE = True
except Exception:
    _sprint_repo = None  # type: ignore[assignment]
    _SPRINT_REPO_AVAILABLE = False

try:
    import sync_projects_to_neon as _sync_projects_module
    _SYNC_PROJECTS_AVAILABLE = True
except Exception:
    _sync_projects_module = None  # type: ignore[assignment]
    _SYNC_PROJECTS_AVAILABLE = False


def _sprint_json_path(project_root: Path, sprint_label: str) -> Path:
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}.json"


def _sprint_json_write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError as _e:
        print(f"[sprint-json] WARNING: could not write {path}: {_e}")


def _sprint_json_read(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

STATIC_DIR = Path(__file__).parent / "static"
ENVIRONMENT = os.environ.get("ENVIRONMENT", "prd").lower()

# Configurable via .env: how long (seconds) a 'working' agent can be silent before
# it is marked 'timed_out'.  Default: 300 s (5 minutes).
AGENT_IDLE_TIMEOUT_SECONDS: int = int(os.environ.get("AGENT_IDLE_TIMEOUT_SECONDS", "300"))
_TIMEOUT_CHECK_INTERVAL: int = 60  # run the check every 60 seconds

_subscribers: list[asyncio.Queue] = []
_start_time: float = 0.0


# ── Git startup metadata (issue #329) ─────────────────────────────────────────
# Captured once at process start; never re-runs git per request.

def _capture_git_value(cmd: list) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


_GIT_SHA: str = _capture_git_value(["git", "rev-parse", "--short", "HEAD"])
_GIT_BRANCH: str = _capture_git_value(["git", "rev-parse", "--abbrev-ref", "HEAD"])
_STARTED_AT: str = datetime.now(timezone.utc).isoformat()


# ── Build hash (cache-busting) ─────────────────────────────────────────────────

def _compute_build_hash() -> str:
    """Compute an 8-char MD5 hash over all JS and CSS files in STATIC_DIR.

    The hash changes whenever any local static asset changes, which lets
    browsers cache assets indefinitely while always loading fresh code after
    a deploy/restart.
    """
    h = hashlib.md5()
    for ext in ("*.js", "*.css"):
        for f in sorted(STATIC_DIR.glob(ext)):
            try:
                h.update(f.read_bytes())
            except OSError:
                pass
    return h.hexdigest()[:8]


_BUILD_HASH: str = _compute_build_hash()
_APP_VERSION: str = "1.0"


def _inject_version_into_html(html: str) -> str:
    """Inject ?v=<hash> query string on local /static/*.js and /static/*.css URLs."""
    # Replace src="/static/foo.js" → src="/static/foo.js?v=<hash>"
    # Replace href="/static/foo.css" → href="/static/foo.css?v=<hash>"
    # Skip URLs that already have a query string.
    pattern = r'((?:src|href)="(/static/[^"?]+\.(?:js|css))")'
    replacement = rf'\g<2>?v={_BUILD_HASH}'

    def _replacer(m: re.Match) -> str:
        attr_name = m.group(1).split("=")[0]  # src or href
        url = m.group(2)
        return f'{attr_name}="{url}?v={_BUILD_HASH}"'

    return re.sub(pattern, _replacer, html)


_HTML_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, must-revalidate",
    "Pragma": "no-cache",
}


def _serve_html(path: Path) -> HTMLResponse:
    """Read an HTML file, inject cache-busting version stamps, and serve with no-cache headers."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=404, detail="Not found")
    content = _inject_version_into_html(content)
    return HTMLResponse(content=content, headers=_HTML_NO_CACHE_HEADERS)


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
    scanned = 0
    cleaned = 0
    try:
        projects = projects_module.load_projects()
    except Exception as exc:
        print(f"[startup-sweep] could not load projects: {exc}")
        elapsed_ms = (time.monotonic() - sweep_start) * 1000
        print(f"[startup-sweep] scanned {scanned} PID files, cleaned {cleaned} orphans in {elapsed_ms:.1f}ms")
        return

    for proj in projects:
        try:
            project_root = _project_root_path(proj["repo"])
            sprints_dir = _commander_dir(project_root) / "sprints"
            if not sprints_dir.exists():
                continue
            for pid_file in sprints_dir.glob("*-pid"):
                scanned += 1
                sprint_label = pid_file.name.removesuffix("-pid")  # e.g. "sprint-9"
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                except (ValueError, OSError):
                    # Unreadable/corrupt PID file — remove it.
                    try:
                        pid_file.unlink()
                    except OSError:
                        pass
                    cleaned += 1
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
                    cleaned += 1
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
                    cleaned += 1
                    print(
                        f"[startup-sweep] cleaned orphan PID file {pid_file}"
                        f" (PID {pid} reused by unrelated process)"
                    )
        except Exception as exc:
            print(f"[startup-sweep] error scanning project {proj.get('repo')}: {exc}")

    elapsed_ms = (time.monotonic() - sweep_start) * 1000
    print(f"[startup-sweep] scanned {scanned} PID files, cleaned {cleaned} orphans in {elapsed_ms:.1f}ms")


def _sprint_status_file_path(project: str, sprint_label: str) -> Optional[Path]:
    """Return the path to the persisted sprint-status JSON file for a project/label.

    Returns None when project is empty (status cannot be attributed to a project).
    Location: <project-root>/.commander/sprints/<label>-status.json
    """
    if not project:
        return None
    project_root = _project_root_path(project)
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}-status.json"


def _restore_sprint_statuses_on_startup() -> None:
    """On startup, reload persisted sprint-status payloads for any sprints still running.

    For each project, scans .commander/sprints/*-status.json files.  If the
    corresponding sprint PID is still alive the payload is loaded into the
    in-memory _sprint_statuses dict — the dashboard resumes tracking without a
    gap.  Stale status files (process dead) are skipped and logged.

    Logs one line per file indicating whether we re-attached or skipped.
    Uses a file-level lock (os.O_EXCL create of a .lock file) to prevent two
    server instances from running the restore simultaneously.
    """
    global _sprint_statuses
    try:
        projects = projects_module.load_projects()
    except Exception as exc:
        print(f"[startup-restore] could not load projects: {exc}")
        return

    attached = 0
    skipped  = 0

    for proj in projects:
        try:
            project_root = _project_root_path(proj["repo"])
            sprints_dir  = _commander_dir(project_root) / "sprints"
            if not sprints_dir.exists():
                continue

            for status_file in sprints_dir.glob("*-status.json"):
                sprint_label = status_file.name.removesuffix("-status.json")

                # Check whether the sprint process is still alive.
                if not _is_sprint_running(project_root, sprint_label):
                    print(
                        f"[startup-restore] skipped {status_file.name}"
                        f" — sprint '{sprint_label}' process no longer running"
                    )
                    skipped += 1
                    continue

                # Process is alive — load the status payload.
                try:
                    raw = status_file.read_text(encoding="utf-8")
                    payload = json.loads(raw)
                except (OSError, json.JSONDecodeError) as exc:
                    print(
                        f"[startup-restore] could not read {status_file.name}: {exc}"
                    )
                    skipped += 1
                    continue

                key = (proj["repo"], sprint_label)
                _sprint_statuses[key] = payload
                print(
                    f"[startup-restore] re-attached to running sprint"
                    f" '{sprint_label}' on {proj['repo']}"
                )
                attached += 1

        except Exception as exc:
            print(f"[startup-restore] error scanning project {proj.get('repo')}: {exc}")

    print(
        f"[startup-restore] completed — {attached} sprint(s) re-attached,"
        f" {skipped} skipped"
    )


def _check_repo_accessible(repo: str) -> bool:
    """Return True if `repo` (owner/repo) exists and is accessible via gh CLI.

    Uses `gh repo view --json name` which returns exit code 0 on success.
    Network errors or missing repos both produce non-zero exit codes.
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "name"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _validate_github_repos() -> None:
    """Validate GITHUB_REPO and GITHUB_ISSUE_TEST_REPO at startup.

    AC (issue #301):
    - GITHUB_REPO missing/inaccessible → sys.exit with descriptive error.
    - GITHUB_ISSUE_TEST_REPO set but missing/inaccessible → print warning, continue.
    """
    # Validate work repo (GITHUB_REPO)
    try:
        work_repo = github_client.repo()
    except ValueError as exc:
        sys.exit(f"[startup] {exc}")

    if not _check_repo_accessible(work_repo):
        sys.exit(
            f"Configured GITHUB_REPO '{work_repo}' does not exist or is inaccessible."
        )

    # Validate test repo (GITHUB_ISSUE_TEST_REPO) — warning only, never fatal
    test_repo = os.environ.get("GITHUB_ISSUE_TEST_REPO", "").strip()
    if test_repo and not _check_repo_accessible(test_repo):
        print(
            f"GITHUB_ISSUE_TEST_REPO '{test_repo}' is set but does not exist or is "
            f"inaccessible — tester issue/label tests will be skipped.",
            flush=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    db.init_db()
    _validate_github_repos()
    _sweep_orphan_pid_files()
    _restore_sprint_statuses_on_startup()
    # Sync projects.json → Neon (non-blocking; warn on failure, never fatal)
    if _SYNC_PROJECTS_AVAILABLE:
        try:
            _result = _sync_projects_module.sync_projects_to_neon()
            logger.info("projects sync complete: %s", _result)
        except Exception as _exc:
            logger.warning("projects sync failed (non-fatal): %s", _exc)

    # Start backup scheduler and queue a startup backup after 30 s
    if _BACKUP_AVAILABLE:
        try:
            _backup_module.start_backup_scheduler()
            _backup_module.schedule_startup_backup(delay_seconds=30)
        except Exception:
            pass  # backup failures never affect server startup
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

logger = logging.getLogger(__name__)


# ── API no-cache middleware (issue #249) ──────────────────────────────────────
# Ensure all /api/* responses carry Cache-Control: no-cache so browsers and
# proxies never serve stale API data on auto-refresh or manual refresh.

@app.middleware("http")
async def add_api_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


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


# ── Health check v2 (issue #229) ─────────────────────────────────────────────
# Global cache: (timestamp, response_dict)
_health_cache: tuple[float, dict] | None = None
_HEALTH_CACHE_TTL = 10.0  # seconds
_GITHUB_AUTH_CACHE: tuple[float, dict] | None = None
_GITHUB_AUTH_CACHE_TTL = 60.0  # seconds
_CHECK_TIMEOUT = 0.5  # 500 ms per individual check
_HEALTH_TOTAL_TIMEOUT = 2.0  # 2 s overall


async def _check_dashboard() -> dict:
    uptime = time.monotonic() - _start_time
    return {"status": "ok", "uptime_sec": int(uptime)}


async def _check_database() -> dict:
    try:
        loop = asyncio.get_event_loop()
        def _run():
            conn = db.get_conn()
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
        await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=_CHECK_TIMEOUT)
        return {"status": "ok"}
    except asyncio.TimeoutError:
        return {"status": "timeout"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


async def _check_github_auth() -> dict:
    global _GITHUB_AUTH_CACHE
    now = time.monotonic()
    if _GITHUB_AUTH_CACHE is not None:
        ts, cached = _GITHUB_AUTH_CACHE
        if now - ts < _GITHUB_AUTH_CACHE_TTL:
            return cached

    async def _run() -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "auth", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            combined = (stdout + stderr).decode("utf-8", errors="replace")
            if proc.returncode == 0:
                # Parse logged-in user from output like "Logged in to github.com as <user>"
                import re as _re
                m = _re.search(r"Logged in to \S+ as (\S+)", combined)
                user = m.group(1) if m else None
                result = {"status": "ok"}
                if user:
                    result["user"] = user
                return result
            # Non-zero exit — determine if expired or missing
            combined_lower = combined.lower()
            if "expired" in combined_lower or "token" in combined_lower:
                return {"status": "expired"}
            return {"status": "missing"}
        except FileNotFoundError:
            return {"status": "missing"}
        except Exception as exc:
            return {"status": "missing", "error": str(exc)}

    try:
        result = await asyncio.wait_for(_run(), timeout=_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        result = {"status": "timeout"}

    _GITHUB_AUTH_CACHE = (now, result)
    return result


async def _check_claude_code_auth() -> dict:
    credentials_path = Path.home() / ".claude" / "credentials.json"
    if not credentials_path.exists():
        # Try running `claude --version` as fallback
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "claude", "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=_CHECK_TIMEOUT,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return {"status": "ok"}
            return {"status": "expired"}
        except asyncio.TimeoutError:
            return {"status": "timeout"}
        except FileNotFoundError:
            return {"status": "missing"}
        except Exception:
            return {"status": "missing"}

    # credentials.json exists — check it's valid JSON and non-empty
    try:
        data = json.loads(credentials_path.read_text(encoding="utf-8"))
        if not data:
            return {"status": "expired"}
        return {"status": "ok"}
    except Exception:
        return {"status": "expired"}


async def _check_disk() -> dict:
    try:
        loop = asyncio.get_event_loop()
        usage = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: shutil.disk_usage(Path(__file__).parent)),
            timeout=_CHECK_TIMEOUT,
        )
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        if free_gb < 2.0:
            status = "critical"
        elif free_gb < 10.0:
            status = "warn"
        else:
            status = "ok"
        return {
            "status": status,
            "free_gb": round(free_gb, 2),
            "total_gb": round(total_gb, 2),
        }
    except asyncio.TimeoutError:
        return {"status": "timeout"}
    except Exception as exc:
        return {"status": "warn", "error": str(exc)}


async def _check_stuck_sprints() -> dict:
    """Check for sprints in 'running' state whose state file mtime is > 2 hours old."""
    try:
        loop = asyncio.get_event_loop()

        def _scan() -> dict:
            two_hours_ago = time.time() - 7200
            stuck_labels: list[str] = []
            try:
                projects = projects_module.load_projects()
            except Exception:
                return {"status": "ok", "count": 0, "labels": []}
            for proj in projects:
                try:
                    project_root = _project_root_path(proj["repo"])
                    sprints_dir = _commander_dir(project_root) / "sprints"
                    if not sprints_dir.exists():
                        continue
                    for state_file in sprints_dir.glob("*-state.json"):
                        try:
                            data = json.loads(state_file.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        if data.get("status") != "running":
                            continue
                        # Check mtime
                        mtime = state_file.stat().st_mtime
                        if mtime < two_hours_ago:
                            # Extract sprint label from filename: sprint-N-state.json
                            label = state_file.name.removesuffix("-state.json")
                            stuck_labels.append(label)
                except Exception:
                    continue
            if stuck_labels:
                return {"status": "warn", "count": len(stuck_labels), "labels": stuck_labels}
            return {"status": "ok", "count": 0, "labels": []}

        result = await asyncio.wait_for(
            loop.run_in_executor(None, _scan),
            timeout=_CHECK_TIMEOUT,
        )
        return result
    except asyncio.TimeoutError:
        return {"status": "timeout"}
    except Exception as exc:
        return {"status": "ok", "count": 0, "labels": [], "error": str(exc)}


# ── SSE broadcast ─────────────────────────────────────────────────────────────

async def broadcast(data: dict):
    msg = json.dumps(data)
    for q in _subscribers:
        await q.put(msg)


# ── agent endpoints ───────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return _serve_html(STATIC_DIR / "home-preview.html")


@app.get("/home")
async def home_redirect():
    return RedirectResponse(url="/", status_code=301)


@app.get("/home-preview")
async def home_preview_redirect():
    return RedirectResponse(url="/", status_code=301)


@app.get("/overview")
async def overview_redirect():
    return RedirectResponse(url="/", status_code=301)


@app.get("/diagnostics")
async def diagnostics_page():
    """Serve the system diagnostics page (issue #230)."""
    return _serve_html(STATIC_DIR / "diagnostics.html")


# ── Legacy UI route (/legacy/<slug>/<tab>) ────────────────────────────────────
# Deprecated legacy route: serves the old index.html UI.
# Emits a warning on every hit so operators can track usage before removal.
# New-ticket creation no longer navigates here — all ticket creation goes
# through the current-UI modal on /project/ pages (issue #272).

@app.get("/legacy/{path:path}")
async def legacy_project_route(path: str):
    """Serve the legacy index.html UI at /legacy/. DEPRECATED — use /project/ instead."""
    logger.warning("Deprecated /legacy/ route hit: /%s", path)
    return _serve_html(STATIC_DIR / "index.html")


# ── /projects/ redirect — 301 to current /project/ UI ─────────────────────────
# Old /projects/<slug>/<tab> bookmarks are redirected to /project/<slug>/<tab>.
# Paths that cannot be cleanly mapped (no slug/tab) go to the dashboard home.

@app.get("/projects/{path:path}")
async def projects_redirect(path: str):
    """Redirect /projects/<slug>/<tab> → /project/<slug>/<tab> (301).

    Any path that does not contain a recognisable slug/tab segment is redirected
    to the dashboard home ('/') instead of serving the legacy UI.
    """
    # Strip leading/trailing slashes and split into parts
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) >= 2:
        slug, tab = parts[0], parts[1]
        return RedirectResponse(url=f"/project/{slug}/{tab}", status_code=301)
    elif len(parts) == 1:
        slug = parts[0]
        return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=301)
    else:
        # No recognisable slug — send to dashboard home
        return RedirectResponse(url="/", status_code=301)


# ── Slug-based project routes (/project/<slug>/...) ───────────────────────────

_VALID_PROJECT_TABS = {"sprint-mgmt", "tickets", "logs"}


@app.get("/project/{slug}")
async def project_slug_no_tab(slug: str):
    """Redirect bare /project/<slug> to /project/<slug>/sprint-mgmt."""
    return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=302)


@app.get("/project/{slug}/{tab}")
async def project_slug_tab(slug: str, tab: str):
    """Serve the project chrome page for valid tabs; redirect invalid tabs to sprint-mgmt."""
    if tab not in _VALID_PROJECT_TABS:
        return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=302)
    return _serve_html(STATIC_DIR / "project.html")


@app.get("/api/health")
async def health_check():
    """GET /api/health — rich dependency health check (issue #229).

    Runs 6 checks concurrently: dashboard, database, github_auth, claude_code_auth,
    disk, stuck_sprints.  Response is cached 10 s.  Returns 200 for ok/degraded,
    503 for down.  No authentication required.
    """
    global _health_cache
    now = time.monotonic()
    if _health_cache is not None:
        ts, cached = _health_cache
        if now - ts < _HEALTH_CACHE_TTL:
            status_code = 503 if cached["status"] == "down" else 200
            return JSONResponse(content=cached, status_code=status_code)

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _check_dashboard(),
                _check_database(),
                _check_github_auth(),
                _check_claude_code_auth(),
                _check_disk(),
                _check_stuck_sprints(),
                return_exceptions=True,
            ),
            timeout=_HEALTH_TOTAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        results = [
            {"status": "timeout"},
            {"status": "timeout"},
            {"status": "timeout"},
            {"status": "timeout"},
            {"status": "timeout"},
            {"status": "timeout"},
        ]

    # Unpack results; replace any unexpected exceptions with error dicts
    def _safe(r, fallback_status: str = "down") -> dict:
        if isinstance(r, Exception):
            return {"status": fallback_status, "error": str(r)}
        return r

    checks = {
        "dashboard":        _safe(results[0], "down"),
        "database":         _safe(results[1], "down"),
        "github_auth":      _safe(results[2], "missing"),
        "claude_code_auth": _safe(results[3], "missing"),
        "disk":             _safe(results[4], "warn"),
        "stuck_sprints":    _safe(results[5], "ok"),
    }

    # Determine overall status
    # "down" if database or github_auth failed
    critical_down = checks["database"]["status"] in ("down", "timeout") or \
                    checks["github_auth"]["status"] in ("expired", "missing", "timeout")
    has_warn = any(
        c.get("status") in ("warn", "critical", "expired", "missing", "timeout")
        for c in checks.values()
    )

    if critical_down:
        overall = "down"
    elif has_warn:
        overall = "degraded"
    else:
        overall = "ok"

    response = {
        "status": overall,
        "checked_at": checked_at,
        "checks": checks,
    }
    _health_cache = (now, response)

    status_code = 503 if overall == "down" else 200
    return JSONResponse(content=response, status_code=status_code)


@app.get("/api/environment")
def get_environment():
    """Return the current runtime environment (prd or uat)."""
    return {"environment": ENVIRONMENT}


@app.get("/api/version")
def get_version():
    """Return build metadata for the running process (issue #329).

    Response shape:
    {
      "version": "1.0",
      "git_sha": "abc1234",
      "branch": "main",
      "started_at": "2026-05-29T12:00:00+00:00"
    }
    """
    return JSONResponse(
        content={
            "version": _APP_VERSION,
            "git_sha": _GIT_SHA[:7] if _GIT_SHA != "unknown" else "unknown",
            "branch": _GIT_BRANCH,
            "started_at": _STARTED_AT,
        },
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@app.get("/api/backup/status")
def get_backup_status():
    """Return the current state of the gist-based config backup.

    Response shape:
    {
      "last_backup_at": "<ISO-8601>" | null,
      "gist_id": "<id>" | null,
      "gist_url": "https://gist.github.com/..." | null,
      "file_count": <int>,
      "last_error": "<message>" | null
    }
    """
    if not _BACKUP_AVAILABLE:
        raise HTTPException(status_code=503, detail="Backup module not available")
    return _backup_module.get_backup_status()


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


@app.post("/api/issues/{issue_id}/close")
def close_issue_endpoint(issue_id: int, repo: Optional[str] = None):
    try:
        github_client.close_issue(issue_id, repo_name=repo)
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
        # Trigger a background backup after a successful projects.json write
        if _BACKUP_AVAILABLE:
            try:
                _backup_module.schedule_backup()
            except Exception:
                pass  # backup trigger failures never affect the response
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

    # Trigger a background backup after a successful projects.json write
    if _BACKUP_AVAILABLE:
        try:
            _backup_module.schedule_backup()
        except Exception:
            pass  # backup trigger failures never affect the response

    return {"ok": True, "removed": removed}


@app.post("/api/projects/sync-to-db")
async def sync_projects_to_db():
    """Trigger a manual sync of projects.json → Neon.

    Returns a JSON summary: {projects_synced, projects_skipped, envs_synced, envs_skipped, errors}.
    """
    if not _SYNC_PROJECTS_AVAILABLE:
        raise HTTPException(status_code=503, detail="sync module not available")
    try:
        return _sync_projects_module.sync_projects_to_neon()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
        sys.executable,
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

# Keyed by (project, sprint_label); populated by POST /api/sprint-status from sprint_manager.py
_sprint_statuses: dict[tuple, dict] = {}


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
def set_sprint_status(payload: SprintStatusPayload):
    global _sprint_statuses
    key = (payload.project, payload.sprint_label)
    data = payload.model_dump()
    _sprint_statuses[key] = data

    # Persist to disk so the status survives a server restart (issue #215).
    # Only persist when we have a project so we know where to write the file.
    if payload.project and payload.sprint_label:
        status_path = _sprint_status_file_path(payload.project, payload.sprint_label)
        if status_path is not None:
            try:
                status_path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic write: write to a temp file then replace to avoid partial reads.
                tmp_path = status_path.with_suffix(".json.tmp")
                tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                os.replace(str(tmp_path), str(status_path))
            except OSError as exc:
                # Non-fatal — in-memory state is still updated.
                print(f"[sprint-status] could not persist status for {payload.sprint_label}: {exc}")

    return {"ok": True}


@app.get("/api/sprint-status")
def get_sprint_status(project: Optional[str] = None):
    running = _all_sprints_running()
    if project:
        running = [r for r in running if r["project"] == project]
    result = []
    for r in running:
        key = (r["project"], r["sprint_label"])
        status = _sprint_statuses.get(key, {})
        issues = status.get("issues", [])
        closed = sum(1 for i in issues if i.get("status") in ("done", "skipped"))
        result.append({
            "project":        r["project"],
            "sprint_label":   r["sprint_label"],
            "pid":            r.get("pid"),
            "issues":         issues,
            "progress":       {"closed": closed, "total": len(issues)},
            "started_at":     status.get("start_timestamp"),
            "wall_clock_secs": status.get("wall_clock_secs", 0.0),
        })
    return {"running_sprints": result}


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


# ── home aggregated endpoint (#216) ──────────────────────────────────────────

_home_cache: dict[str, tuple[float, dict]] = {}
_HOME_CACHE_TTL = 30.0


def _home_project_data(proj: dict, running_sprints: list[dict]) -> dict:
    """Compute per-project home data, cached 30 s per project slug.

    On any GitHub fetch error, returns an idle sentinel with 0 counts so the
    overall endpoint still returns 200.
    """
    repo = proj["repo"]
    slug = repo.split("/")[-1]
    name = proj.get("name", slug)
    icon = proj.get("icon", "ti-folder")

    cache_key = f"home:{slug}"
    now = time.monotonic()
    cached = _home_cache.get(cache_key)
    if cached and now - cached[0] < _HOME_CACHE_TTL:
        return cached[1]

    def _idle() -> dict:
        sentinel: dict = {
            "name": name, "slug": slug, "icon": icon,
            "status": "idle", "uat_count": 0, "backlog_count": 0,
            "last_activity_at": None,
        }
        _home_cache[cache_key] = (now, sentinel)
        return sentinel

    try:
        all_open = github_client.list_all_open_issues(repo_name=repo)
    except Exception:
        return _idle()

    proj_running = [r for r in running_sprints if r["project"] == repo]
    sprint_running_field: dict | None = None
    if proj_running:
        r0 = proj_running[0]
        status_data = _sprint_statuses.get((r0["project"], r0["sprint_label"]), {})
        start_ts = status_data.get("start_timestamp")
        elapsed_sec = 0
        if start_ts:
            try:
                start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                elapsed_sec = int((datetime.now(timezone.utc) - start_dt).total_seconds())
            except Exception:
                pass
        sprint_running_field = {"label": r0["sprint_label"], "elapsed_sec": elapsed_sec}

    uat_issues = [i for i in all_open if any(l["name"] == "UAT" for l in i.get("labels", []))]
    backlog_issues = [i for i in all_open if github_client.classify_issue(i) == "backlog"]

    # Supplement PID-based check with Neon: if Neon says a sprint is running for
    # this project, the sidebar dot is green even if the JSON file was deleted.
    neon_running = False
    if not proj_running and _SPRINT_REPO_AVAILABLE and _sprint_repo is not None:
        try:
            neon_running = any(
                s.status == "running"
                for s in _sprint_repo.list_sprints(project=repo)
            )
        except Exception:
            pass

    if proj_running or neon_running:
        status = "running"
    elif uat_issues:
        status = "uat-pending"
    else:
        status = "idle"

    last_activity_at: str | None = None
    issue_timestamps = [i.get("updatedAt") for i in all_open if i.get("updatedAt")]
    if issue_timestamps:
        last_activity_at = max(issue_timestamps)

    last_sprint_data: dict | None = None
    seen_dirs: set[str] = set()
    for sprints_dir in [
        _commander_dir(_project_root_path(repo)) / "sprints",
        SPRINTS_DIR,
    ]:
        key_str = str(sprints_dir.resolve())
        if not sprints_dir.exists() or key_str in seen_dirs:
            continue
        seen_dirs.add(key_str)
        summary_files = sorted(
            sprints_dir.glob("*-summary-*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if summary_files and last_sprint_data is None:
            try:
                meta = _parse_summary_file(summary_files[0])
                if meta.get("date"):
                    sprint_ts = meta["date"] + "T00:00:00Z"
                    if last_activity_at is None or sprint_ts > last_activity_at:
                        last_activity_at = sprint_ts
                last_sprint_data = {
                    "sprint_num": meta.get("sprint_num"),
                    "date": meta.get("date"),
                    "status": meta.get("status"),
                }
            except Exception:
                pass

    result: dict = {
        "name": name,
        "slug": slug,
        "icon": icon,
        "status": status,
        "uat_count": len(uat_issues),
        "backlog_count": len(backlog_issues),
        "last_activity_at": last_activity_at,
    }
    if sprint_running_field is not None:
        result["sprint_running"] = sprint_running_field
    if last_sprint_data is not None:
        result["last_sprint"] = last_sprint_data

    _home_cache[cache_key] = (now, result)
    return result


def _home_activity_feed(
    all_open_by_repo: dict[str, list[dict]],
    running_sprints: list[dict],
    projects: list[dict],
) -> list[dict]:
    """Build top-5 activity events from open issues and sprint history."""
    events: list[dict] = []

    for repo, issues in all_open_by_repo.items():
        slug = repo.split("/")[-1]
        for issue in issues:
            labels = {l["name"] for l in issue.get("labels", [])}
            ts = issue.get("updatedAt") or issue.get("createdAt") or ""
            if not ts:
                continue
            if "UAT" in labels:
                events.append({
                    "type": "ticket_moved_to_uat",
                    "project": slug,
                    "title": issue.get("title", ""),
                    "sub": f"#{issue.get('number', '')}",
                    "timestamp": ts,
                    "link": issue.get("url", ""),
                })
            elif "need-rework" in labels:
                events.append({
                    "type": "ticket_needs_rework",
                    "project": slug,
                    "title": issue.get("title", ""),
                    "sub": f"#{issue.get('number', '')}",
                    "timestamp": ts,
                    "link": issue.get("url", ""),
                })

    for r in running_sprints:
        repo = r["project"]
        slug = repo.split("/")[-1]
        status_data = _sprint_statuses.get((repo, r["sprint_label"]), {})
        start_ts = status_data.get("start_timestamp")
        if start_ts:
            events.append({
                "type": "sprint_started",
                "project": slug,
                "title": f"{r['sprint_label']} started",
                "sub": slug,
                "timestamp": start_ts,
                "link": f"https://github.com/{repo}/issues?q=label:{r['sprint_label']}",
            })

    seen_summaries: set[str] = set()
    for proj in projects:
        repo = proj["repo"]
        slug = repo.split("/")[-1]
        for sprints_dir in [
            _commander_dir(_project_root_path(repo)) / "sprints",
            SPRINTS_DIR,
        ]:
            if not sprints_dir.exists():
                continue
            for sf in sorted(
                sprints_dir.glob("*-summary-*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:5]:
                uid = str(sf.resolve())
                if uid in seen_summaries:
                    continue
                seen_summaries.add(uid)
                try:
                    meta = _parse_summary_file(sf)
                    if not meta.get("date") or meta.get("sprint_num") is None:
                        continue
                    event_ts = meta["date"] + "T00:00:00Z"
                    sprint_label = f"sprint-{meta['sprint_num']}"
                    raw_status = meta.get("status", "").lower()
                    etype = "sprint_failed" if raw_status in ("failed", "cancelled") else "sprint_completed"
                    link = f"https://github.com/{repo}/issues"
                    state_file = sprints_dir / f"sprint-{meta['sprint_num']}-state.json"
                    if state_file.exists():
                        try:
                            sd = json.loads(state_file.read_text())
                            link = sd.get("summary_issue_url") or link
                        except Exception:
                            pass
                    events.append({
                        "type": etype,
                        "project": slug,
                        "title": f"{sprint_label} {raw_status or 'completed'}",
                        "sub": f"{meta.get('shipped_count', 0)} tickets shipped",
                        "timestamp": event_ts,
                        "link": link,
                    })
                except Exception:
                    pass

    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return events[:5]


@app.get("/api/home")
def get_home():
    """Aggregated Home page payload: stats, per-project summaries, and activity feed.

    Per-project data is cached 30 s (key home:<slug>).
    Always returns HTTP 200 — failing projects degrade to idle with 0 counts.
    """
    projects = projects_module.load_projects()
    running_sprints = _all_sprints_running()

    all_open_by_repo: dict[str, list[dict]] = {}
    proj_data_list: list[dict] = []

    for proj in projects:
        repo = proj["repo"]
        data = _home_project_data(proj, running_sprints)
        proj_data_list.append(data)
        try:
            all_open_by_repo[repo] = github_client.list_all_open_issues(repo_name=repo)
        except Exception:
            all_open_by_repo[repo] = []

    # stats.sprint_running
    sprint_running_projects: list[dict] = []
    now_utc = datetime.now(timezone.utc)
    for r in running_sprints:
        repo = r["project"]
        slug = repo.split("/")[-1]
        proj_cfg = next((p for p in projects if p["repo"] == repo), {})
        status_data = _sprint_statuses.get((repo, r["sprint_label"]), {})
        start_ts = status_data.get("start_timestamp")
        elapsed_sec = 0
        if start_ts:
            try:
                start_dt = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
                elapsed_sec = int((now_utc - start_dt).total_seconds())
            except Exception:
                pass
        sprint_running_projects.append({
            "name": proj_cfg.get("name", slug),
            "sprint_label": r["sprint_label"],
            "elapsed_sec": elapsed_sec,
        })

    # stats.awaiting_uat
    uat_total = 0
    uat_project_set: set[str] = set()
    oldest_uat_ts: str | None = None
    oldest_age_sec: int | None = None

    for repo, issues in all_open_by_repo.items():
        for issue in issues:
            if any(l["name"] == "UAT" for l in issue.get("labels", [])):
                uat_total += 1
                uat_project_set.add(repo)
                ts = issue.get("updatedAt") or issue.get("createdAt")
                if ts and (oldest_uat_ts is None or ts < oldest_uat_ts):
                    oldest_uat_ts = ts

    if oldest_uat_ts:
        try:
            oldest_dt = datetime.fromisoformat(oldest_uat_ts.replace("Z", "+00:00"))
            oldest_age_sec = int((now_utc - oldest_dt).total_seconds())
        except Exception:
            pass

    # stats.sprints_planned
    running_labels_by_repo: dict[str, set[str]] = {}
    for r in running_sprints:
        running_labels_by_repo.setdefault(r["project"], set()).add(r["sprint_label"])

    planned_count = 0
    planned_tickets = 0
    _sprint_re = re.compile(r"^sprint-\d+$")
    for repo, issues in all_open_by_repo.items():
        running_lbls = running_labels_by_repo.get(repo, set())
        label_ticket_counts: dict[str, int] = {}
        for issue in issues:
            for lbl in issue.get("labels", []):
                lname = lbl["name"]
                if _sprint_re.match(lname) and lname not in running_lbls:
                    label_ticket_counts[lname] = label_ticket_counts.get(lname, 0) + 1
        planned_count += len(label_ticket_counts)
        planned_tickets += sum(label_ticket_counts.values())

    # stats.backlog
    backlog_per_proj: list[dict] = []
    total_backlog = 0
    for proj in projects:
        repo = proj["repo"]
        issues = all_open_by_repo.get(repo, [])
        bc = sum(1 for i in issues if github_client.classify_issue(i) == "backlog")
        total_backlog += bc
        if bc > 0:
            backlog_per_proj.append({"name": proj.get("name", repo.split("/")[-1]), "count": bc})
    backlog_per_proj.sort(key=lambda x: x["count"], reverse=True)

    activity = _home_activity_feed(all_open_by_repo, running_sprints, projects)

    return {
        "stats": {
            "sprint_running": {
                "count": len(sprint_running_projects),
                "projects": sprint_running_projects,
            },
            "awaiting_uat": {
                "count": uat_total,
                "projects": len(uat_project_set),
                "oldest_age_sec": oldest_age_sec,
            },
            "sprints_planned": {
                "count": planned_count,
                "total_tickets": planned_tickets,
            },
            "backlog": {
                "count": total_backlog,
                "per_project": backlog_per_proj[:5],
            },
        },
        "projects": proj_data_list,
        "activity": activity,
    }


# ── sprint summary / history endpoints (AC-4 / AC-6 from #24) ────────────────

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
    # Accepts either sprint_label (e.g. "sprint-3" or null for backlog) or
    # the legacy sprint: int field for backward compatibility.
    sprint_label: Optional[str] = None
    sprint: Optional[int] = None
    project: Optional[str] = None


@app.post("/api/issues/{issue_id}/sprint-label")
async def add_sprint_label(issue_id: int, body: SprintLabelBody):
    """Assign a sprint label to an issue (replaces any existing sprint-N labels).

    Accepts either:
    - sprint_label: "sprint-N" string (or null/empty to remove sprint label)
    - sprint: int (legacy; converted to "sprint-N")
    """
    # Resolve the sprint number from whichever field was provided
    if body.sprint_label is not None:
        raw = body.sprint_label.strip()
        if raw == "" or raw == "backlog":
            sprint_num = None
        else:
            m = re.match(r"^sprint-(\d+)$", raw)
            if not m:
                raise HTTPException(400, detail=f"Invalid sprint_label: {raw!r}")
            sprint_num = int(m.group(1))
    elif body.sprint is not None:
        sprint_num = body.sprint
    else:
        raise HTTPException(400, detail="Provide sprint_label or sprint")

    repo = body.project or None
    try:
        github_client.assign_sprint(issue_id, sprint_num, repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    return {"ok": True}


class BatchLabelChange(BaseModel):
    issue_num: int
    sprint_label: str  # e.g. "sprint-3" or "backlog"


class BatchLabelsBody(BaseModel):
    changes: list[BatchLabelChange]
    project: Optional[str] = None


@app.post("/api/sprints/batch-labels")
async def batch_sprint_labels(body: BatchLabelsBody):
    """Batch-move tickets to their target sprint labels.

    Accepts: {"changes": [{"issue_num": N, "sprint_label": "sprint-3"}, ...], "project": "owner/repo"}
    Returns: {"applied": N, "failed": N, "errors": [...]}
    """
    applied = 0
    failed = 0
    errors: list[str] = []

    repo = body.project or None

    for change in body.changes:
        raw = change.sprint_label.strip()
        if raw == "" or raw == "backlog":
            sprint_num = None
        else:
            m = re.match(r"^sprint-(\d+)$", raw)
            if not m:
                errors.append(f"#{change.issue_num}: invalid sprint_label {raw!r}")
                failed += 1
                continue
            sprint_num = int(m.group(1))

        try:
            github_client.assign_sprint(change.issue_num, sprint_num, repo_name=repo)
            applied += 1
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() if e.stderr else str(e)
            errors.append(f"#{change.issue_num}: {err_msg}")
            failed += 1
        except Exception as e:
            errors.append(f"#{change.issue_num}: {e}")
            failed += 1

    return {"applied": applied, "failed": failed, "errors": errors}


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

    cmd = [sys.executable, str(SPRINT_MANAGER_PATH), body.label]
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
    """Check if a sprint process is running by reading its PID file.

    Accepts both ``<label>-pid`` (fully claimed) and ``<label>-pid.pending``
    (claim written by server before subprocess startup completes) so there is
    no transient window where a sprint appears not running.

    Stale files (process dead) are cleaned up and logged so no manual ``rm``
    is ever required.
    """
    sprints_dir = _commander_dir(project_root) / "sprints"
    pid_file    = sprints_dir / f"{sprint_label}-pid"
    pending_file = sprints_dir / f"{sprint_label}-pid.pending"

    import logging as _logging
    _log = _logging.getLogger(__name__)

    for candidate in (pid_file, pending_file):
        if not candidate.exists():
            continue
        raw = ""
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        # pid == 0 means server wrote the pending file right before Popen;
        # the process is being spawned — treat as running to block duplicates.
        if raw == "" or raw == "0":
            return True

        try:
            pid = int(raw)
        except ValueError:
            # Unreadable content — clean up and continue.
            try:
                candidate.unlink()
            except OSError:
                pass
            continue

        try:
            os.kill(pid, 0)  # signal 0 = check if process exists
            return True
        except ProcessLookupError:
            # Process is dead — clean up stale file and log recovery.
            _log.warning(
                "Stale sprint lock detected: %s (PID %s dead) — cleaning up",
                candidate.name,
                raw,
            )
            try:
                candidate.unlink()
            except OSError:
                pass
        except PermissionError:
            # Process exists but owned by another user — treat as running.
            return True
        except OSError:
            pass

    return False


def _all_sprints_running() -> list[dict]:
    """Scan all projects for running sprints. Returns list of {project, sprint_label, pid}."""
    result = []
    projects = projects_module.load_projects()
    for proj in projects:
        root = _project_root_path(proj["repo"])
        sprints_dir = _commander_dir(root) / "sprints"
        if not sprints_dir.exists():
            continue
        seen: set[str] = set()
        # Check both fully-claimed files (*-pid) and pending-claim files (*-pid.pending)
        for pid_file in list(sprints_dir.glob("*-pid")) + list(sprints_dir.glob("*-pid.pending")):
            # Derive label: strip trailing "-pid.pending" or "-pid"
            label = pid_file.name.removesuffix("-pid.pending").removesuffix("-pid")
            if label in seen:
                continue
            seen.add(label)
            if _is_sprint_running(root, label):
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                except (ValueError, OSError):
                    pid = None
                result.append({"project": proj["repo"], "sprint_label": label, "pid": pid})
    return result


def _any_sprint_running() -> Optional[dict]:
    """Scan all projects for a running sprint. Returns first found or None."""
    running = _all_sprints_running()
    return running[0] if running else None


def _all_sprints_running() -> list[dict]:
    """Scan all projects for running sprints. Returns list of {project, sprint_label}."""
    result: list[dict] = []
    projects = projects_module.load_projects()
    for proj in projects:
        root = _project_root_path(proj["repo"])
        sprints_dir = _commander_dir(root) / "sprints"
        if not sprints_dir.exists():
            continue
        seen: set[str] = set()
        # Check both fully-claimed files (*-pid) and pending-claim files (*-pid.pending)
        for pid_file in list(sprints_dir.glob("*-pid")) + list(sprints_dir.glob("*-pid.pending")):
            label = pid_file.name.removesuffix("-pid.pending").removesuffix("-pid")
            if label in seen:
                continue
            seen.add(label)
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
    goal: str | None = None


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


# ── Estimate-summary helpers (issue #211) ────────────────────────────────────

def _size_to_minutes(size: str) -> int:
    """Map a T-shirt size label to wall-clock minutes.

    S = 30 min, M = 2 h (120 min), L = 8 h (480 min).
    Change this single function to adjust all size mappings.
    """
    mapping = {"S": 30, "M": 120, "L": 480}
    return mapping.get(size, 0)


@app.get("/api/sprints/{sprint_label}/estimate-summary")
def get_sprint_estimate_summary(sprint_label: str, project: str):
    """Return a rolled-up estimate summary for a sprint.

    Fetches open issues for the sprint via the existing list_open_issues_with_body
    plumbing, reads size-S/M/L/XL labels from each ticket, and returns:
      - size_counts: dict mapping size -> count (e.g. {"S": 2, "M": 3, "L": 1})
      - total_minutes: int, sum of _size_to_minutes for all sized tickets
      - unsized_numbers: list of issue numbers with no size label
      - sprint_name: human-readable sprint name (e.g. "Sprint 15")
      - total_tickets: total open ticket count in the sprint
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    # Filter to issues belonging to this sprint
    sprint_issues = [
        iss for iss in issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    _SIZE_LABELS = ["S", "M", "L", "XL"]  # ordered smallest to largest
    size_counts: dict[str, int] = {}
    unsized_numbers: list[int] = []
    total_minutes = 0

    for iss in sprint_issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        found_size = None
        for size in _SIZE_LABELS:
            if f"size-{size}" in label_names:
                found_size = size
                break
        if found_size:
            size_counts[found_size] = size_counts.get(found_size, 0) + 1
            total_minutes += _size_to_minutes(found_size)
        else:
            unsized_numbers.append(iss["number"])

    # Extract sprint number for human-readable name
    m = re.search(r"(\d+)", sprint_label)
    sprint_num = int(m.group(1)) if m else None
    sprint_name = f"Sprint {sprint_num}" if sprint_num is not None else sprint_label

    return {
        "sprint_name": sprint_name,
        "sprint_label": sprint_label,
        "total_tickets": len(sprint_issues),
        "size_counts": size_counts,
        "total_minutes": total_minutes,
        "unsized_numbers": unsized_numbers,
    }


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

    project_root = _project_root_path(body.project)
    if _is_sprint_running(project_root, body.sprint_label):
        sprints_dir = _commander_dir(project_root) / "sprints"
        pid_str = "unknown"
        for fname in (f"{body.sprint_label}-pid", f"{body.sprint_label}-pid.pending"):
            candidate = sprints_dir / fname
            if candidate.exists():
                try:
                    pid_str = candidate.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
                break
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
    pid_path    = pid_dir / f"{body.sprint_label}-pid"
    pending_path = pid_dir / f"{body.sprint_label}-pid.pending"

    # ── Two-phase atomic claim (Strategy A, issue #155) ──────────────────────
    # Phase 1: Atomically create the pending file using O_CREAT|O_EXCL so that
    # a concurrent second request races to the same syscall and loses.  The
    # file exists from this point on, so _is_sprint_running will return True
    # for all subsequent callers — there is no gap between "check" and "claim".
    try:
        fd = os.open(
            str(pending_path),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        os.write(fd, b"0")  # placeholder PID; subprocess will rename to -pid
        os.close(fd)
    except FileExistsError:
        # Another request already claimed this slot after _is_sprint_running
        # returned False — return 409 consistently.
        raise HTTPException(
            409,
            detail=f"Sprint {body.sprint_label} is already running on {body.project}",
        )

    stripped_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    goal_path = _sprint_goal_path(project_root, body.sprint_label)
    if goal_path.exists():
        stripped_env["SPRINT_GOAL"] = goal_path.read_text(encoding="utf-8").strip()

    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SPRINT_MANAGER_PATH), body.sprint_label, "--skip-gates"],
            env=stripped_env,
            cwd=str(coder_path),
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )
    except Exception:
        # Popen failed — release the pending claim so the sprint can be retried.
        try:
            pending_path.unlink()
        except OSError:
            pass
        raise

    # Phase 2: Write the real PID then atomically rename pending → pid.
    # os.replace is atomic on POSIX (rename(2)) — the final file either
    # contains the real PID or doesn't exist; there is no half-written state.
    pending_path.write_text(str(proc.pid), encoding="utf-8")
    os.replace(str(pending_path), str(pid_path))

    # Early-crash detection: if the subprocess exits within 2 seconds it almost
    # certainly failed to start (e.g. missing venv dependency).  Read the tail
    # of the dispatch log and return HTTP 502 with the error detail so the
    # caller gets a meaningful message instead of a silent 202.
    try:
        proc.wait(timeout=2.0)
        # Process already exited — read the log tail for the error message.
        log_fh.flush()
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(log_text.splitlines()[-30:]) if log_text else "(no output)"
        except OSError:
            tail = "(could not read log)"
        # Clean up the PID file since the process is already dead.
        try:
            pid_path.unlink()
        except OSError:
            pass
        raise HTTPException(
            502,
            detail=f"Sprint subprocess exited immediately (rc={proc.returncode}). Log tail:\n{tail}",
        )
    except subprocess.TimeoutExpired:
        # Still running after 2 seconds — normal startup, return 202.
        pass

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
    sprints_dir  = _commander_dir(project_root) / "sprints"
    pid_file      = sprints_dir / f"{sprint_label}-pid"
    pending_file  = sprints_dir / f"{sprint_label}-pid.pending"

    # Accept either the fully-claimed file or the pending file.
    active_file = pid_file if pid_file.exists() else (pending_file if pending_file.exists() else None)
    if active_file is None:
        raise HTTPException(404, detail=f"No running sprint found for {sprint_label}")

    try:
        pid = int(active_file.read_text(encoding="utf-8").strip())
    except ValueError:
        for f in (pid_file, pending_file):
            try:
                f.unlink()
            except OSError:
                pass
        raise HTTPException(404, detail=f"Invalid PID file for {sprint_label}")

    # SIGTERM first, then wait up to 5 s for graceful exit, then SIGKILL
    if pid > 0:
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

    for f in (pid_file, pending_file):
        try:
            f.unlink()
        except OSError:
            pass

    # Neon + JSON: mark sprint as cancelled (best-effort — don't fail the kill).
    if _SPRINT_REPO_AVAILABLE and _sprint_repo is not None:
        try:
            _sprint_repo.update_sprint_status(sprint_label, "cancelled")
        except Exception as _e:
            print(f"[neon] WARNING: could not mark sprint {sprint_label!r} cancelled: {_e}")
    project_root = _project_root_path(project)
    json_path = _sprint_json_path(project_root, sprint_label)
    data = _sprint_json_read(json_path)
    if data:
        data["status"] = "cancelled"
        _sprint_json_write(json_path, data)

    return {"ok": True}


@app.get("/api/sprints/{sprint_label}/dispatch-log")
def get_dispatch_log(sprint_label: str, project: str, tail_lines: int = 200):
    """Return the last N lines of the most recent sprint-run-<label>-*.log."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    from log_source import read_log  # local import keeps startup fast
    project_root = _project_root_path(project)
    return read_log("dispatch", project_root, label=sprint_label, tail_lines=tail_lines)


@app.get("/api/sprints/{sprint_label}/issue/{issue_num}/log")
def get_issue_log(sprint_label: str, project: str, issue_num: int, tail_lines: int = 200):
    """Return the last N lines of sprint-issue-<N>.log for the given sprint."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    from log_source import read_log  # local import keeps startup fast
    project_root = _project_root_path(project)
    return read_log("issue", project_root, label=sprint_label, issue_num=issue_num, tail_lines=tail_lines)


# ── Sprint live snapshot + SSE stream (issue #224) ───────────────────────────

def _parse_log_lines_for_live(lines: list[str], limit: int = 50) -> list[dict]:
    """Parse log lines into structured log entries for the live panel.

    Classifies each line into one of: dispatch, success, warn, fail, event.
    Returns the last `limit` entries (oldest-first).
    """
    entries: list[dict] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue

        # Determine line type by content heuristics
        if (
            stripped.startswith("→")
            or stripped.startswith("---")
            or "start_feature.py" in stripped
            or "Dispatching" in stripped
        ):
            line_type = "dispatch"
        elif (
            stripped.startswith("✓")
            or "promoted" in stripped.lower()
            or "merged" in stripped.lower()
            or "completed" in stripped.lower()
            or "done" in stripped.lower()
        ):
            line_type = "success"
        elif (
            "warning" in stripped.lower()
            or stripped.lower().startswith("warn")
            or "[retry]" in stripped.lower()
        ):
            line_type = "warn"
        elif (
            "error" in stripped.lower()
            or "fail" in stripped.lower()
            or "skipped" in stripped.lower()
            or stripped.lower().startswith("err")
        ):
            line_type = "fail"
        else:
            line_type = "event"

        # Use the current UTC time formatted as HH:MM:SS — we don't have per-line
        # timestamps in the log, so we label with a placeholder "—"; callers may
        # pre-process the raw lines before calling this function.
        entries.append({"timestamp": "—", "type": line_type, "message": stripped})

    return entries[-limit:]


def _find_latest_sprint_log(log_dir: Path, sprint_label: str) -> Optional[Path]:
    """Return the most recently modified sprint-run-<label>-*.log file, or None."""
    if not log_dir.exists():
        return None
    candidates = sorted(
        log_dir.glob(f"sprint-run-{sprint_label}-*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


@app.get("/api/sprints/{sprint_label}/live")
def get_sprint_live_snapshot(sprint_label: str, project: str):
    """Return a JSON snapshot of the live running sprint.

    Response shape:
    {
      "time_spent_sec": <int>,
      "started_at": "<ISO8601>",
      "current_ticket": {"number": N, "title": "..."} | null,
      "active_agent": {"name": "coder"|"tester", "model": "...", "pid": N} | null,
      "recent_log_lines": [{"timestamp": "HH:MM:SS", "type": "...", "message": "..."}, ...],
      "issues": [
        {
          "number": <int>,
          "title": <str>,
          "status": "pending"|"in-progress"|"done"|"skipped",
          "agent_status": "running"|"failed"|null,
          "agent": "coder"|"tester"|null,
          "elapsed_secs": <int>|null,
          "size": "S"|"M"|"L"|"XL"|null
        }, ...
      ]
    }
    recent_log_lines contains the last 50 lines.
    issues is sourced from the locked launch snapshot (issue #306).
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    # ── Time spent + started_at from _sprint_statuses in-memory dict ──────────
    status_key = (project, sprint_label)
    status_data = _sprint_statuses.get(status_key, {})

    started_at_str: Optional[str] = status_data.get("start_timestamp")
    started_at_dt: Optional[datetime] = None
    if started_at_str:
        try:
            started_at_dt = datetime.fromisoformat(started_at_str.rstrip("Z"))
            if started_at_dt.tzinfo is None:
                started_at_dt = started_at_dt.replace(tzinfo=timezone.utc)
        except Exception:
            started_at_dt = None

    now_utc = datetime.now(timezone.utc)
    time_spent_sec: int = 0
    if started_at_dt:
        time_spent_sec = max(0, int((now_utc - started_at_dt).total_seconds()))

    # ── current_ticket: the most-recent in-progress issue from sprint status ──
    current_ticket: Optional[dict] = None
    issues = status_data.get("issues", [])
    # Prefer the last issue with status "in-progress"; fall back to last non-done issue
    in_progress = [i for i in issues if i.get("status") == "in-progress"]
    if in_progress:
        iss = in_progress[-1]
        current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
    else:
        pending = [i for i in issues if i.get("status") not in ("done", "skipped")]
        if pending:
            iss = pending[0]
            current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}

    # ── Outcome counts for the stat strip (issue #256) ───────────────────────
    done_count    = sum(1 for i in issues if i.get("status") == "done")
    failed_count  = sum(1 for i in issues if i.get("agent_status") == "failed")
    # Skipped = status==skipped but NOT agent_status==failed (true skips: preflight, dry-run)
    skipped_count = sum(
        1 for i in issues
        if i.get("status") == "skipped" and i.get("agent_status") != "failed"
    )
    total_count   = len(issues)
    complete_count = done_count + failed_count + skipped_count  # all terminal states
    pending_count  = total_count - complete_count

    # ── Est. remaining (issue #256) ──────────────────────────────────────────
    # Primary source: sum of per-ticket estimates (minutes) for non-terminal tickets.
    # Fallback: pending_count × avg wall-clock time per completed ticket (minutes).
    estimates: dict = status_data.get("estimates", {})
    est_remaining_minutes: Optional[int] = None

    if estimates and total_count > 0:
        # estimates keys may be int or str (JSON serialises int keys as strings)
        rem_minutes = 0
        has_any_estimate = False
        for iss in issues:
            num = iss.get("number")
            terminal = iss.get("status") in ("done", "skipped")
            est_entry = estimates.get(str(num)) or estimates.get(num)
            if est_entry:
                has_any_estimate = True
                if not terminal:
                    rem_minutes += int(est_entry.get("minutes", 0))
        if has_any_estimate:
            est_remaining_minutes = rem_minutes

    if est_remaining_minutes is None and complete_count > 0 and pending_count > 0:
        # Fallback: avg wall-clock per completed ticket × pending count
        wall_secs = status_data.get("wall_clock_secs", 0.0)
        avg_secs = wall_secs / complete_count if complete_count > 0 else 0
        est_remaining_minutes = max(0, round(avg_secs * pending_count / 60))

    # AC: No stat cell is left blank — zero is acceptable.
    # If the sprint is active but estimates aren't available yet, show 0 rather than null.
    if est_remaining_minutes is None and total_count > 0:
        est_remaining_minutes = 0

    # ── issues array: snapshot with per-ticket status, agent, elapsed, size ──
    _IN_FLIGHT_AGENT_STATUSES = frozenset({
        "coder_dispatched", "coder_running", "coder_done",
        "tester_dispatched", "tester_running", "tester_done",
    })

    def _parse_ts_utc(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    issues_out: list[dict] = []
    for iss in issues:
        num = iss.get("number")
        raw_agent_status = iss.get("agent_status")

        # status: derive "in-progress" for tickets currently being worked on
        if raw_agent_status in _IN_FLIGHT_AGENT_STATUSES:
            derived_status = "in-progress"
        else:
            derived_status = iss.get("status", "pending")

        # agent_status: normalize to running | failed | null
        if raw_agent_status in ("coder_running", "tester_running"):
            public_agent_status: Optional[str] = "running"
        elif raw_agent_status == "failed":
            public_agent_status = "failed"
        else:
            public_agent_status = None

        # agent: active role or null (only while agent is dispatched or running)
        if raw_agent_status in ("coder_dispatched", "coder_running"):
            active_role: Optional[str] = "coder"
        elif raw_agent_status in ("tester_dispatched", "tester_running"):
            active_role = "tester"
        else:
            active_role = None

        # elapsed_secs: coder_started_at → tester_finished_at (or now if still running)
        coder_start_dt = _parse_ts_utc(iss.get("coder_started_at"))
        if coder_start_dt is not None:
            end_dt = _parse_ts_utc(iss.get("tester_finished_at")) or now_utc
            issue_elapsed: Optional[int] = max(0, int((end_dt - coder_start_dt).total_seconds()))
        else:
            issue_elapsed = None

        # size: from estimates only — never derived from minutes
        est_entry = estimates.get(str(num)) or estimates.get(num)
        size: Optional[str] = est_entry.get("size") if est_entry else None

        issues_out.append({
            "number":       num,
            "title":        iss.get("title", ""),
            "status":       derived_status,
            "agent_status": public_agent_status,
            "agent":        active_role,
            "elapsed_secs": issue_elapsed,
            "size":         size,
        })

    # ── active_agent: derive from sprint state JSON (coder/tester transition) ──
    active_agent: Optional[dict] = None
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    state_path = commander / "sprints" / f"sprint-{n}-state.json"
    if state_path.exists():
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            # Find the issue currently being processed (has coder_started but no tester_finished)
            for iss in state_data.get("issues", []):
                if iss.get("coder_started_at") and not iss.get("tester_finished_at"):
                    agent_name = "tester" if iss.get("tester_started_at") else "coder"
                    active_agent = {"name": agent_name, "model": None, "pid": None}
                    break
        except Exception:
            pass

    # PID from PID file
    pid_file = commander / "sprints" / f"{sprint_label}-pid"
    if pid_file.exists():
        try:
            pid_val = int(pid_file.read_text(encoding="utf-8").strip())
            if active_agent:
                active_agent["pid"] = pid_val
            else:
                active_agent = {"name": "coder", "model": None, "pid": pid_val}
        except Exception:
            pass

    # ── recent_log_lines: last 50 lines from sprint run log ──────────────────
    log_dir = commander / "logs"
    recent_log_lines: list[dict] = []
    log_path = _find_latest_sprint_log(log_dir, sprint_label)
    if log_path:
        try:
            raw_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            recent_log_lines = _parse_log_lines_for_live(raw_lines, limit=50)
        except OSError:
            pass

    return {
        "time_spent_sec": time_spent_sec,
        "started_at": started_at_str,
        "current_ticket": current_ticket,
        "active_agent": active_agent,
        "recent_log_lines": recent_log_lines,
        # Stat strip fields (issue #256)
        "done_count":           done_count,
        "failed_count":         failed_count,
        "skipped_count":        skipped_count,
        "pending_count":        pending_count,
        "total_count":          total_count,
        "complete_count":       complete_count,
        "est_remaining_minutes": est_remaining_minutes,
        # Per-ticket snapshot (issue #306)
        "issues":               issues_out,
    }


@app.get("/api/sprints/{sprint_label}/live/stream")
async def get_sprint_live_stream(sprint_label: str, project: str, request: Request):
    """SSE endpoint that streams incremental log-line events as they occur.

    Events emitted:
    - event: log_line   data: {"timestamp": "...", "type": "...", "message": "..."}
    - event: complete   data: {"reason": "stopped"}   (when sprint ends)
    - keepalive comment every 15 s while idle

    Data source: tails the most recent sprint-run-<label>-*.log file.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    log_dir = commander / "logs"

    async def _stream():
        # Find the current log file — retry briefly in case it hasn't appeared yet
        log_path: Optional[Path] = None
        for _ in range(20):  # up to 2 seconds
            log_path = _find_latest_sprint_log(log_dir, sprint_label)
            if log_path:
                break
            await asyncio.sleep(0.1)

        if not log_path:
            yield f"event: complete\ndata: {json.dumps({'reason': 'no_log_file'})}\n\n"
            return

        # Seek to end of file so we only stream new lines
        try:
            file_size = log_path.stat().st_size
        except OSError:
            file_size = 0

        current_offset = file_size

        while True:
            if await request.is_disconnected():
                return

            # Check if sprint is still running
            is_running = _is_sprint_running(project_root, sprint_label)

            # Read any new bytes from the log file
            try:
                file_size = log_path.stat().st_size
            except OSError:
                file_size = current_offset

            if file_size > current_offset:
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(current_offset)
                        new_text = fh.read(file_size - current_offset)
                    current_offset = file_size

                    new_lines = new_text.splitlines()
                    parsed = _parse_log_lines_for_live(new_lines, limit=len(new_lines))
                    for entry in parsed:
                        yield f"event: log_line\ndata: {json.dumps(entry)}\n\n"
                except OSError:
                    pass

            if not is_running:
                yield f"event: complete\ndata: {json.dumps({'reason': 'stopped'})}\n\n"
                return

            await asyncio.sleep(0.5)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/sprints/create")
async def create_sprint_label(body: SprintCreateBody):
    """Create a sprint-N label for a project. Uses sprint_number if provided, else auto-increments.

    Optionally accepts a goal string (min 10 chars) which is persisted to
    .commander/sprints/<label>-goal.txt after the label is created.
    """
    if body.goal is not None and len(body.goal.strip()) < 10:
        raise HTTPException(400, detail="Sprint goal must be at least 10 characters")
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
    sprint_label = f"sprint-{target_num}"
    eff_goal = (body.goal or sprint_label).strip() or sprint_label

    # Neon write must succeed before any JSON is written (AC-6).
    if _SPRINT_REPO_AVAILABLE and _sprint_repo is not None:
        try:
            _sprint_repo.get_or_create_sprint(
                label=sprint_label,
                goal=eff_goal,
                project=body.project,
            )
        except Exception as _e:
            raise HTTPException(500, detail=f"Neon write failed: {_e}")

    # JSON writes are best-effort (AC-7).
    project_root = _project_root_path(body.project)
    if body.goal is not None:
        goal_path = _sprint_goal_path(project_root, sprint_label)
        goal_path.parent.mkdir(parents=True, exist_ok=True)
        goal_path.write_text(body.goal.strip(), encoding="utf-8")
    _sprint_json_write(
        _sprint_json_path(project_root, sprint_label),
        {"label": sprint_label, "goal": eff_goal, "project": body.project, "status": "pending", "tickets": []},
    )
    return {"ok": True, "sprint_label": sprint_label}


class SprintRenameBody(BaseModel):
    new_sprint_number: int
    project: str


@app.post("/api/sprints/{sprint_label}/rename")
async def rename_sprint_label(sprint_label: str, body: SprintRenameBody):
    """Rename a sprint label to a new sprint number.

    GitHub's label edit API updates all issues automatically — no per-issue
    re-labelling is required.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    if body.new_sprint_number <= 0:
        raise HTTPException(400, detail="Sprint number must be a positive integer")

    new_label = f"sprint-{body.new_sprint_number}"
    if new_label == sprint_label:
        raise HTTPException(400, detail="New sprint number is the same as the current one")

    try:
        existing = github_client.list_sprints(repo_name=body.project)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    if body.new_sprint_number in existing:
        raise HTTPException(409, detail=f"Sprint {body.new_sprint_number} already exists")

    project_root = _project_root_path(body.project)
    if _is_sprint_running(project_root, sprint_label):
        raise HTTPException(409, detail="Cannot rename a sprint that is currently running")

    # Rename the GitHub label (updates all issues automatically via GitHub API)
    try:
        github_client.edit_label(
            sprint_label,
            new_label,
            description=f"Sprint {body.new_sprint_number} issues",
            repo_name=body.project,
        )
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    # Rename local files
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"
    for suffix in ("-goal.txt", "-state.json"):
        old_path = sprints_dir / f"{sprint_label}{suffix}"
        new_path = sprints_dir / f"{new_label}{suffix}"
        if old_path.exists():
            old_path.rename(new_path)

    # Update sprint order JSON
    order_path = _sprint_order_path(project_root)
    if order_path.exists():
        try:
            order: list[str] = json.loads(order_path.read_text(encoding="utf-8"))
            order = [new_label if s == sprint_label else s for s in order]
            order_path.write_text(json.dumps(order), encoding="utf-8")
        except Exception:
            pass

    # Update Neon DB
    if _SPRINT_REPO_AVAILABLE and _sprint_repo is not None:
        try:
            _sprint_repo.rename_sprint(sprint_label, new_label)
        except Exception:
            pass  # Best-effort; GitHub is the source of truth for labels

    return {"ok": True, "old_label": sprint_label, "new_label": new_label}


class SprintTicketReorderBody(BaseModel):
    issue_numbers: list[int]
    project: str


@app.post("/api/sprints/{sprint_label}/tickets/reorder")
def reorder_sprint_tickets(sprint_label: str, body: SprintTicketReorderBody):
    """Reorder tickets within a sprint. Writes to Neon first, then JSON fallback."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    # Neon write must succeed (AC-6).
    if _SPRINT_REPO_AVAILABLE and _sprint_repo is not None:
        try:
            _sprint_repo.reorder_tickets(sprint_label, body.issue_numbers)
        except Exception as _e:
            if "SprintNotFound" in type(_e).__name__ or "not found" in str(_e).lower():
                raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found in DB")
            raise HTTPException(500, detail=f"Neon write failed: {_e}")

    # JSON fallback (AC-7).
    project_root = _project_root_path(body.project)
    json_path = _sprint_json_path(project_root, sprint_label)
    data = _sprint_json_read(json_path)
    if "tickets" in data:
        by_num = {t["issue_number"]: t for t in data["tickets"]}
        data["tickets"] = [
            {**by_num[n], "position": pos}
            for pos, n in enumerate(body.issue_numbers)
            if n in by_num
        ]
        _sprint_json_write(json_path, data)

    return {"ok": True}


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


def _rerun_policy(labels: set[str]) -> tuple[str, list[str]]:
    """Return (action, labels_to_strip) for a sprint ticket based on its current labels.

    action:
        'skip'             — UAT / UAT-approved; leave ticket and labels untouched
        'dispatch_tester'  — SIT ticket; send to tester directly; SIT label preserved
        'dispatch_coder'   — all other states; send to coder; strip appropriate labels
    """
    if labels & {"UAT", "UAT-approved"}:
        return "skip", []
    if "SIT" in labels:
        return "dispatch_tester", []
    if "tester-rejected" in labels:
        return "dispatch_coder", ["tester-rejected"]
    if "needs-rework" in labels or "need-rework" in labels:
        to_strip = ["in-progress"] if "in-progress" in labels else []
        return "dispatch_coder", to_strip
    if "in-progress" in labels:
        return "dispatch_coder", ["in-progress"]
    return "dispatch_coder", []


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


@app.get("/api/sprints/{sprint_label}/state")
def get_sprint_state(sprint_label: str, project: str):
    """Return timing data from sprint-N-state.json for duration display (issue #212).

    Returns:
      - wall_clock_secs: total sprint wall-clock time
      - issues: list of {number, duration_secs, failed} for each issue that has
                timing data (coder_started_at present); duration_secs is computed
                from coder_started_at to tester_finished_at (or status_changed_at
                as fallback).  failed=true when issue status is 'skipped' or
                agent_status is 'failed'.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    state_path = commander / "sprints" / f"sprint-{n}-state.json"

    if not state_path.exists():
        raise HTTPException(404, detail=f"State not found for {sprint_label!r}")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    def _parse_iso(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    issue_durations = []
    for iss in state_data.get("issues", []):
        start_ts = _parse_iso(iss.get("coder_started_at"))
        if start_ts is None:
            continue  # no timing data for this ticket
        end_ts = _parse_iso(iss.get("tester_finished_at")) or _parse_iso(iss.get("status_changed_at"))
        if end_ts is None:
            continue
        duration_secs = max(0.0, end_ts - start_ts)
        failed = (
            iss.get("status") == "skipped"
            or iss.get("agent_status") == "failed"
            or iss.get("failure_reason") is not None
        )
        issue_durations.append({
            "number":       iss["number"],
            "duration_secs": round(duration_secs),
            "failed":       failed,
        })

    return {
        "sprint_label":   sprint_label,
        "wall_clock_secs": state_data.get("wall_clock_secs", 0.0),
        "issues":         issue_durations,
    }


@app.get("/api/sprints/{sprint_label}/outcome")
def get_sprint_outcome(sprint_label: str, project: str):
    """Return frozen outcome data for a completed or stopped sprint.

    Reads sprint-N-state.json plus the latest sprint-run-<label>-*.log to produce:
      - sprint_status: "completed" | "stopped" | None (still running or not found)
      - counts: { done, failed, skipped }
      - wall_clock_secs: total duration
      - ended_at: ISO 8601 timestamp of sprint end (from last issue status_changed_at)
      - issues: list of { number, title, outcome, elapsed_secs } for each issue
      - log_line_count: number of lines in the archived run log
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    state_path = commander / "sprints" / f"sprint-{n}-state.json"
    if not state_path.exists():
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r}")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    # If sprint is still actively running, return 404 so UI doesn't freeze it
    if _is_sprint_running(project_root, sprint_label):
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} is still running")

    def _parse_iso(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    def _fmt_iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")

    # Derive sprint status from summary file (most authoritative)
    sprint_status: Optional[str] = None
    sprints_dir = commander / "sprints"
    for sf in sorted(sprints_dir.glob(f"sprint-{n}-summary-*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = _parse_summary_file(sf)
            raw = (meta.get("status") or "").lower()
            if raw in ("complete", "completed"):
                sprint_status = "completed"
            elif raw in ("stopped", "failed", "cancelled"):
                sprint_status = "stopped"
        except Exception:
            pass
        break

    # Fallback: derive from issue statuses — if all are done/skipped and no failures, completed
    issues_raw = state_data.get("issues", [])
    if sprint_status is None and issues_raw:
        has_pending = any(i.get("status") == "pending" for i in issues_raw)
        has_failed = any(
            i.get("agent_status") == "failed" or i.get("failure_reason")
            for i in issues_raw
        )
        if not has_pending:
            sprint_status = "stopped" if has_failed else "completed"

    if sprint_status is None:
        raise HTTPException(404, detail=f"Cannot determine outcome for {sprint_label!r}")

    # Build issue outcome list
    result_issues = []
    ended_ts: Optional[float] = None
    for iss in issues_raw:
        start_ts = _parse_iso(iss.get("coder_started_at"))
        end_ts = (
            _parse_iso(iss.get("tester_finished_at"))
            or _parse_iso(iss.get("status_changed_at"))
        )
        elapsed_secs = None
        if start_ts is not None and end_ts is not None:
            elapsed_secs = max(0.0, end_ts - start_ts)

        if end_ts and (ended_ts is None or end_ts > ended_ts):
            ended_ts = end_ts

        iss_status = iss.get("status", "pending")
        iss_agent = iss.get("agent_status")
        failure_reason = iss.get("failure_reason")

        if iss_status == "done":
            outcome = "done"
        elif iss_agent == "failed" or failure_reason:
            outcome = "failed"
        elif iss_status == "skipped":
            outcome = "skipped"
        else:
            outcome = "skipped"

        result_issues.append({
            "number":       iss.get("number"),
            "title":        iss.get("title", ""),
            "outcome":      outcome,
            "elapsed_secs": round(elapsed_secs) if elapsed_secs is not None else None,
            "failure_reason": failure_reason,
        })

    # Counts
    done_count    = sum(1 for i in result_issues if i["outcome"] == "done")
    failed_count  = sum(1 for i in result_issues if i["outcome"] == "failed")
    skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

    # Log line count from most recent run log
    log_line_count = 0
    log_dir = commander / "logs"
    if log_dir.exists():
        candidates = sorted(
            log_dir.glob(f"sprint-run-{sprint_label}-*.log"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if candidates:
            try:
                log_line_count = len(candidates[0].read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass

    return {
        "sprint_label":   sprint_label,
        "sprint_status":  sprint_status,
        "counts": {
            "done":    done_count,
            "failed":  failed_count,
            "skipped": skipped_count,
        },
        "wall_clock_secs": state_data.get("wall_clock_secs", 0.0),
        "ended_at":        _fmt_iso(ended_ts),
        "issues":          result_issues,
        "log_line_count":  log_line_count,
    }


class SprintRerunBody(BaseModel):
    confirm: bool


@app.get("/api/sprints/{sprint_label}/rerun/preview")
def rerun_sprint_preview(sprint_label: str, project: str):
    """Return per-ticket rerun preview counts without executing anything.

    Response schema:
      { redispatch_count, tester_count, skip_count, by_ticket: [
          { issue_num, issue_title, action }  # action: dispatch_coder|dispatch_tester|skip
        ]
      }
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_issues = [
        iss for iss in issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    redispatch_count = 0
    tester_count = 0
    skip_count = 0
    by_ticket: list[dict] = []

    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        action, _ = _rerun_policy(current_labels)
        if action == "dispatch_coder":
            redispatch_count += 1
        elif action == "dispatch_tester":
            tester_count += 1
        else:
            skip_count += 1
        by_ticket.append({
            "issue_num": iss["number"],
            "issue_title": iss["title"],
            "action": action,
        })

    return {
        "redispatch_count": redispatch_count,
        "tester_count": tester_count,
        "skip_count": skip_count,
        "by_ticket": by_ticket,
    }


@app.post("/api/sprints/{sprint_label}/rerun")
def rerun_sprint(sprint_label: str, project: str, body: SprintRerunBody):
    """Apply per-ticket re-run policy and dispatch agents selectively.

    Per-ticket routing:
      no-label / in-progress  → coder (strips in-progress)
      tester-rejected         → coder (strips tester-rejected)
      needs-rework            → coder (preserves needs-rework)
      SIT                     → tester directly (preserves SIT label)
      UAT / UAT-approved      → skipped; no agent invoked, no label change
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not body.confirm:
        raise HTTPException(400, detail="confirm must be true")

    project_root = _project_root_path(project)
    if _is_sprint_running(project_root, sprint_label):
        raise HTTPException(409, detail="Cannot reset a sprint that is currently running")

    commander = _commander_dir(project_root)
    log_dir = commander / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = str(uuid.uuid4())
    decision_log_path = log_dir / f"sprint-rerun-{sprint_label}-{ts}.log"
    manifest_path = log_dir / f"sprint-rerun-{sprint_label}-{ts}-manifest.json"
    log_lines: list[str] = []

    try:
        issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_issues = [
        iss for iss in issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    decisions: list[dict] = []
    errors: list[str] = []

    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        action, to_strip = _rerun_policy(current_labels)

        if to_strip:
            try:
                github_client.update_labels(iss["number"], add=[], remove=to_strip, repo_name=project)
            except subprocess.CalledProcessError as e:
                msg = f"#{iss['number']} failed: {e.stderr.strip()}"
                errors.append(msg)
                log_lines.append(json.dumps({
                    "run_id": run_id, "tag": "rerun_decision", "ts": ts,
                    "issue": iss["number"], "action": "error", "error": msg,
                }))
                continue

        decisions.append({
            "issue_num": iss["number"],
            "issue_title": iss["title"],
            "action": action,
            "stripped": to_strip,
        })
        log_lines.append(json.dumps({
            "run_id": run_id, "tag": "rerun_decision", "ts": ts,
            "issue": iss["number"], "action": action,
        }))

    manifest = {
        "run_id": run_id,
        "sprint_label": sprint_label,
        "created_at": ts,
        "decisions": decisions,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    state_file = commander / "sprints" / f"{sprint_label}-state.json"
    state_file.unlink(missing_ok=True)
    log_lines.append(json.dumps({"run_id": run_id, "tag": "state_deleted", "ts": ts}))
    decision_log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    github_client.invalidate(f"open_issues_body:")
    github_client.invalidate(f"open_issues:")
    github_client.invalidate(f"issues:")

    if not SPRINT_MANAGER_PATH.exists():
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    coder_path = _coder_clone_path(project_root)
    sprints_dir = commander / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    pid_path = sprints_dir / f"{sprint_label}-pid"
    pending_path = sprints_dir / f"{sprint_label}-pid.pending"
    run_log_path = log_dir / f"sprint-run-{sprint_label}-{ts}.log"

    try:
        fd = os.open(str(pending_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.write(fd, b"0")
        os.close(fd)
    except FileExistsError:
        raise HTTPException(409, detail=f"Sprint {sprint_label} is already running on {project}")

    stripped_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    goal_path = _sprint_goal_path(project_root, sprint_label)
    if goal_path.exists():
        stripped_env["SPRINT_GOAL"] = goal_path.read_text(encoding="utf-8").strip()

    run_log_fh = open(run_log_path, "w")
    try:
        proc = subprocess.Popen(
            [
                sys.executable, str(SPRINT_MANAGER_PATH), sprint_label,
                "--skip-gates", "--rerun-manifest", str(manifest_path),
            ],
            env=stripped_env,
            cwd=str(coder_path),
            stdout=run_log_fh,
            stderr=run_log_fh,
            start_new_session=True,
        )
    except Exception:
        try:
            pending_path.unlink()
        except OSError:
            pass
        raise

    pending_path.write_text(str(proc.pid), encoding="utf-8")
    os.replace(str(pending_path), str(pid_path))

    try:
        proc.wait(timeout=2.0)
        run_log_fh.flush()
        try:
            log_text = run_log_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(log_text.splitlines()[-30:]) if log_text else "(no output)"
        except OSError:
            tail = "(could not read log)"
        try:
            pid_path.unlink()
        except OSError:
            pass
        raise HTTPException(
            502,
            detail=f"Sprint subprocess exited immediately (rc={proc.returncode}). Log tail:\n{tail}",
        )
    except subprocess.TimeoutExpired:
        pass

    dispatch_count = sum(1 for d in decisions if d["action"] != "skip")
    skip_count = sum(1 for d in decisions if d["action"] == "skip")
    result: dict = {
        "run_id": run_id,
        "decisions": decisions,
        "dispatch_count": dispatch_count,
        "skip_count": skip_count,
        "pid": proc.pid,
        "log": str(run_log_path),
    }
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

    # Delete the sprint label from GitHub; failure is non-fatal (AC4)
    label_deleted = False
    label_delete_error: str | None = None
    try:
        github_client.delete_label(label, repo_name=repo)
        label_deleted = True
    except Exception as exc:
        label_delete_error = str(exc).strip() or "label deletion failed"
        github_client.invalidate("sprints:")

    await broadcast({
        "type": "update",
        "event": {
            "event_type": "sprint_finished",
            "sprint_label": label,
            "label_deleted": label_deleted,
        },
    })

    result: dict = {"closed": closed, "errors": errors, "label_deleted": label_deleted}
    if label_delete_error is not None:
        result["label_delete_error"] = label_delete_error
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
# Image-only extensions allowed for bulk create attachments (issue #260)
_BULK_IMAGE_EXTS = {'.png', '.jpg', '.jpeg'}

# Body size guard threshold (issue #261)
# GitHub hard limit is 65,536 chars; we use 62,000 as a safety margin.
_BC_BODY_SIZE_THRESHOLD = 62_000


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


def _list_existing_assets(cache_dir: Path) -> set[str]:
    """Return filenames already at references/issue-assets/ on the attachments branch."""
    result = subprocess.run(
        ["git", "ls-tree", "--name-only", f"refs/heads/{_ATTACHMENTS_BRANCH}",
         "references/issue-assets/"],
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


def _commit_assets_to_branch(
    cache_dir: Path,
    file_data: list[tuple[str, bytes]],  # (sanitized_filename, content)
) -> None:
    """Commit image files to references/issue-assets/ on the attachments branch and push."""
    import tempfile as _tf

    def _do_commit():
        parent_result = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{_ATTACHMENTS_BRANCH}"],
            capture_output=True, text=True, cwd=str(cache_dir),
        )
        parent_sha = parent_result.stdout.strip() if parent_result.returncode == 0 else None

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

        idx_file = _tf.NamedTemporaryFile(delete=False, suffix=".idx")
        idx_file.close()
        idx_path = idx_file.name
        try:
            env = {"GIT_INDEX_FILE": idx_path, "HOME": str(Path.home())}
            if parent_tree_sha:
                subprocess.run(
                    ["git", "read-tree", parent_tree_sha],
                    capture_output=True, text=True, cwd=str(cache_dir),
                    env={**env, "GIT_DIR": str(cache_dir)},
                )

            for fname, content in file_data:
                dest_path = f"references/issue-assets/{fname}"
                hash_proc = subprocess.run(
                    ["git", "hash-object", "-w", "--stdin"],
                    input=content, capture_output=True,
                    cwd=str(cache_dir),
                )
                if hash_proc.returncode != 0:
                    raise RuntimeError(f"hash-object failed for {fname}")
                blob_sha = hash_proc.stdout.strip().decode()

                subprocess.run(
                    ["git", "update-index", "--add", "--cacheinfo",
                     f"100644,{blob_sha},{dest_path}"],
                    capture_output=True, text=True, cwd=str(cache_dir),
                    env={**env, "GIT_DIR": str(cache_dir)},
                    check=True,
                )

            write_result = subprocess.run(
                ["git", "write-tree"],
                capture_output=True, text=True, cwd=str(cache_dir),
                env={**env, "GIT_DIR": str(cache_dir)},
            )
            new_tree_sha = write_result.stdout.strip()

            commit_cmd = ["git", "commit-tree", new_tree_sha,
                          "-m", "chore(attachments): add bulk images to issue-assets"]
            if parent_sha:
                commit_cmd += ["-p", parent_sha]
            commit_result = subprocess.run(
                commit_cmd,
                capture_output=True, text=True, cwd=str(cache_dir),
            )
            new_commit_sha = commit_result.stdout.strip()

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

    _do_commit()

    push_result = subprocess.run(
        ["git", "push", "origin",
         f"refs/heads/{_ATTACHMENTS_BRANCH}:refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if push_result.returncode == 0:
        return

    # Retry once on push failure
    subprocess.run(
        ["git", "fetch", "origin", _ATTACHMENTS_BRANCH],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    subprocess.run(
        ["git", "update-ref", f"refs/heads/{_ATTACHMENTS_BRANCH}",
         f"refs/remotes/origin/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    _do_commit()
    retry_push = subprocess.run(
        ["git", "push", "origin",
         f"refs/heads/{_ATTACHMENTS_BRANCH}:refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if retry_push.returncode != 0:
        raise RuntimeError(
            f"Push to attachments branch failed after retry: {retry_push.stderr.strip()}"
        )


def _do_pre_commit_bulk_images(job_id: str, repo: str) -> dict[str, str]:
    """Commit all bulk images to references/issue-assets/ and return {orig_filename: raw_url}.

    Called in a thread before any issues are created so image URLs can be injected
    into issue bodies at POST time (no post-create body update needed).
    """
    attach_dir = _bulk_attachment_dir(job_id)
    if not attach_dir or not attach_dir.exists():
        return {}

    job = _bulk_jobs.get(job_id)
    if not job:
        return {}

    attachment_filenames = job.get("attachment_filenames", [])
    if not attachment_filenames:
        return {}

    _ensure_attachments_branch(repo)
    cache_dir = _init_attachment_cache(repo)
    existing = _list_existing_assets(cache_dir)

    file_data: list[tuple[str, bytes]] = []
    used_names: set[str] = set(existing)
    name_map: dict[str, str] = {}  # orig_filename -> sanitized_final_name

    for fname in attachment_filenames:
        fpath = attach_dir / Path(fname).name
        if not fpath.exists():
            continue
        sanitized = _sanitize_filename(fname)
        final_name = _resolve_collision(sanitized, used_names)
        used_names.add(final_name)
        file_data.append((final_name, fpath.read_bytes()))
        name_map[fname] = final_name

    if file_data:
        _commit_assets_to_branch(cache_dir, file_data)

    url_map: dict[str, str] = {}
    for orig_fname, final_name in name_map.items():
        url = (
            f"https://raw.githubusercontent.com/{repo}/"
            f"{_ATTACHMENTS_BRANCH}/references/issue-assets/{final_name}"
        )
        url_map[orig_fname] = url

    return url_map


def _build_body_with_images(body: str, ticket_index: int, job: dict) -> str:
    """Prepend image markdown links for images assigned to this ticket."""
    url_map = job.get("image_url_map") or {}
    if not url_map:
        return body
    assignments = job.get("image_assignments") or []
    links: list[str] = []
    for a in assignments:
        assignment = a.get("assignment")
        if assignment == "all" or assignment == ticket_index:
            fname = a.get("filename", "")
            url = url_map.get(fname)
            if url:
                links.append(f"![{fname}]({url})")
    if not links:
        return body
    return body + "\n\n" + "\n\n".join(links)


def _parse_ba_draft(output: str) -> tuple[str, str, bool]:
    """Extract title and body from BA agent JSON output.

    Returns (title, body, json_ok) where json_ok is False when JSON parsing
    failed and the raw output was used as a fallback (issue #208).
    """
    # Strip markdown code fence if present
    clean = re.sub(r"^```(?:json)?\s*", "", output.strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s*```\s*$", "", clean.strip(), flags=re.MULTILINE)
    clean = clean.strip()

    try:
        data = json.loads(clean)
        return str(data.get("title", "")), str(data.get("body", "")), True
    except json.JSONDecodeError:
        pass

    # Find outermost {...} block
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(clean[start : end + 1])
            return str(data.get("title", "")), str(data.get("body", "")), True
        except json.JSONDecodeError:
            pass

    first_line = output.split("\n")[0].strip()[:80]
    return first_line or "Draft Ticket", output, False


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

    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=sub_env,
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
        err = stderr.decode("utf-8", errors="replace").strip()[:300]
        out = stdout.decode("utf-8", errors="replace").strip()[:300]
        if not err and not out:
            detail = f"exit code {proc.returncode} with no output"
        elif not err:
            detail = f"exit code {proc.returncode}, stdout: {out}"
        else:
            detail = err
        raise HTTPException(502, detail=f"BA agent failed: {detail}")

    output = stdout.decode("utf-8", errors="replace").strip()
    title, body, json_ok = _parse_ba_draft(output)
    if not json_ok:
        raise HTTPException(
            502,
            detail=(
                "BA returned malformed JSON — could not parse ticket fields. "
                f"Raw output starts with: {output[:120]!r}"
            ),
        )
    return {"draft_id": draft_id, "title": title, "body": body}


class CreateTicketBody(BaseModel):
    draft_id: str = ""
    title: str
    body: str = ""
    project: str = ""
    sprint_label: str = ""
    extra_labels: list[str] = []


# ── Single-ticket background estimator (issue #267) ───────────────────────────

_ESTIMATE_ISSUE_SCRIPT = _SERVICES_DIR / "estimate_issue.py"


async def _run_estimator_for_issue(issue_number: int, repo: str) -> None:
    """Run estimate_issue.py in the background for a single newly created ticket.

    Posts the estimate comment and applies the 'estimated' + size-* labels via
    the resilient update_ticket.py path (issue #267).  Failures are non-fatal:
    a warning comment is posted on the issue and the ticket-creation response is
    never affected.  Invalidates the open-issues cache after completion so the
    size badge appears on the next dashboard load.
    """
    import logging as _logging

    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    returncode: int = -1

    try:
        cmd = [
            sys.executable,
            str(_ESTIMATE_ISSUE_SCRIPT),
            "--issue", str(issue_number),
            "--repo", repo,
            "--save-comment",
            "--save-label",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=240.0
            )
            returncode = proc.returncode or 0
        except asyncio.TimeoutError:
            proc.kill()
            _logging.warning(
                f"[estimator] background estimation for #{issue_number} timed out after 240s"
            )
            # Post warning comment via update_ticket.py resilient path
            _post_estimator_warning(issue_number, repo, "estimation timed out after 240s")
            return
    except Exception as exc:
        _logging.warning(f"[estimator] background estimation for #{issue_number} failed: {exc}")
        _post_estimator_warning(issue_number, repo, str(exc))
        return

    if returncode != 0:
        err = stderr_bytes.decode("utf-8", errors="replace").strip()[:300]
        _logging.warning(
            f"[estimator] estimate_issue.py exited {returncode} for #{issue_number}: {err}"
        )
        _post_estimator_warning(issue_number, repo, f"estimator exited {returncode}: {err or '(no output)'}")
        return

    # Invalidate the issues cache so the size badge appears on next load.
    github_client.invalidate(f"open_issues_body:")
    github_client.invalidate(f"open_issues:")
    github_client.invalidate(f"issues:")


def _post_estimator_warning(issue_number: int, repo: str, reason: str) -> None:
    """Post a warning comment on the issue via gh CLI (fire-and-forget).

    Failures here are silently swallowed — the ticket was already created
    successfully and the estimation failure should not surface as an HTTP error.
    """
    import logging as _logging
    body = (
        f"**Background estimation failed for #{issue_number}.**\n\n"
        f"Reason: {reason}\n\n"
        f"Please run `python3 services/sprint_manager/estimate_issue.py "
        f"--issue {issue_number} --save-comment --save-label` manually, "
        f"or wait for the next sprint estimator run."
    )
    try:
        subprocess.run(
            ["gh", "issue", "comment", str(issue_number), "--repo", repo, "--body", body],
            capture_output=True,
            timeout=30,
        )
    except Exception as exc:
        _logging.warning(f"[estimator] could not post warning comment for #{issue_number}: {exc}")


# ── Bulk-create background estimator (issue #265) ─────────────────────────────

# Global semaphore: max 3 concurrent bulk-estimation tasks (mirrors BA concurrency cap).
# Initialized lazily on first use so it is always created in the correct event loop.
_bulk_estimator_semaphore: asyncio.Semaphore | None = None


def _get_bulk_estimator_semaphore() -> asyncio.Semaphore:
    """Return (or create) the global semaphore for bulk estimation tasks."""
    global _bulk_estimator_semaphore
    if _bulk_estimator_semaphore is None:
        _bulk_estimator_semaphore = asyncio.Semaphore(3)
    return _bulk_estimator_semaphore


def _extract_size_from_estimator_stdout(stdout: str) -> str | None:
    """Parse size (S/M/L/XL) from estimate_issue.py stdout.

    The script prints the JSON estimate after the 'Saved:' line.
    """
    import re as _re
    import logging as _logging
    # Brace-matching scan for the first top-level JSON object in stdout
    start = stdout.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(stdout)):
        if stdout[i] == "{":
            depth += 1
        elif stdout[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(stdout[start : i + 1])
                    size = data.get("size")
                    if size in {"S", "M", "L", "XL"}:
                        return size
                except (json.JSONDecodeError, Exception):
                    pass
                break
    return None


async def _run_bulk_estimator_for_ticket(
    job_id: str,
    index: int,
    issue_number: int,
    repo: str,
) -> None:
    """Run the estimator for one bulk-created ticket and update job state.

    State transitions: created → estimating → sized (S/M/L/XL) | estimate_failed.
    Failures post a per-ticket warning comment without blocking other tickets.
    """
    import logging as _logging

    job = _bulk_jobs.get(job_id)
    if not job:
        return
    ticket = job["tickets"][index]

    # Transition to estimating
    ticket["state"] = "estimating"
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    semaphore = _get_bulk_estimator_semaphore()

    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    returncode: int = -1

    try:
        async with semaphore:
            cmd = [
                sys.executable,
                str(_ESTIMATE_ISSUE_SCRIPT),
                "--issue", str(issue_number),
                "--repo", repo,
                "--save-comment",
                "--save-label",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=240.0
                )
                returncode = proc.returncode or 0
            except asyncio.TimeoutError:
                proc.kill()
                _logging.warning(
                    f"[bulk-estimator] estimation for #{issue_number} (job {job_id}) timed out after 240s"
                )
                _post_estimator_warning(issue_number, repo, "bulk estimation timed out after 240s")
                # Re-fetch job in case it was updated while waiting
                job = _bulk_jobs.get(job_id)
                if job:
                    ticket = job["tickets"][index]
                    ticket["state"] = "estimate_failed"
                    ticket["estimate_error"] = "estimation timed out after 240s"
                    _persist_bulk_job(job)
                    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
                return
    except Exception as exc:
        _logging.warning(f"[bulk-estimator] estimation for #{issue_number} failed: {exc}")
        _post_estimator_warning(issue_number, repo, str(exc))
        job = _bulk_jobs.get(job_id)
        if job:
            ticket = job["tickets"][index]
            ticket["state"] = "estimate_failed"
            ticket["estimate_error"] = str(exc)[:200]
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    job = _bulk_jobs.get(job_id)
    if not job:
        return
    ticket = job["tickets"][index]

    if returncode != 0:
        err = stderr_bytes.decode("utf-8", errors="replace").strip()[:300]
        reason = f"estimator exited {returncode}: {err or '(no output)'}"
        _logging.warning(f"[bulk-estimator] estimate_issue.py exited {returncode} for #{issue_number}: {err}")
        _post_estimator_warning(issue_number, repo, reason)
        ticket["state"] = "estimate_failed"
        ticket["estimate_error"] = reason
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    # Parse size from stdout
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    size = _extract_size_from_estimator_stdout(stdout_text)

    # Transition to sized
    ticket["state"] = "sized"
    ticket["estimate_size"] = size  # "S", "M", "L", "XL", or None
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    # Invalidate open-issues cache so size badge appears on next dashboard load
    github_client.invalidate("open_issues_body:")
    github_client.invalidate("open_issues:")
    github_client.invalidate("issues:")


@app.post("/api/tickets/create", status_code=201)
async def create_ticket_from_draft(
    background_tasks: BackgroundTasks,
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

    # Kick off the estimator as a background task (issue #267).
    # Resolve the repo now (while in the request context) so the background
    # coroutine has a concrete string.
    try:
        est_repo = github_client.get_repo_for_operation(project or None)
    except Exception:
        est_repo = None
    if est_repo and _ESTIMATE_ISSUE_SCRIPT.exists():
        background_tasks.add_task(_run_estimator_for_issue, number, est_repo)

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

# In-memory registry of bulk job attachment temp directories (job_id -> Path)
_bulk_attachment_dirs: dict[str, Path] = {}


def _bulk_attachment_dir(job_id: str) -> Path | None:
    """Return the temp directory holding attachment data for a bulk job, if any."""
    return _bulk_attachment_dirs.get(job_id)


def _cleanup_bulk_attachment_dir(job_id: str) -> None:
    """Remove the temp directory for a bulk job's attachments (best-effort)."""
    d = _bulk_attachment_dirs.pop(job_id, None)
    if d and d.exists():
        shutil.rmtree(d, ignore_errors=True)


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
        _cleanup_bulk_attachment_dir(jid)


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

    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=sub_env,
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
        out = stdout.decode("utf-8", errors="replace").strip()[:300]
        if not err and not out:
            detail = f"exit code {proc.returncode} with no output"
        elif not err:
            detail = f"exit code {proc.returncode}, stdout: {out}"
        else:
            detail = err
        ticket["state"] = "failed"
        ticket["error"] = f"BA agent failed: {detail}"
        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    output = stdout.decode("utf-8", errors="replace").strip()
    title, body, json_ok = _parse_ba_draft(output)

    # If BA output was not valid JSON, surface a clear error (issue #208)
    if not json_ok:
        ticket["state"] = "failed"
        ticket["error"] = (
            "BA returned malformed JSON — could not parse ticket fields. "
            f"Raw output starts with: {output[:120]!r}"
        )
        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

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

    # Pre-commit images before any issues are created so URLs can go in the body directly
    if job.get("has_attachments") and not job.get("image_url_map"):
        try:
            url_map = await asyncio.to_thread(_do_pre_commit_bulk_images, job_id, job["repo"])
            job["image_url_map"] = url_map
            _persist_bulk_job(job)
        except Exception as pre_err:
            logger.warning("Bulk image pre-commit failed: %s", str(pre_err)[:200])
            job["image_url_map"] = {}

    tickets = job["tickets"]
    n = len(tickets)
    flush_idx = 0
    # Track estimation tasks so the flusher can wait for them before job_done (issue #265)
    estimation_tasks: list[asyncio.Task] = []

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
            # Inject image links into body before creating the issue
            issue_repo = ticket.get("_repo") or None
            labels = ["backlog"] + ticket.get("_default_labels", [])
            body_with_images = _build_body_with_images(
                ticket["body"] or "", flush_idx, job
            )

            # Body size guard (issue #261): block POST if over threshold
            if len(body_with_images) > _BC_BODY_SIZE_THRESHOLD:
                ticket["state"] = "size_warning"
                ticket["body"] = body_with_images
                ticket["body_char_count"] = len(body_with_images)
                ticket["body_over_by"] = len(body_with_images) - _BC_BODY_SIZE_THRESHOLD
                ticket["_default_labels"] = labels
                ticket["_repo"] = issue_repo
                ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
                _persist_bulk_job(job)
                await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
                flush_idx += 1
                continue

            created_issue_number: int | None = None
            created_issue_repo: str | None = None
            try:
                number, url = github_client.create_issue(
                    title=ticket["title"],
                    body=body_with_images,
                    labels=labels,
                    repo_name=issue_repo,
                )
                ticket["state"] = "created"
                ticket["issue_num"] = number
                ticket["issue_url"] = url
                ticket["body"] = body_with_images
                ticket["body_preview"] = body_with_images[:200]
                ticket["label_pills"] = labels
                created_issue_number = number
                try:
                    created_issue_repo = issue_repo or github_client.get_repo_for_operation(None)
                except Exception:
                    created_issue_repo = None
            except Exception as e:
                ticket["state"] = "failed"
                ticket["error"] = f"GitHub issue creation failed: {str(e)[:200]}"

            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            # Remove internal fields
            ticket.pop("_default_labels", None)
            ticket.pop("_repo", None)
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

            # Kick off background estimation after successful issue creation (issue #265).
            # Track the task so the flusher waits for all estimates before job_done.
            if created_issue_number is not None and created_issue_repo and _ESTIMATE_ISSUE_SCRIPT.exists():
                est_task = asyncio.create_task(
                    _run_bulk_estimator_for_ticket(
                        job_id, flush_idx, created_issue_number, created_issue_repo
                    )
                )
                estimation_tasks.append(est_task)
            elif created_issue_number is not None:
                # Estimation cannot start — surface a clear reason so job_done still fires
                # instead of leaving the ticket in "created" (non-terminal) forever.
                if not created_issue_repo:
                    _est_err = "could not resolve repository for estimation"
                else:
                    _est_err = "estimate_issue.py not found"
                ticket["state"] = "estimate_failed"
                ticket["estimate_error"] = _est_err
                _persist_bulk_job(job)
                await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

            flush_idx += 1

        elif ticket["state"] in ("pending", "drafting"):
            # Not ready yet — wait a bit
            await asyncio.sleep(0.5)

        else:
            flush_idx += 1

    # Wait for all estimation background tasks to complete before sending job_done.
    # This ensures the SSE stream stays open until all tickets reach a terminal state
    # (sized or estimate_failed). Errors are swallowed — each task handles its own
    # failure path and updates the ticket state independently.
    if estimation_tasks:
        await asyncio.gather(*estimation_tasks, return_exceptions=True)

    # Check if all done (size_warning tickets are not done — they await user remediation).
    # Terminal states now include sized and estimate_failed (issue #265).
    job = _bulk_jobs.get(job_id)
    if job:
        all_done = all(
            t["state"] in ("sized", "estimate_failed", "failed", "skipped", "size_warning")
            for t in job["tickets"]
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

    # Clean up attachment temp dir now that all issues have been processed
    _cleanup_bulk_attachment_dir(job_id)


class BulkCreateBody(BaseModel):
    repo: str
    default_labels: list[str] = []
    prompts: list[str]
    concurrency: int = 3


@app.post("/api/tickets/bulk", status_code=202)
async def bulk_create_start(
    repo: str = Form(...),
    prompts: str = Form(...),
    default_labels: str = Form(default=""),
    concurrency: int = Form(default=3),
    files: list[UploadFile] = File(default=[]),
    assignments: str = Form(default=""),
):
    """Start a bulk ticket creation job.

    Accepts multipart/form-data so optional image files can be included.
    Returns {job_id} immediately; use the SSE stream endpoint to track progress.
    """
    _prune_old_bulk_jobs()

    # Parse prompts JSON array
    try:
        prompts_list: list[str] = json.loads(prompts)
        if not isinstance(prompts_list, list):
            raise ValueError("not a list")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(422, detail="'prompts' must be a JSON array of strings")

    # Parse default_labels JSON array (may be empty string)
    default_labels_list: list[str] = []
    if default_labels.strip():
        try:
            default_labels_list = json.loads(default_labels)
            if not isinstance(default_labels_list, list):
                raise ValueError("not a list")
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(422, detail="'default_labels' must be a JSON array of strings")

    # Validate repo
    projects = projects_module.load_projects()
    if not any(p["repo"] == repo for p in projects):
        raise HTTPException(422, detail=f"Repo '{repo}' is not a configured project")

    # Validate concurrency
    if concurrency not in _ALLOWED_CONCURRENCY:
        raise HTTPException(422, detail=f"Concurrency must be one of {sorted(_ALLOWED_CONCURRENCY)}")

    # Filter blank prompts
    clean_prompts = [p.strip() for p in prompts_list if p.strip()]
    if not clean_prompts:
        raise HTTPException(422, detail="Batch must contain at least one non-blank prompt")
    if len(clean_prompts) > _MAX_BULK_PROMPTS:
        raise HTTPException(
            422,
            detail=f"Batch limit is {_MAX_BULK_PROMPTS} prompts (got {len(clean_prompts)})"
        )

    # Validate default_labels — each must already exist in the repo
    if default_labels_list:
        existing_labels = {lbl["name"] for lbl in github_client.list_labels(repo_name=repo)}
        bad = [lbl for lbl in default_labels_list if lbl not in existing_labels]
        if bad:
            raise HTTPException(
                422,
                detail=f"Unknown labels (not in repo): {', '.join(bad)}"
            )

    # Parse image assignments: [{filename, assignment: "all" | ticket_index}]
    image_assignments: list[dict] = []
    if assignments.strip():
        try:
            parsed_assignments = json.loads(assignments)
            if not isinstance(parsed_assignments, list):
                raise ValueError("not a list")
            for a in parsed_assignments:
                asgn = a.get("assignment", "all")
                image_assignments.append({
                    "filename": str(a.get("filename", "")),
                    "assignment": asgn if asgn == "all" else int(asgn),
                })
        except (json.JSONDecodeError, ValueError, TypeError):
            raise HTTPException(422, detail="'assignments' must be a JSON array")

    # Validate and store uploaded image files (PNG/JPG only for bulk create)
    valid_upload_files = [f for f in files if f.filename]
    attachment_file_data: list[tuple[str, bytes]] = []  # (original_filename, content)
    if valid_upload_files:
        batch_size = 0
        for upload in valid_upload_files:
            ext = Path(upload.filename).suffix.lower()
            if ext not in _BULK_IMAGE_EXTS:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' has disallowed extension '{ext}'. "
                           f"Only PNG and JPG images are allowed.",
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
                    detail="Upload batch exceeds the 50 MB total limit.",
                )
            attachment_file_data.append((upload.filename, content))

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Persist attachment bytes to a temp directory keyed by job_id
    if attachment_file_data:
        attach_dir = Path(tempfile.mkdtemp(prefix=f"bc_attach_{job_id}_"))
        _bulk_attachment_dirs[job_id] = attach_dir
        for orig_name, content in attachment_file_data:
            (attach_dir / Path(orig_name).name).write_bytes(content)

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
            "attachment_warning": None,
            "started_at": None,
            "finished_at": None,
            "retry_count": 0,
            "last_error": None,
        }
        for i, prompt in enumerate(clean_prompts)
    ]

    job = {
        "job_id": job_id,
        "status": "running",
        "repo": repo,
        "default_labels": default_labels_list,
        "concurrency": concurrency,
        "created_at": now,
        "stop_requested": False,
        "has_attachments": len(attachment_file_data) > 0,
        "attachment_filenames": [n for n, _ in attachment_file_data],
        "image_assignments": image_assignments,
        "image_url_map": None,
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
    if ticket["state"] in ("pending", "failed", "size_warning"):
        ticket["state"] = "skipped"
        ticket.pop("_default_labels", None)
        ticket.pop("_repo", None)
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        # Check if job should be marked done now that this blocking ticket is skipped
        all_done = all(
            tt["state"] in ("created", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        has_size_warnings = any(t["state"] == "size_warning" for t in job["tickets"])
        if all_done and not has_size_warnings:
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})
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
        # After BA, create the issue with image links injected into body
        t = job["tickets"][body.index]
        if t.get("state") == "draft_ready":
            issue_repo = t.get("_repo") or None
            labels = ["backlog"] + t.get("_default_labels", [])
            body_with_images = _build_body_with_images(
                t.get("body") or "", body.index, job
            )

            # Body size guard (issue #261)
            if len(body_with_images) > _BC_BODY_SIZE_THRESHOLD:
                t["state"] = "size_warning"
                t["body"] = body_with_images
                t["body_char_count"] = len(body_with_images)
                t["body_over_by"] = len(body_with_images) - _BC_BODY_SIZE_THRESHOLD
                t["_default_labels"] = labels
                t["_repo"] = issue_repo
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
                _persist_bulk_job(job)
                await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
            else:
                try:
                    number, url = github_client.create_issue(
                        title=t["title"],
                        body=body_with_images,
                        labels=labels,
                        repo_name=issue_repo,
                    )
                    t["state"] = "created"
                    t["issue_num"] = number
                    t["issue_url"] = url
                    t["body"] = body_with_images
                    t["body_preview"] = body_with_images[:200]
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
            tt["state"] in ("created", "failed", "skipped", "size_warning") for tt in job["tickets"]
        )
        if all_done:
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})

    asyncio.create_task(_retry_task())
    return {"ok": True}


async def _post_ticket_body_to_github(job_id: str, index: int, body_text: str) -> None:
    """Post a ticket body directly to GitHub (no BA drafting) and update job state."""
    job = _bulk_jobs.get(job_id)
    if not job:
        return
    tickets = job["tickets"]
    if index < 0 or index >= len(tickets):
        return
    t = tickets[index]

    # Transition to drafting state while posting
    t["state"] = "drafting"
    t["started_at"] = datetime.now(timezone.utc).isoformat()
    t["error"] = None
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

    issue_repo = t.get("_repo") or job.get("repo") or None
    labels = ["backlog"] + job.get("default_labels", [])

    # Use the stored title if available, otherwise derive from body first line
    title = t.get("title") or ""
    if not title:
        first_line = (body_text.strip().splitlines() or [""])[0]
        title = first_line.lstrip("# ").strip()[:120] or "Untitled ticket"

    # Body size guard (issue #261)
    if len(body_text) > _BC_BODY_SIZE_THRESHOLD:
        t["state"] = "size_warning"
        t["body"] = body_text
        t["body_char_count"] = len(body_text)
        t["body_over_by"] = len(body_text) - _BC_BODY_SIZE_THRESHOLD
        t["_default_labels"] = labels
        t["_repo"] = issue_repo
        t["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
        # Update job status (size_warning pauses processing)
        all_done = all(
            tt["state"] in ("created", "failed", "skipped", "size_warning") for tt in job["tickets"]
        )
        if all_done:
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})
        return

    try:
        number, url = github_client.create_issue(
            title=title,
            body=body_text,
            labels=labels,
            repo_name=issue_repo,
        )
        t["state"] = "created"
        t["body"] = body_text
        t["issue_num"] = number
        t["issue_url"] = url
        t["body_preview"] = body_text[:200]
        t["label_pills"] = labels
        t["finished_at"] = datetime.now(timezone.utc).isoformat()
        t.pop("_default_labels", None)
        t.pop("_repo", None)
    except Exception as e:
        err_msg = str(e)[:300]
        t["state"] = "failed"
        t["error"] = err_msg
        t["last_error"] = err_msg
        t["retry_count"] = (t.get("retry_count") or 0) + 1
        t["finished_at"] = datetime.now(timezone.utc).isoformat()

    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

    # Update job status
    all_done = all(
        tt["state"] in ("created", "failed", "skipped", "size_warning") for tt in job["tickets"]
    )
    if all_done:
        job["status"] = "done"
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})


class BulkRetryWithBodyBody(BaseModel):
    index: int
    body: str


@app.post("/api/tickets/bulk/{job_id}/retry-with-body")
async def bulk_retry_ticket_with_body(job_id: str, body: BulkRetryWithBodyBody):
    """Retry a failed ticket by POSTing the supplied body directly to GitHub (no BA drafting)."""
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] not in ("failed", "skipped"):
        return {"ok": True, "state": ticket["state"]}

    job["status"] = "running"
    _persist_bulk_job(job)

    asyncio.create_task(_post_ticket_body_to_github(job_id, body.index, body.body))
    return {"ok": True}


@app.post("/api/tickets/bulk/{job_id}/retry-with-image")
async def bulk_retry_with_image(
    job_id: str,
    index: int = Form(...),
    body_text: str = Form(default=""),
    file: UploadFile = File(default=None),
):
    """Retry a failed ticket with an optional new image committed to issue-assets."""
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if index < 0 or index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[index]
    if ticket["state"] not in ("failed", "skipped"):
        return {"ok": True, "state": ticket["state"]}

    final_body = body_text or ticket.get("body") or ""

    # Commit new image and append its URL to body
    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in _BULK_IMAGE_EXTS:
            raise HTTPException(
                422,
                detail=f"Only PNG and JPG images are allowed (got '{ext}').",
            )
        content = await file.read()
        if len(content) > _MAX_FILE_SIZE_BYTES:
            raise HTTPException(422, detail="File exceeds the 25 MB per-file limit.")

        repo = job["repo"]
        sanitized = _sanitize_filename(file.filename)

        def _commit_single_image() -> str:
            _ensure_attachments_branch(repo)
            cache_dir = _init_attachment_cache(repo)
            existing = _list_existing_assets(cache_dir)
            final_name = _resolve_collision(sanitized, existing)
            _commit_assets_to_branch(cache_dir, [(final_name, content)])
            return (
                f"https://raw.githubusercontent.com/{repo}/"
                f"{_ATTACHMENTS_BRANCH}/references/issue-assets/{final_name}"
            )

        try:
            img_url = await asyncio.to_thread(_commit_single_image)
            final_body = final_body + f"\n\n![{file.filename}]({img_url})"
        except Exception as e:
            raise HTTPException(500, detail=f"Image commit failed: {str(e)[:200]}")

    if len(final_body) > 65536:
        final_body = final_body[:65536]

    job["status"] = "running"
    _persist_bulk_job(job)

    asyncio.create_task(_post_ticket_body_to_github(job_id, index, final_body))
    return {"ok": True}


class BulkRetryAllBody(BaseModel):
    bodies: dict[str, str]  # str(index) -> body text


@app.post("/api/tickets/bulk/{job_id}/retry-all")
async def bulk_retry_all_failed(job_id: str, body: BulkRetryAllBody):
    """Retry all failed tickets by POSTing each ticket's edited body directly to GitHub."""
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]

    retried = 0
    for idx_str, body_text in body.bodies.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx < 0 or idx >= len(tickets):
            continue
        t = tickets[idx]
        if t["state"] != "failed":
            continue
        job["status"] = "running"
        asyncio.create_task(_post_ticket_body_to_github(job_id, idx, body_text))
        retried += 1

    if retried > 0:
        _persist_bulk_job(job)

    return {"ok": True, "retried": retried}


# ── Body-size remediation endpoints (issue #261) ─────────────────────────────

class SizeRemedyCommentBody(BaseModel):
    index: int


@app.post("/api/tickets/bulk/{job_id}/size-remedy-comment")
async def bulk_size_remedy_comment(job_id: str, body: SizeRemedyCommentBody):
    """Remediation: create issue with body trimmed to threshold, post overflow as comment.

    Accepts a ticket in size_warning state, creates the issue with a trimmed body,
    then immediately posts the overflow as a follow-up comment.
    """
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] != "size_warning":
        return {"ok": True, "state": ticket["state"]}

    full_body = ticket.get("body") or ""
    title = ticket.get("title") or "Untitled ticket"
    issue_repo = ticket.get("_repo") or job.get("repo") or None
    labels = ticket.get("_default_labels") or (["backlog"] + job.get("default_labels", []))

    # Trim body to fit within threshold, appending a note about the overflow
    overflow_note = "\n\n---\n*Body exceeded size limit — continued in first comment.*"
    max_trimmed = _BC_BODY_SIZE_THRESHOLD - len(overflow_note)
    trimmed_body = full_body[:max_trimmed] + overflow_note
    overflow_content = full_body[max_trimmed:]

    # Transition to drafting state while posting
    ticket["state"] = "drafting"
    ticket["started_at"] = datetime.now(timezone.utc).isoformat()
    ticket["error"] = None
    _persist_bulk_job(job)
    job["status"] = "running"
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _remedy_task():
        try:
            number, url = await asyncio.to_thread(
                github_client.create_issue,
                title=title,
                body=trimmed_body,
                labels=labels,
                repo_name=issue_repo,
            )
            # Post overflow as comment
            comment_body = f"*Continued from issue body (overflow content):*\n\n{overflow_content}"
            await asyncio.to_thread(
                github_client.add_comment,
                issue_id=number,
                body=comment_body,
                repo_name=issue_repo,
            )
            ticket["state"] = "created"
            ticket["issue_num"] = number
            ticket["issue_url"] = url
            ticket["body"] = trimmed_body
            ticket["body_preview"] = trimmed_body[:200]
            ticket["label_pills"] = labels
            ticket.pop("_default_labels", None)
            ticket.pop("_repo", None)
            ticket.pop("body_char_count", None)
            ticket.pop("body_over_by", None)
        except Exception as e:
            ticket["state"] = "failed"
            ticket["error"] = f"Size remedy failed: {str(e)[:200]}"
            ticket["last_error"] = ticket["error"]
            ticket["retry_count"] = (ticket.get("retry_count") or 0) + 1

        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

        all_done = all(
            tt["state"] in ("created", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        has_size_warnings = any(t["state"] == "size_warning" for t in job["tickets"])
        if all_done and not has_size_warnings:
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})

    asyncio.create_task(_remedy_task())
    return {"ok": True}


class SizeRemedyImagesBody(BaseModel):
    index: int


def _extract_and_replace_base64_images(body_text: str, repo: str) -> tuple[str, int]:
    """Find all base64/data-URI images in body, upload to attachments branch, replace with links.

    Returns (updated_body, image_count).
    """
    import re
    import base64 as _base64
    import hashlib

    # Match markdown image syntax with data URI: ![alt](data:image/...;base64,...)
    # Also matches bare data URIs in <img src="data:..."> or plain data:... references
    pattern = re.compile(
        r'!\[([^\]]*)\]\(data:image/([a-zA-Z]+);base64,([A-Za-z0-9+/=\s]+)\)'
    )

    _ensure_attachments_branch(repo)
    cache_dir = _init_attachment_cache(repo)
    existing = set(_list_existing_assets(cache_dir))

    replacements: list[tuple[str, str, str, str]] = []  # (full_match, alt, ext, raw_url)
    file_data: list[tuple[str, bytes]] = []
    used_names: set[str] = set(existing)

    for m in pattern.finditer(body_text):
        full_match = m.group(0)
        alt = m.group(1)
        ext = m.group(2).lower()
        b64_data = re.sub(r'\s', '', m.group(3))
        try:
            img_bytes = _base64.b64decode(b64_data)
        except Exception:
            continue  # skip if decode fails

        # Use SHA256 hash as filename to avoid duplicates
        digest = hashlib.sha256(img_bytes).hexdigest()[:16]
        fname = f"img-{digest}.{ext}"
        final_name = _resolve_collision(fname, used_names)
        used_names.add(final_name)
        file_data.append((final_name, img_bytes))

        raw_url = (
            f"https://raw.githubusercontent.com/{repo}/"
            f"{_ATTACHMENTS_BRANCH}/references/issue-assets/{final_name}"
        )
        replacements.append((full_match, alt, ext, raw_url))

    if not file_data:
        return body_text, 0

    _commit_assets_to_branch(cache_dir, file_data)

    # Replace each match with a markdown link
    updated = body_text
    for i, (full_match, alt, _ext, raw_url) in enumerate(replacements):
        link_alt = alt or f"image-{i + 1}"
        updated = updated.replace(full_match, f"![{link_alt}]({raw_url})", 1)

    return updated, len(replacements)


@app.post("/api/tickets/bulk/{job_id}/size-remedy-images")
async def bulk_size_remedy_images(job_id: str, body: SizeRemedyImagesBody):
    """Remediation: extract base64 images from body, upload to attachments branch, replace with links.

    After replacement the body length is rechecked. If still over threshold the ticket stays in
    size_warning state with updated counts. If under threshold the issue is created immediately.
    """
    job = _bulk_jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] != "size_warning":
        return {"ok": True, "state": ticket["state"]}

    full_body = ticket.get("body") or ""
    title = ticket.get("title") or "Untitled ticket"
    issue_repo = ticket.get("_repo") or job.get("repo") or None
    labels = ticket.get("_default_labels") or (["backlog"] + job.get("default_labels", []))
    repo = job.get("repo") or ""

    # Transition to drafting while processing
    ticket["state"] = "drafting"
    ticket["started_at"] = datetime.now(timezone.utc).isoformat()
    ticket["error"] = None
    _persist_bulk_job(job)
    job["status"] = "running"
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _image_remedy_task():
        try:
            updated_body, img_count = await asyncio.to_thread(
                _extract_and_replace_base64_images, full_body, repo
            )
        except Exception as e:
            ticket["state"] = "size_warning"
            ticket["body"] = full_body
            ticket["body_char_count"] = len(full_body)
            ticket["body_over_by"] = len(full_body) - _BC_BODY_SIZE_THRESHOLD
            ticket["_default_labels"] = labels
            ticket["_repo"] = issue_repo
            ticket["error"] = f"Image upload failed: {str(e)[:200]}"
            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            return

        if img_count == 0:
            # No images found — go back to size_warning with same body
            ticket["state"] = "size_warning"
            ticket["body"] = full_body
            ticket["body_char_count"] = len(full_body)
            ticket["body_over_by"] = len(full_body) - _BC_BODY_SIZE_THRESHOLD
            ticket["_default_labels"] = labels
            ticket["_repo"] = issue_repo
            ticket["error"] = "No inlined base64 images found to convert"
            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            return

        # Recheck size after image replacement
        if len(updated_body) > _BC_BODY_SIZE_THRESHOLD:
            # Still over — stay in size_warning with updated body and counts
            ticket["state"] = "size_warning"
            ticket["body"] = updated_body
            ticket["body_char_count"] = len(updated_body)
            ticket["body_over_by"] = len(updated_body) - _BC_BODY_SIZE_THRESHOLD
            ticket["_default_labels"] = labels
            ticket["_repo"] = issue_repo
            ticket["error"] = None
            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            return

        # Body now fits — create the issue
        try:
            number, url = await asyncio.to_thread(
                github_client.create_issue,
                title=title,
                body=updated_body,
                labels=labels,
                repo_name=issue_repo,
            )
            ticket["state"] = "created"
            ticket["issue_num"] = number
            ticket["issue_url"] = url
            ticket["body"] = updated_body
            ticket["body_preview"] = updated_body[:200]
            ticket["label_pills"] = labels
            ticket.pop("_default_labels", None)
            ticket.pop("_repo", None)
            ticket.pop("body_char_count", None)
            ticket.pop("body_over_by", None)
            ticket["error"] = None
        except Exception as e:
            ticket["state"] = "failed"
            ticket["error"] = f"GitHub issue creation failed: {str(e)[:200]}"
            ticket["last_error"] = ticket["error"]
            ticket["retry_count"] = (ticket.get("retry_count") or 0) + 1

        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

        all_done = all(
            tt["state"] in ("created", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        has_size_warnings = any(t["state"] == "size_warning" for t in job["tickets"])
        if all_done and not has_size_warnings:
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})

    asyncio.create_task(_image_remedy_task())
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


# ── deploy / promote endpoint ─────────────────────────────────────────────────

_PROMOTE_SCRIPT_ROOT = Path(__file__).parent.parent.parent  # apps/dashboard -> apps -> repo root


class PromoteBody(BaseModel):
    draft: bool = True


@app.post("/api/deploy/promote")
def promote_to_master(body: PromoteBody):
    """Shell out to scripts/promote_to_master.py and return the PR URL.

    Returns: {"pr_url": "<url>"}
    Exit codes from the script: 0 = success, 1 = precondition, 2 = GitHub API failure.
    """
    script_path = _PROMOTE_SCRIPT_ROOT / "scripts" / "promote_to_master.py"
    if not script_path.exists():
        raise HTTPException(500, detail="promote_to_master.py not found")

    cmd = [sys.executable, str(script_path)]
    if body.draft:
        cmd.append("--draft")
    else:
        cmd.append("--ready")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_PROMOTE_SCRIPT_ROOT))

    if result.returncode == 0:
        pr_url = result.stdout.strip()
        return {"pr_url": pr_url}
    elif result.returncode == 1:
        err = result.stderr.strip() or result.stdout.strip() or "Precondition failed"
        raise HTTPException(400, detail=err)
    else:
        err = result.stderr.strip() or result.stdout.strip() or "GitHub API failure"
        raise HTTPException(502, detail=err)


# ── Static asset routes with long-lived cache headers (issue #249) ────────────
# Explicit routes for JS and CSS files must appear before the StaticFiles mount.
# Browsers can cache these indefinitely because the build hash in the query
# string changes whenever the file content changes.

@app.get("/static/{filename:path}")
async def static_assets(filename: str):
    """Serve static files with appropriate cache headers.

    JS and CSS files get Cache-Control: public, max-age=31536000, immutable
    because index.html always references them with a versioned query string.
    Other files fall through to a plain FileResponse with no special caching.
    """
    file_path = STATIC_DIR / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    ext = file_path.suffix.lower()
    if ext in (".js", ".css"):
        return FileResponse(
            file_path,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
    return FileResponse(file_path)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
