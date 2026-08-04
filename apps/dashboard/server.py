"""FastAPI app factory — thin entry point for the Commander dashboard (issue #1267).

All route handlers live in ``apps/dashboard/routers/``.  This file owns only
app construction, middleware, lifespan wiring, and ``include_router`` calls.
Helper functions used by router files are defined in ``startup.py`` and are
injected into this module's namespace so ``_server().<helper>`` calls work.
"""
import asyncio
import importlib.util as _importlib_util
import logging
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional


# ── Dependency auto-install (must precede any third-party import) ─────────────

def _auto_install_deps() -> None:
    """Install requirements.txt if any key dependency is missing."""
    if _importlib_util.find_spec("fastapi") is not None:
        return
    _repo_root = Path(__file__).parent.parent.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))
    from services.logging import log as _install_log  # noqa: PLC0415
    _req = _repo_root / "requirements.txt"
    _result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(_req)],
        capture_output=True, text=True,
    )
    if _result.returncode == 0:
        _install_log.info("deps_auto_install", f"auto-installed from {_req}",
                          output=_result.stdout.strip() or None)
    else:
        _install_log.error("deps_auto_install", f"pip install failed: {_result.stderr.strip()}")
        sys.exit(_result.returncode)


_auto_install_deps()


# ── Third-party and local imports ─────────────────────────────────────────────

from dotenv import load_dotenv  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

# Load .env before importing local modules so DB_PATH and other vars are set.
load_dotenv(Path(__file__).parent / ".env")

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]

import db  # noqa: E402
import github_client  # noqa: E402
import github_events_sync  # noqa: E402
import sprint_state  # noqa: E402
import projects as projects_module  # noqa: E402
import live_metrics as _live_metrics  # noqa: E402

from services.logging import log as _slog, setup_logging as _setup_logging  # noqa: E402

try:
    _setup_logging()
except Exception:
    pass


# ── Inject startup.py helpers into this module's namespace ───────────────────
# startup.py is server.py with route handlers, lifespan, and the app factory
# block stripped out.  Copying its namespace here ensures router files that do
# ``_server().<helper>`` find every function and global they need.

import startup as _startup_module  # noqa: E402
sys.modules[__name__].__dict__.update(
    {k: v for k, v in vars(_startup_module).items() if not k.startswith("__")}
)
del _startup_module

# Explicit re-imports from startup so ruff can resolve these names statically.
from startup import (  # noqa: E402
    AGENT_IDLE_TIMEOUT_SECONDS,
    ENVIRONMENT,
    STATIC_DIR,
    _BACKUP_AVAILABLE,
    _GIT_BRANCH,
    _GIT_SHA,
    _TIMEOUT_CHECK_INTERVAL,
    _backup_module,
    _check_gh_auth,
    _mark_inflight_jobs_failed,
    _mirror_sync_repos,
    _periodic_orphan_sweep_loop,
    _restore_sprint_statuses_on_startup,
    _status_md_sync_loop,
    _sweep_orphan_db_running_rows,
    _sweep_orphan_pid_files,
    _validate_github_repos,
    _warn_nonconforming_sprint_labels,
)

# Override the logger so log messages show "server" instead of "startup".
logger = logging.getLogger(__name__)


# ── Background tasks ─────────────────────────────────────────────────────────
# Redefined here (overriding startup's copies) so broadcast is resolved lazily
# at call time, after all routers are fully registered.

async def _cache_refresh_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            from routers.logs_service import broadcast as _bc  # noqa: PLC0415
            await _bc({"type": "update", "event": {"event_type": "cache_refresh"}})
        except Exception:
            pass


async def _timeout_loop() -> None:
    while True:
        await asyncio.sleep(_TIMEOUT_CHECK_INTERVAL)
        try:
            count = db.timeout_idle_agents(AGENT_IDLE_TIMEOUT_SECONDS)
            if count:
                from routers.logs_service import broadcast as _bc  # noqa: PLC0415
                await _bc({"type": "update", "event": {"event_type": "agent_timeout", "count": count}})
        except Exception:
            pass


_DB_QUICK_CHECK_INTERVAL = 1800  # 30 minutes


async def _periodic_db_integrity_loop() -> None:
    """Background task: run PRAGMA quick_check every 30 min; log CRITICAL and broadcast on corruption.

    AC5 of issue #2037: corruption sat undetected for ~3 hours because the running
    process masked it and only startup re-ran the check.  This loop catches it while
    the server is live so operators are alerted within one check interval.
    """
    await asyncio.sleep(120)  # startup already ran _startup_integrity_check; wait before first check
    while True:
        try:
            status = db.alert_if_corrupt()
            if status != "ok":
                try:
                    from routers.logs_service import broadcast as _bc  # noqa: PLC0415
                    await _bc({
                        "type": "update",
                        "event": {"event_type": "db_corruption_alert", "status": status},
                    })
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("[db-integrity] periodic quick_check error: %s", exc)
        await asyncio.sleep(_DB_QUICK_CHECK_INTERVAL)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    _slog.event(
        "server.startup", project="dashboard", request_id=str(uuid.uuid4()),
        environment=ENVIRONMENT, git_sha=_GIT_SHA, git_branch=_GIT_BRANCH,
    )
    db.init_db()
    try:
        from routers.board_cache import set_main_loop as _set_board_cache_loop  # noqa: PLC0415
        _set_board_cache_loop(asyncio.get_event_loop())
    except Exception:
        pass
    _check_gh_auth()
    _validate_github_repos()
    _sweep_orphan_pid_files()
    _sweep_orphan_db_running_rows()
    _restore_sprint_statuses_on_startup()
    _warn_nonconforming_sprint_labels()
    await _mark_inflight_jobs_failed()
    if _BACKUP_AVAILABLE:
        try:
            _backup_module.start_backup_scheduler()
            _backup_module.schedule_startup_backup(delay_seconds=30)
        except Exception:
            pass
    task1 = asyncio.create_task(_cache_refresh_loop())
    task2 = asyncio.create_task(_timeout_loop())
    task3 = asyncio.create_task(_periodic_orphan_sweep_loop())
    task4 = asyncio.create_task(_status_md_sync_loop())
    task6 = asyncio.create_task(_periodic_db_integrity_loop())
    _bootstrap_repos = _mirror_sync_repos()
    await asyncio.to_thread(github_events_sync.bootstrap_full_sync, _bootstrap_repos)
    task5 = asyncio.create_task(github_events_sync.run_issues_sync_loop(_bootstrap_repos))
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    task5.cancel()
    task6.cancel()
    for t in (task1, task2, task3, task4, task5, task6):
        try:
            await t
        except asyncio.CancelledError:
            pass
    _slog.event(
        "server.shutdown", project="dashboard", request_id=str(uuid.uuid4()),
        environment=ENVIRONMENT, git_sha=_GIT_SHA, git_branch=_GIT_BRANCH,
        uptime_seconds=round(time.monotonic() - _start_time, 1),
    )


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(lifespan=lifespan)

from routers import (  # noqa: E402
    agent_guide_router,
    api_volume_router,
    docs_router,
    changelog_router,
    estimate_jobs_router,
    activity_router,
    analytics_router,
    backup_router,
    bulk_tickets_router,
    calibration_router,
    deploy_router,
    doctor_router,
    environments_router,
    estimates_router,
    finish_card_router,
    finish_progress_router,
    home_milestone_router,
    issues_router,
    log_search_router,
    logs_router,
    maintenance_router,
    metrics_router,
    milestones_router,
    mis_sizing_router,
    xl_suggestions_router,
    sprint_collisions_router,
    resolve_conflict_router,
    llm_provider_router,
    running_router,
    dev_report_router,
    failures_router,
    brain_router,
    token_usage_debug_router,
    pages_router,
    project_branches_router,
    projects_router,
    runs_router,
    settings_router,
    settings_sync_router,
    signoff_router,
    sprint_crud_router,
    sprint_dispatch_router,
    sprint_finish_router,
    sprint_history_router,
    sprint_labels_router,
    sprint_live_router,
    sprint_nav_router,
    sprint_planning_router,
    sprint_preflight_router,
    sprint_run_router,
    sprint_summaries_router,
    sprints_router,
    status_router,
    system_misc_router,
    system_router,
    tickets_router,
    timeline_router,
)
from routers.bulk_tickets import _get_bulk_job  # noqa: E402
from routers.logs_service import broadcast, _subscribers  # noqa: E402
from routers.milestones_service import resolve_bulk_milestone as _resolve_bulk_milestone  # noqa: E402

app.include_router(api_volume_router)
app.include_router(token_usage_debug_router)
app.include_router(pages_router)
app.include_router(activity_router)
app.include_router(analytics_router)
app.include_router(backup_router)
app.include_router(calibration_router)
app.include_router(deploy_router)
app.include_router(doctor_router)
app.include_router(environments_router)
app.include_router(estimates_router)
app.include_router(finish_card_router)
app.include_router(finish_progress_router)
app.include_router(issues_router)
app.include_router(log_search_router)
app.include_router(logs_router)
app.include_router(maintenance_router)
app.include_router(metrics_router)
app.include_router(milestones_router)
app.include_router(mis_sizing_router)
app.include_router(xl_suggestions_router)
app.include_router(sprint_collisions_router)
app.include_router(projects_router)
app.include_router(runs_router)
# scheduler_router is mounted via routers/sprints.py (rides the already-mounted
# sprints router by design); this app-level include was a duplicate — removed.
app.include_router(settings_router)
app.include_router(settings_sync_router)
app.include_router(signoff_router)
app.include_router(sprint_crud_router)
app.include_router(sprint_dispatch_router)
app.include_router(sprint_finish_router)
app.include_router(sprint_history_router)
app.include_router(sprint_labels_router)
app.include_router(sprint_live_router)
app.include_router(sprint_nav_router)
app.include_router(sprint_planning_router)
app.include_router(sprint_preflight_router)
app.include_router(sprint_run_router)
app.include_router(sprint_summaries_router)
app.include_router(sprints_router)
app.include_router(status_router)
app.include_router(system_router)
app.include_router(system_misc_router)
app.include_router(tickets_router)
app.include_router(timeline_router)
app.include_router(home_milestone_router)
app.include_router(project_branches_router)
app.include_router(bulk_tickets_router)
app.include_router(resolve_conflict_router)
app.include_router(llm_provider_router)
app.include_router(running_router)
app.include_router(dev_report_router)
app.include_router(docs_router)
app.include_router(agent_guide_router)
app.include_router(changelog_router)
app.include_router(estimate_jobs_router)
app.include_router(failures_router)
app.include_router(brain_router)


# ── Middleware ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def _bearer_auth(request: Request, call_next):
    from routers.auth import bearer_auth_gate  # noqa: PLC0415
    if (early := bearer_auth_gate(request)) is not None:
        return early
    return await call_next(request)


@app.middleware("http")
async def _attach_request_id(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    return await call_next(request)


@app.middleware("http")
async def add_api_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.middleware("http")
async def _count_request_paths(request: Request, call_next):
    response = await call_next(request)
    route = request.scope.get("route")
    path = (
        route.path
        if (route is not None and hasattr(route, "path"))
        else request.url.path
    )
    import api_volume as _av  # noqa: PLC0415
    _av.record_request(path)
    return response


# ── Static files ──────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
