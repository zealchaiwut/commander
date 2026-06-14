import asyncio
import hashlib
import importlib.util as _importlib_util
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
        capture_output=True,
        text=True,
    )
    if _result.returncode == 0:
        _install_log.info(
            "deps_auto_install",
            f"auto-installed dependencies from {_req}",
            output=_result.stdout.strip() or None,
        )
    else:
        _install_log.error(
            "deps_auto_install",
            f"pip install failed (exit {_result.returncode}): {_result.stderr.strip()}",
        )
        sys.exit(_result.returncode)


_auto_install_deps()

try:
    import psutil as _psutil
except ImportError:
    _psutil = None  # type: ignore[assignment]

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, model_validator

# Load .env before importing local modules so that DB_PATH and other env vars
# are available when db.py executes its module-level startup checks.
load_dotenv(Path(__file__).parent / ".env")

import db
import github_client
import github_events_sync
import live_metrics as _live_metrics
import projects as projects_module

# Structured event logging (services/logging.py at repo root)
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from services.logging import log as _slog, setup_logging as _setup_logging

# Install size-rotating prd.log handler at import time so the uvicorn worker
# (which imports server:app) is covered without disk-exhaustion risk (issue #762).
try:
    _setup_logging()
except Exception:  # logging must never break startup
    pass
from services.sprint_manager.estimate_issue import (
    fetch_issue as _ei_fetch_issue,
    run_estimator as _ei_run_estimator,
    apply_label as _ei_apply_label,
    apply_estimated_status as _ei_apply_estimated_status,
)
from services.sprint_manager import fill_acceptance_criteria as _fill_ac
from services.sprint_manager.state_machine import (
    TicketState as _TicketState,
    transition as _sm_transition,
    TransitionError as _TransitionError,
)

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
    from services.sprint_manager.settings_sync import (
        load_local_snapshot as _ss_load_local,
        load_neon_snapshot as _ss_load_neon,
        compute_diff as _ss_compute_diff,
        is_already_in_sync as _ss_already_in_sync,
        apply_upload as _ss_apply_upload,
        apply_fetch as _ss_apply_fetch,
        get_sync_status as _ss_get_status,
        save_sync_status as _ss_save_status,
    )
    _SYNC_SETTINGS_AVAILABLE = True
except Exception:
    _SYNC_SETTINGS_AVAILABLE = False

# Neon dual-write was removed in issue #758 — SQLite + local JSON is the primary
# (and only live) store. Neon is now an optional export target reached solely via
# scripts/export_to_neon.py, so there is no startup sync or per-flow Neon write to
# disable here.

from sizing import SIZE_TO_MINUTES as _SIZE_TO_MINUTES, letter_from_minutes as _letter_from_minutes, minutes_from_letter as _minutes_from_letter

try:
    _SCAFFOLD_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
    if str(_SCAFFOLD_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCAFFOLD_SCRIPTS_DIR))
    from scaffold_project import scaffold_data as _scaffold_data
    _SCAFFOLD_AVAILABLE = True
except ImportError:
    _scaffold_data = None  # type: ignore[assignment]
    _SCAFFOLD_AVAILABLE = False

try:
    import clean_sprint_files as _clean_sprint_files
    _CLEAN_SPRINT_AVAILABLE = True
except ImportError:
    _clean_sprint_files = None  # type: ignore[assignment]
    _CLEAN_SPRINT_AVAILABLE = False

try:
    import mis_sizing as _mis_sizing
    _MIS_SIZING_AVAILABLE = True
except ImportError:
    _mis_sizing = None  # type: ignore[assignment]
    _MIS_SIZING_AVAILABLE = False

try:
    from dag_builder import CycleError as _CycleError, build_dag as _build_dag
    _DAG_BUILDER_AVAILABLE = True
except ImportError:
    _CycleError = None  # type: ignore[assignment,misc]
    _build_dag = None  # type: ignore[assignment]
    _DAG_BUILDER_AVAILABLE = False


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
_orphans_removed_total: int = 0


# ── Git startup metadata (issue #329) ─────────────────────────────────────────
# Captured once at process start; never re-runs git per request.

def _capture_git_value(cmd: list) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "unknown"


_GIT_SHA: str = _capture_git_value(["git", "rev-parse", "HEAD"])
_GIT_BRANCH: str = _capture_git_value(["git", "rev-parse", "--abbrev-ref", "HEAD"])
_GIT_COMMIT_MSG: str = _capture_git_value(["git", "log", "-1", "--pretty=%s"])
_STARTED_AT: str = datetime.now(timezone.utc).isoformat()
_BUILD_TIMESTAMP: str = _STARTED_AT


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

# ── Per-env last-deploy timestamps (persisted across requests, reset on restart) ─
_DEPLOY_TIMES_FILE = Path(__file__).parent / "runtime" / "deploy-times.json"

def _load_deploy_times() -> dict:
    try:
        return json.loads(_DEPLOY_TIMES_FILE.read_text()) if _DEPLOY_TIMES_FILE.exists() else {}
    except Exception:
        return {}

def _save_deploy_time(slug: str, env: str) -> None:
    times = _load_deploy_times()
    times[f"{slug}/{env}"] = datetime.now(timezone.utc).isoformat()
    try:
        _DEPLOY_TIMES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DEPLOY_TIMES_FILE.write_text(json.dumps(times))
    except Exception:
        pass

_deploy_times: dict = _load_deploy_times()

# ── GitHub CLI auth preflight state (issue #424) ──────────────────────────────
# Populated once at startup by _check_gh_auth(); served via /api/gh-auth-status.
_GH_AUTH_STATUS: dict = {"ok": True, "message": ""}


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
    """Scan all projects' PID files and remove orphans.

    A PID file is orphaned when:
    - The process no longer exists (ProcessLookupError from os.kill(pid, 0))
    - The process exists but its argv doesn't contain sprint_manager.py followed
      by the expected sprint label (PID reuse by an unrelated process)

    Live sprint_manager.py processes for the correct label are left untouched.
    """
    global _orphans_removed_total
    sweep_start = time.monotonic()
    scanned = 0
    cleaned = 0

    def _remove_orphan(pid_file: Path, pid: int | None, reason: str) -> None:
        nonlocal cleaned
        global _orphans_removed_total
        _slog.event(
            "orphan_pid_detected",
            project="dashboard",
            event="orphan_pid_detected",
            pid=pid,
            file_path=str(pid_file),
            reason=reason,
        )
        try:
            pid_file.unlink()
        except OSError:
            pass
        cleaned += 1
        _orphans_removed_total += 1

    try:
        projects = projects_module.load_projects()
    except Exception as exc:
        logger.warning("[startup-sweep] could not load projects: %s", exc)
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
                except (ValueError, OSError) as exc:
                    logger.warning("[startup-sweep] malformed PID file %s: %s", pid_file, exc)
                    _remove_orphan(pid_file, None, "unreadable")
                    continue

                # Check if the process exists.
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    _remove_orphan(pid_file, pid, "process_not_found")
                    continue
                except PermissionError as exc:
                    # Process exists but we can't signal it (different user).
                    logger.warning("[startup-sweep] permission denied checking pid %s in %s: %s", pid, pid_file, exc)
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
                    _remove_orphan(pid_file, pid, "pid_reuse")
        except Exception as exc:
            logger.warning("[startup-sweep] error scanning project %s: %s", proj.get("repo"), exc)

    elapsed_ms = (time.monotonic() - sweep_start) * 1000
    print(f"[startup-sweep] scanned {scanned} PID files, cleaned {cleaned} orphans in {elapsed_ms:.1f}ms")

    # Reconcile any plan.json files left in state=running with no alive PID (issue #507)
    _sweep_plan_json_states(projects)


def _sweep_plan_json_states(projects: list) -> None:
    """Reconcile plan.json files with state=running that have no alive PID (issue #507).

    Called once on startup after PID sweeping completes.  Any sprint whose
    plan.json says running but whose PID is dead gets reconciled to needs_rework.
    """
    reconciled = 0
    for proj in projects:
        try:
            project_root = _project_root_path(proj["repo"])
            sprints_dir = _commander_dir(project_root) / "sprints"
            if not sprints_dir.exists():
                continue
            for plan_file in sprints_dir.glob("*-plan.json"):
                label = plan_file.name.removesuffix("-plan.json")
                try:
                    data = json.loads(plan_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict) or data.get("state") != "running":
                    continue
                pid_file    = sprints_dir / f"{label}-pid"
                pending_file = sprints_dir / f"{label}-pid.pending"
                pid_alive = False
                for candidate in (pid_file, pending_file):
                    if not candidate.exists():
                        continue
                    try:
                        raw = candidate.read_text(encoding="utf-8").strip()
                        if raw in ("", "0"):
                            pid_alive = True
                            break
                        pid = int(raw)
                        os.kill(pid, 0)
                        pid_alive = True
                        break
                    except (ProcessLookupError, ValueError, OSError):
                        pass
                    except PermissionError:
                        pid_alive = True
                        break
                if not pid_alive:
                    data["state"] = "needs_rework"
                    data["end_reason"] = "process lost"
                    try:
                        tmp = plan_file.with_suffix(".json.tmp")
                        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                        os.replace(str(tmp), str(plan_file))
                        reconciled += 1
                        print(f"[startup-sweep] reconciled {label} plan.json: running→needs_rework (PID dead)")
                    except Exception as exc:
                        print(f"[startup-sweep] could not reconcile {label} plan.json: {exc}")
        except Exception as exc:
            print(f"[startup-sweep] plan.json sweep error for {proj.get('repo')}: {exc}")
    if reconciled:
        print(f"[startup-sweep] reconciled {reconciled} stale running plan.json entries")


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
    archived_total = 0

    for proj in projects:
        try:
            project_root = _project_root_path(proj["repo"])
            sprints_dir  = _commander_dir(project_root) / "sprints"
            if not sprints_dir.exists():
                continue

            # Archived sprint files live in .commander/sprints/archive/ and are
            # intentionally skipped by the per-file scans below (glob is
            # non-recursive). Count them once so we can emit a single summary
            # line instead of one skip line per archived file (issue #735).
            archive_dir = sprints_dir / "archive"
            if archive_dir.is_dir():
                archived_total += sum(1 for p in archive_dir.iterdir() if p.is_file())

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

            # Second pass: pick up running sprints whose sprint_manager posted
            # to a different port (so *-status.json was never written).
            for state_file in sprints_dir.glob("*-state.json"):
                sprint_label = state_file.name.removesuffix("-state.json")
                key = (proj["repo"], sprint_label)
                if key in _sprint_statuses:
                    continue  # already loaded from status.json above
                if not _is_sprint_running(project_root, sprint_label):
                    continue
                try:
                    payload = json.loads(state_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    print(
                        f"[startup-restore] could not read {state_file.name}: {exc}"
                    )
                    skipped += 1
                    continue
                _sprint_statuses[key] = payload
                print(
                    f"[startup-restore] re-attached (state.json fallback) to running sprint"
                    f" '{sprint_label}' on {proj['repo']}"
                )
                attached += 1

        except Exception as exc:
            print(f"[startup-restore] error scanning project {proj.get('repo')}: {exc}")

    if archived_total:
        print(f"[startup-restore] Skipped {archived_total} archived sprint files")
    print(
        f"[startup-restore] completed — {attached} sprint(s) re-attached,"
        f" {skipped} skipped"
    )


def _check_repo_accessible(repo: str) -> bool:
    """Return True if `repo` (owner/repo) exists and is accessible via gh CLI.

    Uses `gh repo view --json name` which returns exit code 0 on success.

    A non-zero exit can mean the repo is genuinely missing OR that the call
    failed for a transient reason (GitHub API rate limit, network blip). Only
    a *definitive* "not found" should be treated as inaccessible — a transient
    failure must NOT block dashboard startup, so we assume-accessible and let
    the warning path handle it. (Bug: a rate-limited startup check sys.exit'd
    the whole server.)
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", repo, "--json", "name"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True
        err = (result.stderr or "") + (result.stdout or "")
        err_l = err.lower()
        transient = (
            "rate limit" in err_l
            or "timeout" in err_l
            or "timed out" in err_l
            or "connection" in err_l
            or "could not resolve host" in err_l
            or "temporar" in err_l
            or "503" in err_l
            or "502" in err_l
        )
        if transient:
            _slog.warn(
                "repo_check_transient",
                f"gh repo view for {repo} failed transiently; assuming accessible: {err.strip()[:200]}",
                repo=repo,
            )
            return True
        # Definitive failure (e.g. "Could not resolve to a Repository", 404).
        return False
    except subprocess.TimeoutExpired:
        # Transient — do not block startup.
        _slog.warn("repo_check_timeout", f"gh repo view for {repo} timed out; assuming accessible", repo=repo)
        return True
    except FileNotFoundError:
        # gh not installed — that's a real misconfiguration.
        return False


def _check_gh_auth() -> None:
    """Preflight check: verify gh CLI is installed and has the repo scope.

    Never raises; never exits. On failure, populates _GH_AUTH_STATUS and
    emits a structured warning via _slog with the required fields from issue #424.
    """
    global _GH_AUTH_STATUS

    if not shutil.which("gh"):
        _GH_AUTH_STATUS = {
            "ok": False,
            "event": "gh_auth_check_failed",
            "message": "GitHub CLI (gh) is not installed",
            "remediation": "Install from https://cli.github.com",
        }
        _slog.warn(
            "gh_auth_check_failed",
            "gh CLI not found in PATH",
            scope_required="repo",
            scope_present=False,
            remediation="Install GitHub CLI: https://cli.github.com",
        )
        return

    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout + result.stderr

        if result.returncode != 0:
            _GH_AUTH_STATUS = {
                "ok": False,
                "event": "gh_auth_check_failed",
                "message": "GitHub CLI is not authenticated",
                "remediation": "Run: gh auth login",
            }
            _slog.warn(
                "gh_auth_check_failed",
                "gh CLI not authenticated",
                scope_required="repo",
                scope_present=False,
                remediation="gh auth login",
            )
            return

        scope_match = re.search(r"Token scopes:\s*(.+)", output)
        if scope_match:
            raw_scopes = scope_match.group(1)
            scopes = [s.strip().strip("'\",") for s in raw_scopes.split(",")]
            scope_present = "repo" in scopes
        else:
            scope_present = False

        if not scope_present:
            _GH_AUTH_STATUS = {
                "ok": False,
                "event": "gh_auth_check_failed",
                "message": "GitHub CLI token is missing the 'repo' scope",
                "remediation": "Run: gh auth refresh -s repo",
            }
            _slog.warn(
                "gh_auth_check_failed",
                "gh CLI token missing 'repo' scope",
                scope_required="repo",
                scope_present=False,
                remediation="gh auth refresh -s repo",
            )
            return

        _GH_AUTH_STATUS = {"ok": True, "message": ""}
        _slog.info(
            "gh_auth_check_passed",
            "gh CLI authenticated with repo scope",
            scope_required="repo",
            scope_present=True,
        )

    except subprocess.TimeoutExpired:
        _GH_AUTH_STATUS = {
            "ok": False,
            "event": "gh_auth_check_failed",
            "message": "GitHub CLI auth check timed out",
            "remediation": "Run: gh auth status",
        }
        _slog.warn(
            "gh_auth_check_failed",
            "gh auth status timed out after 10s",
            scope_required="repo",
            scope_present=False,
            remediation="gh auth status",
        )
    except OSError as exc:
        _GH_AUTH_STATUS = {
            "ok": False,
            "event": "gh_auth_check_failed",
            "message": f"gh auth check error: {exc}",
            "remediation": "Check gh CLI installation",
        }
        _slog.warn(
            "gh_auth_check_failed",
            f"gh auth check OS error: {exc}",
            scope_required="repo",
            scope_present=False,
            remediation="Check gh CLI installation",
        )


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


async def _periodic_orphan_sweep_loop() -> None:
    """Sweep orphan PID files every 5 minutes while the dashboard is running."""
    while True:
        await asyncio.sleep(300)
        try:
            _sweep_orphan_pid_files()
        except Exception as exc:
            print(f"[periodic-sweep] unexpected error: {exc}")


_STATUS_SYNC_INTERVAL = 30  # seconds


async def _status_md_sync_loop() -> None:
    """Regenerate and commit STATUS.md every 30 s when sprint progress changes."""
    await asyncio.sleep(30)  # let server finish startup before first run
    _sync_script = _REPO_ROOT / "scripts" / "sync_status_md.py"
    while True:
        try:
            if _sync_script.exists():
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(_sync_script),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=55)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    logger.warning("[status-sync] timed out")
                else:
                    if proc.returncode == 0:
                        logger.info("[status-sync] %s", stdout.decode().strip())
                    elif proc.returncode == 2:
                        logger.warning("[status-sync] error: %s", stderr.decode().strip())
                    # exit 1 = no change, normal
        except Exception as exc:
            logger.warning("[status-sync] unexpected error: %s", exc)
        await asyncio.sleep(_STATUS_SYNC_INTERVAL)


# ── Log event naming convention ──────────────────────────────────────────────
# Event names use a <namespace>.<action> pattern with three namespaces:
#   server.*  — server lifecycle events (startup, shutdown)
#   route.*   — HTTP route handler events (entry, error)
#   sprint.*  — sprint workflow events (dispatch)
# The namespaces are intentionally distinct; route.* events carry request_id
# and route/method fields, while server.* events carry environment/git metadata.
# ─────────────────────────────────────────────────────────────────────────────


def _mirror_sync_repos() -> list[str]:
    """Resolve the repos whose issues should be mirrored into the local DB.

    Uses every tracked project's repo (issue #756); falls back to the detected
    default repo. Duplicates are removed while preserving order.
    """
    repos: list[str] = []
    try:
        for proj in projects_module.load_projects():
            repo_name = proj.get("repo")
            if repo_name:
                repos.append(repo_name)
    except Exception as exc:
        logger.warning("[issues-mirror] could not load projects: %s", exc)
    if not repos:
        try:
            repos.append(github_client.repo())
        except Exception:
            pass
    seen: set[str] = set()
    ordered: list[str] = []
    for r in repos:
        if r not in seen:
            seen.add(r)
            ordered.append(r)
    return ordered


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time
    _start_time = time.monotonic()
    _slog.event(
        "server.startup",
        project="dashboard",
        request_id=str(uuid.uuid4()),
        environment=ENVIRONMENT,
        git_sha=_GIT_SHA,
        git_branch=_GIT_BRANCH,
    )
    db.init_db()
    _check_gh_auth()
    _validate_github_repos()
    _sweep_orphan_pid_files()
    _restore_sprint_statuses_on_startup()
    await _mark_inflight_jobs_failed()
    # Neon dual-write removed (issue #758): projects.json is the runtime source of
    # truth. Mirror it to Neon on demand with scripts/export_to_neon.py, not here.

    # Start backup scheduler and queue a startup backup after 30 s
    if _BACKUP_AVAILABLE:
        try:
            _backup_module.start_backup_scheduler()
            _backup_module.schedule_startup_backup(delay_seconds=30)
        except Exception:
            pass  # backup failures never affect server startup
    task1 = asyncio.create_task(_cache_refresh_loop())
    task2 = asyncio.create_task(_timeout_loop())
    task3 = asyncio.create_task(_periodic_orphan_sweep_loop())
    task4 = asyncio.create_task(_status_md_sync_loop())
    # Bootstrap full sync (issue #760): on first run from an empty/absent DB
    # (detected via the missing schema-marker row), run a one-time full GitHub
    # sync synchronously before handing off to the ETag loop so the dashboard is
    # never served empty. A second start finds the marker and skips straight to
    # the loop. Never raises — an empty DB must not crash startup.
    _bootstrap_repos = _mirror_sync_repos()
    await asyncio.to_thread(
        github_events_sync.bootstrap_full_sync, _bootstrap_repos
    )
    # Issues-mirror sync loop (issue #756): keep the local DB read model fresh
    # via ETag-conditional polling so renders consume zero GitHub quota.
    task5 = asyncio.create_task(
        github_events_sync.run_issues_sync_loop(_bootstrap_repos)
    )
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    task5.cancel()
    for t in (task1, task2, task3, task4, task5):
        try:
            await t
        except asyncio.CancelledError:
            pass
    _slog.event(
        "server.shutdown",
        project="dashboard",
        request_id=str(uuid.uuid4()),
        environment=ENVIRONMENT,
        git_sha=_GIT_SHA,
        git_branch=_GIT_BRANCH,
        uptime_seconds=round(time.monotonic() - _start_time, 1),
    )


app = FastAPI(lifespan=lifespan)

# Strangler-fig routers (issue #761): extracted route clusters live in
# apps/dashboard/routers/ and are mounted here. New endpoints go there, NOT
# in this file — the COMMANDER_GATE_MONOLITH gate rejects server.py growth.
from routers import (  # noqa: E402
    activity_router,
    analytics_router,
    backup_router,
    doctor_router,
    home_milestone_router,
    log_search_router,
    milestones_router,
    roadmap_router,
    runs_router,
    signoff_router,
    sprint_history_router,
    sprints_router,
    status_router,
    system_router,
    tickets_router,
)

app.include_router(activity_router)
app.include_router(analytics_router)
app.include_router(backup_router)
app.include_router(doctor_router)
app.include_router(log_search_router)
app.include_router(milestones_router)
from routers.milestones_service import resolve_bulk_milestone as _resolve_bulk_milestone  # noqa: E402
app.include_router(runs_router)
app.include_router(signoff_router)
app.include_router(sprint_history_router)
app.include_router(sprints_router)
app.include_router(status_router)
app.include_router(system_router)
app.include_router(tickets_router)
app.include_router(home_milestone_router)
app.include_router(roadmap_router)

logger = logging.getLogger(__name__)


# ── API no-cache middleware (issue #249) ──────────────────────────────────────
# Ensure all /api/* responses carry Cache-Control: no-cache so browsers and
# proxies never serve stale API data on auto-refresh or manual refresh.

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
    # owner/repo from COMMANDER_PROJECT (sprint-dispatched agents). Optional;
    # interactive sessions fall back to the working-dir basename.
    project:       Optional[str] = None


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
    from_existing: bool = False


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


def _health_collect_gh_auth_scopes() -> dict | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=5,
        )
        output = result.stdout + result.stderr
        authorized = result.returncode == 0
        scopes: list[str] = []
        m = re.search(r"Token scopes:\s*(.+)", output)
        if m:
            scopes = [s.strip().strip("'\",") for s in m.group(1).split(",") if s.strip()]
        return {"authorized": authorized, "scopes": scopes}
    except Exception:
        return None


def _health_collect_disk() -> dict | None:
    try:
        partition_path: Path | None = None
        try:
            projs = projects_module.load_projects()
            for p in projs:
                candidate = _commander_dir(_project_root_path(p["repo"]))
                if candidate.exists():
                    partition_path = candidate
                    break
        except Exception:
            pass
        if partition_path is None:
            partition_path = _PROJECTS_BASE
        usage = shutil.disk_usage(partition_path)
        free_percent = usage.free / usage.total * 100.0
        return {"partition": str(partition_path), "free_percent": round(free_percent, 2)}
    except Exception:
        return None


def _health_collect_sprints() -> dict | None:
    try:
        return {"running_count": len(_all_sprints_running())}
    except Exception:
        return None


def _health_collect_orphan_pids() -> dict | None:
    try:
        count = 0
        projs = projects_module.load_projects()
        for proj in projs:
            root = _project_root_path(proj["repo"])
            sprints_dir = _commander_dir(root) / "sprints"
            if not sprints_dir.exists():
                continue
            for pid_file in sprints_dir.glob("*-pid"):
                try:
                    pid = int(pid_file.read_text(encoding="utf-8").strip())
                except (ValueError, OSError):
                    count += 1
                    continue
                try:
                    os.kill(pid, 0)
                except (ProcessLookupError, OSError):
                    count += 1
        return {"count": count}
    except Exception:
        return None


def _health_collect_recent_dispatches() -> list | None:
    try:
        entries: list[dict] = []
        projs = projects_module.load_projects()
        for proj in projs:
            root = _project_root_path(proj["repo"])
            sprints_dir = _commander_dir(root) / "sprints"
            if not sprints_dir.exists():
                continue
            for state_file in sprints_dir.glob("*-state.json"):
                try:
                    data = json.loads(state_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                sprint_label = state_file.name.removesuffix("-state.json")
                issues = data.get("issues") or []
                has_failed = any(
                    i.get("agent_status") == "failed" or i.get("failure_reason") or i.get("status") == "skipped"
                    for i in issues
                )
                all_done = bool(issues) and all(i.get("status") == "done" for i in issues)
                outcome = "success" if (all_done and not has_failed) else "failure"
                ts_str = data.get("start_timestamp")
                try:
                    if ts_str:
                        ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        timestamp = ts_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        sort_key = ts_str
                    else:
                        raise ValueError("no start_timestamp")
                except Exception:
                    mtime = state_file.stat().st_mtime
                    timestamp = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    sort_key = str(mtime)
                entries.append({
                    "timestamp": timestamp,
                    "outcome": outcome,
                    "target": f"{proj['repo']}/{sprint_label}",
                    "_sort_key": sort_key,
                })
        entries.sort(key=lambda e: e["_sort_key"], reverse=True)
        return [{"timestamp": e["timestamp"], "outcome": e["outcome"], "target": e["target"]} for e in entries[:5]]
    except Exception:
        return None


def _compute_health_status(
    gh_auth: dict | None,
    disk: dict | None,
    orphan_pids: dict | None,
    recent_dispatches: list | None,
) -> str:
    has_null = any(x is None for x in [gh_auth, disk, orphan_pids, recent_dispatches])
    disk_critical = disk is not None and disk["free_percent"] < 5.0
    gh_unauthorized = gh_auth is not None and not gh_auth["authorized"]
    orphan_critical = orphan_pids is not None and orphan_pids["count"] >= 3
    if disk_critical or gh_unauthorized or orphan_critical:
        return "unhealthy"
    disk_degraded = disk is not None and disk["free_percent"] < 15.0
    last_dispatch_failure = (
        recent_dispatches is not None
        and len(recent_dispatches) > 0
        and recent_dispatches[0]["outcome"] == "failure"
    )
    any_orphans = orphan_pids is not None and orphan_pids["count"] > 0
    if has_null or disk_degraded or last_dispatch_failure or any_orphans:
        return "degraded"
    return "ok"


# ── SSE broadcast ─────────────────────────────────────────────────────────────

async def broadcast(data: dict):
    msg = json.dumps(data)
    for q in _subscribers:
        await q.put(msg)


# ── agent endpoints ───────────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/", method="GET")
    return _serve_html(STATIC_DIR / "home.html")


@app.get("/brief")
async def brief_redirect():
    """Legacy /brief bookmarks → daily brief home at /."""
    return RedirectResponse(url="/", status_code=301)


@app.get("/home")
async def home_redirect():
    return RedirectResponse(url="/", status_code=301)


@app.get("/overview")
async def overview_redirect():
    return RedirectResponse(url="/", status_code=301)


# /diagnostics moved to routers/system.py (issue #794)


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

_VALID_PROJECT_TABS = {"sprint-mgmt", "tickets", "logs", "sprint-history", "status", "metrics", "notes", "settings", "global-settings", "roadmap"}


@app.get("/project/{slug}")
async def project_slug_no_tab(slug: str):
    """Redirect bare /project/<slug> to /project/<slug>/sprint-mgmt."""
    return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=302)


@app.get("/project/{slug}/analytics")
async def project_slug_analytics(slug: str):
    """Retired standalone analytics page — analytics is now an in-chrome tab
    (with the project nav). Redirect old links to the in-chrome Analytics tab."""
    return RedirectResponse(url=f"/project/{slug}/metrics", status_code=302)


@app.get("/project/{slug}/{tab}")
async def project_slug_tab(slug: str, tab: str):
    """Serve the project chrome page for valid tabs; redirect invalid tabs to sprint-mgmt."""
    if tab not in _VALID_PROJECT_TABS:
        return RedirectResponse(url=f"/project/{slug}/sprint-mgmt", status_code=302)
    return _serve_html(STATIC_DIR / "project.html")


@app.get("/api/health")
async def health_check(request: Request):
    """GET /api/health — structured operational health check (issue #474).

    Checks uptime, gh auth scopes, disk pressure, running sprints, orphan PIDs,
    and recent dispatch outcomes.  Always returns HTTP 200; the status field
    carries the health signal ("ok", "degraded", or "unhealthy").
    Response is cached 10 s.  No authentication required.
    """
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/health", method="GET")
    global _health_cache
    now = time.monotonic()
    if _health_cache is not None:
        ts, cached = _health_cache
        if now - ts < _HEALTH_CACHE_TTL:
            return JSONResponse(content=cached, status_code=200)

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    loop = asyncio.get_event_loop()

    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, _health_collect_gh_auth_scopes),
                loop.run_in_executor(None, _health_collect_disk),
                loop.run_in_executor(None, _health_collect_sprints),
                loop.run_in_executor(None, _health_collect_orphan_pids),
                loop.run_in_executor(None, _health_collect_recent_dispatches),
                return_exceptions=True,
            ),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        results = [None, None, None, None, None]

    def _safe(r):
        return None if isinstance(r, Exception) else r

    gh_auth = _safe(results[0])
    disk = _safe(results[1])
    sprints = _safe(results[2])
    orphan_pids = _safe(results[3])
    recent_dispatches = _safe(results[4])

    status = _compute_health_status(gh_auth, disk, orphan_pids, recent_dispatches)

    response = {
        "status": status,
        "uptime_seconds": int(time.monotonic() - _start_time),
        "gh_auth_scopes": gh_auth,
        "disk": disk,
        "sprints": sprints,
        "orphan_pids": orphan_pids,
        "orphans_removed": _orphans_removed_total,
        "recent_dispatches": recent_dispatches,
        "checked_at": checked_at,
    }
    _health_cache = (now, response)
    return JSONResponse(content=response, status_code=200)


@app.get("/api/environment")
def get_environment():
    """Return the current runtime environment (prd or uat)."""
    return {"environment": ENVIRONMENT}


# /api/version and /api/gh-auth-status moved to routers/system.py (issue #794)


_seen_agent_sessions: set[str] = set()


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
    """Extract (role, issue_num) from an agent name string (issue #719).

    Names are formatted as 'role·label·branch·#short' with an optional trailing
    '·issue-<N>' component appended when the dispatcher set CLAUDE_AGENT_ISSUE.
    The role is the first '·'-delimited component; the issue number is the
    'issue-<N>' token if present. Either may be None for legacy/raw-UUID names.
    """
    role = None
    issue_num = None
    if agent_name and "·" in agent_name:
        role = agent_name.split("·")[0] or None
    if agent_name:
        m = re.search(r"issue-(\d+)", agent_name)
        if m:
            issue_num = int(m.group(1))
    return role, issue_num


@app.post("/api/agent-event")
async def receive_event(request: Request, event: AgentEvent):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/agent-event", method="POST", event_type=event.event_type)

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
    await broadcast({"type": "update", "event": event.model_dump()})
    return {"ok": True}


@app.post("/api/token-usage")
async def receive_token_usage(event: TokenUsageEvent):
    if not event.input_tokens and not event.output_tokens:
        return {"ok": True}
    # Prefer the explicit owner/repo the dispatcher tagged; the working-dir
    # basename ("uat", "coder") can't split cost per project.
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
    await broadcast({"type": "update", "event": event.model_dump()})
    return {"ok": True}


# /api/agents and /api/events moved to routers/activity.py (issue #794)


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

def _gh_graphql_reset_seconds() -> Optional[int]:
    """Seconds until the GitHub GraphQL budget resets, or None.

    Queries the rate_limit endpoint, which is REST (core) and does not itself
    count against any limit, so it is safe to call on an error path.
    """
    try:
        import time as _t
        r = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".resources.graphql.reset"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return max(0, int(r.stdout.strip()) - int(_t.time()))
    except Exception:
        pass
    return None


def _gh_error(e: subprocess.CalledProcessError) -> HTTPException:
    detail = e.stderr.strip() if e.stderr else str(e)
    # Map a GitHub rate-limit failure to a clean 429 with a reset countdown, so
    # callers (e.g. the Sprint Mgmt board) can say "rate limit, retry in Ns"
    # instead of a generic failure. Refills hourly.
    if "rate limit" in detail.lower():
        reset_in = _gh_graphql_reset_seconds()
        msg = "GitHub API rate limit reached."
        if reset_in:
            msg += f" Retry in ~{reset_in // 60}m {reset_in % 60}s."
        else:
            msg += " It refills hourly; retry shortly."
        return HTTPException(status_code=429, detail=msg)
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


class CreateLabelBody(BaseModel):
    name: str
    color: str = "a2eeef"
    description: str = ""
    repo: Optional[str] = None


@app.post("/api/github/labels")
def post_create_label(body: CreateLabelBody):
    """Create a new GitHub label in the repo; returns updated label list."""
    body.name = body.name.strip()
    if not body.name:
        raise HTTPException(400, detail="Label name is required.")
    try:
        github_client.create_label(
            body.name, body.color,
            description=body.description,
            repo_name=body.repo,
        )
        return github_client.list_labels(repo_name=body.repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.get("/api/sprint-nav-status")
def get_sprint_nav_status(repo: str = ""):
    """GitHub-backed sprint status for the nav-bar pill.

    Read-only and derived entirely from GitHub labels/issues, so it works on a
    machine that is NOT running the sprint (the runner's local state.json/PID
    files are never shared cross-machine). Cached 30s via github_client.

    A sprint is "finished" when its "Sprint N Executive Summary" issue exists
    (label ``sprint-summary``); otherwise it is "running" and progress is
    inferred from each ticket's workflow column.
    """
    repo_name = repo or None
    try:
        sprint_nums = github_client.list_sprints(repo_name=repo_name)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    if not sprint_nums:
        return {"has_sprint": False}

    # Current sprint = sprint with active work (in-progress/SIT/UAT) if any,
    # otherwise the highest-numbered sprint that has issues.
    # This ensures a running sprint takes precedence over a higher-numbered
    # planned sprint that only has backlog tickets.
    all_sprint_issues: dict[int, list[dict]] = {}
    for n in reversed(sprint_nums):
        issues = github_client.list_issues(n, repo_name=repo_name)
        if issues:
            all_sprint_issues[n] = issues

    if not all_sprint_issues:
        return {"has_sprint": False}

    # Fetch finished summaries once — used both to skip finished sprints when
    # picking "current" and to populate the panel summary link below.
    finished_summaries = _finished_sprint_summaries(repo_name)

    _ACTIVE_COLS = {"in-progress", "sit", "uat", "needs-rework"}
    current = None
    raw_issues: list[dict] = []
    # Prefer the lowest-numbered UNFINISHED sprint that has active (non-backlog) work.
    # Skipping finished sprints prevents a higher-numbered running sprint from being
    # eclipsed by a lower-numbered sprint whose only remaining work is UAT sign-off.
    for n in sorted(all_sprint_issues.keys()):
        if f"sprint-{n}" in finished_summaries:
            continue  # sprint already finished — skip when looking for running sprint
        issues = all_sprint_issues[n]
        if any(i.get("column") in _ACTIVE_COLS for i in issues):
            current = n
            raw_issues = issues
            break
    # Fall back to highest-numbered sprint with any issues (covers finished-only state)
    if current is None:
        current = max(all_sprint_issues.keys())
        raw_issues = all_sprint_issues[current]

    # The "Sprint N Executive Summary" issue may carry sprint-N label in addition
    # to sprint-summary. Strip any sprint-summary / docs issues so they don't
    # inflate ticket counts.
    _NON_WORK_LABELS = {"sprint-summary", "docs", "documentation"}
    work_issues = [
        i for i in raw_issues
        if not _NON_WORK_LABELS & {lbl.get("name", "") for lbl in i.get("labels", [])}
    ]
    summary_issue = finished_summaries.get(f"sprint-{current}")

    columns = {"backlog": 0, "in-progress": 0, "sit": 0, "uat": 0, "done": 0, "needs-rework": 0}
    for i in work_issues:
        col = i.get("column", "backlog")
        columns[col] = columns.get(col, 0) + 1

    return {
        "has_sprint": True,
        "repo": github_client.get_repo_for_operation(repo_name),
        "sprint": current,
        "state": "finished" if summary_issue else "running",
        "total": len(work_issues),
        "done": columns["done"],
        "uat": columns["uat"],
        "columns": columns,
        "summary_issue": summary_issue,
    }


def _sprint_progress_file_path(project: str) -> Optional[Path]:
    """Return the path to the persisted sprint-progress JSON file for a project."""
    if not project:
        return None
    project_root = _project_root_path(project)
    return _commander_dir(project_root) / "runtime" / "sprint-progress.json"


@app.get("/api/sprint-progress")
def get_sprint_progress(project: str = "", repo: str = ""):
    """Unified sprint progress endpoint — single source of truth for all three pill components.

    Priority:
    1. In-memory live status from sprint_manager (most recent, pushed via POST /api/sprint-status)
    2. Persisted JSON file (survives server restart)
    3. GitHub-backed fallback via sprint-nav-status logic

    Persists the result to .commander/runtime/sprint-progress.json so the UI
    can re-hydrate from disk when the server restarts or live data is not yet
    available.
    """
    repo_name = repo or (project or None)

    # ── 1. Try live in-memory status ─────────────────────────────────────────
    running = _all_sprints_running()
    if project:
        running = [r for r in running if r["project"] == project]

    for r in running:
        key = (r["project"], r["sprint_label"])
        status = _sprint_statuses.get(key, {})
        issues = status.get("issues", [])
        if not issues:
            # Disk fallback: sprint_manager writes <label>-state.json regardless
            # of which port it posts to. Read the FULL sub-label state (e.g.
            # sprint-67.1-state.json) so the pill reflects the running sub-sprint
            # and its live progress instead of a GitHub-derived base guess (the
            # pill showed "S71 0/11" while 67.1 ran — same gap #950 fixed for /live).
            try:
                _sp = (_commander_dir(_project_root_path(r["project"]))
                       / "sprints" / f"{r['sprint_label']}-state.json")
                if _sp.exists():
                    _disk = json.loads(_sp.read_text(encoding="utf-8"))
                    issues = _disk.get("issues", [])
                    if issues and status.get("sprint_number") is None:
                        status = {**status, "sprint_number": _disk.get("sprint_number")}
            except Exception:
                pass
        if not issues:
            continue
        sprint_number = status.get("sprint_number")
        if sprint_number is None:
            label = r["sprint_label"]
            m = re.search(r"\d+", label)
            sprint_number = int(m.group()) if m else 0

        done = sum(
            1 for i in issues
            if i.get("status") in ("done", "skipped", "failed")
        )
        total = len(issues)

        result = {
            "has_sprint": True,
            "sprint_label": r["sprint_label"],
            "sprint": sprint_number,
            "done": done,
            "total": total,
            "run_state": "running",
            "source": "live",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        _persist_sprint_progress(project or r["project"], result)
        return result

    # ── 2. Try persisted file ────────────────────────────────────────────────
    file_project = project or ""
    progress_path = _sprint_progress_file_path(file_project)
    if progress_path and progress_path.exists():
        try:
            cached = json.loads(progress_path.read_text(encoding="utf-8"))
            if cached.get("has_sprint"):
                return cached
        except (OSError, json.JSONDecodeError):
            pass

    # ── 3. GitHub fallback ───────────────────────────────────────────────────
    try:
        gh_data = get_sprint_nav_status(repo=repo or (project or ""))
    except Exception:
        return {"has_sprint": False}

    if not gh_data.get("has_sprint"):
        return {"has_sprint": False}

    sprint_num = gh_data.get("sprint", 0)
    gh_done = (gh_data.get("done") or 0) + (gh_data.get("uat") or 0)
    gh_total = gh_data.get("total") or 0
    gh_state = gh_data.get("state", "running")

    result = {
        "has_sprint": True,
        "sprint_label": f"sprint-{sprint_num}",
        "sprint": sprint_num,
        "done": gh_done,
        "total": gh_total,
        "run_state": gh_state,
        "columns": gh_data.get("columns", {}),
        "source": "github",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    _persist_sprint_progress(file_project, result)
    return result


def _persist_sprint_progress(project: str, data: dict) -> None:
    """Write sprint progress data atomically to .commander/runtime/sprint-progress.json."""
    path = _sprint_progress_file_path(project)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError:
        pass


@app.get("/api/sprint-nav-summary")
def get_sprint_nav_summary(number: int, repo: str = ""):
    """Return a sprint-summary issue's markdown body (GitHub-backed), fetched on
    demand when the user opens the nav panel."""
    repo_name = repo or None
    try:
        issue = github_client.get_issue(number, repo_name=repo_name)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    return {
        "number": issue.get("number"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "body": issue.get("body") or "",
    }


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
def approve_issue(request: Request, issue_id: int, repo: Optional[str] = None):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/approve", method="POST", issue_id=issue_id)
    try:
        github_client.approve_issue(issue_id, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/approve", level="error", issue_id=issue_id, error=str(e))
        raise _gh_error(e)


@app.post("/api/issues/{issue_id}/reject")
def reject_issue(request: Request, issue_id: int, body: RejectBody, repo: Optional[str] = None):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/reject", method="POST", issue_id=issue_id)
    try:
        github_client.reject_issue(issue_id, body.reason, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/reject", level="error", issue_id=issue_id, error=str(e))
        raise _gh_error(e)


@app.post("/api/issues/{issue_id}/close")
def close_issue_endpoint(request: Request, issue_id: int, repo: Optional[str] = None):
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/close", method="POST", issue_id=issue_id)
    try:
        github_client.close_issue(issue_id, repo_name=repo)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/issues/{issue_id}/close", level="error", issue_id=issue_id, error=str(e))
        raise _gh_error(e)


@app.get("/api/issues/{issue_id}/test-report")
def get_test_report(issue_id: int, repo: Optional[str] = None):
    try:
        return github_client.get_test_report(issue_id, repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))


@app.get("/api/estimator/health")
def estimator_health():
    """Check whether the estimator agent (claude CLI) is available."""
    import shutil
    available = shutil.which("claude") is not None
    return {"available": available}


@app.post("/api/issues/{issue_id}/estimate")
def estimate_issue_on_demand(request: Request, issue_id: int, repo: str, force: bool = True):
    """Run the issue estimator on demand and apply the size label.

    Returns {"ok": True, "size": "S"|"M"|"L"|"XL"} on success.
    The force param is accepted for API compatibility; the endpoint always runs fresh.
    """
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id,
                route="/api/issues/{issue_id}/estimate", method="POST", issue_id=issue_id)
    try:
        issue_data = _ei_fetch_issue(issue_id, repo)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        detail = f"Could not fetch issue #{issue_id}: {e}"
        if stderr:
            detail += f" — stderr: {stderr}"
        raise HTTPException(404, detail=detail)

    estimate, error_type = _ei_run_estimator(issue_id, issue_data)
    if estimate is None:
        raise HTTPException(
            500,
            detail={"message": f"Estimation failed for #{issue_id}", "error_type": error_type},
        )

    size = estimate.get("size")
    if not size:
        raise HTTPException(500, detail="Estimator returned no size")

    minutes: int = estimate.get("minutes") or _minutes_from_letter(size)
    if not estimate.get("minutes"):
        estimate["minutes"] = minutes

    try:
        _ei_apply_label(issue_id, repo, size)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        detail = f"Failed to apply size label: {e}"
        if stderr:
            detail += f" — stderr: {stderr}"
        raise HTTPException(500, detail=detail)

    _ei_apply_estimated_status(issue_id, repo)

    project_root = _project_root_path(repo)
    estimates_dir = _commander_dir(project_root) / "estimates"
    estimates_dir.mkdir(parents=True, exist_ok=True)
    estimate_path = estimates_dir / f"issue-{issue_id}.json"
    estimate_path.write_text(json.dumps(estimate, indent=2), encoding="utf-8")

    return {"ok": True, "size": size, "minutes": minutes}


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


@app.delete("/api/projects/{slug}/settings")
def delete_project_settings(slug: str):
    """Clear the project-level settings override.

    After deletion GET /api/projects/{slug}/settings returns global defaults.
    Returns 404 if the project slug is not found.
    Must be registered before DELETE /api/projects/{owner}/{repo_name} to avoid
    first-match routing collision on two-segment paths.
    """
    repo = _resolve_project_slug(slug)
    _settings_repo.delete_setting("project", APP_CONFIG_KEY, project=repo)
    _invalidate_home_cache(slug)
    effective = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    return build_effective_response(effective)


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


@app.get("/api/projects/{project}/running-sprint")
def get_running_sprint(project: str):
    """Return the currently running sprint for the given project slug.

    200 — { label, started_at (ISO 8601 UTC), pid }
    204 — no sprint running (or only stale PID files)
    404 — project not registered
    """
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == project or p["repo"] == project),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")

    project_root = _project_root_path(matched["repo"])
    sprints_dir = _commander_dir(project_root) / "sprints"

    if not sprints_dir.exists():
        return Response(status_code=204)

    seen: set[str] = set()
    for pid_file in list(sprints_dir.glob("*-pid")) + list(sprints_dir.glob("*-pid.pending")):
        label = pid_file.name.removesuffix("-pid.pending").removesuffix("-pid")
        if label in seen:
            continue
        seen.add(label)
        if _is_sprint_running(project_root, label):
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pid = 0
            try:
                mtime = pid_file.stat().st_mtime
                started_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except OSError:
                started_at = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
            return {"label": label, "started_at": started_at, "pid": pid}

    return Response(status_code=204)


# /api/projects/{slug}/events (and _VALID_EVENT_SOURCES) moved to
# routers/activity.py (issue #794)


# ── Settings API (issue #639) ────────────────────────────────────────────────

import services.sprint_manager.settings_repo as _settings_repo
from services.sprint_manager.settings_schema import (
    APP_CONFIG_KEY,
    SECRET_FIELDS,
    KNOWN_FIELDS,
    build_effective_response,
)
from services.sprint_manager.deploy_config_schema import (
    DEPLOY_CONFIG_KEY,
    SUPPORTED_ENVS as _DEPLOY_SUPPORTED_ENVS,
    SUPPORTED_HOSTS as _DEPLOY_SUPPORTED_HOSTS,
    seed_for as _deploy_seed_for,
    merge_seed as _deploy_merge_seed,
    merge_for_put as _deploy_merge_for_put,
    build_deploy_config_response as _build_deploy_config_response,
    known_deploy_slugs as _deploy_known_slugs,
    overview_entries_for as _deploy_overview_entries_for,
)


def _resolve_project_slug(slug: str) -> str:
    """Resolve a project slug to the full repo string (owner/repo).

    Matches by last path component or exact match (mirrors get_project_events pattern).
    Raises HTTPException 404 if not found.
    """
    try:
        all_projects = projects_module.load_projects()
    except Exception as exc:
        _slog.warn(
            "resolve_project_slug.load_failed",
            f"load_projects raised while resolving slug '{slug}': {exc}",
            slug=slug,
            error=str(exc),
        )
        all_projects = []

    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return matched["repo"]


def _validate_settings_body(body: dict) -> None:
    """Validate a PUT settings request body.

    Raises HTTPException 422 for secret fields, 400 for unknown fields.
    """
    for key in body:
        if key in SECRET_FIELDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Field '{key}' is a secret and cannot be written via this endpoint. "
                    "Use the dedicated secret management endpoint."
                ),
            )
    unknown = [k for k in body if k not in KNOWN_FIELDS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown settings field(s): {', '.join(sorted(unknown))}. "
                   f"Allowed fields: {', '.join(sorted(KNOWN_FIELDS))}",
        )


@app.get("/api/settings")
def get_global_settings():
    """Return effective global settings.

    Non-secret fields are returned as-is with defaults applied.
    Secret fields appear as boolean presence flags (<field>_set).
    """
    stored = _settings_repo.get_setting_scoped("global", APP_CONFIG_KEY)
    return build_effective_response(stored)


_AGENT_MODEL_KEYS = (
    "default_model", "coder_model", "tester_model",
    "estimator_model", "documentor_model",
)


def _propagate_models_to_sprint_yaml(body: dict) -> list[str]:
    """Write any agent-model fields from a settings Save into each project's
    sprint.yaml ``agent_config`` — the single source ``sprint_manager`` reads at
    dispatch. Without this the Save only updated the settings DB while the run
    kept using the stale sprint.yaml model (coder/estimator stayed opus despite
    a sonnet Save). Returns the repos updated."""
    model_cfg = {k: v for k, v in body.items() if k in _AGENT_MODEL_KEYS and v}
    if not model_cfg:
        return []
    try:
        from services.sprint_manager.settings_sync import _update_sprint_yaml_agent_config
    except Exception:
        return []
    updated: list[str] = []
    for proj in projects_module.load_projects():
        repo = (proj.get("repo") or "").strip()
        if not repo:
            continue
        try:
            sy = _commander_dir(_project_root_path(repo)) / "sprint.yaml"
            if sy.exists():
                _update_sprint_yaml_agent_config(sy, model_cfg)
                updated.append(repo)
        except Exception:
            continue
    return updated


@app.put("/api/settings")
def put_global_settings(body: dict):
    """Persist a global settings override.

    Only the supplied keys are written; existing keys not in the body are preserved.
    Returns 400 for unknown keys, 422 for secret fields.
    """
    _validate_settings_body(body)
    current = _settings_repo.get_setting_scoped("global", APP_CONFIG_KEY)
    merged = {**current, **body}
    _settings_repo.set_setting("global", APP_CONFIG_KEY, merged)
    # Single source: agent-model fields also land in each project's sprint.yaml
    # so the dispatcher (reads sprint.yaml, not the settings DB) applies them.
    _propagate_models_to_sprint_yaml(body)
    return build_effective_response(merged)


def _project_json_for_repo(repo: str) -> dict | None:
    return next(
        (p for p in projects_module.load_projects() if p.get("repo") == repo),
        None,
    )


def _apply_project_identity_defaults(
    resp: dict,
    repo: str,
    proj_override: dict,
) -> dict:
    """Use projects.json identity when the project override has no explicit value.

    Schema defaults (ti-folder / gray) must not mask projects.json — only keys
    stored in the project-scoped override win over the JSON file.
    """
    proj_json = _project_json_for_repo(repo)
    if not proj_json:
        return resp
    if "icon" not in proj_override:
        resp["icon"] = proj_json.get("icon", resp.get("icon", "ti-folder"))
    if "color" not in proj_override:
        resp["color"] = proj_json.get("color", resp.get("color", "gray"))
    if not proj_override.get("display_name"):
        resp["display_name"] = proj_json.get("name", resp.get("display_name", ""))
    return resp


@app.get("/api/projects/{slug}/settings")
def get_project_settings(slug: str):
    """Return effective project settings (project overrides merged over global).

    Non-secret fields returned with defaults applied; secrets as boolean flags.
    Returns 404 when the project slug does not exist.
    """
    repo = _resolve_project_slug(slug)
    proj_override = _settings_repo.get_setting_scoped("project", APP_CONFIG_KEY, project=repo)
    stored = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    resp = build_effective_response(stored)
    return _apply_project_identity_defaults(resp, repo, proj_override)


@app.put("/api/projects/{slug}/settings")
def put_project_settings(slug: str, body: dict):
    """Persist a project-level settings override.

    Only the supplied keys are written as a project override.
    Global settings are not affected.
    Returns 400 for unknown keys, 422 for secret fields, 404 if project not found.
    """
    repo = _resolve_project_slug(slug)
    _validate_settings_body(body)
    current_project_override = _settings_repo.get_setting_scoped("project", APP_CONFIG_KEY, project=repo)
    merged = {**current_project_override, **body}
    _settings_repo.set_setting("project", APP_CONFIG_KEY, merged, project=repo)
    # Single source: write agent-model fields into THIS project's sprint.yaml so
    # the dispatcher applies them (settings DB alone is not read by the run).
    _model_cfg = {k: v for k, v in body.items() if k in _AGENT_MODEL_KEYS and v}
    if _model_cfg:
        try:
            from services.sprint_manager.settings_sync import _update_sprint_yaml_agent_config
            _sy = _commander_dir(_project_root_path(repo)) / "sprint.yaml"
            if _sy.exists():
                _update_sprint_yaml_agent_config(_sy, _model_cfg)
        except Exception:
            pass
    display_patch: dict = {}
    if "icon" in body:
        display_patch["icon"] = body["icon"]
    if "color" in body:
        display_patch["color"] = body["color"]
    if "display_name" in body:
        display_patch["name"] = body["display_name"]
    if "tracked" in body:
        display_patch["tracked"] = body["tracked"]
    if display_patch:
        projects_module.save_project_display_fields(repo, **display_patch)
    _invalidate_home_cache(slug)
    effective = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    resp = build_effective_response(effective)
    return _apply_project_identity_defaults(resp, repo, merged)


# ── Deploy config API (issue #722) ───────────────────────────────────────────


def _validate_deploy_config_body(body: dict) -> None:
    """Validate a PUT deploy-config body.

    Raises HTTPException 400 for unsupported environments, non-object entries,
    or invalid host values.
    """
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="Deploy config body must be an object keyed by environment.",
        )
    for env, entry in body.items():
        if env not in _DEPLOY_SUPPORTED_ENVS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported environment '{env}'. "
                    f"Allowed: {', '.join(_DEPLOY_SUPPORTED_ENVS)}"
                ),
            )
        if not isinstance(entry, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Environment '{env}' config must be an object.",
            )
        host = entry.get("host")
        if host is not None and host not in _DEPLOY_SUPPORTED_HOSTS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Environment '{env}': host must be one of "
                    f"{', '.join(_DEPLOY_SUPPORTED_HOSTS)}; got '{host}'."
                ),
            )


def _enrich_local_working_dirs(repo: str, resp: dict) -> None:
    """Fill working_dir for local entries that lack one from the env paths.

    host=local working_dir defaults to the existing on-disk env path so callers
    get a usable default without the user having to re-enter it.
    """
    envs = projects_module.get_project_environments(repo)
    if not envs:
        envs = _derive_project_environments(repo)
    for env, entry in resp.items():
        if entry.get("host") == "local" and not entry.get("working_dir"):
            if env in envs:
                entry["working_dir"] = envs[env]


def _deploy_config_response(slug: str, repo: str, stored: dict) -> dict:
    """Build the GET-shaped response: seed defaults merged with stored, masked."""
    merged = _deploy_merge_seed(_deploy_seed_for(slug), stored or {})
    resp = _build_deploy_config_response(merged)
    _enrich_local_working_dirs(repo, resp)
    return resp


@app.get("/api/projects/{slug}/deploy-config")
def get_project_deploy_config(slug: str):
    """Return per-environment deploy config (seed defaults merged with overrides).

    render_api_key is never returned in cleartext — each render entry carries
    render_api_key_set (bool) and render_api_key_masked. Returns 404 for an
    unknown slug.
    """
    repo = _resolve_project_slug(slug)
    stored = _settings_repo.get_setting_scoped("project", DEPLOY_CONFIG_KEY, project=repo)
    resp = _deploy_config_response(slug, repo, stored)
    _enrich_deploy_readiness(resp)
    return resp


@app.put("/api/projects/{slug}/deploy-config")
def put_project_deploy_config(slug: str, body: dict):
    """Persist a per-environment deploy config override.

    Merges per environment over the stored config; a new render_api_key replaces
    the stored secret, while an omitted/null key leaves it unchanged. Returns
    400 for unsupported envs/hosts, 404 for an unknown slug.
    """
    repo = _resolve_project_slug(slug)
    _validate_deploy_config_body(body)
    current = _settings_repo.get_setting_scoped("project", DEPLOY_CONFIG_KEY, project=repo)
    merged = _deploy_merge_for_put(current or {}, body)
    _settings_repo.set_setting("project", DEPLOY_CONFIG_KEY, merged, project=repo)
    return _deploy_config_response(slug, repo, merged)


@app.post("/api/projects/{slug}/environments/{env}/deploy-config/validate")
def validate_deploy_config_field(slug: str, env: str, body: dict):
    """Validate an inline working_dir / port edit before it is persisted (#769).

    Body may carry ``working_dir`` and/or ``port``. Each is checked and a failure
    returns 400 with a user-visible message so the Deploy card can surface an
    inline error and skip the PUT:

      - working_dir → must exist on disk AND be a git clone.
      - port        → must be an integer 1–65535 AND not already in use on host.

    Returns 404 for an unknown slug, 200 ``{"ok": true}`` when all supplied
    fields are valid.
    """
    _resolve_project_slug(slug)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Validation body must be an object.")
    if "working_dir" in body:
        try:
            _deploy_validation.validate_working_dir(body["working_dir"])
        except _deploy_validation.DeployValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "port" in body:
        try:
            port = _deploy_validation.validate_port(body["port"])
        except _deploy_validation.DeployValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if _deploy_validation.port_in_use(port):
            raise HTTPException(
                status_code=400,
                detail=f"Port {port} is already in use on this host.",
            )
    return {"ok": True, "env": env}


# ── Local deploy / restart actions (issue #723) ──────────────────────────────

from services.sprint_manager import deploy_actions as _deploy_actions


def _enrich_deploy_readiness(config: dict) -> None:
    """Attach deploy + lifecycle readiness fields to each local env entry."""
    for _env, entry in (config or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("host") != "local":
            continue
        deploy_ready, deploy_errors = _deploy_actions.check_deploy_readiness(entry)
        restart_ready, restart_errors = _deploy_actions.check_restart_readiness(entry)
        stop_ready, stop_errors = _deploy_actions.check_stop_readiness(entry)
        start_ready, start_errors = _deploy_actions.check_start_readiness(entry)
        entry["deploy_ready"] = deploy_ready
        entry["deploy_errors"] = deploy_errors
        entry["restart_ready"] = restart_ready
        entry["restart_errors"] = restart_errors
        entry["stop_ready"] = stop_ready
        entry["start_ready"] = start_ready
        entry["stop_errors"] = stop_errors
        entry["start_errors"] = start_errors


from services.sprint_manager import render_actions as _render_actions
from services.sprint_manager import deploy_validation as _deploy_validation


def _render_deploy_environment(entry: dict, env: str) -> dict:
    """Trigger a new Render deploy for a host=render env (issue #725).

    Validates render_service_id/render_api_key (400 before any Render call),
    POSTs to the Render deploys endpoint server-side, and returns a status
    snapshot. The render_api_key is never echoed back to the caller.
    """
    try:
        service_id, api_key = _render_actions.require_render_target(entry)
    except _render_actions.RenderActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        _status, payload = _render_actions.call_render(
            "POST", _render_actions.deploy_url(service_id), api_key
        )
    except _render_actions.RenderApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    deploy = payload.get("deploy", payload) if isinstance(payload, dict) else {}
    raw_status = deploy.get("status") if isinstance(deploy, dict) else None
    return {
        "ok": True,
        "env": env,
        "host": "render",
        "action": "deploy",
        "status": _render_actions.normalize_status(raw_status),
    }


def _render_restart_environment(entry: dict, env: str) -> dict:
    """Restart a host=render service via the Render restart endpoint (issue #725)."""
    try:
        service_id, api_key = _render_actions.require_render_target(entry)
    except _render_actions.RenderActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        _render_actions.call_render(
            "POST", _render_actions.restart_url(service_id), api_key
        )
    except _render_actions.RenderApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {"ok": True, "env": env, "host": "render", "action": "restart"}


def _merged_deploy_config(slug: str, repo: str) -> dict:
    """Return seed-merged stored deploy config (raw, secrets intact)."""
    stored = _settings_repo.get_setting_scoped("project", DEPLOY_CONFIG_KEY, project=repo)
    return _deploy_merge_seed(_deploy_seed_for(slug), stored or {})


def _restart_environment(entry: dict) -> dict:
    """Restart a local environment from its config entry.

    Strategy: launchd kickstart when a label is set, else stop+start scripts.
    The dashboard's own process is restarted via a DETACHED helper so this call
    can return before launchd kills the worker. Validation failures raise
    HTTPException 400; a failed command raises 500.
    """
    try:
        _deploy_actions.require_restart_target(entry)
    except _deploy_actions.DeployActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    label = _deploy_actions.restart_label(entry)

    # Dashboard's own process — detach so the response flushes before kickstart.
    if label and _deploy_actions.is_self_restart(entry):
        cmd = _deploy_actions.build_self_restart_command(label)
        subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"method": "launchd-self", "detached": True, "launchd_label": label}

    if label:
        cmd = _deploy_actions.build_kickstart_command(label)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=result.stderr.strip() or result.stdout.strip() or "kickstart failed",
            )
        return {
            "method": "launchd",
            "launchd_label": label,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    # Script fallback: stop then start. Inject PORT=<configured> so the scripts
    # bind the configured port instead of a hardcoded default (issue #769).
    stop, start = _deploy_actions.stop_start_scripts(entry)
    restart_env = _deploy_actions.build_restart_env(entry)
    script_cwd = _deploy_actions.script_working_dir(entry)

    # Self-restart over scripts: the `stop` step would kill THIS process before
    # `start` runs (cause of "Failed to fetch" + a dashboard that never comes
    # back). Detach a `sleep; stop; start` helper in a new session so the
    # response flushes first and the helper survives the stop to run start.
    if _deploy_actions.is_self_restart_dir(entry, str(_REPO_ROOT)):
        cmd = _deploy_actions.build_detached_restart_command(stop, start)
        popen_kw: dict = {
            "start_new_session": True,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if restart_env is not None:
            popen_kw["env"] = restart_env
        if script_cwd:
            popen_kw["cwd"] = script_cwd
        subprocess.Popen(cmd, **popen_kw)
        return {"method": "scripts-self-detached", "detached": True}

    steps = []
    for phase, script in (("stop", stop), ("start", start)):
        run_kw: dict = {
            "capture_output": True,
            "text": True,
            "env": restart_env,
        }
        if script_cwd:
            run_kw["cwd"] = script_cwd
        result = subprocess.run(["sh", "-c", script], **run_kw)
        steps.append({
            "phase": phase,
            "script": script,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        })
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"{phase} script failed: {result.stderr.strip() or script}",
            )
    return {"method": "scripts", "steps": steps}


@app.post("/api/projects/{slug}/environments/{env}/deploy")
def deploy_environment(slug: str, env: str):
    """Pull-only deploy for a local environment, then auto-restart it.

    Runs ``git pull --ff-only origin <branch>`` inside the configured
    ``working_dir`` (never merge/push/PR/checkout), returns the raw pull stdout
    and the new HEAD sha, then triggers the restart action for the same env.
    Rejects (400) when ``working_dir``/``branch`` are absent — before any shell
    command runs. Returns 404 for an unknown slug.
    """
    repo = _resolve_project_slug(slug)
    merged = _merged_deploy_config(slug, repo)
    _enrich_local_working_dirs(repo, merged)
    entry = _deploy_actions.get_env_entry(merged, env)

    # host=render → trigger a Render deploy server-side (issue #725).
    if _render_actions.is_render_host(entry):
        return _render_deploy_environment(entry, env)

    ready, readiness_errors = _deploy_actions.check_deploy_readiness(entry)
    if not ready:
        raise HTTPException(status_code=400, detail="; ".join(readiness_errors))

    try:
        working_dir, branch = _deploy_actions.require_deploy_target(entry)
    except _deploy_actions.DeployActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    pull = subprocess.run(
        _deploy_actions.build_pull_command(branch),
        capture_output=True, text=True, cwd=working_dir,
    )
    if pull.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=pull.stderr.strip() or pull.stdout.strip() or "git pull failed",
        )

    head = subprocess.run(
        _deploy_actions.build_head_sha_command(),
        capture_output=True, text=True, cwd=working_dir,
    )
    head_sha = head.stdout.strip()

    # AC: after a successful pull, auto-trigger restart for the same env.
    # Best-effort — a restart-config problem must not mask a successful pull.
    try:
        restart_result = _restart_environment(entry)
    except HTTPException as exc:
        restart_result = {"ok": False, "error": exc.detail}

    _save_deploy_time(slug, env)
    _deploy_times[f"{slug}/{env}"] = datetime.now(timezone.utc).isoformat()

    resp = {
        "ok": True,
        "env": env,
        "branch": branch,
        "working_dir": working_dir,
        "pull_output": pull.stdout,
        "head": head_sha,
        "restart": restart_result,
    }
    if restart_result.get("detached"):
        return JSONResponse(status_code=202, content=resp)
    return resp


@app.post("/api/projects/{slug}/environments/{env}/restart")
def restart_environment(slug: str, env: str):
    """Restart a local environment's service.

    Uses ``launchctl kickstart -k`` when a ``launchd_label`` is configured, else
    falls back to the configured stop+start scripts. For the dashboard's own
    process the work is detached and the call returns 202 immediately. Rejects
    (400) when no valid restart target is configured; 404 for an unknown slug.
    """
    repo = _resolve_project_slug(slug)
    merged = _merged_deploy_config(slug, repo)
    _enrich_local_working_dirs(repo, merged)
    entry = _deploy_actions.get_env_entry(merged, env)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"No deploy config for environment '{env}'")

    # host=render → restart the Render service server-side (issue #725).
    if _render_actions.is_render_host(entry):
        return _render_restart_environment(entry, env)

    ready, readiness_errors = _deploy_actions.check_restart_readiness(entry)
    if not ready:
        raise HTTPException(status_code=400, detail="; ".join(readiness_errors))

    result = _restart_environment(entry)
    if result.get("detached"):
        return JSONResponse(status_code=202, content={"ok": True, "env": env, **result})
    return {"ok": True, "env": env, **result}


def _stop_environment(entry: dict) -> dict:
    """Stop a local environment without destroying it (issue #771).

    Strategy: ``launchctl bootout`` when a launchd_label is set, else the
    configured ``stop`` script. Validation failures raise HTTPException 400; a
    failed command raises 500. Unlike restart's ``kickstart``, ``bootout``
    removes the service from the domain so it stays down until Start.
    """
    try:
        _deploy_actions.require_stop_target(entry)
    except _deploy_actions.DeployActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    label = _deploy_actions.restart_label(entry)
    if label:
        cmd = _deploy_actions.build_bootout_command(label)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=result.stderr.strip() or result.stdout.strip() or "bootout failed",
            )
        return {
            "method": "launchd-bootout",
            "launchd_label": label,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    stop, _start = _deploy_actions.stop_start_scripts(entry)
    restart_env = _deploy_actions.build_restart_env(entry)
    script_cwd = _deploy_actions.script_working_dir(entry)
    run_kw: dict = {"capture_output": True, "text": True, "env": restart_env}
    if script_cwd:
        run_kw["cwd"] = script_cwd
    result = subprocess.run(["sh", "-c", stop], **run_kw)
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"stop script failed: {result.stderr.strip() or stop}",
        )
    return {"method": "script", "script": stop, "stdout": result.stdout, "stderr": result.stderr}


def _start_environment(entry: dict) -> dict:
    """Start a local environment without pulling new code (issue #771).

    Strategy: ``launchctl bootstrap gui/<uid> <plist>`` when a launchd_label +
    launchd_plist are set, else the configured ``start`` script. Validation
    failures raise HTTPException 400; a failed command raises 500.
    """
    try:
        _deploy_actions.require_start_target(entry)
    except _deploy_actions.DeployActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    label = _deploy_actions.restart_label(entry)
    plist = _deploy_actions.launchd_plist(entry)
    if label and plist:
        cmd = _deploy_actions.build_bootstrap_command(plist)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=result.stderr.strip() or result.stdout.strip() or "bootstrap failed",
            )
        return {
            "method": "launchd-bootstrap",
            "launchd_label": label,
            "launchd_plist": plist,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    _stop, start = _deploy_actions.stop_start_scripts(entry)
    restart_env = _deploy_actions.build_restart_env(entry)
    script_cwd = _deploy_actions.script_working_dir(entry)
    run_kw: dict = {"capture_output": True, "text": True, "env": restart_env}
    if script_cwd:
        run_kw["cwd"] = script_cwd
    result = subprocess.run(["sh", "-c", start], **run_kw)
    if result.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"start script failed: {result.stderr.strip() or start}",
        )
    return {"method": "script", "script": start, "stdout": result.stdout, "stderr": result.stderr}


@app.post("/api/projects/{slug}/environments/{env}/stop")
def stop_environment(slug: str, env: str):
    """Stop a local environment's service without destroying it (issue #771).

    Uses ``launchctl bootout`` when a ``launchd_label`` is configured, else the
    configured ``stop`` script. Rejects (400) when no stop target is configured;
    404 for an unknown slug. host=render has no stop equivalent through this
    dashboard, so the UI hides Start/Stop for render — this endpoint rejects it.
    """
    repo = _resolve_project_slug(slug)
    merged = _merged_deploy_config(slug, repo)
    _enrich_local_working_dirs(repo, merged)
    entry = _deploy_actions.get_env_entry(merged, env)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"No deploy config for environment '{env}'")
    if _render_actions.is_render_host(entry):
        raise HTTPException(
            status_code=400,
            detail="Stop is not supported for host=render environments",
        )
    ready, readiness_errors = _deploy_actions.check_stop_readiness(entry)
    if not ready:
        raise HTTPException(status_code=400, detail="; ".join(readiness_errors))
    result = _stop_environment(entry)
    return {"ok": True, "env": env, **result}


@app.post("/api/projects/{slug}/environments/{env}/start")
def start_environment(slug: str, env: str):
    """Start a local environment's service without pulling code (issue #771).

    Uses ``launchctl bootstrap`` when ``launchd_label`` + ``launchd_plist`` are
    configured, else the configured ``start`` script. Rejects (400) when no
    start target is configured; 404 for an unknown slug. host=render is rejected
    (the UI hides Start/Stop for render).
    """
    repo = _resolve_project_slug(slug)
    merged = _merged_deploy_config(slug, repo)
    _enrich_local_working_dirs(repo, merged)
    entry = _deploy_actions.get_env_entry(merged, env)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"No deploy config for environment '{env}'")
    if _render_actions.is_render_host(entry):
        raise HTTPException(
            status_code=400,
            detail="Start is not supported for host=render environments",
        )
    ready, readiness_errors = _deploy_actions.check_start_readiness(entry)
    if not ready:
        raise HTTPException(status_code=400, detail="; ".join(readiness_errors))
    result = _start_environment(entry)
    return {"ok": True, "env": env, **result}


@app.get("/api/projects/{slug}/environments/{env}/run-state")
def environment_run_state(slug: str, env: str):
    """Return the live run state of a local environment (issue #771).

    ``{"state": "running" | "stopped" | "idle"}``. For a launchd-managed env the
    state comes from ``launchctl print`` (rc 0 → running, non-zero → stopped). A
    script-only env has no reliable probe, so it reports ``idle`` (unknown). Only
    host=local is supported; render run state is derived client-side from the
    deploy status, so this endpoint rejects host=render with 400.
    """
    repo = _resolve_project_slug(slug)
    merged = _merged_deploy_config(slug, repo)
    entry = _deploy_actions.get_env_entry(merged, env)
    if entry is None:
        raise HTTPException(status_code=400, detail=f"No deploy config for environment '{env}'")
    if _render_actions.is_render_host(entry):
        raise HTTPException(
            status_code=400,
            detail="Run state is only available for host=local environments",
        )
    label = _deploy_actions.restart_label(entry)
    if not label:
        return {"ok": True, "env": env, "host": "local", "state": "idle"}
    result = subprocess.run(
        _deploy_actions.build_print_command(label), capture_output=True, text=True
    )
    state = _deploy_actions.interpret_run_state(result.returncode)
    return {"ok": True, "env": env, "host": "local", "state": state}


@app.get("/api/projects/{slug}/environments/{env}/deploy-status")
def environment_deploy_status(slug: str, env: str):
    """Return the normalized latest-deploy status for a host=render env (issue #725).

    Polls ``GET /v1/services/{id}/deploys?limit=1`` server-side and returns
    ``{"status": "queued|building|live|failed"}``. Missing render config → 400;
    a 401/404 from Render → 502 with a specific message. Only host=render is
    supported (local envs have no remote deploy status to poll).
    """
    repo = _resolve_project_slug(slug)
    merged = _merged_deploy_config(slug, repo)
    entry = _deploy_actions.get_env_entry(merged, env)
    if not _render_actions.is_render_host(entry):
        raise HTTPException(
            status_code=400,
            detail="Deploy status is only available for host=render environments",
        )
    try:
        service_id, api_key = _render_actions.require_render_target(entry)
    except _render_actions.RenderActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        _status, payload = _render_actions.call_render(
            "GET", _render_actions.status_url(service_id), api_key
        )
    except _render_actions.RenderApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    # Surface commit SHA + last-deploy timestamp for the Deploy-tab card (#726).
    info = _render_actions.latest_deploy_from_payload(payload)
    return {
        "ok": True,
        "env": env,
        "host": "render",
        "status": info["status"],
        "commit": info["commit"],
        "finished_at": info["finished_at"],
    }


@app.get("/api/deploy/overview")
def get_deploy_overview():
    """Aggregate deployable environments across all known projects (issue #726).

    Powers the Deploy tab: one secret-safe card entry per configured
    environment (``prd``, ``uat``) for every project that ships deploy config
    (commander, perf-coach) plus any project listed in ``projects.json``. Each
    entry carries ``project``, ``env``, ``host`` and host-specific fields; the
    render_api_key is never returned. Live commit SHA / status are fetched
    client-side per card (local via /api/health, render via deploy-status).
    """
    slugs: list[str] = list(_deploy_known_slugs())
    try:
        for proj in projects_module.load_projects():
            s = proj["repo"].split("/")[-1]
            if s not in slugs:
                slugs.append(s)
    except Exception:
        pass

    environments: list[dict] = []
    for slug in slugs:
        try:
            repo = _resolve_project_slug(slug)
        except HTTPException:
            # perf-coach (and any seed-only project) may not be in projects.json;
            # its stored override lookup just returns empty and seed defaults win.
            repo = f"zealchaiwut/{slug}"
        merged = _merged_deploy_config(slug, repo)
        # issue #769 — fill working_dir for local envs from on-disk env paths so
        # the card shows the run folder even with no stored override.
        _enrich_local_working_dirs(repo, merged)
        _enrich_deploy_readiness(merged)
        for card in _deploy_overview_entries_for(slug, merged):
            cfg = merged.get(card["env"], {})
            card["deploy_ready"] = cfg.get("deploy_ready", card["host"] == "render")
            card["deploy_errors"] = cfg.get("deploy_errors", [])
            card["restart_ready"] = cfg.get("restart_ready", card["host"] == "render")
            card["restart_errors"] = cfg.get("restart_errors", [])
            card["stop_ready"] = cfg.get("stop_ready", card["host"] == "render")
            card["start_ready"] = cfg.get("start_ready", card["host"] == "render")
            card["stop_errors"] = cfg.get("stop_errors", [])
            card["start_errors"] = cfg.get("start_errors", [])
            if card["host"] == "local":
                card["git_sha"] = _GIT_SHA
                card["git_commit_msg"] = _GIT_COMMIT_MSG
                card["server_started_at"] = _STARTED_AT
                card["last_deployed_at"] = _deploy_times.get(f"{slug}/{card['env']}")
            environments.append(card)

    return {"environments": environments}


# ── Settings sync (issue #644) ───────────────────────────────────────────────


@app.get("/api/settings/sync/status")
def get_settings_sync_status():
    """Return last-synced timestamp for settings sync.

    Returns {"last_synced": str|null} where str is ISO 8601 UTC.
    """
    ts = _ss_get_status(_SYNC_STATUS_FILE) if _SYNC_SETTINGS_AVAILABLE else None
    return {"last_synced": ts}


class _SyncDiffBody(BaseModel):
    direction: str  # "upload" or "fetch"


@app.post("/api/settings/sync/diff")
def post_settings_sync_diff(body: _SyncDiffBody):
    """Compute a settings diff without applying any writes.

    direction="upload" — compares local files → Neon
    direction="fetch"  — compares Neon → local files

    Returns {"diff": [...], "already_in_sync": bool}.
    Each diff item has: status ("added"|"removed"|"unchanged"), key, value.
    """
    if body.direction not in ("upload", "fetch"):
        raise HTTPException(status_code=400, detail="direction must be 'upload' or 'fetch'")
    if not _SYNC_SETTINGS_AVAILABLE:
        raise HTTPException(status_code=503, detail="settings sync module unavailable")

    try:
        local_snap = _ss_load_local(_PROJECTS_FILE, sprint_yaml_path=_SPRINT_YAML_PATH)
        neon_snap = _ss_load_neon(_settings_repo)
        diff = _ss_compute_diff(local_snap, neon_snap, body.direction)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "diff": diff,
        "already_in_sync": _ss_already_in_sync(diff),
    }


class _SyncCommitBody(BaseModel):
    direction: str  # "upload" or "fetch"


@app.post("/api/settings/sync/commit")
def post_settings_sync_commit(body: _SyncCommitBody):
    """Apply a settings sync after user confirms the diff preview.

    direction="upload" — writes changed values to Neon; local files unchanged
    direction="fetch"  — writes changed values to local files; Neon unchanged

    Returns {"ok": true, "synced_at": str (ISO 8601 UTC)}.
    """
    if body.direction not in ("upload", "fetch"):
        raise HTTPException(status_code=400, detail="direction must be 'upload' or 'fetch'")
    if not _SYNC_SETTINGS_AVAILABLE:
        raise HTTPException(status_code=503, detail="settings sync module unavailable")

    try:
        local_snap = _ss_load_local(_PROJECTS_FILE, sprint_yaml_path=_SPRINT_YAML_PATH)
        neon_snap = _ss_load_neon(_settings_repo)
        diff = _ss_compute_diff(local_snap, neon_snap, body.direction)

        if body.direction == "upload":
            _ss_apply_upload(diff, _settings_repo)
        else:
            _ss_apply_fetch(diff, _PROJECTS_FILE, _SPRINT_YAML_PATH)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _ss_save_status(_SYNC_STATUS_FILE, synced_at)

    return {"ok": True, "synced_at": synced_at}


# ── Filesystem browser (issue #643) ──────────────────────────────────────────

_FS_BROWSE_ROOT: Path = Path.home()


@app.get("/api/fs/list")
def fs_list(path: str = ""):
    """Return immediate subdirectories of the given path.

    The path must resolve to within _FS_BROWSE_ROOT.  Traversal attempts
    (../, symlinks escaping root, absolute paths outside root) return 403.
    Returns {"entries": [{"name": str, "path": str}], "current": str}.
    """
    root = _FS_BROWSE_ROOT.resolve()

    if not path or path.strip() in ("~", ""):
        target = root
    else:
        # Handle ~ prefix
        if path.startswith("~/"):
            path = str(root / path[2:])
        elif path == "~":
            path = str(root)

        candidate = Path(path)
        # Normalize ../ etc without following symlinks
        normalized = Path(os.path.normpath(str(candidate)))

        # Must be under browse root before any symlink resolution
        if not normalized.is_relative_to(root):
            raise HTTPException(status_code=403, detail="Forbidden")

        # Walk each component; reject if any symlink escapes root.
        # This catches escape-then-reenter chains that resolve() misses.
        cur = root
        for part in normalized.relative_to(root).parts:
            cur = cur / part
            if cur.is_symlink():
                try:
                    link_resolved = cur.resolve()
                    if not link_resolved.is_relative_to(root):
                        raise HTTPException(status_code=403, detail="Forbidden")
                except OSError:
                    raise HTTPException(status_code=403, detail="Forbidden")

        target = normalized

    if not target.exists() or not target.is_dir():
        return {"entries": [], "current": str(target)}

    entries = []
    try:
        for item in sorted(target.iterdir()):
            if item.is_symlink():
                continue  # never follow symlinks out of browse root
            if not item.is_dir():
                continue
            # Skip hidden dirs
            if item.name.startswith("."):
                continue
            entries.append({"name": item.name, "path": str(item)})
    except OSError as exc:
        logger.info("[fs_list] error listing %s: %s", target, exc)

    return {"entries": entries, "current": str(target)}


# ── Environment paths (issue #643) ────────────────────────────────────────────

def _derive_project_environments(repo: str) -> dict[str, str]:
    """Best-effort guess of env paths from the on-disk project layout when none
    are saved, so the Settings form prefills instead of showing placeholders.

    Nested layout: ~/dev/<name>/{prd,uat,coder,tester}
    Flat layout:   ~/dev/<name> (prd) + ~/dev/<name>-{coder,tester}, ~/dev/<name>/uat
    Only paths that actually exist on disk are returned.
    """
    name = repo.split("/")[-1]
    dev = _PROJECTS_BASE  # ~/dev
    found: dict[str, str] = {}
    nested = dev / name
    for env in ("prd", "uat", "coder", "tester"):
        cand = nested / env
        if cand.is_dir():
            found[env] = str(cand)
    if found:
        return found
    # Flat layout fallback
    flat = {
        "prd": dev / name,
        "uat": dev / name / "uat",
        "coder": dev / f"{name}-coder",
        "tester": dev / f"{name}-tester",
    }
    for env, cand in flat.items():
        if cand.is_dir():
            found[env] = str(cand)
    return found


@app.get("/api/projects/{slug}/environments")
def get_project_environments(slug: str):
    """Return environment paths stored in projects.json for this project.

    When none are saved, auto-derive them from the on-disk layout so the form
    prefills (the user can edit + save to persist)."""
    repo = _resolve_project_slug(slug)
    envs = projects_module.get_project_environments(repo)
    if not envs:
        envs = _derive_project_environments(repo)
    return {
        "environments": [
            {"env": env, "local_directory": local_dir}
            for env, local_dir in envs.items()
        ]
    }


class _EnvEntry(BaseModel):
    env: str
    local_directory: str


class _PutEnvironmentsBody(BaseModel):
    environments: list[_EnvEntry]


@app.put("/api/projects/{slug}/environments")
def put_project_environments(slug: str, body: _PutEnvironmentsBody):
    """Validate and persist environment paths for this project.

    Each path must (a) exist on disk and (b) be a git working clone.
    Returns 422 with a descriptive error for the first invalid entry.
    Returns 404 if the project slug is not found.
    """
    repo = _resolve_project_slug(slug)

    errors = []
    for entry in body.environments:
        env = entry.env.strip()
        local_dir = entry.local_directory.strip()
        if not env:
            errors.append("env name must not be blank")
            continue
        p = Path(local_dir)
        if not p.exists():
            errors.append(
                f"'{env}': path '{local_dir}' does not exist on this machine"
            )
            continue
        if not (p / ".git").exists():
            errors.append(
                f"'{env}': path '{local_dir}' is not a git repository (.git not found)"
            )
            continue
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(p),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            errors.append(
                f"'{env}': git rev-parse timed out for path '{local_dir}'"
                " (repo may be on an unreachable network mount)"
            )
            continue
        if result.returncode != 0:
            errors.append(
                f"'{env}': path '{local_dir}' is not a valid git repository"
                " (git rev-parse failed — repo may be corrupted or misconfigured)"
            )
            continue

    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    envs_dict = {e.env.strip(): e.local_directory.strip() for e in body.environments}
    projects_module.save_project_environments(repo, envs_dict)
    return {"ok": True, "environments": envs_dict}


# ── Env-var editor (issue #727) ───────────────────────────────────────────────

import env_file as _env_file


def _env_working_dir(slug: str, repo: str, env: str) -> str | None:
    """Resolve the on-disk directory holding an environment's `.env` file.

    Prefers the stored/derived environment path (the working clone), and falls
    back to a host=local deploy-config working_dir. Returns None when nothing is
    configured for the requested env.
    """
    envs = projects_module.get_project_environments(repo)
    if not envs:
        envs = _derive_project_environments(repo)
    if env in envs and envs[env]:
        return envs[env]
    merged = _merged_deploy_config(slug, repo)
    entry = (merged or {}).get(env) or {}
    return entry.get("working_dir") or None


@app.get("/api/projects/{slug}/environments/{env}/env-vars")
def get_env_vars(slug: str, env: str):
    """Read the environment's `.env` file and return parsed key/value pairs.

    Pairs are returned in file order. Values are plaintext — masking is a
    display-only concern handled client-side. Returns 404 for an unknown slug or
    an env with no configured directory.
    """
    repo = _resolve_project_slug(slug)
    working_dir = _env_working_dir(slug, repo, env)
    if not working_dir:
        raise HTTPException(
            status_code=404,
            detail=f"No directory configured for environment '{env}'",
        )
    env_path = Path(working_dir) / ".env"
    return {
        "env": env,
        "working_dir": working_dir,
        "vars": _env_file.read_env_vars(env_path),
    }


class _EnvVarItem(BaseModel):
    key: str
    value: str = ""


class _PutEnvVarsBody(BaseModel):
    vars: list[_EnvVarItem]


@app.put("/api/projects/{slug}/environments/{env}/env-vars")
def put_env_vars(slug: str, env: str, body: _PutEnvVarsBody):
    """Write env-var changes back to the environment's `.env` file.

    Preserves original line order and inline comments for unchanged keys,
    rewrites changed keys in place, appends new keys at the end, and drops
    removed keys. An empty KEY is rejected (400) before any write. A filesystem
    failure (e.g. read-only `.env`) returns 500 so the client can keep the
    user's edits. Returns 404 for an unknown slug or unconfigured env.
    """
    repo = _resolve_project_slug(slug)
    working_dir = _env_working_dir(slug, repo, env)
    if not working_dir:
        raise HTTPException(
            status_code=404,
            detail=f"No directory configured for environment '{env}'",
        )

    pairs: list[tuple[str, str]] = []
    for item in body.vars:
        key = item.key.strip()
        if not key:
            raise HTTPException(
                status_code=400,
                detail="Each variable must have a non-empty KEY.",
            )
        pairs.append((key, item.value))

    env_path = Path(working_dir) / ".env"
    try:
        _env_file.write_env_vars(env_path, pairs)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write {env_path}: {exc}",
        )
    return {
        "ok": True,
        "env": env,
        "working_dir": working_dir,
        "vars": _env_file.read_env_vars(env_path),
    }


# ── Scaffold docs (issue #681) ────────────────────────────────────────────────

def _scaffold_resolve_working_clone(slug: str) -> tuple[str, Path]:
    """Resolve project slug → (repo, working_clone_path) with traversal guard.

    Returns the main working clone directory and validates it is inside _PROJECTS_BASE.
    Raises HTTPException 404 for unknown slug, 400 if path escapes _PROJECTS_BASE.
    """
    repo = _resolve_project_slug(slug)
    project_root = _project_root_path(repo)
    working_clone = _main_clone_path(project_root)
    resolved = working_clone.resolve()
    base_resolved = _PROJECTS_BASE.resolve()
    if not str(resolved).startswith(str(base_resolved) + "/") and resolved != base_resolved:
        raise HTTPException(
            status_code=400,
            detail=f"Resolved project root '{resolved}' is outside the configured projects directory '{base_resolved}'",
        )
    return repo, working_clone


@app.get("/api/projects/{slug}/docs/scaffold/check")
def get_scaffold_check(slug: str):
    """Check whether the project's working clone has the standard docs structure.

    Runs scaffold_project --check (no writes). Returns:
      { compliant, missing, stray, project_root }
    where missing/stray are lists of relative paths.
    """
    if not _SCAFFOLD_AVAILABLE:
        raise HTTPException(status_code=503, detail="scaffold_project module unavailable")
    repo, working_clone = _scaffold_resolve_working_clone(slug)
    project_name = working_clone.name
    if project_name in ("main", "prd") and working_clone.parent != working_clone:
        project_name = working_clone.parent.name
    result = _scaffold_data(working_clone, project_name, check=True)
    return {
        "compliant": result["compliant"],
        "missing": result["missing"],
        "stray": result["stray"],
        "project_root": str(working_clone),
    }


class _ScaffoldApplyBody(BaseModel):
    confirm: bool = False


@app.post("/api/projects/{slug}/docs/scaffold/apply")
def post_scaffold_apply(slug: str, body: _ScaffoldApplyBody):
    """Apply the standard docs scaffold to the project's working clone.

    Requires { confirm: true } in the request body; returns 400 otherwise.
    Existing files are NEVER overwritten. Returns { created, compliant }.
    """
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to apply scaffold")
    if not _SCAFFOLD_AVAILABLE:
        raise HTTPException(status_code=503, detail="scaffold_project module unavailable")
    repo, working_clone = _scaffold_resolve_working_clone(slug)
    project_name = working_clone.name
    if project_name in ("main", "prd") and working_clone.parent != working_clone:
        project_name = working_clone.parent.name
    result = _scaffold_data(working_clone, project_name, check=False)
    return {
        "created": result["created"],
        "compliant": result["compliant"],
    }


# ── Sprint file archive maintenance (issue #735) ──────────────────────────────

class _SprintCleanupBody(BaseModel):
    project: str
    dry_run: bool = False


@app.post("/api/maintenance/sprints/cleanup")
def post_sprint_cleanup(body: _SprintCleanupBody):
    """Archive stale per-sprint runtime files for a project's finished sprints.

    Moves sprint-N-plan.json, the zero-issue sprint-N.json placeholder, and
    sprint-N-state.json for finished sprints into .commander/sprints/archive/.
    Status, estimate, and summary files are never touched; nothing is deleted.

    With { dry_run: true } it returns the same shape as a preview without
    moving anything. Returns { archived: [...], kept_count: N }.
    """
    if not _CLEAN_SPRINT_AVAILABLE:
        raise HTTPException(status_code=503, detail="clean_sprint_files module unavailable")

    project = (body.project or "").strip()
    if not project:
        raise HTTPException(status_code=400, detail="project is required")

    project_root = _project_root_path(project)
    sprints_dir = _commander_dir(project_root) / "sprints"
    if not sprints_dir.exists():
        return {"archived": [], "kept_count": 0, "dry_run": body.dry_run}

    # A sprint counts as finished when it has a posted summary issue OR a local
    # summary markdown, AND no live process is running it. The summary-issue set
    # is GitHub-backed; running state uses the dashboard's authoritative check.
    finished_nums: set[int] = set()
    try:
        for label in _finished_sprint_summaries(project).keys():
            m = re.match(r"^sprint-(\d+)$", label)
            if m:
                finished_nums.add(int(m.group(1)))
    except Exception:
        pass

    def _running_check(_dir: Path, n: int) -> bool:
        return _is_sprint_running(project_root, f"sprint-{n}")

    try:
        result = _clean_sprint_files.run_cleanup(
            sprints_dir,
            dry_run=body.dry_run,
            has_summary_issue=lambda n: n in finished_nums,
            running_check=_running_check,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Archive failed: {exc}") from exc
    return {
        "archived": result["archived"],
        "kept_count": result["kept_count"],
        "dry_run": result["dry_run"],
    }


# ── Branch cleanup (issue #634) ───────────────────────────────────────────────

_PROTECTED_BRANCHES: frozenset[str] = frozenset({"develop", "master", "main", "attachments"})
_STALE_BRANCH_RE = re.compile(r"^(feat|feature|sprint)/")


@app.get("/api/projects/{owner}/{repo_name}/branches/stale")
def get_stale_branches(owner: str, repo_name: str):
    """Return sorted list of feat/* or sprint/* branch names that are both
    merged (their PR was merged) and still exist on the remote.
    develop, master, main, and attachments are always excluded.
    """
    repo = f"{owner}/{repo_name}"

    # 1. Collect heads of merged PRs matching the allowed patterns
    merged_heads: set[str] = set()
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "merged",
             "--limit", "200", "--json", "headRefName"],
            capture_output=True, text=True, check=False,
        )
        for pr in json.loads(result.stdout or "[]"):
            head = pr.get("headRefName", "")
            if _STALE_BRANCH_RE.match(head) and head not in _PROTECTED_BRANCHES:
                merged_heads.add(head)
    except Exception:
        pass

    if not merged_heads:
        return []

    # 2. List currently existing branches that match the patterns
    existing: set[str] = set()
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/branches", "--paginate", "--jq", ".[].name"],
            capture_output=True, text=True, check=False,
        )
        for name in result.stdout.splitlines():
            name = name.strip()
            if name and _STALE_BRANCH_RE.match(name) and name not in _PROTECTED_BRANCHES:
                existing.add(name)
    except Exception:
        pass

    return sorted(merged_heads & existing)


@app.delete("/api/projects/{owner}/{repo_name}/branches/{branch:path}", status_code=200)
def delete_project_branch(owner: str, repo_name: str, branch: str):
    """Delete a feat/* or sprint/* branch from the remote.
    Returns 400 for protected branches (develop/master/main/attachments) or
    branches not matching the allowed patterns.
    """
    if branch in _PROTECTED_BRANCHES:
        raise HTTPException(status_code=400,
                            detail=f"Cannot delete protected branch: {branch!r}")
    if not _STALE_BRANCH_RE.match(branch):
        raise HTTPException(
            status_code=400,
            detail=f"Only feat/*, feature/*, or sprint/* branches may be deleted. Got: {branch!r}",
        )

    repo = f"{owner}/{repo_name}"
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "DELETE",
             f"repos/{repo}/git/refs/heads/{branch}"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete {branch!r}: {result.stderr.strip()}",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "branch": branch}


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

    # Build subprocess command (scripts/ live at the repo root, not under apps/dashboard)
    script_path = _REPO_ROOT / "scripts" / "init_project.py"
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
    if body.from_existing:
        cmd.append("--from-existing")

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


# ── docs freshness endpoints (#589) ──────────────────────────────────────────

class DocsFreshnessWarning(BaseModel):
    repo: str
    doc_path: str
    trigger_ref: str
    trigger_type: str = "push"
    trigger_url: Optional[str] = None


class DocsFreshnessCheckPayload(BaseModel):
    repo: str
    trigger_ref: str
    trigger_type: str = "push"
    trigger_url: Optional[str] = None
    stale_docs: list[str] = []
    cleared_docs: list[str] = []


@app.post("/api/docs-freshness/check", status_code=200)
def docs_freshness_check(payload: DocsFreshnessCheckPayload):
    """Receive a freshness check result and update the warnings table.

    stale_docs — doc paths that are now stale (upsert warnings).
    cleared_docs — doc paths that have been updated (clear warnings).
    """
    upserted, cleared = [], []
    for doc in payload.stale_docs:
        db.upsert_docs_warning(
            repo=payload.repo,
            doc_path=doc,
            trigger_ref=payload.trigger_ref,
            trigger_type=payload.trigger_type,
            trigger_url=payload.trigger_url,
        )
        upserted.append(doc)
    for doc in payload.cleared_docs:
        db.clear_docs_warning(repo=payload.repo, doc_path=doc)
        cleared.append(doc)
    return {"ok": True, "upserted": upserted, "cleared": cleared}


@app.get("/api/docs-freshness/warnings")
def get_docs_freshness_warnings(repo: Optional[str] = None):
    return db.get_active_docs_warnings(repo=repo)


@app.delete("/api/docs-freshness/warnings/{warning_id}")
def clear_docs_freshness_warning(warning_id: int):
    found = db.clear_docs_warning_by_id(warning_id)
    return {"ok": True, "cleared": found}


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


def _invalidate_home_cache(slug: str) -> None:
    """Drop cached /api/home payload for *slug* after identity-changing settings writes."""
    _home_cache.pop(f"home:{slug}", None)


def _project_identity_for_home(repo: str, proj: dict, slug: str) -> dict[str, str]:
    """Resolve icon/color/display name for home payloads (settings override projects.json)."""
    icon = proj.get("icon", "ti-folder")
    color = proj.get("color", "gray")
    name = proj.get("name", slug)
    try:
        proj_override = _settings_repo.get_setting_scoped("project", APP_CONFIG_KEY, project=repo)
        if proj_override.get("icon"):
            icon = proj_override["icon"]
        if proj_override.get("color"):
            color = proj_override["color"]
        if proj_override.get("display_name"):
            name = proj_override["display_name"]
    except Exception:
        pass
    return {"name": name, "icon": icon, "color": color}


def _home_project_data(proj: dict, running_sprints: list[dict]) -> dict:
    """Compute per-project home data, cached 30 s per project slug.

    On any GitHub fetch error, returns an idle sentinel with 0 counts so the
    overall endpoint still returns 200.
    """
    repo = proj["repo"]
    slug = repo.split("/")[-1]
    identity = _project_identity_for_home(repo, proj, slug)
    name = identity["name"]
    icon = identity["icon"]
    color = identity["color"]

    cache_key = f"home:{slug}"
    now = time.monotonic()
    cached = _home_cache.get(cache_key)
    if cached and now - cached[0] < _HOME_CACHE_TTL:
        return cached[1]

    def _idle() -> dict:
        sentinel: dict = {
            "name": name, "slug": slug, "repo": repo, "icon": icon, "color": color,
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

    # The durable SQLite sprints table (issue #757) is authoritative for the
    # running check, so the PID-based scan above is sufficient. The former Neon
    # supplement was removed in issue #758.
    if proj_running:
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
        "repo": repo,
        "icon": icon,
        "color": color,
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
            elif "needs-rework" in labels or "need-rework" in labels:  # READ-only: backward compat
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
    sprint: Optional[int] = None        # None = remove all sprint labels (legacy)
    sprint_label: Optional[str] = None  # e.g. "sprint-15.1"; takes precedence over sprint


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

    Body: {"issue": 21, "sprint": 3}               — assigns sprint-3 (legacy)
    Body: {"issue": 21, "sprint_label": "sprint-15.1"} — assigns sprint-15.1 (dotted sub-label)
    Body: {"issue": 21, "sprint": null}             — removes all sprint-* labels

    On success: invalidates cache, broadcasts SSE sprint_plan_update, returns {"ok": true}.
    Creates sprint-N label if it doesn't exist.
    """
    action_id = str(uuid.uuid4())

    # Capture current sprint labels before the change for from_sprint
    try:
        _issue_data = github_client.get_issue(body.issue)
        _cur_labels = [lbl["name"] for lbl in _issue_data.get("labels", [])]
        _from_sprint = next(
            (lbl for lbl in _cur_labels if _SPRINT_LABEL_RE_ALL.match(lbl)), None
        ) or "backlog"
    except Exception:
        _from_sprint = None

    try:
        if body.sprint_label is not None:
            # Dotted or plain label string — use the string-based assign function
            label = body.sprint_label.strip() or None
            if label and not _SPRINT_LABEL_RE.match(label):
                raise HTTPException(400, detail=f"Invalid sprint_label: {label!r}")
            github_client.assign_sprint_by_label(body.issue, label)
        else:
            github_client.assign_sprint(body.issue, body.sprint)
        # Invalidate open_issues_body cache so next GET reflects the change
        github_client.invalidate("open_issues_body:")
        github_client.invalidate("open_issues:")
        github_client.invalidate("sprints:")
        github_client.invalidate("sprint_labels:")
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    _to_sprint: str | None
    if body.sprint_label is not None:
        _to_sprint = (body.sprint_label.strip() or None) or "backlog"
    elif body.sprint is not None:
        _to_sprint = f"sprint-{body.sprint}"
    else:
        _to_sprint = "backlog"

    _emit_dashboard_event(
        project="dashboard",
        type="ticket_moved",
        target=f"#{body.issue}",
        detail={"from_sprint": _from_sprint, "to_sprint": _to_sprint},
        action_id=action_id,
    )
    if _to_sprint and _to_sprint != "backlog" and _to_sprint != _from_sprint:
        _emit_dashboard_event(
            project="dashboard",
            type="label_added",
            target=f"#{body.issue}",
            detail={"label": _to_sprint},
            action_id=action_id,
        )
    if _from_sprint and _from_sprint != "backlog" and _from_sprint != _to_sprint:
        _emit_dashboard_event(
            project="dashboard",
            type="label_removed",
            target=f"#{body.issue}",
            detail={"label": _from_sprint},
            action_id=action_id,
        )

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
    action_id = str(uuid.uuid4())

    # Capture from_sprint before change
    try:
        _issue_data = github_client.get_issue(issue_id, repo_name=body.project or None)
        _cur_labels = [lbl["name"] for lbl in _issue_data.get("labels", [])]
        _from_sprint = next(
            (lbl for lbl in _cur_labels if _SPRINT_LABEL_RE_ALL.match(lbl)), None
        ) or "backlog"
    except Exception:
        _from_sprint = None

    # Resolve the sprint label from whichever field was provided
    if body.sprint_label is not None:
        raw = body.sprint_label.strip()
        if raw == "" or raw == "backlog":
            label_to_assign: str | None = None
        elif _SPRINT_LABEL_RE.match(raw):
            label_to_assign = raw
        else:
            raise HTTPException(400, detail=f"Invalid sprint_label: {raw!r}")
    elif body.sprint is not None:
        label_to_assign = f"sprint-{body.sprint}"
    else:
        raise HTTPException(400, detail="Provide sprint_label or sprint")

    repo = body.project or None
    try:
        github_client.assign_sprint_by_label(issue_id, label_to_assign, repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    _to_sprint = label_to_assign or "backlog"
    _emit_dashboard_event(
        project=body.project or "dashboard",
        type="ticket_moved",
        target=f"#{issue_id}",
        detail={"from_sprint": _from_sprint, "to_sprint": _to_sprint},
        action_id=action_id,
    )
    if label_to_assign and label_to_assign != _from_sprint:
        _emit_dashboard_event(
            project=body.project or "dashboard",
            type="label_added",
            target=f"#{issue_id}",
            detail={"label": label_to_assign},
            action_id=action_id,
        )
    if _from_sprint and _from_sprint != "backlog" and _from_sprint != label_to_assign:
        _emit_dashboard_event(
            project=body.project or "dashboard",
            type="label_removed",
            target=f"#{issue_id}",
            detail={"label": _from_sprint},
            action_id=action_id,
        )
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


_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)?$")
_SUMMARY_TITLE_RE = re.compile(r"^Sprint \d+(\.\d+)*\s+Executive Summary$")
_SUMMARY_TITLE_NUM_RE = re.compile(r"^Sprint (\d+(?:\.\d+)*)\s+Executive Summary$")


def _finished_sprint_summaries(repo_name: str | None) -> dict[str, dict]:
    """Map ``sprint-<N>`` label -> summary issue {number,url,title} for sprints
    that have a posted "Sprint N Executive Summary" issue.

    These summary issues are labeled ``sprint-summary``/``docs`` (NOT ``sprint-N``),
    so the sprint number is parsed from the TITLE. This is the single
    GitHub-backed signal used by both the nav pill and the board to mark a
    sprint finished (works cross-machine).
    """
    try:
        repo = github_client.get_repo_for_operation(repo_name)
    except Exception:
        return {}
    try:
        # Cached in github_client (summary_issues: TTL) — this was an uncached
        # gh issue list (GraphQL) on the board/nav hot path, 5 call sites.
        issues = github_client.list_summary_issues(repo_name=repo)
    except Exception:
        issues = []

    result: dict[str, dict] = {}
    for iss in issues:
        m = _SUMMARY_TITLE_NUM_RE.match(iss.get("title", "") or "")
        if not m:
            continue  # e.g. a feature ticket that merely carries the label
        label = f"sprint-{m.group(1)}"
        prev = result.get(label)
        if prev is None or (iss.get("number") or 0) > (prev.get("number") or 0):
            result[label] = {
                "number": iss.get("number"),
                "url": iss.get("url"),
                "title": iss.get("title"),
            }
    return result

_REPO_ROOT = Path(__file__).parent.parent.parent
SPRINT_MANAGER_PATH = _REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
SPRINT_LOG_PATH = Path(__file__).parent / "sprints" / "sprint_run.log"
# Read once; default covers both dashboard banner and ntfy push notifications.
# sprint_manager validates the value — no validation added here.
_ALERT_MODES = os.environ.get("COMMANDER_ALERT_MODES", "dashboard-banner,ntfy")

_SPRINT_LABEL_RE_ALL = re.compile(r"^sprint-\d+(\.\d+)?$")


def _dashboard_actor() -> str:
    return os.environ.get("COMMANDER_USER", "dashboard")


def _emit_dashboard_event(
    project: str,
    type: str,
    target: str,
    detail: dict,
    action_id: str,
) -> None:
    try:
        db.record_event(
            project=project,
            source="dashboard",
            actor=_dashboard_actor(),
            type=type,
            target=target,
            detail=detail,
            action_id=action_id,
        )
    except Exception:
        pass


def _read_sprint_summary_url(project_root: Path, sprint_label: str) -> Optional[str]:
    """Return the summary-issue URL from the ingested DB row or state file."""
    row = db.get_sprint(sprint_label)
    if row and row.get("summary_issue_url"):
        return row["summary_issue_url"]

    from routers import sprint_artifact_service  # noqa: PLC0415
    state = sprint_artifact_service.load_state_file(
        _commander_dir(project_root) / "sprints", sprint_label,
    )
    if state:
        return state.get("summary_issue_url")
    return None


def _sprint_label_sort_key(label: str) -> tuple:
    """Return (N, M) tuple for natural sprint label ordering.

    Plain sprint-N returns (N, 0); dotted sprint-N.M returns (N, M).
    """
    m = re.match(r"^sprint-(\d+)(?:\.(\d+))?$", label)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else 0)


def _next_sprint_sublabel(sprint_label: str, existing_label_names: set[str]) -> str:
    """Compute the next sibling sub-label for a sprint re-run.

    sprint-25   → sprint-25.1 (or sprint-25.2 if sprint-25.1 already exists)
    sprint-25.1 → sprint-25.2 (next sibling, not a child)
    sprint-25.2 → sprint-25.3
    """
    m = re.match(r"^(sprint-\d+)(?:\.(\d+))?$", sprint_label)
    if not m:
        raise ValueError(f"Invalid sprint label: {sprint_label!r}")
    base = m.group(1)
    current_suffix = int(m.group(2)) if m.group(2) else 0
    candidate = current_suffix + 1
    while True:
        label = f"{base}.{candidate}"
        if label not in existing_label_names:
            return label
        candidate += 1


class SprintRunBody(BaseModel):
    label: str
    goal: str
    budget: Optional[int] = None


@app.post("/api/sprint-run")
def run_sprint(request: Request, body: SprintRunBody):
    """Spawn sprint_manager.py as a detached background process."""
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/sprint-run", method="POST", sprint_label=body.label)
    if not _SPRINT_LABEL_RE.match(body.label):
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprint-run", level="error", sprint_label=body.label, error="invalid sprint label")
        raise HTTPException(400, detail=f"Invalid sprint label: {body.label!r}")
    if not SPRINT_MANAGER_PATH.exists():
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprint-run", level="error", sprint_label=body.label, error="sprint_manager.py not found")
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    SPRINT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(SPRINT_LOG_PATH, "a")

    cmd = [sys.executable, str(SPRINT_MANAGER_PATH), body.label]
    if body.budget is not None:
        cmd += [f"--budget={body.budget}"]
    cmd += ["--alert-mode", _ALERT_MODES]

    subprocess.Popen(
        cmd,
        env={**os.environ, "SPRINT_GOAL": body.goal},
        stdout=log_fh,
        stderr=log_fh,
        start_new_session=True,
    )
    _slog.event("sprint.dispatch", project="dashboard", request_id=request.state.request_id, sprint_label=body.label, dispatch_type="simple")
    _emit_dashboard_event(
        project="dashboard",
        type="sprint_run",
        target=body.label,
        detail={"sprint_id": body.label},
        action_id=str(uuid.uuid4()),
    )
    return {"ok": True, "label": body.label}


# ── Sprint Management endpoints (issue #95) ──────────────────────────────────

_PROJECTS_BASE = Path.home() / "dev"

# Sync status file — persists last-synced timestamp
_SYNC_STATUS_FILE = Path(__file__).parent.parent.parent.parent / ".commander" / "settings.last_synced"

# Sprint.yaml path discovery — walk up from the server file
def _find_sprint_yaml() -> Optional[Path]:
    current = Path(__file__).resolve().parent
    while True:
        candidate = current / ".commander" / "sprint.yaml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None

_SPRINT_YAML_PATH: Optional[Path] = _find_sprint_yaml()
_PROJECTS_FILE: Path = projects_module.PROJECTS_FILE


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


def _main_clone_path(project_root: Path) -> Path:
    """Return the main working clone for a project root.

    Nested layout: <project_root>/main/ (has .git)
    Flat layout:   <project_root> itself
    """
    nested = project_root / "main"
    if nested.is_dir() and (nested / ".git").exists():
        return nested
    return project_root


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _effective_agent_models(project_root: Path) -> dict:
    """Resolve the model each sprint agent will actually use, read straight from
    the project's sprint.yaml agent_config — the SAME source sprint_manager
    reads at dispatch. Mirrors _resolve_model precedence: per-agent key →
    default_model → hardcoded. Surfaced in the preflight so the run-confirm
    modal shows the real models (no drift with a settings snapshot).
    """
    agent_cfg: dict = {}
    try:
        import yaml as _yaml
        sy = _commander_dir(project_root) / "sprint.yaml"
        if sy.exists():
            data = _yaml.safe_load(sy.read_text(encoding="utf-8")) or {}
            ac = data.get("agent_config")
            if isinstance(ac, dict):
                agent_cfg = ac
    except Exception:
        agent_cfg = {}

    default_model = agent_cfg.get("default_model") or None

    def _resolve(key: str, hardcoded: str) -> str:
        return str(agent_cfg.get(key) or default_model or hardcoded)

    # Tester is risk-routed (issue #790): agent_config.tester.by_risk.
    by_risk = {"LOW": "claude-haiku-4-5", "MEDIUM": "claude-haiku-4-5", "HIGH": "claude-sonnet-4-6"}
    tester_sub = agent_cfg.get("tester")
    if isinstance(tester_sub, dict) and isinstance(tester_sub.get("by_risk"), dict):
        by_risk = {str(k).upper(): str(v) for k, v in tester_sub["by_risk"].items()}

    return {
        "coder":         _resolve("coder_model", "claude-sonnet-4-6"),
        "estimator":     _resolve("estimator_model", "claude-sonnet-4-6"),
        "documentor":    _resolve("documentor_model", "claude-sonnet-4-6"),
        "tester_by_risk": by_risk,
        "default_model": default_model,
    }


def _sprint_order_path(project_root: Path) -> Path:
    return _commander_dir(project_root) / "sprint-order.json"


def _sprint_goal_path(project_root: Path, sprint_label: str) -> Path:
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}-goal.txt"


# Shared sprint-dispatch env builder now lives in routers.dispatch_service so
# the extracted routers and the sprint_manager-facing run/finish endpoints share
# one implementation (issue #795). Re-exported under the original private name so
# existing callers and tests (which patch server._build_sprint_subprocess_env)
# keep working unchanged.
from routers.dispatch_service import (  # noqa: E402
    build_sprint_subprocess_env as _build_sprint_subprocess_env,
)


def _sprint_plan_path(project_root: Path, sprint_label: str) -> Path:
    return _commander_dir(project_root) / "sprints" / f"{sprint_label}-plan.json"


# Unified lifecycle (docs/architecture/sprint-lifecycle.md): new writes use
# draft/planned/running/ready_to_merge/needs_rework/completed. `planning` and
# `cancelled` are legacy values kept readable for pre-redesign plan.json files
# (forward-only migration) — they are never written anew.
_VALID_PLAN_STATES: frozenset[str] = frozenset({
    "draft", "planned", "running", "ready_to_merge", "needs_rework", "completed",
    "planning", "cancelled",
})

# Plan states from which a label may never be dispatched again (sprint-lifecycle
# redesign P0, docs/milestones/sprint-lifecycle-redesign.md). One label = one
# attempt: re-dispatching a terminal label is what produced multi-attempt
# forensics under a single sprint label (sprint-68.6 ran three times).
_TERMINAL_PLAN_STATES: frozenset[str] = frozenset({
    "completed", "ready_to_merge", "needs_rework",
    "cancelled",  # legacy files only
})

# Plan states that mean "definitely not running" — everything terminal plus the
# pre-dispatch states.
_NOT_RUNNING_PLAN_STATES: frozenset[str] = _TERMINAL_PLAN_STATES | frozenset({
    "draft", "planned", "planning",
})


def _reject_terminal_label_redispatch(project_root: Path, sprint_label: str) -> None:
    """Raise 409 when the label *actually ran* and reached a terminal state.

    Re-runs must create a child sub-sprint (POST /api/sprints/{label}/rerun)
    instead of re-dispatching the same label, so every label maps to exactly
    one run in logs, agent_runs, and the History ledger.

    The durable lifecycle store is the `sprints` table (issue #757); plan.json
    is a deprecated cache. A finish/sweep path can stamp a bare
    ``{"state": "completed"}`` into plan.json for a label that never dispatched
    anything (no tickets, no DB row, no run) — trusting that phantom blocked a
    brand-new sprint from ever running. So: block only on evidence of a real
    run — a terminal row in the durable table, or a terminal plan.json that
    still carries the tickets it ran. A terminal-but-empty plan.json alone is
    not a run.
    """
    # 1. Durable lifecycle table — authoritative when a row exists.
    durable_state = None
    try:
        row = db.get_sprint(sprint_label)
        if row:
            durable_state = row.get("state")
    except Exception:
        durable_state = None  # DB best-effort; fall through to plan.json
    if durable_state in _TERMINAL_PLAN_STATES:
        _raise_terminal_redispatch(sprint_label, durable_state)

    # 2. No durable row: only the plan.json cache claims terminal. Honor it
    #    only when it has real run evidence (the tickets it dispatched). A bare
    #    {"state": "completed"} with no tickets is a phantom — let it run.
    if durable_state is None:
        plan = _read_plan_json(project_root, sprint_label) or {}
        state = plan.get("state")
        if state in _TERMINAL_PLAN_STATES and plan.get("tickets"):
            _raise_terminal_redispatch(sprint_label, state)


def _raise_terminal_redispatch(sprint_label: str, state: str) -> None:
    raise HTTPException(
        409,
        detail=(
            f"Sprint {sprint_label} already ran (state={state}). "
            "Same-label re-dispatch is disabled — use Re-run to create a "
            "child sub-sprint instead."
        ),
    )


def _read_plan_json(project_root: Path, sprint_label: str) -> Optional[dict]:
    """Read plan.json; handles old list format (returns {"tickets":[...]}) and new dict format."""
    path = _sprint_plan_path(project_root, sprint_label)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {"tickets": raw}
        if isinstance(raw, dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _sprint_rerun_into_map(project_root: Path) -> dict[str, str]:
    """Map parent sprint labels → child re-run sub-sprint labels still in play."""
    sprints_dir = _commander_dir(project_root) / "sprints"
    result: dict[str, str] = {}
    if not sprints_dir.exists():
        return result
    for state_file in sprints_dir.glob("sprint-*-state.json"):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            parent = state_file.name.replace("-state.json", "")
            sub = data.get("rerun_into")
            if sub:
                result[parent] = sub
        except (OSError, json.JSONDecodeError):
            continue
    for plan_file in sprints_dir.glob("sprint-*-plan.json"):
        label = plan_file.name[: -len("-plan.json")]
        plan = _read_plan_json(project_root, label)
        if not plan:
            continue
        parent = plan.get("parent")
        if not parent:
            continue
        result[parent] = label
    return result


def _write_plan_json(project_root: Path, sprint_label: str, data: dict) -> None:
    """Write plan.json atomically."""
    path = _sprint_plan_path(project_root, sprint_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _plan_json_set_state(
    project_root: Path,
    sprint_label: str,
    state: str,
    **extra_fields,
) -> None:
    """Update state in plan.json, creating the file if missing.

    plan.json is a deprecated cache (issue #757) — the durable lifecycle state
    now lives in the `sprints` table.  This still writes it for dual-write.
    """
    existing = _read_plan_json(project_root, sprint_label) or {}
    existing["state"] = state
    existing.update(extra_fields)
    _write_plan_json(project_root, sprint_label, existing)


# ── Sprint sign-off gate (issue #862) ────────────────────────────────────────
# A newly planned sprint enters a "pending sign-off" state that blocks
# dispatch until an explicit approval is recorded. The state is stored in
# plan.json under the `signoff` key so it survives restarts/reloads and never
# silently advances on read:
#   pending  -> {"signoff": {"state": "pending"}}
#   approved -> {"signoff": {"state": "approved", "approver": <who>,
#                            "approved_at": <iso-8601>}}
# Sprints created before this feature have no `signoff` key — they return None
# (no gate) so existing/legacy sprints are unaffected.

def _sprint_signoff_state(project_root: Path, sprint_label: str) -> Optional[str]:
    """Return 'pending', 'approved', or None for a sprint's sign-off gate."""
    plan = _read_plan_json(project_root, sprint_label)
    if not plan:
        return None
    signoff = plan.get("signoff")
    if isinstance(signoff, dict):
        st = signoff.get("state")
        if st in ("pending", "approved"):
            return st
    return None


def _sprint_signoff_set_approved(
    project_root: Path, sprint_label: str, approver: str, approved_at: str,
) -> None:
    """Record approval in plan.json and clear the pending gate.

    Also lifts the lifecycle state out of `draft` to `planned` so the sprint
    reads as ready-to-run once the gate is cleared (AC5).
    """
    existing = _read_plan_json(project_root, sprint_label) or {}
    existing["signoff"] = {
        "state": "approved",
        "approver": approver,
        "approved_at": approved_at,
    }
    if existing.get("state") in (None, "draft"):
        existing["state"] = "planned"
    _write_plan_json(project_root, sprint_label, existing)


def _sprint_signoff_cleanup_files(project_root: Path, sprint_label: str) -> None:
    """Remove a dissolved sprint's local files (plan/goal/state/json)."""
    sprints_dir = _commander_dir(project_root) / "sprints"
    for path in (
        _sprint_plan_path(project_root, sprint_label),
        _sprint_goal_path(project_root, sprint_label),
        _sprint_json_path(project_root, sprint_label),
        sprints_dir / f"{sprint_label}-state.json",
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _assert_sprint_signed_off(project_root: Path, sprint_label: str) -> None:
    """Raise 409 when a sprint is still awaiting sign-off (issue #862).

    Called from the run path so a pending sprint cannot be dispatched — the
    same gate that mutes the Run Sprint button on the board.
    """
    if _sprint_signoff_state(project_root, sprint_label) == "pending":
        raise HTTPException(
            409,
            detail=(
                f"Sprint {sprint_label} is pending sign-off — approve it on the "
                "board before running."
            ),
        )


def _sprint_db_set_state(
    sprint_label: str,
    project: str,
    state: str,
    **extra_fields,
) -> None:
    """Mirror a sprint lifecycle transition into the `sprints` table (issue #757).

    Best-effort: any DB failure is swallowed so it never breaks the request —
    plan.json dual-write remains the cache and the sweep paths reconcile drift.
    """
    try:
        if state == "running":
            db.record_sprint_start(
                sprint_label, project=project or "",
                started_at=extra_fields.get("started_at"),
            )
        elif state == "completed":
            db.record_sprint_finish(
                sprint_label,
                ended_at=extra_fields.get("ended_at"),
                end_reason=extra_fields.get("end_reason"),
            )
        elif state == "ready_to_merge":
            db.record_sprint_ready_to_merge(
                sprint_label,
                end_reason=extra_fields.get("end_reason"),
                ended_at=extra_fields.get("ended_at"),
            )
        elif state in ("needs_rework", "cancelled", "failed"):
            # cancelled/failed are legacy callers — all bad endings land in
            # needs_rework under the unified lifecycle.
            db.record_sprint_needs_rework(
                sprint_label,
                end_reason=extra_fields.get("end_reason"),
                ended_at=extra_fields.get("ended_at"),
            )
    except Exception:
        pass


def _get_sprint_pid(project_root: Path, sprint_label: str) -> Optional[int]:
    """Return PID from PID file, or None if missing/unset."""
    sprints_dir = _commander_dir(project_root) / "sprints"
    for f in (
        sprints_dir / f"{sprint_label}-pid",
        sprints_dir / f"{sprint_label}-pid.pending",
    ):
        if f.exists():
            try:
                raw = f.read_text(encoding="utf-8").strip()
                return int(raw) if raw not in ("", "0") else None
            except (ValueError, OSError):
                pass
    return None


def _load_sprint_order(project_root: Path, all_sprint_labels: list[str]) -> list[str]:
    """Load sprint order from file; fill missing/new sprint labels in natural order."""
    order_path = _sprint_order_path(project_root)
    saved: list[str] = []
    if order_path.exists():
        try:
            saved = json.loads(order_path.read_text(encoding="utf-8"))
        except Exception:
            saved = []

    all_labels = set(all_sprint_labels)
    saved_set = set(saved)

    # Start with known order, filter out sprints that no longer exist
    result = [s for s in saved if s in all_labels]
    # Append any new sprints not in saved order (natural sort)
    new_sprints = sorted(all_labels - saved_set, key=_sprint_label_sort_key)
    result.extend(new_sprints)
    return result


def _sprint_pid_alive(project_root: Path, sprint_label: str) -> bool:
    """Return True if the sprint's PID (or pending claim) is alive (issue #757).

    Scans both `{label}-pid` and `{label}-pid.pending`, treating a placeholder
    "0"/"" PID as a still-starting claim. Cleans up files holding a dead or
    unparseable PID. Pure liveness — no state interpretation.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    sprints_dir  = _commander_dir(project_root) / "sprints"
    pid_file     = sprints_dir / f"{sprint_label}-pid"
    pending_file = sprints_dir / f"{sprint_label}-pid.pending"
    for candidate in (pid_file, pending_file):
        if not candidate.exists():
            continue
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in ("", "0"):
            return True  # pending claim — still starting up
        try:
            pid = int(raw)
        except ValueError:
            try:
                candidate.unlink()
            except OSError:
                pass
            continue
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            _log.warning(
                "Stale sprint lock: %s (PID %s dead) — cleaning up",
                candidate.name, raw,
            )
            try:
                candidate.unlink()
            except OSError:
                pass
        except PermissionError:
            return True
        except OSError:
            pass
    return False


def _live_manager_pid(project_root: Path, sprint_label: str) -> Optional[int]:
    """Authoritative "is the sprint_manager process actually alive right now?".

    Returns the live PID (or 0 for a still-starting two-phase ``-pid.pending``
    claim) only when a process for THIS exact sprint label is running; None
    otherwise. When psutil is available the argv is checked (sprint_manager.py
    followed by the label) to guard against PID reuse by an unrelated process.

    This probe is independent of any persisted DB/plan state. It exists so a
    sprint whose lifecycle row was flipped to a terminal state by a reconcile
    race — e.g. a startup sweep landing mid-dispatch before the PID file was
    written — can be recovered while the manager is in fact still working
    (symptom: a running sprint shows "done"/needs_rework moments after dispatch).
    """
    sprints_dir = _commander_dir(project_root) / "sprints"
    for f in (sprints_dir / f"{sprint_label}-pid",
              sprints_dir / f"{sprint_label}-pid.pending"):
        if not f.exists():
            continue
        try:
            raw = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in ("", "0"):
            return 0  # two-phase pending claim — manager is starting up
        try:
            pid = int(raw)
        except ValueError:
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue  # dead pid — not a live manager
        except PermissionError:
            return pid  # alive, owned by another user — can't argv-check
        except OSError:
            continue
        # Alive — guard against PID reuse with an argv check when possible.
        if _psutil is not None:
            try:
                argv = _psutil.Process(pid).cmdline()
                sm_idx = next(
                    i for i, a in enumerate(argv) if "sprint_manager.py" in a
                )
                if not (len(argv) > sm_idx + 1 and argv[sm_idx + 1] == sprint_label):
                    continue  # pid reused by an unrelated process
            except StopIteration:
                continue  # alive pid but not a sprint_manager — reuse
            except Exception:
                pass  # can't introspect — trust the live pid
        return pid
    return None


def _heal_sprint_to_running(project_root: Path, sprint_label: str) -> None:
    """Re-assert a sprint's lifecycle state as running across DB + plan.json.

    Called when a live manager PID is found for a sprint whose stored state was
    wrongly flipped to a terminal value. record_sprint_start clears the stale
    ended_at/end_reason as part of the transition.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)
    row = None
    try:
        row = db.get_sprint(sprint_label)
    except Exception:
        pass
    project = (row or {}).get("project") or ""
    started_at = (row or {}).get("started_at")
    try:
        _sprint_db_set_state(sprint_label, project, "running", started_at=started_at)
    except Exception:
        pass
    try:
        _plan_json_set_state(project_root, sprint_label, "running",
                             started_at=started_at)
    except Exception:
        pass
    _log.warning(
        "Sprint %s: stored state was terminal but manager PID is alive — "
        "healed back to running", sprint_label,
    )


def _is_sprint_running(project_root: Path, sprint_label: str) -> bool:
    """Check if a sprint is running.

    The durable `sprints` table is the authoritative source (issue #757): a
    sprint is running only when DB state='running' AND its PID is alive. A
    PID-dead + DB-running row is reconciled to state='needs_rework' and reported as
    not running. Falls back to plan.json + PID-file scanning only for legacy
    sprints that have no DB row yet.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    # ── DB-authoritative path (issue #757) ───────────────────────────────────
    # Only trust a DB row that belongs to THIS project. The sprints table is keyed
    # by label (globally unique, mirroring the Neon uq_sprints_label constraint),
    # but project_root scopes this call — guard against a same-label row from a
    # different project (or a stale row in a shared DB) hijacking the decision.
    try:
        _db_row = db.get_sprint(sprint_label)
    except Exception:
        _db_row = None
    if _db_row is not None:
        _row_proj = str(_db_row.get("project") or "")
        _row_slug = _row_proj.split("/")[-1] if "/" in _row_proj else _row_proj
        # Trust the row only when its project slug matches this project_root.
        # An empty/missing project (legacy CLI write) is not trusted for a named
        # project — fall back to plan.json + PID for those.
        if _row_slug != project_root.name:
            _db_row = None
    if _db_row is not None:
        if _db_row.get("state") != "running":
            # Recovery: a reconcile race can flip a still-running sprint to a
            # terminal state (e.g. a startup sweep landing mid-dispatch before
            # the PID file is written). If the manager is provably alive, the
            # stored state is wrong — heal it back to running instead of
            # reporting the sprint dead forever.
            if _live_manager_pid(project_root, sprint_label) is not None:
                _heal_sprint_to_running(project_root, sprint_label)
                return True
            return False
        if _sprint_pid_alive(project_root, sprint_label):
            return True
        # DB=running but PID dead — reconcile both stores to needs_rework.
        _log.warning(
            "Sprint %s: DB state=running but no alive PID — reconciling to needs_rework",
            sprint_label,
        )
        try:
            db.record_sprint_needs_rework(sprint_label, end_reason="process lost")
        except Exception:
            pass
        try:
            _plan_json_set_state(project_root, sprint_label, "needs_rework",
                                 end_reason="process lost")
        except Exception:
            pass
        return False

    plan = _read_plan_json(project_root, sprint_label)
    if plan is not None:
        plan_state = plan.get("state")
        if plan_state in _NOT_RUNNING_PLAN_STATES:
            # Same recovery as the DB branch for legacy (no-DB-row) sprints: a
            # live manager overrides a stale terminal plan.json.
            if _live_manager_pid(project_root, sprint_label) is not None:
                _heal_sprint_to_running(project_root, sprint_label)
                return True
            return False
        if plan_state == "running":
            sprints_dir = _commander_dir(project_root) / "sprints"
            pid_file     = sprints_dir / f"{sprint_label}-pid"
            pending_file = sprints_dir / f"{sprint_label}-pid.pending"
            for candidate in (pid_file, pending_file):
                if not candidate.exists():
                    continue
                raw = ""
                try:
                    raw = candidate.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if raw in ("", "0"):
                    return True  # pending claim — still starting up
                try:
                    pid = int(raw)
                except ValueError:
                    try:
                        candidate.unlink()
                    except OSError:
                        pass
                    continue
                try:
                    os.kill(pid, 0)
                    return True
                except ProcessLookupError:
                    _log.warning(
                        "Stale sprint lock: %s (PID %s dead) — cleaning up",
                        candidate.name,
                        raw,
                    )
                    try:
                        candidate.unlink()
                    except OSError:
                        pass
                except PermissionError:
                    return True
                except OSError:
                    pass
            # plan.json=running but no alive PID — reconcile to needs_rework
            _log.warning(
                "Sprint %s: plan.json=running but no alive PID — reconciling to needs_rework",
                sprint_label,
            )
            try:
                _plan_json_set_state(project_root, sprint_label, "needs_rework",
                                     end_reason="process lost")
            except Exception:
                pass
            return False
        # Unknown state value — fall through to PID check below

    # ── Legacy path: no plan.json (or unknown state) — scan PID files ────────
    sprints_dir  = _commander_dir(project_root) / "sprints"
    pid_file     = sprints_dir / f"{sprint_label}-pid"
    pending_file = sprints_dir / f"{sprint_label}-pid.pending"

    pid_alive = False
    for candidate in (pid_file, pending_file):
        if not candidate.exists():
            continue
        raw = ""
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw in ("", "0"):
            pid_alive = True
            break
        try:
            pid = int(raw)
        except ValueError:
            try:
                candidate.unlink()
            except OSError:
                pass
            continue
        try:
            os.kill(pid, 0)
            pid_alive = True
            break
        except ProcessLookupError:
            _log.warning(
                "Stale sprint lock: %s (PID %s dead) — cleaning up",
                candidate.name,
                raw,
            )
            try:
                candidate.unlink()
            except OSError:
                pass
        except PermissionError:
            pid_alive = True
            break
        except OSError:
            pass

    if plan is None:
        # Lazy migration: create plan.json for this legacy sprint
        try:
            if pid_alive:
                _plan_json_set_state(project_root, sprint_label, "running",
                                     started_at=datetime.now(timezone.utc).isoformat())
                _log.info("[plan-migrate] %s: created plan.json state=running (legacy PID)", sprint_label)
            else:
                _plan_json_set_state(project_root, sprint_label, "completed")
                _log.info("[plan-migrate] %s: created plan.json state=completed (no PID, historical)", sprint_label)
        except Exception:
            pass

    return pid_alive


def _all_sprints_running() -> list[dict]:
    """Scan all projects for running sprints.

    Primary: reads plan.json state=running (authoritative).
    Fallback: checks PID files for legacy sprints with no plan.json yet.
    PID files whose process is dead are reconciled to state=needs_rework as a side-effect.

    Returns list of {project, sprint_label, pid}.
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    result: list[dict] = []
    projects = projects_module.load_projects()
    for proj in projects:
        root = _project_root_path(proj["repo"])
        sprints_dir = _commander_dir(root) / "sprints"
        if not sprints_dir.exists():
            continue
        seen: set[str] = set()

        # Primary: plan.json files with state=running
        for plan_file in sprints_dir.glob("*-plan.json"):
            label = plan_file.name.removesuffix("-plan.json")
            seen.add(label)
            try:
                data = json.loads(plan_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or data.get("state") != "running":
                # Recovery: a stale terminal plan.json with a provably-alive
                # manager is still running — a reconcile race flipped it. Heal
                # and report it, so the running pane doesn't drop a live sprint.
                if isinstance(data, dict):
                    _recover_pid = _live_manager_pid(root, label)
                    if _recover_pid is not None:
                        _heal_sprint_to_running(root, label)
                        result.append({
                            "project": proj["repo"], "sprint_label": label,
                            "pid": _recover_pid or None,
                        })
                continue
            # Verify PID alive; reconcile dead ones to cancelled
            pid = _get_sprint_pid(root, label)
            pid_alive = False
            if pid is not None:
                try:
                    os.kill(pid, 0)
                    pid_alive = True
                except ProcessLookupError:
                    pass
                except PermissionError:
                    pid_alive = True
            else:
                # No PID yet (pending claim with "0") — treat as running
                pid_pending = sprints_dir / f"{label}-pid.pending"
                pid_file = sprints_dir / f"{label}-pid"
                for f in (pid_file, pid_pending):
                    if f.exists():
                        try:
                            raw = f.read_text(encoding="utf-8").strip()
                            if raw in ("", "0"):
                                pid_alive = True
                        except OSError:
                            pass

            if pid_alive:
                result.append({"project": proj["repo"], "sprint_label": label, "pid": pid})
            else:
                # plan.json=running but no alive PID — reconcile
                _log.warning(
                    "Sprint %s: plan.json=running but PID dead — reconciling to needs_rework",
                    label,
                )
                try:
                    data["state"] = "needs_rework"
                    data["end_reason"] = "process lost"
                    tmp = plan_file.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    os.replace(str(tmp), str(plan_file))
                except Exception:
                    pass

        # Fallback: PID files for sprints with no plan.json (legacy running sprints)
        for pid_file in list(sprints_dir.glob("*-pid")) + list(sprints_dir.glob("*-pid.pending")):
            label = pid_file.name.removesuffix("-pid.pending").removesuffix("-pid")
            if label in seen:
                continue
            seen.add(label)
            if _is_sprint_running(root, label):
                pid = _get_sprint_pid(root, label)
                result.append({"project": proj["repo"], "sprint_label": label, "pid": pid})

    return result


def _any_sprint_running(project: str | None = None) -> Optional[dict]:
    """Scan for a running sprint. If project given, scoped to that project only."""
    running = _all_sprints_running()
    if project:
        running = [r for r in running if r["project"] == project]
    return running[0] if running else None


class SprintMgmtRunBody(BaseModel):
    project: str
    sprint_label: str
    migrate_from: list[int] = []


class SprintCreateBody(BaseModel):
    project: str
    sprint_number: int | None = None
    goal: str | None = None
    # Optional tickets to apply the new sprint label to as part of creation (#857).
    tickets: list[int] | None = None


@app.get("/api/sprint-management/issues")
def get_sprint_management_issues(repo: str):
    """Return all open issues + sprint list + display order for a project.

    Also returns:
    - empty_sprint_labels: sprint labels that have 0 open tickets (stale/ghost sprints)
    - placeholder_sprint: the next sprint number to show as a drop target (max+1)

    Issues include a sprint_label field (e.g. "sprint-15" or "sprint-15.1") that
    identifies their exact sprint label, including dotted sub-labels.
    """
    try:
        issues = github_client.list_open_issues_with_body(repo_name=repo, limit=200)
        sprints = github_client.list_sprints(repo_name=repo)
        all_sprint_labels = github_client.list_sprint_labels(repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

    sprint_label_re = re.compile(r"^sprint-(\d+(?:\.\d+)*)$")
    result_issues = []
    # Count open tickets per sprint label (both plain and dotted)
    sprint_ticket_counts: dict[str, int] = {lbl: 0 for lbl in all_sprint_labels}

    # Resolve estimates dir once for stale-hash checks (issue #453)
    try:
        _est_project_root = _project_root_path(repo)
        _estimates_dir = _commander_dir(_est_project_root) / "estimates"
    except Exception:
        _estimates_dir = None

    for iss in issues:
        _is_summary = (
            any(lbl["name"] == "sprint-summary" for lbl in iss.get("labels", []))
            or bool(_SUMMARY_TITLE_RE.match(iss.get("title", "") or ""))
        )
        if _is_summary:
            continue  # hide sprint-summary issues from pane (AC P1-3)
        sprint_num = None
        found_sprint_label = None
        for lbl in iss.get("labels", []):
            m = sprint_label_re.match(lbl["name"])
            if m:
                found_sprint_label = lbl["name"]
                sprint_num = int(m.group(1).split(".")[0])
                break

        iss_body = iss.get("body", "") or ""
        estimate_stale = _check_estimate_stale(iss["number"], iss_body, _estimates_dir)

        result_issues.append({
            "number": iss["number"],
            "title": iss["title"],
            "body": iss_body,
            "labels": iss.get("labels", []),
            "sprint": sprint_num,
            "sprint_label": found_sprint_label,
            "status": github_client.classify_issue(iss),
            "url": iss.get("url", ""), "created_at": iss.get("createdAt", "") or iss.get("created_at", ""),
            "estimate_stale": estimate_stale, "milestone": github_client.milestone_view(iss),
        })
        if found_sprint_label is not None and found_sprint_label in sprint_ticket_counts:
            sprint_ticket_counts[found_sprint_label] += 1

    # For "empty sprint" cleanup: only care about plain sprint-N labels
    plain_sprint_counts = {
        n: sprint_ticket_counts.get(f"sprint-{n}", 0) for n in sprints
    }
    active_sprint_nums = [n for n, count in plain_sprint_counts.items() if count > 0]
    # Also consider sub-labels when determining if a base sprint is active
    for lbl in all_sprint_labels:
        m = sprint_label_re.match(lbl)
        if m and "." in m.group(1) and sprint_ticket_counts.get(lbl, 0) > 0:
            base = int(m.group(1).split(".")[0])
            if base not in active_sprint_nums:
                active_sprint_nums.append(base)
    min_active_sprint = min(active_sprint_nums) if active_sprint_nums else None

    empty_sprint_labels = [
        f"sprint-{n}" for n in sorted(plain_sprint_counts.keys())
        if plain_sprint_counts[n] == 0
        and min_active_sprint is not None
        and n < min_active_sprint
    ]

    # Finished sprints = those with a posted "Sprint N Executive Summary" issue.
    finished_map = _finished_sprint_summaries(repo)
    finished_set = set(finished_map.keys())

    # Sprint labels to render as panes: any with tickets, PLUS empty labels that
    # are NOT finished — so a freshly-created sprint (0 tickets, no summary) still
    # shows as a drop target. Finished sprints whose tickets are all closed are
    # not resurrected as empty planning panes.
    renderable_sprint_labels = [
        lbl for lbl in all_sprint_labels
        if sprint_ticket_counts.get(lbl, 0) > 0 or lbl not in finished_set
    ]
    project_root = _project_root_path(repo)
    order = _load_sprint_order(project_root, renderable_sprint_labels)

    # Apply per-sprint plan.json ordering; fallback to ascending issue number (issue #441)
    sprint_issues_map: dict[str, list] = {}
    unassigned_issues = []
    for iss in result_issues:
        lbl = iss.get("sprint_label")
        if lbl:
            sprint_issues_map.setdefault(lbl, []).append(iss)
        else:
            unassigned_issues.append(iss)
    ordered_result: list = []
    sprint_parents: dict[str, Optional[str]] = {}
    sprint_plan_states: dict[str, str] = {}
    for lbl, iss_list in sprint_issues_map.items():
        plan_data = _read_plan_json(project_root, lbl)
        if plan_data is not None:
            sprint_parents[lbl] = plan_data.get("parent")
            if isinstance(plan_data, dict) and plan_data.get("state"):
                sprint_plan_states[lbl] = plan_data["state"]
            try:
                raw_tickets = plan_data.get("tickets", plan_data) if isinstance(plan_data, dict) else plan_data
                plan_order: list[int] = raw_tickets if isinstance(raw_tickets, list) else []
                plan_idx = {n: i for i, n in enumerate(plan_order)}
                iss_list.sort(key=lambda i: plan_idx.get(i["number"], len(plan_order)))
            except Exception:
                iss_list.sort(key=lambda i: i["number"])
        else:
            sprint_parents[lbl] = None
            iss_list.sort(key=lambda i: i["number"])
        ordered_result.extend(iss_list)
    ordered_result.extend(unassigned_issues)
    result_issues = ordered_result

    # Finished sprints (computed above) are surfaced so the board marks them
    # finished and stops showing NEXT UP / pre-flight — same GitHub-backed signal
    # the nav pill uses (cross-machine).
    finished_sprints = sorted(finished_set)

    # Placeholder/next sprint = max existing + 1 (not the lowest free number — a
    # deleted early label must not reset the next sprint back to 1). The max is
    # taken over both sprint labels AND finished-summary numbers, so it stays
    # correct even if a finished sprint's label was later removed.
    _max_num = 0
    for n in sprints:
        try:
            _max_num = max(_max_num, int(n))
        except (TypeError, ValueError):
            pass
    for lbl in finished_map:
        m = sprint_label_re.match(lbl)
        if m:
            _max_num = max(_max_num, int(m.group(1).split(".")[0]))
    placeholder_sprint = _max_num + 1

    sprint_rerun_into = _sprint_rerun_into_map(project_root)

    # Sign-off gate state per renderable label (issue #862) — drives the
    # PENDING SIGN-OFF badge and the muted Run Sprint button on the board.
    # Read over all renderable labels (not just those with tickets) so a fresh
    # 0-ticket sprint still reports its pending gate.
    sprint_signoff: dict[str, str] = {}
    for lbl in renderable_sprint_labels:
        st = _sprint_signoff_state(project_root, lbl)
        if st is not None:
            sprint_signoff[lbl] = st

    return {
        "sprints": sprints,
        "order": order,
        "issues": result_issues,
        "empty_sprint_labels": empty_sprint_labels,
        "placeholder_sprint": placeholder_sprint,
        "sprint_parents": sprint_parents,
        "sprint_plan_states": sprint_plan_states,
        "finished_sprints": finished_sprints,
        "sprint_rerun_into": sprint_rerun_into,
        "sprint_signoff": sprint_signoff,
    }


@app.get("/api/sprints/timeline")
def get_sprint_timeline(project: str):
    """Return Gantt-ready timeline data for all ran sprints in a project (issue #431).

    Reads sprint-N-state.json files from the commander directory.
    Each entry includes: sprint_label, display_name, state, start_date, end_date, ticket_count.

    State values: "running" | "cancelled" | "completed"
    """
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"

    if not sprints_dir.exists():
        return {"sprints": []}

    def _parse_iso_ts(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    def _to_iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = []
    state_label_re = re.compile(r"^sprint-(\d+(?:\.\d+)?)-state\.json$")

    for state_file in sprints_dir.glob("sprint-*-state.json"):
        m = state_label_re.match(state_file.name)
        if not m:
            continue
        sprint_num_str = m.group(1)
        sprint_label = f"sprint-{sprint_num_str}"

        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        start_ts_str = state_data.get("start_timestamp")
        start_ts = _parse_iso_ts(start_ts_str)
        if start_ts is None:
            continue

        wall_clock = state_data.get("wall_clock_secs", 0.0) or 0.0
        issues = state_data.get("issues", [])
        ticket_count = len(issues)

        is_running = _is_sprint_running(project_root, sprint_label)

        if is_running:
            state = "running"
            end_ts = datetime.now(timezone.utc).timestamp()
        else:
            # Check cancelled flag from sprint JSON
            json_path = _sprint_json_path(project_root, sprint_label)
            sprint_json = _sprint_json_read(json_path)
            if sprint_json.get("status") in ("cancelled", "needs_rework"):
                state = "cancelled"
            else:
                state = "completed"
            end_ts = start_ts + wall_clock if wall_clock > 0 else start_ts

        results.append({
            "label": sprint_label,
            "display_name": f"Sprint {sprint_num_str}",
            "state": state,
            "start_date": _to_iso(start_ts),
            "end_date": _to_iso(end_ts),
            "ticket_count": ticket_count,
        })

    # Sort chronologically by start_date
    results.sort(key=lambda s: s["start_date"])

    return {"sprints": results}


@app.get("/api/sprints/summaries")
def get_sprint_summaries(project: str):
    """Return all sprint-summary issues for a project (open + optionally closed).

    Query params:
      project=<owner/repo>
      state=open|all  (default: open)

    Response shape:
      { "summaries": [ { number, title, sprint_number, sprint_sub_label, state,
                          outcome, url, created_at, summary_file_path } ] }
    """
    try:
        repo = github_client.get_repo_for_operation(project)
    except Exception as e:
        raise HTTPException(400, detail=str(e))

    sprint_label_re = re.compile(r"^sprint-(\d+)(?:\.(\d+))?$")

    try:
        result = subprocess.run(
            [
                "gh", "issue", "list", "--repo", repo,
                "--label", "sprint-summary",
                "--state", "open",
                "--json", "number,title,labels,state,url,createdAt",
                "--limit", "200",
            ],
            capture_output=True, text=True, timeout=15,
        )
        open_issues: list[dict] = json.loads(result.stdout or "[]") if result.returncode == 0 else []
    except Exception:
        open_issues = []

    # Title-regex fallback: fetch all open issues and find legacy summaries without the label
    try:
        all_open = github_client.list_open_issues_with_body(repo_name=project, limit=200)
        seen_nums = {i["number"] for i in open_issues}
        for iss in all_open:
            if iss["number"] in seen_nums:
                continue
            if _SUMMARY_TITLE_RE.match(iss.get("title", "") or ""):
                open_issues.append(iss)
    except Exception:
        pass

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"

    def _build_summary(iss: dict, is_closed: bool) -> dict:
        num = iss["number"]
        title = iss.get("title", "")

        # Determine sprint number and sub-label from labels
        sprint_number: Optional[int] = None
        sprint_sub_label: Optional[str] = None
        for lbl in iss.get("labels", []):
            m = sprint_label_re.match(lbl["name"] if isinstance(lbl, dict) else lbl)
            if m:
                sprint_number = int(m.group(1))
                sprint_sub_label = m.group(2)
                break

        # Fallback: parse sprint number from title ("Sprint 21 Executive Summary")
        if sprint_number is None:
            tm = re.match(r"^Sprint (\d+)(?:\.(\d+))?\s+Executive Summary$", title)
            if tm:
                sprint_number = int(tm.group(1))
                sprint_sub_label = tm.group(2)

        # Compute outcome using same logic as finish-card endpoint
        outcome = "completed"
        if sprint_number is not None:
            sprint_n = str(sprint_number)
            if sprint_sub_label:
                sprint_label = f"sprint-{sprint_number}.{sprint_sub_label}"
            else:
                sprint_label = f"sprint-{sprint_number}"

            # Check cancelled state from sprint json
            fc_json_path = _sprint_json_path(project_root, sprint_label)
            fc_sprint_json = _sprint_json_read(fc_json_path)
            is_cancelled = fc_sprint_json.get("status") in ("cancelled", "needs_rework")

            # Check summary file for status
            fc_sprint_status: Optional[str] = None
            if sprints_dir.exists():
                for sf in sorted(sprints_dir.glob(f"sprint-{sprint_n}-summary-*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
                    try:
                        meta = _parse_summary_file(sf)
                        raw = (meta.get("status") or "").lower()
                        if raw in ("complete", "completed"):
                            fc_sprint_status = "completed"
                        elif raw in ("stopped", "failed", "cancelled"):
                            fc_sprint_status = "stopped"
                            if raw == "cancelled":
                                is_cancelled = True
                    except Exception:
                        pass
                    break

            if is_cancelled:
                outcome = "cancelled"
            elif _has_rework_tickets(sprint_label, project):
                outcome = "has_rework"
            else:
                outcome = "completed"

        # Find summary file path
        summary_file_path: Optional[str] = None
        if sprint_number is not None and sprints_dir.exists():
            sprint_n = str(sprint_number)
            cands = sorted(sprints_dir.glob(f"sprint-{sprint_n}-summary-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                summary_file_path = f".commander/sprints/{cands[0].name}"

        return {
            "number": num,
            "title": title,
            "sprint_number": sprint_number,
            "sprint_sub_label": sprint_sub_label,
            "state": "closed" if is_closed else "open",
            "outcome": outcome,
            "url": iss.get("url", iss.get("html_url", "")),
            "created_at": iss.get("createdAt", iss.get("created_at", "")),
            "summary_file_path": summary_file_path,
        }

    summaries = [_build_summary(iss, False) for iss in open_issues]

    # Sort: newest sprint number first; for sub-labels, higher sub sorts before base
    def _sort_key(s: dict):
        n = s["sprint_number"] or 0
        sub = int(s["sprint_sub_label"]) if s["sprint_sub_label"] else 0
        return (-n, -sub)

    summaries.sort(key=_sort_key)

    return {"summaries": summaries}


# READ-only: recognises both spellings so migration removes whichever is present
_MIGRATION_STATUS_LABELS = {"UAT", "UAT-approved", "SIT", "in-progress", "needs-rework", "need-rework"}


# ── Sprint-issues helpers ─────────────────────────────────────────────────────

def _get_sprint_issues(project: str, sprint_label: str) -> list[dict]:
    """Fetch open GitHub issues and filter to those carrying sprint_label."""
    issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    return [iss for iss in issues if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))]


# ── Estimate-summary helpers (issue #211) ────────────────────────────────────

def _size_to_minutes(size: str) -> int:
    """Map a T-shirt size label to agent-effort minutes via SIZE_TO_MINUTES."""
    return _SIZE_TO_MINUTES.get(size, 0)


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
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

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


_SIZE_LABELS = {"size-S", "size-M", "size-L", "size-XL"}
_SIZE_LETTER_BY_LABEL = {"size-S": "S", "size-M": "M", "size-L": "L", "size-XL": "XL"}
_PF_NON_WORK = {"sprint-summary", "docs", "documentation"}


def _size_from_github_labels(label_names: set[str]) -> str | None:
    """Return S/M/L/XL from GitHub size-* labels, or None."""
    for lbl in _SIZE_LABELS:
        if lbl in label_names:
            return _SIZE_LETTER_BY_LABEL[lbl]
    return None


def _load_issue_estimate_json(estimates_dir: Path, issue_num: int) -> dict | None:
    est_path = estimates_dir / f"issue-{issue_num}.json"
    if not est_path.exists():
        return None
    try:
        return json.loads(est_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _resolve_issue_estimate(iss: dict, estimates_dir: Path) -> dict:
    """Single source of truth: merge local estimate JSON + GitHub size-* labels.

    Returns ``{size, files, estimated, source}`` where ``source`` is
    ``'json'``, ``'label'``, or ``None``. Either JSON or a GitHub label counts
    as estimated; ``files`` always come from JSON when present.
    """
    label_names = {lbl["name"] for lbl in iss.get("labels", [])}
    est = _load_issue_estimate_json(estimates_dir, iss["number"])
    files: list[str] = []
    size: str | None = None
    source: str | None = None
    if est:
        files = list(est.get("files_likely_affected") or [])
        raw_size = est.get("size")
        if raw_size:
            size = str(raw_size)
            source = "json"
    if not size:
        label_size = _size_from_github_labels(label_names)
        if label_size:
            size = label_size
            source = "label"
    return {
        "size": size,
        "files": files,
        "estimated": size is not None,
        "source": source,
    }


def _issue_has_estimate(iss: dict, estimates_dir: Path) -> bool:
    return _resolve_issue_estimate(iss, estimates_dir)["estimated"]


@app.post("/api/sprints/{sprint_label}/preflight-fix")
async def preflight_fix(sprint_label: str, project: str):
    """Fix auto-fixable pre-flight issues for a sprint, streaming progress as SSE.

    For each work ticket in the sprint that is missing acceptance criteria or a
    size estimate: generate AC (append-only, idempotent) and/or run the estimator
    to apply a size label. Streams `log` events (the current action) and a final
    `done` event with counts. Conflicts (file-overlap / dep-order) are not
    auto-fixable and are left untouched.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    repo = github_client.get_repo_for_operation(project)
    try:
        issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    project_root = _project_root_path(project)
    estimates_dir = _commander_dir(project_root) / "estimates"

    work: list[dict] = []
    for iss in issues:
        labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if labels & _PF_NON_WORK:
            continue
        needs_ac = not _fill_ac.has_acceptance_criteria(iss.get("body") or "")
        needs_size = not _issue_has_estimate(iss, estimates_dir)
        if needs_ac or needs_size:
            work.append({"num": iss["number"], "needs_ac": needs_ac, "needs_size": needs_size})

    def _sse(event: str, payload) -> str:
        return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

    async def _stream():
        total = len(work)
        if total == 0:
            yield _sse("log", "No auto-fixable pre-flight issues found.")
            yield _sse("done", {"filled": 0, "estimated": 0, "skipped": 0, "errors": [], "total": 0})
            return

        filled = estimated = skipped = 0
        errors: list[str] = []
        yield _sse("log", f"Fixing {total} pre-flight ticket(s)…")

        for idx, item in enumerate(work, start=1):
            num = item["num"]
            if item["needs_ac"]:
                yield _sse("log", f"Generating acceptance criteria for #{num} ({idx}/{total})…")
                try:
                    status, err = await asyncio.to_thread(_fill_ac.fill_issue, num, repo, False)
                    if status == "filled":
                        filled += 1
                    elif status == "skipped":
                        skipped += 1
                    else:
                        errors.append(f"#{num} AC: {err or 'failed'}")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"#{num} AC: {e}")

            if item["needs_size"]:
                yield _sse("log", f"Estimating #{num} ({idx}/{total})…")
                try:
                    ok = await asyncio.to_thread(_preflight_estimate_one, num, repo)
                    if ok:
                        estimated += 1
                    else:
                        errors.append(f"#{num} estimate: failed")
                except Exception as e:  # noqa: BLE001
                    errors.append(f"#{num} estimate: {e}")

        github_client.invalidate("open_issues_body:")
        github_client.invalidate("open_issues:")
        summary = {"filled": filled, "estimated": estimated, "skipped": skipped,
                   "errors": errors, "total": total}
        yield _sse("log", f"Done — {filled} AC added, {estimated} estimated"
                          + (f", {len(errors)} error(s)" if errors else "") + ".")
        yield _sse("done", summary)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _preflight_estimate_one(issue_num: int, repo: str) -> bool:
    """Estimate one issue and apply its size label. Returns True on success.

    Mirrors POST /api/issues/{id}/estimate but as a blocking helper for the
    preflight-fix executor thread.
    """
    issue_data = _ei_fetch_issue(issue_num, repo)
    estimate, _err = _ei_run_estimator(issue_num, issue_data)
    if not estimate or not estimate.get("size"):
        return False
    _ei_apply_label(issue_num, repo, estimate["size"])
    _ei_apply_estimated_status(issue_num, repo)
    return True


def _sprint_dag_tickets(project_root: Path, sprint_issues: list[dict]) -> list[dict]:
    """Build the ticket list for build_dag from sprint issues + their estimate files."""
    estimates_dir = _commander_dir(project_root) / "estimates"
    tickets = []
    for iss in sprint_issues:
        num = iss["number"]
        estimate_path = estimates_dir / f"issue-{num}.json"
        files_touched: list[str] = []
        if estimate_path.exists():
            try:
                est = json.loads(estimate_path.read_text(encoding="utf-8"))
                files_touched = est.get("files_likely_affected") or []
            except (json.JSONDecodeError, OSError):
                pass
        tickets.append({"id": f"#{num}", "files_touched": files_touched})
    return tickets


@app.get("/api/sprints/{sprint_label}/cycle-check")
def get_sprint_cycle_check(sprint_label: str, project: str):
    """Run DAG cycle detection for a sprint before dispatch.

    Returns {"has_cycle": false} when acyclic.
    Returns {"has_cycle": true, "error": "cycle_detected", "cycles": [...]} when cycle(s) found.
    Returns {"has_cycle": false, "warning": "dag_builder_unavailable"} if dag_builder not loaded.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    if not _DAG_BUILDER_AVAILABLE:
        return {"has_cycle": False, "warning": "dag_builder_unavailable"}

    try:
        issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_issues = [
        iss for iss in issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    project_root = _project_root_path(project)
    tickets = _sprint_dag_tickets(project_root, sprint_issues)
    result = _build_dag(tickets)

    if isinstance(result, _CycleError):
        payload = result.to_payload()
        return {"has_cycle": True, **payload}

    return {"has_cycle": False}


@app.get("/api/sprints/{sprint_label}/conflicts")
def get_sprint_conflicts(sprint_label: str, project: str):
    """Return all pairs of pending tickets in a sprint that share at least one file path.

    Pending = issues with no in-progress/sit/uat/done label (i.e. backlog status).
    File paths are sourced from .commander/estimates/issue-<N>.json files_likely_affected.

    Returns {"conflicts": [...], "pending_count": N} on success.
    Returns 404 when no issues with sprint_label exist.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        all_issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_issues = [
        iss for iss in all_issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    if not sprint_issues:
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    pending_issues = [
        iss for iss in sprint_issues
        if github_client.classify_issue(iss) == "backlog"
    ]

    project_root = _project_root_path(project)
    estimates_dir = _commander_dir(project_root) / "estimates"

    ticket_files: list[dict] = []
    for iss in pending_issues:
        num = iss["number"]
        files: list[str] = []
        est_path = estimates_dir / f"issue-{num}.json"
        if est_path.exists():
            try:
                est = json.loads(est_path.read_text(encoding="utf-8"))
                files = est.get("files_likely_affected") or []
            except (json.JSONDecodeError, OSError):
                pass
        ticket_files.append({"id": num, "title": iss["title"], "files": set(files)})

    conflicts = []
    for i in range(len(ticket_files)):
        for j in range(i + 1, len(ticket_files)):
            a, b = ticket_files[i], ticket_files[j]
            shared = sorted(a["files"] & b["files"])
            if shared:
                conflicts.append({
                    "ticket1_id": a["id"],
                    "ticket1_title": a["title"],
                    "ticket2_id": b["id"],
                    "ticket2_title": b["title"],
                    "shared_files": shared,
                })

    return {"conflicts": conflicts, "pending_count": len(pending_issues)}


@app.get("/api/sprints/{sprint_label}/dep-order")
def get_sprint_dep_order(sprint_label: str, project: str):
    """Return dependency order hints derived from file-overlap DAG for pending tickets.

    For each ticket with at least one DAG edge, returns upstream (should run after)
    and downstream (should run before) lists.  If the DAG contains cycles, returns
    has_cycle=True with in_cycle_tickets so the frontend can render the warning.

    Returns {"has_cycle": bool, "dep_hints": {...}, "pending_count": N}.
    Returns 404 when no issues with sprint_label exist.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        all_issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_issues = [
        iss for iss in all_issues
        if any(lbl["name"] == sprint_label for lbl in iss.get("labels", []))
    ]

    if not sprint_issues:
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    pending_issues = [
        iss for iss in sprint_issues
        if github_client.classify_issue(iss) == "backlog"
    ]

    project_root = _project_root_path(project)
    estimates_dir = _commander_dir(project_root) / "estimates"

    ticket_files: list[dict] = []
    for iss in pending_issues:
        num = iss["number"]
        files: list[str] = []
        est_path = estimates_dir / f"issue-{num}.json"
        if est_path.exists():
            try:
                est = json.loads(est_path.read_text(encoding="utf-8"))
                files = est.get("files_likely_affected") or []
            except (json.JSONDecodeError, OSError):
                pass
        ticket_files.append({"id": num, "title": iss["title"], "files": files})

    if _build_dag is None:
        return {"has_cycle": False, "cycles": [], "in_cycle_tickets": [], "dep_hints": {}, "pending_count": len(pending_issues), "warning": "dag_builder_unavailable"}

    dag_tickets = [{"id": str(tf["id"]), "files_touched": tf["files"]} for tf in ticket_files]
    title_map = {str(tf["id"]): tf["title"] for tf in ticket_files}

    result = _build_dag(dag_tickets)

    if isinstance(result, _CycleError):
        in_cycle_ids = [int(tid) for cycle in result.cycles for tid in cycle]
        return {
            "has_cycle": True,
            "cycles": result.cycles,
            "in_cycle_tickets": sorted(set(in_cycle_ids)),
            "dep_hints": {},
            "pending_count": len(pending_issues),
        }

    # Build per-ticket upstream / downstream from DAG edges.
    upstream: dict[str, list[dict]] = {str(tf["id"]): [] for tf in ticket_files}
    downstream: dict[str, list[dict]] = {str(tf["id"]): [] for tf in ticket_files}
    for src, dst in result.edges:
        downstream[src].append({"id": int(dst), "title": title_map.get(dst, "")})
        upstream[dst].append({"id": int(src), "title": title_map.get(src, "")})

    dep_hints: dict[int, dict] = {}
    for tf in ticket_files:
        tid = str(tf["id"])
        u = upstream[tid]
        d = downstream[tid]
        if u or d:
            dep_hints[tf["id"]] = {"upstream": u, "downstream": d}

    return {
        "has_cycle": False,
        "cycles": [],
        "in_cycle_tickets": [],
        "dep_hints": dep_hints,
        "pending_count": len(pending_issues),
    }


def _check_estimate_stale(issue_num: int, current_body: str, estimates_dir) -> bool:
    """Return True if the stored estimate is stale (body changed or hash missing).

    Returns False when no estimate exists (no badge needed) or when the body
    hash matches the stored value.  Returns True when an estimate exists but
    lacks a body_hash field, or when the hash differs from the current body.
    """
    if estimates_dir is None:
        return False
    est_path = estimates_dir / f"issue-{issue_num}.json"
    if not est_path.exists():
        return False
    try:
        est = json.loads(est_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    stored_hash = est.get("body_hash")
    if not stored_hash:
        return True  # missing hash → treat as stale (AC: existing records without body_hash)
    current_hash = hashlib.sha256(current_body.encode()).hexdigest()
    return current_hash != stored_hash


_STALE_ESTIMATE_DAYS = 7


@app.get("/api/sprints/{sprint_label}/preflight")
def get_sprint_preflight(sprint_label: str, project: str):
    """Preflight check returned before running a sprint.

    Returns DAG visualization data (layers, edges, ticket metadata) alongside ok flag,
    warnings (unestimated, stale_estimates, missing_ac), and cycle path if detected.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    dag_payload: dict | None = None
    warnings: dict = {"unestimated": [], "stale_estimates": [], "missing_ac": []}
    cycle_path: list[str] | None = None
    sprint_issues: list[dict] = []

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
        if sprint_issues:
            project_root = _project_root_path(project)
            estimates_dir = _commander_dir(project_root) / "estimates"
            stale_cutoff = datetime.now(timezone.utc) - timedelta(days=_STALE_ESTIMATE_DAYS)

            ticket_map: dict[str, dict] = {}
            for iss in sprint_issues:
                num = iss["number"]
                label_names = {lbl["name"] for lbl in iss.get("labels", [])}
                if "blocked" in label_names:
                    state = "blocked"
                elif "UAT" in label_names:
                    state = "UAT"
                elif "SIT" in label_names:
                    state = "SIT"
                elif "in-progress" in label_names:
                    state = "in-progress"
                else:
                    state = "backlog"

                size: str | None = None
                files_touched: list[str] = []
                est_stale = False
                resolved = _resolve_issue_estimate(iss, estimates_dir)
                size = resolved["size"]
                files_touched = resolved["files"]
                est_path = estimates_dir / f"issue-{num}.json"
                if est_path.exists():
                    try:
                        mtime = datetime.fromtimestamp(est_path.stat().st_mtime, tz=timezone.utc)
                        est_stale = mtime < stale_cutoff
                    except OSError:
                        pass

                body = iss.get("body") or ""
                has_ac = "## Acceptance Criteria" in body

                tid = f"#{num}"
                ticket_map[tid] = {
                    "id": tid,
                    "number": num,
                    "title": iss.get("title", ""),
                    "state": state,
                    "size": size,
                    "files_touched": files_touched,
                }

                if not resolved["estimated"]:
                    warnings["unestimated"].append(tid)
                if est_stale:
                    warnings["stale_estimates"].append(tid)
                if not has_ac:
                    warnings["missing_ac"].append(tid)

            layers: list[list[str]]
            edges: list[list[str]]
            if _DAG_BUILDER_AVAILABLE:
                dag_tickets = [
                    {"id": tid, "files_touched": ticket_map[tid]["files_touched"]}
                    for tid in ticket_map
                ]
                dag_result = _build_dag(dag_tickets)
                if isinstance(dag_result, _CycleError):
                    layers = [list(ticket_map.keys())]
                    edges = []
                    if dag_result.cycles:
                        cycle_path = dag_result.cycles[0]
                else:
                    layers = dag_result.layers
                    edges = [[e[0], e[1]] for e in dag_result.edges]
            else:
                layers = [list(ticket_map.keys())]
                edges = []

            dag_payload = {
                "layers": layers,
                "edges": edges,
                "tickets": list(ticket_map.values()),
            }
    except subprocess.CalledProcessError:
        pass  # DAG is decorative — don't fail the preflight

    # Generate mis-sizing flags using the sprint issues already fetched above
    mis_sizing_flags: dict | None = None
    if _MIS_SIZING_AVAILABLE:
        try:
            _ms_commander = _commander_dir(_project_root_path(project))
            _ms_issues = sprint_issues if sprint_issues else []
            mis_sizing_flags = _mis_sizing.generate_and_save_flags(
                _ms_commander, sprint_label, _ms_issues
            )
        except Exception:
            pass  # Flags are decorative — don't fail the preflight

    return {
        "ok": True,
        "sprint_label": sprint_label,
        "project": project,
        "dag": dag_payload,
        "warnings": warnings,
        "cycle": cycle_path,
        "stale_threshold_days": _STALE_ESTIMATE_DAYS,
        "mis_sizing_flags": mis_sizing_flags,
        # Effective agent models the run will use — shown in the confirm modal.
        "models": _effective_agent_models(_project_root_path(project)),
    }


@app.post("/api/sprints/run", status_code=202)
def run_sprint_managed(request: Request, body: SprintMgmtRunBody):
    """Spawn sprint_manager.py for the given project + sprint.

    - cwd = project's coder clone
    - ANTHROPIC_API_KEY stripped from subprocess env
    - stdout/stderr → .commander/logs/sprint-run-<label>-<ts>.log
    - PID → .commander/sprints/<label>-pid
    - migrate_from: list of sprint numbers whose open tickets are moved to target sprint
      before dispatch; rollback on any failure.
    """
    _slog.event("route.entry", project="dashboard", request_id=request.state.request_id, route="/api/sprints/run", method="POST", sprint_label=body.sprint_label, target_project=body.project)
    if not _SPRINT_LABEL_RE.match(body.sprint_label):
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprints/run", level="error", sprint_label=body.sprint_label, error="invalid sprint label")
        raise HTTPException(400, detail=f"Invalid sprint label: {body.sprint_label!r}")
    if not SPRINT_MANAGER_PATH.exists():
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprints/run", level="error", sprint_label=body.sprint_label, error="sprint_manager.py not found")
        raise HTTPException(502, detail=f"sprint_manager.py not found at {SPRINT_MANAGER_PATH}")

    project_root = _project_root_path(body.project)
    running = _any_sprint_running(project=body.project)
    if running:
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprints/run", level="error", sprint_label=body.sprint_label, error="sprint already running", running_sprint=running.get("sprint_label"))
        raise HTTPException(
            409,
            detail=(
                f"Cannot start sprint: {running['sprint_label']} is currently running"
                f" on {running['project']}"
            ),
        )
    coder_path   = _coder_clone_path(project_root)
    commander    = _commander_dir(project_root)

    # One label = one attempt: terminal labels are never re-dispatched (P0).
    _reject_terminal_label_redispatch(project_root, body.sprint_label)

    # Pending sign-off gate (issue #862): a planned sprint must be approved
    # before it can run. Blocks silent advance past the governance gate.
    _assert_sprint_signed_off(project_root, body.sprint_label)

    # Block empty runs before spawn — instant no-op sprints leave no live logs.
    from services.sprint_manager import sprint_manager as _sm_run
    backlog = _sm_run.list_backlog_issues(body.sprint_label, body.project)
    if not backlog:
        child = _sprint_rerun_into_map(project_root).get(body.sprint_label)
        if child:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No dispatchable tickets on {body.sprint_label}. "
                    f"Tickets were moved to {child} — use Run on that sprint instead."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail=(
                f"No dispatchable tickets on {body.sprint_label}. "
                "Use Re-run to create a sub-sprint, or restore sprint labels on GitHub."
            ),
        )

    # ── Cycle detection: hard-block run if dependency graph has cycles ────────
    if _DAG_BUILDER_AVAILABLE:
        try:
            _cycle_issues = github_client.list_open_issues_with_body(repo_name=body.project, limit=200)
            _cycle_sprint_issues = [
                iss for iss in _cycle_issues
                if any(lbl["name"] == body.sprint_label for lbl in iss.get("labels", []))
            ]
            _cycle_tickets = _sprint_dag_tickets(project_root, _cycle_sprint_issues)
            _dag_result = _build_dag(_cycle_tickets)
            if isinstance(_dag_result, _CycleError):
                _payload = _dag_result.to_payload()
                _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprints/run", level="error", sprint_label=body.sprint_label, error="cycle_detected", cycles=_payload["cycles"])
                raise HTTPException(422, detail=_payload)
        except HTTPException:
            raise
        except subprocess.CalledProcessError as e:
            raise _gh_error(e)

    # ── Mis-sizing flags: hard-block run if pending flags remain ─────────────
    if _MIS_SIZING_AVAILABLE:
        if _mis_sizing.has_unresolved_flags(commander, body.sprint_label):
            raise HTTPException(
                422,
                detail=(
                    "Cannot start sprint: unresolved mis-sizing flags in review queue. "
                    "Open the Run Sprint dialog and resolve all flagged tickets first."
                ),
            )

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

    # Write state=running before spawning subprocess (issue #507).
    # Durable lifecycle state goes to the `sprints` table (issue #757); plan.json
    # is dual-written as a deprecated cache.
    _started_at = datetime.now(timezone.utc).isoformat()
    try:
        _plan_json_set_state(
            project_root,
            body.sprint_label,
            "running",
            started_at=_started_at,
        )
    except Exception:
        pass
    _sprint_db_set_state(
        body.sprint_label, body.project, "running", started_at=_started_at
    )

    stripped_env = _build_sprint_subprocess_env()
    goal_path = _sprint_goal_path(project_root, body.sprint_label)
    if goal_path.exists():
        stripped_env["SPRINT_GOAL"] = goal_path.read_text(encoding="utf-8").strip()

    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SPRINT_MANAGER_PATH), body.sprint_label,
             "--alert-mode", _ALERT_MODES],
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
        _slog.event("route.error", project="dashboard", request_id=request.state.request_id, route="/api/sprints/run", level="error", sprint_label=body.sprint_label, target_project=body.project, error=f"subprocess exited immediately rc={proc.returncode}")
        raise HTTPException(
            502,
            detail=f"Sprint subprocess exited immediately (rc={proc.returncode}). Log tail:\n{tail}",
        )
    except subprocess.TimeoutExpired:
        # Still running after 2 seconds — normal startup, return 202.
        pass

    _slog.event("sprint.dispatch", project="dashboard", request_id=request.state.request_id, sprint_label=body.sprint_label, target_project=body.project, dispatch_type="managed", pid=proc.pid)
    # Scoped activity-log event for the sprint target (issue #721)
    _emit_dashboard_event(
        project=body.project,
        type="sprint_started",
        target=body.sprint_label,
        detail={"sprint_id": body.sprint_label},
        action_id=str(uuid.uuid4()),
    )
    return {
        "ok": True,
        "sprint_label": body.sprint_label,
        "pid": proc.pid,
        "log": str(log_path),
        "migrated_count": migrated_count,
        "migrate_from": body.migrate_from,
    }


@app.get("/api/sprints/{sprint_label}/state")
def get_sprint_state(sprint_label: str, project: str):
    """Return the full plan.json payload for a sprint (issue #507).

    Creates plan.json lazily on first access for legacy sprints that pre-date
    this feature.  State values: see _VALID_PLAN_STATES (unified lifecycle).
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    import logging as _logging
    _log = _logging.getLogger(__name__)
    project_root = _project_root_path(project)
    plan = _read_plan_json(project_root, sprint_label)
    if plan is None:
        # Lazy migration for legacy sprint (issue #507)
        sprints_dir = _commander_dir(project_root) / "sprints"
        has_pid = (sprints_dir / f"{sprint_label}-pid").exists() or \
                  (sprints_dir / f"{sprint_label}-pid.pending").exists()
        if has_pid and _is_sprint_running(project_root, sprint_label):
            new_state = "running"
        else:
            new_state = "completed"
        try:
            _plan_json_set_state(project_root, sprint_label, new_state)
            _log.info("[plan-migrate] %s: created plan.json state=%s (lazy, first state access)", sprint_label, new_state)
        except Exception:
            pass
        plan = _read_plan_json(project_root, sprint_label)
    if plan is None:
        raise HTTPException(404, detail=f"Could not read or create plan.json for {sprint_label}")
    return plan


@app.delete("/api/sprints/run/{sprint_label}", status_code=200)
def kill_sprint(sprint_label: str, project: str):
    """SIGTERM then SIGKILL the running sprint process for the given project/label.

    Unix only (macOS, Linux). Windows is not a supported platform for process
    termination — os.kill() with SIGTERM/SIGKILL is unavailable there.
    """
    if sys.platform == "win32":
        raise HTTPException(
            status_code=501,
            detail="Process termination via SIGTERM/SIGKILL is not supported on Windows. Run Commander on macOS or Linux.",
        )
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

    # Unix-only: SIGTERM first, wait up to 5 s for graceful exit, then SIGKILL
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

    # A user cancel is a needs_rework ending under the unified lifecycle
    # (docs/architecture/sprint-lifecycle.md): the distinction is kept as
    # end_reason, not as a separate `cancelled` state. Tickets are NOT given
    # the need-rework GitHub label here — only tickets that actually failed
    # get it (from the sprint manager's failure paths).
    _cancel_reason = "stopped by user"
    project_root = _project_root_path(project)
    json_path = _sprint_json_path(project_root, sprint_label)
    data = _sprint_json_read(json_path)
    if data:
        data["status"] = "needs_rework"
        data["end_reason"] = _cancel_reason
        _sprint_json_write(json_path, data)

    # Write state=needs_rework to plan.json (issue #507) + DB (issue #757)
    try:
        _plan_json_set_state(project_root, sprint_label, "needs_rework",
                             end_reason=_cancel_reason)
    except Exception:
        pass
    _sprint_db_set_state(sprint_label, project, "needs_rework",
                         end_reason=_cancel_reason)

    # Record the reason on the sprint summary issue so GitHub keeps the "why"
    # without any DB lookup. Best-effort: the summary issue may not exist yet
    # when the run is cancelled early.
    try:
        _summary_url = _read_sprint_summary_url(project_root, sprint_label)
        _summary_num = None
        if _summary_url:
            _m_sum = re.search(r"/issues/(\d+)", _summary_url)
            _summary_num = int(_m_sum.group(1)) if _m_sum else None
        if _summary_num:
            github_client.add_comment(
                _summary_num,
                f"⚠️ Sprint `{sprint_label}` was stopped by the user — marked "
                f"**needs_rework** (end reason: *{_cancel_reason}*). Tickets were "
                "not labeled `need-rework`; re-run via a child sub-sprint.",
                repo_name=project,
            )
    except Exception:
        pass

    _emit_dashboard_event(
        project=project or "dashboard",
        type="sprint_cancelled",
        target=sprint_label,
        detail={"sprint_id": sprint_label},
        action_id=str(uuid.uuid4()),
    )
    return {"ok": True}


# ── Logs: run history (issue #419) ───────────────────────────────────────────

@app.get("/api/logs/runs")
def get_logs_runs(
    project: Optional[str] = None,
    sprint_label: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """Return paginated sprint run history read from sprint state JSON files."""
    # Validate and parse date filters
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)  # naive input: assume UTC
        except ValueError:
            raise HTTPException(
                400,
                detail=f"Invalid start_date {start_date!r}. Use ISO 8601 format, e.g. 2024-06-01.",
            )

    if end_date:
        try:
            parsed_end = datetime.fromisoformat(end_date)
            if parsed_end.tzinfo is None:
                parsed_end = parsed_end.replace(tzinfo=timezone.utc)  # naive input: assume UTC
            # Date-only string: extend to end of day
            if "T" not in end_date:
                parsed_end = parsed_end.replace(hour=23, minute=59, second=59, microsecond=999999)
            end_dt = parsed_end
        except ValueError:
            raise HTTPException(
                400,
                detail=f"Invalid end_date {end_date!r}. Use ISO 8601 format, e.g. 2024-06-30.",
            )

    items: list[dict] = []

    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    for proj in all_projects:
        repo = proj.get("repo", "")
        project_root = _project_root_path(repo)
        sprints_dir = _commander_dir(project_root) / "sprints"

        if not sprints_dir.exists():
            continue

        for state_path in sprints_dir.glob("sprint-*-state.json"):
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            state_project = state_data.get("project") or repo
            state_sprint_label = state_data.get("sprint_label", "")
            start_ts_str = state_data.get("start_timestamp")
            wall_clock = float(state_data.get("wall_clock_secs") or 0.0)
            issues = state_data.get("issues") or []

            if not start_ts_str:
                continue

            try:
                start_time_dt = datetime.fromisoformat(start_ts_str.rstrip("Z"))
                if start_time_dt.tzinfo is None:
                    start_time_dt = start_time_dt.replace(tzinfo=timezone.utc)  # DB stores naive UTC
            except ValueError:
                continue

            end_time_dt = start_time_dt + timedelta(seconds=wall_clock)

            compact_ts = start_time_dt.strftime("%Y%m%dT%H%M%S")
            run_id = f"sprint-{state_sprint_label}-{compact_ts}"

            has_failed = any(
                i.get("agent_status") == "failed"
                or i.get("failure_reason")
                or i.get("status") == "skipped"
                for i in issues
            )
            all_done = bool(issues) and all(i.get("status") == "done" for i in issues)
            if all_done and not has_failed:
                outcome = "success"
            elif has_failed:
                outcome = "partial"
            else:
                outcome = "unknown"

            # Apply filters
            if project and state_project != project:
                continue
            if sprint_label and state_sprint_label != sprint_label:
                continue
            if start_dt and start_time_dt < start_dt:
                continue
            if end_dt and start_time_dt > end_dt:
                continue

            items.append({
                "run_id": run_id,
                "project": state_project,
                "sprint_label": state_sprint_label,
                "start_time": start_time_dt.isoformat(),
                "end_time": end_time_dt.isoformat(),
                "ticket_count": len(issues),
                "outcome": outcome,
            })

    items.sort(key=lambda x: x["start_time"], reverse=True)

    total = len(items)
    offset = (page - 1) * page_size
    paged = items[offset: offset + page_size]

    return {"items": paged, "page": page, "page_size": page_size, "total": total}


@app.post("/api/logs/sync-github")
def post_logs_sync_github(project: Optional[str] = None):
    """Poll GitHub Events API for the project repo and upsert into the events table."""
    if not project:
        return {"synced": 0, "skipped": 0, "rate_limited": False, "error": "project required"}
    try:
        result = github_events_sync.sync_github_events(project=project, repo=project)
    except Exception as exc:
        return {"synced": 0, "skipped": 0, "rate_limited": False, "error": str(exc)}
    return result


@app.get("/api/sprints/{sprint_label}/state-full")
def get_sprint_state_full(sprint_label: str, project: str):
    """Return full sprint state including per-ticket issues for the comparison view (issue #435)."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    sprints_dir = _commander_dir(project_root) / "sprints"

    if not sprints_dir.exists():
        raise HTTPException(404, detail="No sprints directory found")

    for state_path in sprints_dir.glob("sprint-*-state.json"):
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if state_data.get("sprint_label") != sprint_label:
            continue

        issues = state_data.get("issues") or []
        dispatch_count = sum(1 for i in issues if i.get("coder_started_at") is not None)
        has_failed = any(
            i.get("agent_status") == "failed"
            or i.get("failure_reason")
            or i.get("status") == "skipped"
            for i in issues
        )
        all_done = bool(issues) and all(i.get("status") == "done" for i in issues)
        if all_done and not has_failed:
            outcome = "success"
        elif has_failed:
            outcome = "partial"
        else:
            outcome = "unknown"

        return {**state_data, "outcome": outcome, "dispatch_count": dispatch_count}

    raise HTTPException(404, detail=f"Sprint state not found for {sprint_label!r}")


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
      "active_agents": [{"name": "coder"|"tester", "ticket": {"number": N, "title": "..."}, "pid": N}, ...],
      "pipeline_mode": <bool>,
      "levels": [{"level": N, "total": N, "merged": N, "state": "complete"|"active"|"waiting"}, ...],
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

    # Disk fallback: sprint_manager writes state.json directly regardless of
    # which port the dashboard is on. Use it when in-memory cache is empty
    # (server restart, or sprint_manager posting to a different port).
    if not status_data:
        # Use the FULL sprint label for the state filename. A re-run sub-label
        # like "sprint-66.5" has its own sprint-66.5-state.json; the old regex
        # extracted only the base number ("66"), so /live read the PARENT
        # sprint's stale state and the running pane showed no active issue on a
        # re-run (operator: "can't see which issue is working when you re-run").
        _fb_path = commander / "sprints" / f"{sprint_label}-state.json"
        if _fb_path.exists():
            try:
                status_data = json.loads(_fb_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    started_at_str: Optional[str] = status_data.get("start_timestamp")
    started_at_dt: Optional[datetime] = None
    if started_at_str:
        try:
            started_at_dt = datetime.fromisoformat(started_at_str.rstrip("Z"))
            if started_at_dt.tzinfo is None:
                started_at_dt = started_at_dt.replace(tzinfo=timezone.utc)  # DB stores naive UTC
        except Exception:
            started_at_dt = None

    now_utc = datetime.now(timezone.utc)
    time_spent_sec: int = 0
    if started_at_dt:
        time_spent_sec = max(0, int((now_utc - started_at_dt).total_seconds()))

    # ── current_ticket: the ticket an agent is actively working ───────────────
    current_ticket: Optional[dict] = None
    issues = status_data.get("issues", [])
    # Prefer the ticket with an in-flight agent (state.json keeps status="pending"
    # while agent_status flips to coder_running/tester_running), then any issue
    # marked in-progress, then the first non-terminal one.
    _ACTIVE_AGENT = ("coder_dispatched", "coder_running", "tester_dispatched", "tester_running")
    active_iss = [i for i in issues if i.get("agent_status") in _ACTIVE_AGENT]
    in_progress = [i for i in issues if i.get("status") == "in-progress"]
    if active_iss:
        iss = active_iss[-1]
        current_ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
    elif in_progress:
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
                    stored_mins = est_entry.get("minutes")
                    if stored_mins and isinstance(stored_mins, (int, float)) and stored_mins > 0:
                        rem_minutes += int(stored_mins)
                    else:
                        rem_minutes += _minutes_from_letter(est_entry.get("size", ""))
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
        # DB timestamps are stored without tzinfo; strip trailing Z and assume UTC.
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

        # size + minutes: populate both; derive missing field from the present one
        est_entry = estimates.get(str(num)) or estimates.get(num)
        raw_size: Optional[str] = est_entry.get("size") if est_entry else None
        raw_minutes: Optional[int] = est_entry.get("minutes") if est_entry else None
        if raw_size and not raw_minutes:
            raw_minutes = _minutes_from_letter(raw_size)
        elif raw_minutes and not raw_size:
            raw_size = _letter_from_minutes(raw_minutes)

        issues_out.append({
            "number":         num,
            "title":          iss.get("title", ""),
            "status":         derived_status,
            "agent_status":   public_agent_status,
            "agent":          active_role,
            "elapsed_secs":   issue_elapsed,
            "size":           raw_size,
            "minutes":        raw_minutes,
            "dispatch_level": iss.get("dispatch_level", 0),
            # Size-routed coder model (issue #789) so the running pane's Coder
            # badge can show which model is implementing the ticket.
            "coder_model":          iss.get("coder_model"),
            # Surface the actual failure category/reason so the inspector shows
            # the real cause (e.g. CRASH) instead of a hardcoded "gate failed".
            "category":       iss.get("category"),
            "failure_reason": iss.get("failure_reason"),
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
                    # Show the size-routed coder model in the header active-agent
                    # chip (issue #789); tester model is risk-routed elsewhere.
                    agent_model = iss.get("coder_model") if agent_name == "coder" else None
                    active_agent = {"name": agent_name, "model": agent_model, "pid": None}
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

    # ── Pipeline mode + dual active agents + per-level progress (issue #739) ──
    # pipeline_mode is persisted on the sprint state by the sprint manager. When
    # set, the board renders two active-agent cards and a waiting-level structure.
    pipeline_mode = bool(status_data.get("pipeline_mode", False))

    # active_agents: every in-flight agent derived from per-issue lifecycle
    # timestamps. Serial mode yields at most one; pipeline mode yields a coder and
    # a tester working different tickets at once. Ordered [coder, tester].
    agent_pid = active_agent.get("pid") if active_agent else None
    coder_entry = None
    tester_entry = None
    for iss in issues:
        ticket = {"number": iss.get("number"), "title": iss.get("title", "")}
        cs, cf = iss.get("coder_started_at"), iss.get("coder_finished_at")
        ts, tf = iss.get("tester_started_at"), iss.get("tester_finished_at")
        if ts and not tf:
            tester_entry = {"name": "tester", "ticket": ticket, "pid": agent_pid}
        elif cs and not cf:
            coder_entry = {"name": "coder", "ticket": ticket, "pid": agent_pid}
    active_agents: list[dict] = [e for e in (coder_entry, tester_entry) if e]
    # Fall back to the single state/pid-derived agent when no issue-level timestamps
    # are available (keeps the serial live-log badge working unchanged).
    if not active_agents and active_agent:
        active_agents = [active_agent]

    # levels: per dispatch-level progress for the pipeline board (extracted to
    # live_metrics.compute_levels — issue #803 — so this endpoint can grow the
    # Running-view metric strip without growing the guarded server.py monolith).
    levels_out = _live_metrics.compute_levels(issues)

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
        # Dual active agents + pipeline structure (issue #739)
        "active_agents": active_agents,
        "pipeline_mode": pipeline_mode,
        "levels": levels_out,
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
        # Running-view metric strip (issue #803) — agent_runs-derived keys are
        # present only when their source exists, so the frontend hides cards.
        **_live_metrics.running_metrics(sprint_label, project),
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
    """Create a sprint-N label as a verified, retry-and-rollback sequence (#857).

    The step orchestration (create label -> apply ticket labels -> write plan,
    each verified, step 1 retried once, rolled back on failure) lives in
    sprints_service; a failed step surfaces as a loud, step-named HTTP error.
    """
    from routers import sprints_service  # noqa: PLC0415 — deferred (router import cycle)
    try:
        sprint_label = sprints_service.create_sprint_verified(
            body.project, body.sprint_number, body.goal, body.tickets,
        )
    except sprints_service.SprintCreationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    _emit_dashboard_event(
        project=body.project or "dashboard",
        type="sprint_created",
        target=sprint_label,
        detail={"sprint_name": sprint_label},
        action_id=str(uuid.uuid4()),
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
    for suffix in ("-goal.txt", "-state.json", "-plan.json"):
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

    # Mirror the rename into the durable SQLite sprints table (issue #758 removed
    # the Neon mirror; best-effort — GitHub is the source of truth for labels).
    try:
        db.rename_sprint(sprint_label, new_label)
    except Exception:
        pass

    return {"ok": True, "old_label": sprint_label, "new_label": new_label}


class SprintTicketReorderBody(BaseModel):
    issue_numbers: list[int]
    project: str


@app.post("/api/sprints/{sprint_label}/tickets/reorder")
def reorder_sprint_tickets(sprint_label: str, body: SprintTicketReorderBody):
    """Reorder tickets within a sprint. Persists to local JSON + durable SQLite."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    # Persist to local JSON; the durable SQLite ticket-order write happens below.
    # The Neon mirror was removed in issue #758.
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

    # Durable ticket order (issue #757); best-effort so it never breaks the reorder.
    try:
        db.set_sprint_ticket_order(sprint_label, body.issue_numbers)
    except Exception:
        pass

    return {"ok": True}


@app.post("/api/sprints/{sprint_label}/plan")
async def save_sprint_plan(sprint_label: str, project: str, request: Request):
    """Persist ticket execution order to {label}-plan.json (issue #441).

    Preserves existing state/timestamp fields (issue #507) when updating tickets.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, detail="Body must be a JSON array of integers")
    if not isinstance(body, list) or not all(isinstance(n, int) for n in body):
        raise HTTPException(400, detail="Body must be a JSON array of integers")
    project_root = _project_root_path(project)
    existing = _read_plan_json(project_root, sprint_label) or {}
    existing["tickets"] = body
    _write_plan_json(project_root, sprint_label, existing)
    # Durable ticket order (issue #757); plan.json is the deprecated cache.
    try:
        db.set_sprint_ticket_order(sprint_label, body)
    except Exception:
        pass
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


class SprintCleanupBody(BaseModel):
    project: str


@app.post("/api/sprints/cleanup-empty")
async def cleanup_empty_sprints(body: SprintCleanupBody):
    """Delete leading consecutive empty sprint labels from GitHub (issue #658).

    Only removes sprint-N labels that appear before the first sprint with ≥ 1 open
    ticket (in ascending number order). Trailing empty sprints — those after any
    sprint that has tickets — are preserved. If no sprint has tickets, nothing is
    deleted.
    """
    try:
        all_sprint_labels = github_client.list_sprint_labels(repo_name=body.project)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    try:
        issues = github_client.list_open_issues_with_body(repo_name=body.project, limit=200)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    sprint_re_any   = re.compile(r"^sprint-(\d+)(?:\.\d+)?$")
    sprint_re_plain = re.compile(r"^sprint-(\d+)$")

    # Collect base sprint numbers that have ≥ 1 open ticket (plain or dotted sub-labels)
    active_base_nums: set[int] = set()
    for iss in issues:
        for lbl in iss.get("labels", []):
            m = sprint_re_any.match(lbl["name"])
            if m:
                active_base_nums.add(int(m.group(1)))

    # Iterate plain sprint-N labels in ascending order; collect consecutive leading empties
    plain_labels = sorted(
        [l for l in all_sprint_labels if sprint_re_plain.match(l)],
        key=lambda l: int(sprint_re_plain.match(l).group(1)),  # type: ignore[union-attr]
    )

    leading_empty: list[str] = []
    found_active = False
    for label in plain_labels:
        base_num = int(sprint_re_plain.match(label).group(1))  # type: ignore[union-attr]
        if base_num in active_base_nums:
            found_active = True
            break
        leading_empty.append(label)

    # AC3: if no sprint has tickets, nothing is eligible for cleanup
    if not found_active:
        leading_empty = []

    deleted = []
    errors = []
    for label in leading_empty:
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


# Session-state labels stripped when a sprint is re-run (issue #811). These
# describe a ticket's in-flight work session — needs-rework, an active
# in-progress run, an away/parked SIT session, or a tester rejection. The
# moment a fresh re-run begins no such session exists yet, so the labels are
# stale and misrepresent what needs attention. This is the label taxonomy the
# re-run sweep consults; labels outside it (e.g. bug, priority-high, size-M)
# are intrinsic to the ticket and are always preserved.
_SESSION_STATE_LABELS = frozenset({
    "needs-rework",
    "need-rework",
    "in-progress",
    "sit-away",
    "tester-rejected",
})


def _stale_session_labels(labels) -> list[str]:
    """Return the session-state labels present in `labels`, sorted.

    Used on sprint re-run to strip stale status labels (issue #811). Only
    labels in the `_SESSION_STATE_LABELS` taxonomy are returned; everything
    else is preserved. Returns an empty list when no stale labels are present,
    so callers can re-run cleanly when there is nothing to strip.
    """
    return sorted(set(labels) & _SESSION_STATE_LABELS)


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


@app.get("/api/estimates/batch")
def get_estimates_batch(project: str, issues: str = ""):
    """Return summed estimated_hours and per-issue size/confidence for a list of issue numbers.

    Query params:
      - project: repo slug (owner/repo)
      - issues: comma-separated issue numbers, e.g. "431,432,433"

    Returns:
      total_hours: float|null — null when any issue lacks an estimate
      complete: bool — true when every issue has an estimate
      issues: {num_str: {size, confidence, ...}|null}
      total_tokens: int|null — aggregated estimated tokens; null when no issue has an estimate
      total_cost_usd: float|null — estimated cost at Haiku 4.5 rates; null when no estimate
      estimated_count: int — number of issues with a cached estimate
      partial: bool — true when some but not all issues have an estimate
    """
    # Estimated tokens per size (mirrors SIZE_TO_MINUTES * 1000 ratio from sizing.py).
    # Haiku 4.5 blended cost: 60 % input at $0.80/M + 40 % output at $4.00/M = $2.08/M.
    _SIZE_TOKENS: dict[str, int] = {"S": 5_000, "M": 15_000, "L": 30_000, "XL": 60_000}
    _COST_PER_TOKEN: float = (0.80 * 0.6 + 4.00 * 0.4) / 1_000_000  # $2.08 per million

    issue_nums = [int(p) for p in issues.split(",") if p.strip().isdigit()]

    if not issue_nums:
        return {"total_hours": 0.0, "complete": True, "issues": {},
                "total_tokens": None, "total_cost_usd": None,
                "estimated_count": 0, "partial": False}

    try:
        project_root = _project_root_path(project)
        estimates_dir = _commander_dir(project_root) / "estimates"
        if not estimates_dir.is_dir():
            return {"total_hours": None, "complete": False,
                    "issues": {str(n): None for n in issue_nums},
                    "total_tokens": None, "total_cost_usd": None,
                    "estimated_count": 0, "partial": False}
    except Exception:
        return {"total_hours": None, "complete": False,
                "issues": {str(n): None for n in issue_nums},
                "total_tokens": None, "total_cost_usd": None,
                "estimated_count": 0, "partial": False}

    total = 0.0
    total_tokens = 0
    complete = True
    estimated_count = 0
    per_issue: dict = {}
    for num in issue_nums:
        path = estimates_dir / f"issue-{num}.json"
        if not path.exists():
            complete = False
            per_issue[str(num)] = None
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            h = data.get("estimated_hours")
            size = data.get("size")
            confidence = data.get("confidence")
            if h is None:
                complete = False
            else:
                total += float(h)
            tokens = _SIZE_TOKENS.get(size or "", 0)
            total_tokens += tokens
            estimated_count += 1
            per_issue[str(num)] = {
                "size": size,
                "confidence": confidence,
                "files_likely_affected": data.get("files_likely_affected", []),
                "risk_flags": data.get("risk_flags", []),
                "summary": data.get("summary", ""),
            }
        except (json.JSONDecodeError, OSError, ValueError):
            complete = False
            per_issue[str(num)] = None

    has_any = estimated_count > 0
    return {
        "total_hours": total if complete else None,
        "complete": complete,
        "issues": per_issue,
        "total_tokens": total_tokens if has_any else None,
        "total_cost_usd": total_tokens * _COST_PER_TOKEN if has_any else None,
        "estimated_count": estimated_count,
        "partial": has_any and estimated_count < len(issue_nums),
    }


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


def _has_rework_tickets(sprint_label: str, project: str) -> bool:
    """Return True if the sprint should read as rework rather than completed.

    A finished sprint is rework when any open work ticket either carries a
    rework/rejected label (needs-rework or tester-rejected — a tester rejection
    is treated as a failed sprint) or never reached a done/UAT state (e.g. the
    coder failed, so nothing shipped). Non-work tickets (summary/docs) are
    ignored. A sprint whose work all reached UAT/UAT-approved (or is fully
    closed) reads as completed.
    """
    NON_WORK = {"sprint-summary", "docs", "documentation"}
    REWORK = {"needs-rework", "need-rework", "tester-rejected"}
    DONE = {"UAT", "UAT-approved", "released"}
    try:
        issues = _get_sprint_issues(project, sprint_label)
    except Exception:
        return False
    for iss in issues:
        labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if labels & NON_WORK:
            continue
        if labels & REWORK:
            return True
        if not (labels & DONE):
            # open work ticket that never reached a done/UAT state → unfinished/failed
            return True
    return False


def _sprint_has_own_run_outcome(project_root: Path, sprint_label: str) -> bool:
    """True when outcome data for *this* label exists (not a sibling/base run)."""
    plan = _read_plan_json(project_root, sprint_label)
    if plan and plan.get("state") in ("planning", "draft", "planned"):
        return False

    row = db.get_sprint(sprint_label)
    if row and row.get("run_ingested_at"):
        return True

    from routers import sprint_artifact_service  # noqa: PLC0415
    sprints_dir = _commander_dir(project_root) / "sprints"
    return sprint_artifact_service.resolve_state_path(sprints_dir, sprint_label) is not None


def _outcome_from_ingested_row(
    row: dict,
    sprint_label: str,
    project: str,
) -> dict:
    """Build outcome payload from DB-ingested run artifacts (lifecycle P3)."""
    from routers import sprint_artifact_service  # noqa: PLC0415

    enrich = sprint_artifact_service.enrichment_from_db_row(row)
    stored_state = row.get("state") or ""
    lifecycle = db.canonical_lifecycle(stored_state)
    end_reason = row.get("end_reason")
    is_cancelled = lifecycle == "needs_rework" and (end_reason or "").startswith("stopped")

    try:
        issues_raw = json.loads(row.get("issues_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        issues_raw = []

    result_issues = []
    for iss in issues_raw:
        tid = iss.get("ticket_id") or iss.get("number")
        agent = (iss.get("agent_status") or "").lower()
        fr = iss.get("failure_reason")
        st = (iss.get("state") or "").lower()
        if st == "merged" or agent in ("completed", "done"):
            outcome = "done"
        elif agent == "failed" or fr:
            outcome = "failed"
        else:
            outcome = "skipped"
        result_issues.append({
            "number": tid,
            "title": iss.get("title", ""),
            "outcome": outcome,
            "elapsed_secs": iss.get("time_spent"),
            "failure_reason": fr,
        })

    if is_cancelled:
        pane_state = "cancelled"
        sprint_status = "stopped"
    elif _has_rework_tickets(sprint_label, project):
        pane_state = "has_rework"
        sprint_status = "stopped"
    else:
        pane_state = "completed"
        sprint_status = "completed"

    done_count = sum(1 for i in result_issues if i["outcome"] == "done")
    failed_count = sum(1 for i in result_issues if i["outcome"] == "failed")
    skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

    surl = enrich.get("summary_issue_url")
    summary_issue_num = enrich.get("summary_issue_num")

    return {
        "sprint_label": sprint_label,
        "state": pane_state,
        "lifecycle": lifecycle,
        "end_reason": end_reason,
        "sprint_status": sprint_status,
        "counts": {
            "done": done_count,
            "failed": failed_count,
            "skipped": skipped_count,
        },
        "wall_clock_secs": enrich.get("duration") or row.get("wall_clock_secs") or 0,
        "ended_at": None,
        "issues": result_issues,
        "log_line_count": 0,
        "summary_issue_url": surl,
        "summary_issue_num": summary_issue_num,
    }


@app.get("/api/sprints/{sprint_label}/outcome")
def get_sprint_outcome(sprint_label: str, project: str):
    """Return frozen outcome data for a completed or stopped sprint.

    Reads sprint-N-state.json plus the latest sprint-run-<label>-*.log to produce:
      - state: "running" | "completed" | "has_rework" | "cancelled"
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

    # Running sprints return immediately — no state file required
    if _is_sprint_running(project_root, sprint_label):
        return {"sprint_label": sprint_label, "state": "running", "lifecycle": "running"}

    if not _sprint_has_own_run_outcome(project_root, sprint_label):
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r} (not run yet)")

    ingested = db.get_sprint(sprint_label)
    if ingested and ingested.get("run_ingested_at"):
        return _outcome_from_ingested_row(ingested, sprint_label, project)

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label

    # Check sprint-N.json for a stopped status (may exist even without a state file)
    json_path = _sprint_json_path(project_root, sprint_label)
    sprint_json = _sprint_json_read(json_path)
    is_cancelled: bool = sprint_json.get("status") in ("cancelled", "needs_rework")

    state_path = commander / "sprints" / f"sprint-{n}-state.json"
    from routers import sprint_artifact_service  # noqa: PLC0415
    resolved = sprint_artifact_service.resolve_state_path(commander / "sprints", sprint_label)
    if resolved is not None:
        state_path = resolved
    if not state_path.exists():
        if is_cancelled:
            return {"sprint_label": sprint_label, "state": "cancelled",
                    "lifecycle": "needs_rework",
                    "end_reason": sprint_json.get("end_reason")}
        raise HTTPException(404, detail=f"Outcome not found for {sprint_label!r}")

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

    def _fmt_iso(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")

    # Derive sprint status from summary file (most authoritative)
    sprint_status: Optional[str] = None
    sprints_dir = commander / "sprints"
    for sf in sorted(
        list((commander / "sprints").glob(f"{sprint_label}-summary-*.md"))
        + list((commander / "sprints").glob(f"sprint-{n}-summary-*.md")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            meta = _parse_summary_file(sf)
            raw = (meta.get("status") or "").lower()
            if raw in ("complete", "completed"):
                sprint_status = "completed"
            elif raw in ("stopped", "failed", "cancelled"):
                sprint_status = "stopped"
                if raw == "cancelled":
                    is_cancelled = True
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

    # Derive 4-state outcome for pane coloring
    if is_cancelled:
        pane_state = "cancelled"
    elif _has_rework_tickets(sprint_label, project):
        pane_state = "has_rework"
    else:
        pane_state = "completed"

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

    # Retroactive label override: if an issue is marked "failed" in state.json but
    # currently carries a UAT label on GitHub (manually applied after the sprint ran),
    # treat it as "done" so the card reflects the true current state.
    failed_nums = [i["number"] for i in result_issues if i["outcome"] == "failed" and i["number"]]
    if failed_nums:
        try:
            repo = github_client.get_repo_for_operation(project)
            r = subprocess.run(
                ["gh", "issue", "list", "--repo", repo,
                 "--state", "all", "--label", "UAT",
                 "--json", "number",
                 "--limit", "200"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                uat_nums = {i["number"] for i in (json.loads(r.stdout) or [])}
                for ri in result_issues:
                    if ri["outcome"] == "failed" and ri["number"] in uat_nums:
                        ri["outcome"] = "done"
        except Exception:
            pass

    # Counts
    done_count    = sum(1 for i in result_issues if i["outcome"] == "done")
    failed_count  = sum(1 for i in result_issues if i["outcome"] == "failed")
    skipped_count = sum(1 for i in result_issues if i["outcome"] == "skipped")

    # Retroactive override may have cleared all failures — upgrade stopped → completed
    if sprint_status == "stopped" and failed_count == 0:
        sprint_status = "completed"

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

    # Sprint Summary issue (issue #613: finished outcome band links)
    summary_issue_url: Optional[str] = state_data.get("summary_issue_url")
    summary_issue_num: Optional[int] = None
    if summary_issue_url:
        m_sn = re.search(r"/issues/(\d+)", summary_issue_url)
        if m_sn:
            summary_issue_num = int(m_sn.group(1))

    return {
        "sprint_label":      sprint_label,
        "state":             pane_state,
        # Unified lifecycle (sprint-lifecycle.md): pane vocabulary mapped to
        # the one enum shared with the History pane.
        "lifecycle":         db.canonical_lifecycle(pane_state),
        "end_reason":        sprint_json.get("end_reason"),
        "sprint_status":     sprint_status,
        "counts": {
            "done":    done_count,
            "failed":  failed_count,
            "skipped": skipped_count,
        },
        "wall_clock_secs":   state_data.get("wall_clock_secs", 0.0),
        "ended_at":          _fmt_iso(ended_ts),
        "issues":            result_issues,
        "log_line_count":    log_line_count,
        "summary_issue_url": summary_issue_url,
        "summary_issue_num": summary_issue_num,
    }


# ── Estimate-vs-Actual report (issue #575) ───────────────────────────────────

@app.get("/api/sprints/{sprint_label}/estimate-vs-actual")
def get_sprint_estimate_vs_actual(sprint_label: str, project: str):
    """Return per-ticket estimate-vs-actual comparison for a finished sprint.

    Response schema:
      {
        "sprint_label": "sprint-43",
        "tickets": [
          {
            "ticket_id": 574,
            "title": "...",
            "estimated_size": "L",          # null if no estimate
            "estimated_minutes": 30,        # null if no estimate
            "actual_elapsed_seconds": 1234, # null if timestamps missing
            "actual_elapsed_minutes": 20.6, # null if timestamps missing
            "delta_minutes": -9.4,          # actual - estimated; null if either is null
            "status": "done"
          }
        ]
      }

    Returns 404 if the sprint is not finished (still running, planning, or not found).
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    if _is_sprint_running(project_root, sprint_label):
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} is still in progress")

    plan = _read_plan_json(project_root, sprint_label)
    plan_state = (plan or {}).get("state", "")
    if plan_state in ("planning", "draft", "planned", "running"):
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} is not finished")
    if plan_state == "cancelled":
        # Legacy files only — new stops land in needs_rework, which DID run
        # and may have a meaningful estimate-vs-actual report.
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} was cancelled")

    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    state_path = commander / "sprints" / f"sprint-{n}-state.json"

    if not state_path.exists():
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    try:
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=f"Could not read state file: {e}")

    issues_raw = state_data.get("issues", [])

    # If plan.json has no definitive "completed" state, derive from issue statuses
    if plan_state != "completed" and issues_raw:
        has_pending = any(i.get("status") == "pending" for i in issues_raw)
        if has_pending:
            raise HTTPException(404, detail=f"Sprint {sprint_label!r} is not finished")
    elif plan_state != "completed" and not issues_raw:
        raise HTTPException(404, detail=f"Sprint {sprint_label!r} not found")

    def _parse_ts(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    estimates_dir = commander / "estimates"
    tickets = []

    for iss in issues_raw:
        issue_num = iss.get("number")
        title = iss.get("title", "")
        status = iss.get("status", "pending")

        estimated_size: Optional[str] = None
        estimated_minutes: Optional[int] = None
        if issue_num is not None:
            est_path = estimates_dir / f"issue-{issue_num}.json"
            if est_path.exists():
                try:
                    est_data = json.loads(est_path.read_text(encoding="utf-8"))
                    estimated_size = est_data.get("size") or None
                    if estimated_size:
                        estimated_minutes = _size_to_minutes(estimated_size) or None
                except (json.JSONDecodeError, OSError):
                    pass

        start_ts = _parse_ts(iss.get("coder_started_at"))
        end_ts = (
            _parse_ts(iss.get("tester_finished_at"))
            or _parse_ts(iss.get("status_changed_at"))
        )
        actual_elapsed_seconds: Optional[float] = None
        actual_elapsed_minutes: Optional[float] = None
        if start_ts is not None and end_ts is not None:
            actual_elapsed_seconds = max(0.0, end_ts - start_ts)
            actual_elapsed_minutes = round(actual_elapsed_seconds / 60, 1)

        delta_minutes: Optional[float] = None
        if estimated_minutes is not None and actual_elapsed_minutes is not None:
            delta_minutes = round(actual_elapsed_minutes - estimated_minutes, 1)

        tickets.append({
            "ticket_id":              issue_num,
            "title":                  title,
            "estimated_size":         estimated_size,
            "estimated_minutes":      estimated_minutes,
            "actual_elapsed_seconds": round(actual_elapsed_seconds) if actual_elapsed_seconds is not None else None,
            "actual_elapsed_minutes": actual_elapsed_minutes,
            "delta_minutes":          delta_minutes,
            "status":                 status,
        })

    return {
        "sprint_label": sprint_label,
        "tickets":      tickets,
    }


# ── Estimator calibration view (issue #576) ──────────────────────────────────

@app.get("/api/calibration")
def get_calibration(project: str):
    """Return average actual time per size bucket across all finished sprints.

    Scans every sprint-N-state.json in the project's .commander/sprints/
    directory, matches each ticket's actual elapsed time with its cached
    size estimate, then averages by bucket.

    Response schema:
      {
        "buckets": {
          "S":  { "avg_minutes": 8.5, "count": 5, "canonical_minutes": 5 },
          "M":  { "avg_minutes": 18.2, "count": 3, "canonical_minutes": 15 },
          "L":  { "avg_minutes": null, "count": 0, "canonical_minutes": 30 },
          "XL": { "avg_minutes": null, "count": 0, "canonical_minutes": 60 }
        }
      }
    avg_minutes is null for buckets with no completed tickets that have
    both a size estimate and a recorded actual time.
    """
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"

    def _parse_ts(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    # Accumulate (total_minutes, count) per bucket
    buckets_raw: dict[str, list[float]] = {"S": [], "M": [], "L": [], "XL": []}

    if sprints_dir.exists():
        for state_path in sprints_dir.glob("sprint-*-state.json"):
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            for iss in state_data.get("issues", []):
                issue_num = iss.get("number")
                if issue_num is None:
                    continue

                # Count any ticket that reached a completed state — done/passed
                # plus uat/merged — so calibration isn't starved when tickets
                # finish via UAT rather than a literal "done" status.
                if iss.get("status") not in ("done", "passed", "uat", "merged"):
                    continue

                start_ts = _parse_ts(iss.get("coder_started_at"))
                end_ts = (
                    _parse_ts(iss.get("tester_finished_at"))
                    or _parse_ts(iss.get("status_changed_at"))
                )
                if start_ts is None or end_ts is None:
                    continue
                elapsed_minutes = max(0.0, end_ts - start_ts) / 60.0

                est_path = estimates_dir / f"issue-{issue_num}.json"
                if not est_path.exists():
                    continue
                try:
                    est_data = json.loads(est_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                size = est_data.get("size") or None
                if size not in buckets_raw:
                    continue

                buckets_raw[size].append(elapsed_minutes)

    canonical = {"S": 5, "M": 15, "L": 30, "XL": 60}
    result_buckets: dict[str, dict] = {}
    for size in ("S", "M", "L", "XL"):
        vals = buckets_raw[size]
        result_buckets[size] = {
            "avg_minutes": round(sum(vals) / len(vals), 1) if vals else None,
            "count":       len(vals),
            "canonical_minutes": canonical[size],
        }

    return {"buckets": result_buckets}


def _has_rework_tickets(sprint_label: str, project: str) -> bool:
    """Return True if the sprint should read as rework rather than completed.

    A finished sprint is rework when any open work ticket either carries a
    rework/rejected label (needs-rework or tester-rejected — a tester rejection
    is treated as a failed sprint) or never reached a done/UAT state (e.g. the
    coder failed, so nothing shipped). Non-work tickets (summary/docs) are
    ignored. A sprint whose work all reached UAT/UAT-approved (or is fully
    closed) reads as completed.
    """
    NON_WORK = {"sprint-summary", "docs", "documentation"}
    REWORK = {"needs-rework", "need-rework", "tester-rejected"}
    DONE = {"UAT", "UAT-approved", "released"}
    try:
        issues = _get_sprint_issues(project, sprint_label)
    except Exception:
        return False
    for iss in issues:
        labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if labels & NON_WORK:
            continue
        if labels & REWORK:
            return True
        if not (labels & DONE):
            # open work ticket that never reached a done/UAT state → unfinished/failed
            return True
    return False


def _count_rework_tickets(sprint_label: str, project: str) -> int:
    """Return number of open issues in the sprint carrying needs-rework label."""
    try:
        r = github_client.get_repo_for_operation(project)
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", r,
             "--label", sprint_label,
             "--label", "needs-rework",
             "--json", "number",
             "--limit", "100"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return len(json.loads(result.stdout or "[]"))
    except Exception:
        pass
    return 0


# ── Sprint metrics endpoint (issue #475) ─────────────────────────────────────

@app.get("/api/metrics/sprints")
def get_sprint_metrics(request: Request):
    """Return per-sprint aggregate metrics across all registered projects.

    Query params:
      from=YYYY-MM-DD  (default: 30 days ago)
      to=YYYY-MM-DD    (default: today)

    Response: JSON array of sprint metric objects.
    """
    today = datetime.now(tz=timezone.utc).date()

    raw_from = request.query_params.get("from")
    raw_to = request.query_params.get("to")

    if raw_from is None and raw_to is None:
        from_date = today - timedelta(days=30)
        to_date = today
    else:
        if raw_from is not None:
            try:
                from_date = datetime.strptime(raw_from, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    400,
                    detail=f"Invalid 'from' date {raw_from!r} — expected YYYY-MM-DD",
                )
        else:
            from_date = today - timedelta(days=30)

        if raw_to is not None:
            try:
                to_date = datetime.strptime(raw_to, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    400,
                    detail=f"Invalid 'to' date {raw_to!r} — expected YYYY-MM-DD",
                )
        else:
            to_date = today

    if from_date > to_date:
        raise HTTPException(
            400,
            detail=f"'from' date ({from_date}) must not be after 'to' date ({to_date})",
        )

    from_dt = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
    to_dt = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)

    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []

    results = []
    seen_paths: set[Path] = set()

    for proj in all_projects:
        repo = proj.get("repo", "")
        if not repo:
            continue

        project_root = _project_root_path(repo)
        sprints_dir = _commander_dir(project_root) / "sprints"

        if not sprints_dir.exists():
            continue

        for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
            if state_file in seen_paths:
                continue
            seen_paths.add(state_file)

            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            start_ts_str = state_data.get("start_timestamp")
            if not start_ts_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_ts_str.rstrip("Z")).replace(
                    tzinfo=timezone.utc
                )
            except Exception:
                continue

            if not (from_dt <= start_dt <= to_dt):
                continue

            sprint_label_val = state_data.get(
                "sprint_label",
                state_file.stem.replace("-state", ""),
            )
            project_val = state_data.get("project") or repo

            wall_clock_secs = float(state_data.get("wall_clock_secs") or 0.0)
            issues = state_data.get("issues", [])

            done_count = sum(1 for i in issues if i.get("status") == "done")
            failed_count = sum(1 for i in issues if i.get("status") == "failed")
            skipped_count = sum(1 for i in issues if i.get("status") == "skipped")

            coder_count = sum(1 for i in issues if i.get("coder_started_at"))
            tester_count = sum(1 for i in issues if i.get("tester_started_at"))
            reviewer_count = 1 if state_data.get("reviewer_status") not in (None, "") else 0
            documenter_count = 1 if state_data.get("documenter_status") not in (None, "") else 0

            tokens_in = int(state_data.get("total_tokens_in") or 0)
            tokens_out = int(state_data.get("total_tokens_out") or 0)
            total_tokens = tokens_in + tokens_out
            token_estimate = total_tokens if total_tokens > 0 else None

            results.append({
                "sprint_label": sprint_label_val,
                "project": project_val,
                "duration_minutes": round(wall_clock_secs / 60, 2),
                "ticket_count": len(issues),
                "ticket_outcomes_breakdown": {
                    "done": done_count,
                    "failed": failed_count,
                    "skipped": skipped_count,
                    "needs_rework": 0,
                },
                "agent_dispatch_counts": {
                    "coder": coder_count,
                    "tester": tester_count,
                    "reviewer": reviewer_count,
                    "documenter": documenter_count,
                },
                "total_token_estimate": token_estimate,
            })

    return results


# ── Analytics metrics endpoint (issue #648 / ANL-3) ──────────────────────────

# Per-token prices in USD (input, output). Update when model pricing changes.
MODEL_PRICE_MAP: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":              (0.80 / 1_000_000, 4.00 / 1_000_000),
    "claude-haiku-4-5-20251001":     (0.80 / 1_000_000, 4.00 / 1_000_000),
    "claude-sonnet-4-6":             (3.00 / 1_000_000, 15.00 / 1_000_000),
    "claude-opus-4-8":               (15.00 / 1_000_000, 75.00 / 1_000_000),
}


def _parse_iso_date(date_str: str, name: str, *, end_of_day: bool = False) -> datetime:
    """Parse a YYYY-MM-DD string into a UTC datetime, raising HTTP 400 on bad input."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, detail=f"Invalid {name!r} date {date_str!r} — expected YYYY-MM-DD")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def _analytics_parse_ts(ts: str | None) -> datetime | None:
    """Parse an ISO 8601 UTC timestamp (optionally Z-suffixed) or return None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _analytics_elapsed_minutes(start: str | None, end: str | None) -> float | None:
    """Minutes between two ISO timestamps, or None when either is missing/bad."""
    s = _analytics_parse_ts(start)
    e = _analytics_parse_ts(end)
    if s is None or e is None:
        return None
    return (e - s).total_seconds() / 60.0


_TESTER_REJECTED_CATEGORY = "TESTER_REJECTED"


def _count_tester_rejections(issue: dict) -> int:
    """Infer how many times the tester rejected a completed ticket.

    Sources, in priority order (the max wins so any single signal counts):
      * ``tester_attempt_count`` — N attempts means N-1 rejections (exact, when present)
      * ``status_history`` — entries flagged TESTER_REJECTED or a rejected status
      * ``category`` / ``failure_reason`` — a trailing rejection signal worth 1

    Returns 0 for a clean first-pass ticket.
    """
    attempt = int(issue.get("tester_attempt_count") or 0)
    rejections = max(attempt - 1, 0)

    history = issue.get("status_history") or []
    if isinstance(history, list):
        hist_rejects = 0
        for entry in history:
            if not isinstance(entry, dict):
                continue
            cat = str(entry.get("category") or "")
            status = str(entry.get("status") or "").lower()
            if cat == _TESTER_REJECTED_CATEGORY or "reject" in status:
                hist_rejects += 1
        rejections = max(rejections, hist_rejects)

    category = str(issue.get("category") or "")
    failure_reason = str(issue.get("failure_reason") or "").lower()
    if category == _TESTER_REJECTED_CATEGORY or "reject" in failure_reason:
        rejections = max(rejections, 1)

    return rejections


def _compute_analytics_metrics(project_root: Path,
                                since: str | None = None,
                                until: str | None = None,
                                sprint_filter: str | None = None) -> dict:
    """Aggregate delivery-health metrics from sprint state files and token_usage.

    Returns a dict with keys: first_pass_rate, rework_rate, avg_duration,
    throughput, cost. All numeric fields are 0 when no data is available.
    """
    today = datetime.now(tz=timezone.utc).date()

    since_dt = _parse_iso_date(since, "since") if since else None
    until_dt = _parse_iso_date(until, "until", end_of_day=True) if until else None

    sprints_dir = _commander_dir(project_root) / "sprints"

    coder_durations_by_size: dict[str, list[float]] = {}
    coder_all: list[float] = []
    tester_all: list[float] = []

    total_completed = 0
    first_pass_count = 0
    rework_count = 0
    rework_2plus = 0

    sprint_ticket_counts: list[int] = []
    sprint_lengths: list[float] = []

    # Token counts are sourced from the status files (model_name is joined from
    # the token_usage table below for pricing only).
    status_tokens_in = 0
    status_tokens_out = 0

    estimates_dir = _commander_dir(project_root) / "estimates"

    if sprints_dir.exists():
        for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            sprint_label_val = state_data.get("sprint_label", "")

            if sprint_filter and sprint_label_val != sprint_filter:
                continue

            start_ts_str = state_data.get("start_timestamp")
            if start_ts_str:
                try:
                    start_dt = datetime.fromisoformat(start_ts_str.rstrip("Z")).replace(tzinfo=timezone.utc)
                except Exception:
                    start_dt = None
            else:
                start_dt = None

            if start_dt:
                if since_dt and start_dt < since_dt:
                    continue
                if until_dt and start_dt > until_dt:
                    continue

            wall_clock_secs = float(state_data.get("wall_clock_secs") or 0.0)
            issues = state_data.get("issues", [])
            sprint_done = [i for i in issues if i.get("status") == "done"]

            sprint_ticket_counts.append(len(sprint_done))
            if wall_clock_secs > 0:
                sprint_lengths.append(wall_clock_secs / 86400.0)

            status_tokens_in += int(state_data.get("total_tokens_in") or 0)
            status_tokens_out += int(state_data.get("total_tokens_out") or 0)

            for issue in sprint_done:
                total_completed += 1
                rejections = _count_tester_rejections(issue)
                if rejections == 0:
                    first_pass_count += 1
                else:
                    rework_count += 1
                    if rejections >= 2:
                        rework_2plus += 1

                # Coder duration
                coder_start = issue.get("coder_started_at")
                coder_end = issue.get("coder_finished_at")
                if coder_start and coder_end:
                    try:
                        s = datetime.fromisoformat(coder_start.rstrip("Z")).replace(tzinfo=timezone.utc)
                        e = datetime.fromisoformat(coder_end.rstrip("Z")).replace(tzinfo=timezone.utc)
                        dur_min = (e - s).total_seconds() / 60.0
                        coder_all.append(dur_min)

                        issue_num = issue.get("number")
                        size = None
                        if issue_num and estimates_dir.exists():
                            est_file = estimates_dir / f"issue-{issue_num}.json"
                            if est_file.exists():
                                try:
                                    est = json.loads(est_file.read_text(encoding="utf-8"))
                                    size = est.get("size")
                                except Exception:
                                    pass
                        if size:
                            coder_durations_by_size.setdefault(size, []).append(dur_min)
                    except Exception:
                        pass

                # Tester duration
                tester_start = issue.get("tester_started_at")
                tester_end = issue.get("tester_finished_at")
                if tester_start and tester_end:
                    try:
                        s = datetime.fromisoformat(tester_start.rstrip("Z")).replace(tzinfo=timezone.utc)
                        e = datetime.fromisoformat(tester_end.rstrip("Z")).replace(tzinfo=timezone.utc)
                        tester_all.append((e - s).total_seconds() / 60.0)
                    except Exception:
                        pass

    first_pass_rate = first_pass_count / total_completed if total_completed else 0
    rework_rate_val = rework_count / total_completed if total_completed else 0

    avg_coder = sum(coder_all) / len(coder_all) if coder_all else 0
    avg_tester = sum(tester_all) / len(tester_all) if tester_all else 0
    coder_by_size = {
        sz: round(sum(vals) / len(vals), 2)
        for sz, vals in coder_durations_by_size.items()
        if vals
    }

    avg_tickets = sum(sprint_ticket_counts) / len(sprint_ticket_counts) if sprint_ticket_counts else 0
    avg_length = sum(sprint_lengths) / len(sprint_lengths) if sprint_lengths else 0

    # Cost: token counts come from the status files (status_tokens_in/out).
    # model_name is joined from the token_usage table for pricing only — we
    # derive a blended per-token price (exact when a single model is used) and
    # apply it to the status-file token totals.
    cost_by_role: dict[str, float] = {"coder": 0.0, "tester": 0.0, "estimator": 0.0}
    tu_in = tu_out = 0
    tu_cost_in = tu_cost_out = 0.0
    role_tokens: dict[str, int] = {"coder": 0, "tester": 0, "estimator": 0}
    try:
        rows = db.get_token_usage_by_agent_model()
        for row in rows:
            try:
                role = (row.get("agent_role") or "unknown").lower()
                model = (row.get("model_name") or "").lower()
                price_in, price_out = MODEL_PRICE_MAP.get(model, (0.0, 0.0))
                in_tokens = int(row.get("total_input", 0) or 0)
                out_tokens = int(row.get("total_output", 0) or 0)
                tu_in += in_tokens
                tu_out += out_tokens
                tu_cost_in += in_tokens * price_in
                tu_cost_out += out_tokens * price_out
                if role in role_tokens:
                    role_tokens[role] += in_tokens + out_tokens
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("token-usage cost: skipping row %r — %s", row, exc)
    except Exception as exc:
        logger.debug("token-usage cost: db query failed — %s", exc)

    blended_price_in = (tu_cost_in / tu_in) if tu_in else 0.0
    blended_price_out = (tu_cost_out / tu_out) if tu_out else 0.0
    cost_per_sprint_total = (status_tokens_in * blended_price_in +
                             status_tokens_out * blended_price_out)

    # Distribute the total cost across roles by their token_usage share.
    total_role_tokens = sum(role_tokens.values())
    if total_role_tokens > 0:
        for r in cost_by_role:
            cost_by_role[r] = cost_per_sprint_total * role_tokens[r] / total_role_tokens

    num_sprints = len(sprint_ticket_counts) if sprint_ticket_counts else 1
    cost_per_sprint_avg = cost_per_sprint_total / num_sprints
    cost_per_ticket_avg = cost_per_sprint_total / total_completed if total_completed else 0

    rework_cost_annotation = (
        round(cost_per_ticket_avg * 0.32, 4) if rework_count > 0 else 0.0
    )

    return {
        "first_pass_rate": {
            "rate": round(first_pass_rate, 4),
            "passed": first_pass_count,
            "total_completed": total_completed,
        },
        "rework_rate": {
            "rate": round(rework_rate_val, 4),
            "count": rework_count,
            "rework_2plus": rework_2plus,
            "total": total_completed,
        },
        "avg_duration": {
            "coder_minutes": round(avg_coder, 2),
            "tester_minutes": round(avg_tester, 2),
            "coder_by_size": coder_by_size,
        },
        "throughput": {
            "avg_tickets_per_sprint": round(avg_tickets, 2),
            "avg_sprint_length_days": round(avg_length, 4),
            "avg_sprint_length_minutes": round(avg_length * 1440, 2),
        },
        "cost": {
            "per_sprint": {
                "total": round(cost_per_sprint_avg, 4),
                "by_role": {k: round(v / num_sprints, 4) for k, v in cost_by_role.items()},
            },
            "per_ticket": {
                "avg": round(cost_per_ticket_avg, 4),
                "by_role": {
                    k: round(v / total_completed, 4) if total_completed else 0.0
                    for k, v in cost_by_role.items()
                },
                "rework_cost_annotation": rework_cost_annotation,
            },
        },
    }


@app.get("/api/projects/{slug}/analytics/metrics")
def get_project_analytics_metrics(slug: str, request: Request):
    """GET /api/projects/{slug}/analytics/metrics — ANL-3 delivery-health metrics.

    Query params: since=YYYY-MM-DD, until=YYYY-MM-DD, sprint=<sprint-label>.
    Returns 404 for unknown slugs; all numeric fields are 0 when no data.
    """
    repo = _resolve_project_slug(slug)
    project_root = _project_root_path(repo)

    since = request.query_params.get("since")
    until = request.query_params.get("until")
    sprint_filter = request.query_params.get("sprint")

    return _compute_analytics_metrics(project_root, since=since, until=until,
                                      sprint_filter=sprint_filter)


# ── Calibration analytics endpoint (issue #649) ──────────────────────────────

_CALIBRATION_SIZES = ("S", "M", "L", "XL")
_CALIBRATION_SIZE_SETTING_KEYS = {
    "S": "estimation_s_minutes",
    "M": "estimation_m_minutes",
    "L": "estimation_l_minutes",
    "XL": "estimation_xl_minutes",
}


def _compute_calibration(
    repo: str,
    since: str | None = None,
    until: str | None = None,
    sprint_filter: str | None = None,
) -> dict:
    """Aggregate estimated vs actual time per size tier for the given project.

    Returns by_size (keyed S/M/L/XL) and a flat points list.
    All sizes always present; sizes with no data have count=0 and null stats.
    """
    # Validate date params first so bad input returns 400 before any DB work.
    since_dt = _parse_iso_date(since, "since") if since else None
    until_dt = _parse_iso_date(until, "until", end_of_day=True) if until else None

    # Resolve configured_minutes from project settings (falls back to defaults).
    try:
        stored = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    except Exception:
        stored = {}
    effective = build_effective_response(stored)
    configured_minutes = {sz: effective[key] for sz, key in _CALIBRATION_SIZE_SETTING_KEYS.items()}

    # Skeleton result — all sizes always present regardless of data availability.
    by_size = {
        sz: {
            "configured_minutes": configured_minutes[sz],
            "count": 0,
            "min_minutes": None,
            "avg_minutes": None,
            "max_minutes": None,
        }
        for sz in _CALIBRATION_SIZES
    }
    points: list[dict] = []

    # Source is status + estimate files only — Neon is not queried (issue #718).
    project_root = _project_root_path(repo)
    sprints_dir = _commander_dir(project_root) / "sprints"
    estimates_dir = _commander_dir(project_root) / "estimates"

    buckets: dict[str, list[float]] = {sz: [] for sz in _CALIBRATION_SIZES}
    issue_rows: list[tuple] = []

    if sprints_dir.exists():
        for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
            try:
                state_data = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            if sprint_filter and state_data.get("sprint_label", "") != sprint_filter:
                continue

            start_dt = _analytics_parse_ts(state_data.get("start_timestamp"))
            if start_dt:
                if since_dt and start_dt < since_dt:
                    continue
                if until_dt and start_dt > until_dt:
                    continue

            for issue in state_data.get("issues", []):
                # Count any lifecycle "done-equivalent" status, not just literal
                # "done": the sprint-lifecycle redesign settles passed tickets as
                # uat/merged/passed, so a "done"-only filter silently blanked the
                # est-vs-actual plot for newer sprints (hotfix #3).
                if issue.get("status") not in ("done", "uat", "merged", "passed"):
                    continue

                issue_num = issue.get("number")
                size = None
                if issue_num is not None and estimates_dir.exists():
                    est_file = estimates_dir / f"issue-{issue_num}.json"
                    if est_file.exists():
                        try:
                            est = json.loads(est_file.read_text(encoding="utf-8"))
                            size = est.get("size")
                        except Exception:
                            size = None
                if size not in buckets:
                    continue

                coder_min = _analytics_elapsed_minutes(
                    issue.get("coder_started_at"), issue.get("coder_finished_at"))
                tester_min = _analytics_elapsed_minutes(
                    issue.get("tester_started_at"), issue.get("tester_finished_at"))
                if coder_min is None and tester_min is None:
                    continue

                actual_minutes = (coder_min or 0.0) + (tester_min or 0.0)
                buckets[size].append(actual_minutes)
                issue_rows.append((issue_num, size, actual_minutes))

    # Compute aggregates.
    for sz in _CALIBRATION_SIZES:
        vals = buckets[sz]
        if vals:
            by_size[sz]["count"] = len(vals)
            by_size[sz]["min_minutes"] = round(min(vals), 2)
            by_size[sz]["avg_minutes"] = round(sum(vals) / len(vals), 2)
            by_size[sz]["max_minutes"] = round(max(vals), 2)

    # Build points list.
    for issue_number, estimated_size, actual_minutes in issue_rows:
        points.append({
            "issue_number": issue_number,
            "estimated_size": estimated_size,
            "estimated_minutes": configured_minutes[estimated_size],
            "actual_minutes": round(actual_minutes, 2),
        })

    return {"by_size": by_size, "points": points}


@app.get("/api/projects/{slug}/analytics/calibration")
def get_project_analytics_calibration(slug: str, request: Request):
    """GET /api/projects/{slug}/analytics/calibration — estimated vs actual per size tier.

    Query params: since=YYYY-MM-DD, until=YYYY-MM-DD, sprint=<sprint-label>.
    Returns 404 for unknown slugs; sizes with no data have count=0 and null stats.
    """
    repo = _resolve_project_slug(slug)

    since = request.query_params.get("since")
    until = request.query_params.get("until")
    sprint_filter = request.query_params.get("sprint")

    return _compute_calibration(repo, since=since, until=until, sprint_filter=sprint_filter)


# ── Daily report endpoint (issue #478) ───────────────────────────────────────

@app.post("/api/reports/daily")
async def generate_daily_report(request: Request):
    """Trigger daily summary report generation.

    Optional JSON body: {"date": "YYYY-MM-DD"} (default: today).
    Writes .commander/reports/YYYY-MM-DD.md and returns the file path.
    """
    target_date_str: Optional[str] = None
    try:
        body = await request.json()
        if isinstance(body, dict):
            target_date_str = body.get("date")
    except Exception:
        pass

    cmd = [sys.executable, str(Path(__file__).parent.parent.parent / "scripts" / "generate_daily_report.py")]
    if target_date_str:
        cmd.extend(["--date", target_date_str])

    env = os.environ.copy()
    if "DB_PATH" not in env or not env["DB_PATH"].strip():
        db_candidate = Path(__file__).parent / "commander.db"
        if db_candidate.exists():
            env["DB_PATH"] = str(db_candidate)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path(__file__).parent.parent.parent),
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, detail="Report generation timed out after 30 s")

    if result.returncode != 0:
        raise HTTPException(
            500,
            detail=f"Report generation failed: {result.stderr.strip() or result.stdout.strip()}",
        )

    out_line = result.stdout.strip()
    report_path = out_line.removeprefix("Report written to ").strip()
    return {"ok": True, "path": report_path, "message": out_line}


@app.get("/api/sprints/{sprint_label}/finish-card")
def get_sprint_finish_card(sprint_label: str, project: str):
    """Return data for the floating finish-report card above a sprint pane.

    Always returns HTTP 200. Clients must check the ``state`` field in the
    response body — do NOT rely on HTTP 404 to detect a missing sprint.
    (Before issue #671 this endpoint returned 404 when no state file existed;
    it now returns HTTP 200 with state="no_data" instead.)

    For running sprints: state="running", in_flight_count, pending_count,
    done_count, wall_clock_secs, started_at.

    For finished sprints: state in (completed|has_rework|cancelled),
    done_count, failed_count, skipped_count, rework_count, wall_clock_secs,
    ended_at, summary_issue_url, summary_issue_num.

    When no sprint state file exists on disk: state="no_data", sprint_label,
    sprint_number only (HTTP 200, not 404).
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    fc_m = re.search(r"(\d+)", sprint_label)
    fc_n = fc_m.group(1) if fc_m else sprint_label
    sprint_number = int(fc_n) if fc_n.isdigit() else None

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    if _is_sprint_running(project_root, sprint_label):
        status_key = (project, sprint_label)
        status_data = _sprint_statuses.get(status_key, {})
        live_issues = status_data.get("issues", [])
        in_flight = sum(1 for i in live_issues if i.get("status") == "in-progress")
        pending = sum(1 for i in live_issues if i.get("status") == "pending")
        done = sum(1 for i in live_issues if i.get("status") == "done")
        started_at_str: Optional[str] = status_data.get("start_timestamp")
        wall_clock_secs = 0.0
        if started_at_str:
            try:
                started_dt = datetime.fromisoformat(started_at_str.rstrip("Z"))
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                wall_clock_secs = (datetime.now(timezone.utc) - started_dt).total_seconds()
            except Exception:
                pass
        return {
            "sprint_label":    sprint_label,
            "sprint_number":   sprint_number,
            "state":           "running",
            "in_flight_count": in_flight,
            "pending_count":   pending,
            "done_count":      done,
            "wall_clock_secs": wall_clock_secs,
            "started_at":      started_at_str,
        }

    if not _sprint_has_own_run_outcome(project_root, sprint_label):
        return {
            "sprint_label":  sprint_label,
            "sprint_number": sprint_number,
            "state":         "no_data",
        }

    fc_json_path = _sprint_json_path(project_root, sprint_label)
    fc_sprint_json = _sprint_json_read(fc_json_path)
    fc_is_cancelled: bool = fc_sprint_json.get("status") in ("cancelled", "needs_rework")

    state_path = commander / "sprints" / f"sprint-{fc_n}-state.json"
    if not state_path.exists():
        if fc_is_cancelled:
            return {
                "sprint_label":      sprint_label,
                "sprint_number":     sprint_number,
                "state":             "cancelled",
                "done_count":        0,
                "failed_count":      0,
                "skipped_count":     0,
                "rework_count":      0,
                "wall_clock_secs":   0.0,
                "ended_at":          None,
                "summary_issue_url": None,
                "summary_issue_num": None,
            }
        return {
            "sprint_label":  sprint_label,
            "sprint_number": sprint_number,
            "state":         "no_data",
        }

    try:
        fc_state_data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(500, detail=str(e))

    def _fc_parse_iso(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.rstrip("Z"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return None

    sprints_dir = commander / "sprints"
    fc_sprint_status: Optional[str] = None
    for sf in sorted(sprints_dir.glob(f"sprint-{fc_n}-summary-*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = _parse_summary_file(sf)
            raw = (meta.get("status") or "").lower()
            if raw in ("complete", "completed"):
                fc_sprint_status = "completed"
            elif raw in ("stopped", "failed", "cancelled"):
                fc_sprint_status = "stopped"
                if raw == "cancelled":
                    fc_is_cancelled = True
        except Exception:
            pass
        break

    fc_issues_raw = fc_state_data.get("issues", [])
    if fc_sprint_status is None and fc_issues_raw:
        has_pending = any(i.get("status") == "pending" for i in fc_issues_raw)
        has_failed = any(i.get("agent_status") == "failed" or i.get("failure_reason") for i in fc_issues_raw)
        if not has_pending:
            fc_sprint_status = "stopped" if has_failed else "completed"

    done_count    = sum(1 for i in fc_issues_raw if i.get("status") == "done")
    failed_count  = sum(1 for i in fc_issues_raw if i.get("agent_status") == "failed" or i.get("failure_reason"))
    skipped_count = sum(
        1 for i in fc_issues_raw
        if i.get("status") == "skipped" and not (i.get("agent_status") == "failed" or i.get("failure_reason"))
    )

    fc_ended_ts: Optional[float] = None
    for iss in fc_issues_raw:
        end_ts = _fc_parse_iso(iss.get("tester_finished_at")) or _fc_parse_iso(iss.get("status_changed_at"))
        if end_ts and (fc_ended_ts is None or end_ts > fc_ended_ts):
            fc_ended_ts = end_ts
    ended_at = (
        datetime.fromtimestamp(fc_ended_ts, tz=timezone.utc).strftime("%H:%M")
        if fc_ended_ts else None
    )

    if fc_is_cancelled:
        card_state = "cancelled"
        rework_count = 0
    elif _has_rework_tickets(sprint_label, project):
        card_state = "has_rework"
        rework_count = _count_rework_tickets(sprint_label, project)
    else:
        card_state = "completed"
        rework_count = 0

    summary_issue_url: Optional[str] = fc_state_data.get("summary_issue_url")
    summary_issue_num: Optional[int] = None
    if summary_issue_url:
        m_num = re.search(r"/issues/(\d+)", summary_issue_url)
        if m_num:
            summary_issue_num = int(m_num.group(1))

    return {
        "sprint_label":      sprint_label,
        "sprint_number":     sprint_number,
        "state":             card_state,
        "lifecycle":         db.canonical_lifecycle(card_state),
        "end_reason":        fc_sprint_json.get("end_reason"),
        "done_count":        done_count,
        "failed_count":      failed_count,
        "skipped_count":     skipped_count,
        "rework_count":      rework_count,
        "wall_clock_secs":   fc_state_data.get("wall_clock_secs", 0.0),
        "ended_at":          ended_at,
        "summary_issue_url": summary_issue_url,
        "summary_issue_num": summary_issue_num,
    }


@app.get("/api/sprints/{sprint_label}/branch-status")
def get_sprint_branch_status(sprint_label: str, project: str):
    """Check if the sprint branch exists on GitHub.

    Uses gh CLI with a 2-second hard timeout; returns {exists, branch}.
    If the CLI times out or fails, returns exists=False so the UI shows
    the amber fallback without blocking page load.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        repo = github_client.get_repo_for_operation(project)
    except Exception:
        return {"exists": False, "branch": f"sprint/{sprint_label}"}

    branch_name = f"sprint/{sprint_label}"
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/git/ref/heads/{branch_name}"],
            capture_output=True, text=True, timeout=2,
        )
        exists = result.returncode == 0
    except Exception:
        exists = False

    # Check for open PR from sprint branch → develop or master
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    pr_title: Optional[str] = None
    try:
        pr_result = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", branch_name,
             "--state", "open", "--json", "number,url,title", "--limit", "1"],
            capture_output=True, text=True, timeout=3,
        )
        if pr_result.returncode == 0 and pr_result.stdout.strip():
            prs = json.loads(pr_result.stdout)
            if prs:
                pr_url = prs[0].get("url")
                pr_number = prs[0].get("number")
                pr_title = prs[0].get("title")
    except Exception as pr_err:
        _slog.warn(
            "sprint_pr_lookup_failed",
            f"PR lookup for {branch_name!r} failed: {pr_err}",
            branch=branch_name,
            error=str(pr_err),
        )

    return {"exists": exists, "branch": branch_name,
            "pr_url": pr_url, "pr_number": pr_number, "pr_title": pr_title}


class SprintRerunBody(BaseModel):
    confirm: bool


class SprintRerunV2Body(BaseModel):
    # Empty list = re-run all non-UAT tickets (legacy behaviour). When the
    # per-ticket modal sends a selection, only those tickets move to the child
    # sprint. `confirm` is accepted-and-ignored for backward compatibility with
    # the old SprintRerunBody callers.
    ticket_numbers: list[int] = []
    auto_run: bool = True
    confirm: bool | None = None


def _ticket_rerun_category(labels: set[str]) -> str:
    """Map a ticket's current labels to a rerun category string."""
    if labels & {"UAT", "UAT-approved"}:
        return "UAT"
    if "SIT" in labels:
        return "SIT"
    if "needs-rework" in labels or "need-rework" in labels:
        return "needs-rework"
    return "queued"


@app.get("/api/sprints/{sprint_label}/rerun/preview")
def rerun_sprint_preview(sprint_label: str, project: str):
    """Return per-ticket rerun preview counts without executing anything (legacy).

    Response schema:
      { new_label, redispatch_count, tester_count, skip_count, by_ticket: [
          { issue_num, issue_title, action }  # action: dispatch_coder|dispatch_tester|skip
        ]
      }
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

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

    existing_label_names = {lbl["name"] for lbl in github_client.list_labels(repo_name=project)}
    new_label = _next_sprint_sublabel(sprint_label, existing_label_names)

    return {
        "new_label": new_label,
        "redispatch_count": redispatch_count,
        "tester_count": tester_count,
        "skip_count": skip_count,
        "by_ticket": by_ticket,
    }


@app.get("/api/sprints/{sprint_label}/rerun-preview")
def rerun_sprint_preview_v2(sprint_label: str, project: str):
    """Return per-ticket rerun preview with checkbox-ready ticket list.

    Response schema:
      {
        suggested_versioned_label: str,
        tickets: [{ number, title, category, checked }]
          # category: UAT | SIT | needs-rework | queued
          # checked: true for non-UAT tickets (default selection)
      }
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    existing_label_names = {lbl["name"] for lbl in github_client.list_labels(repo_name=project)}
    suggested_versioned_label = _next_sprint_sublabel(sprint_label, existing_label_names)

    _NON_WORK_LABELS_RR = {"sprint-summary", "docs", "documentation"}
    tickets = []
    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if current_labels & _NON_WORK_LABELS_RR:
            continue  # skip sprint-summary / docs tickets
        category = _ticket_rerun_category(current_labels)
        tickets.append({
            "number": iss["number"],
            "title": iss["title"],
            "category": category,
            "checked": category != "UAT",
        })

    return {
        "suggested_versioned_label": suggested_versioned_label,
        "tickets": tickets,
    }


def _await_rerun_relabel(project: str, sub_label: str, expected: list[int],
                         timeout_s: float = 15.0, interval_s: float = 2.0) -> set[int]:
    """Wait until every re-run ticket actually carries ``sub_label``.

    The per-issue label edits succeed synchronously, but GitHub's issue *list* is
    eventually consistent and the local issues mirror (which both the board pane
    and the sprint dispatch read) only reflects the move after a re-sync. Without
    this, the running pane / sprint_manager can start on a partial set (issue:
    re-run started before all tickets had moved to sprint-N.x). Re-syncs the
    mirror each round and polls until all expected numbers appear under
    ``sub_label`` (or the timeout elapses). Returns the confirmed-present set.
    """
    want = set(expected)
    if not want:
        return set()
    deadline = time.monotonic() + timeout_s
    present: set[int] = set()
    while True:
        try:
            github_events_sync.sync_issues_mirror(project)
        except Exception:
            pass
        github_client.invalidate("open_issues_body:")
        github_client.invalidate("open_issues:")
        try:
            present = want & {i["number"] for i in _get_sprint_issues(project, sub_label)}
        except Exception:
            present = set()
        if want.issubset(present) or time.monotonic() >= deadline:
            return present
        time.sleep(interval_s)


@app.post("/api/sprints/{sprint_label}/rerun")
def rerun_sprint(sprint_label: str, project: str, body: SprintRerunV2Body):
    """Create an independent sub-sprint from selected non-UAT tickets.

    Moves the chosen non-UAT tickets carrying sprint_label to a new versioned
    sub-sprint label (sprint-N.1, sprint-N.2, …), creates plan.json with a parent
    reference, and (when auto_run) dispatches sprint_manager for the sub-sprint.
    The original sprint label, plan.json, branch, and PR are NOT modified.

    Body: { ticket_numbers?: int[], auto_run?: bool }
      - ticket_numbers: which non-UAT tickets to move; empty = all of them.
      - auto_run: dispatch immediately (default true); false leaves them queued
        in the child sprint for a manual run.
    Response: { sub_label, noop, dispatch_count, decisions, moved, [errors] }
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    project_root = _project_root_path(project)

    if _is_sprint_running(project_root, sprint_label):
        raise HTTPException(409, detail=f"Sprint {sprint_label} is currently running")

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    _NON_WORK_LABELS_RR = {"sprint-summary", "docs", "documentation"}
    decisions: list[dict] = []
    # issue_num -> set of its current label names, so the move loop can strip
    # stale session-state labels without re-fetching (issue #811).
    issue_labels: dict[int, set[str]] = {}
    for iss in sprint_issues:
        current_labels = {lbl["name"] for lbl in iss.get("labels", [])}
        if current_labels & _NON_WORK_LABELS_RR:
            continue
        issue_labels[iss["number"]] = current_labels
        action, _ = _rerun_policy(current_labels)
        decisions.append({
            "issue_num": iss["number"],
            "issue_title": iss["title"],
            "action": action,
        })

    to_move = [d for d in decisions if d["action"] != "skip"]

    # Honor an explicit ticket selection from the per-ticket modal; empty means all.
    if body.ticket_numbers:
        selected = set(body.ticket_numbers)
        to_move = [d for d in to_move if d["issue_num"] in selected]

    if not to_move:
        return {
            "noop": True,
            "dispatch_count": 0,
            "message": "No issues require re-dispatch; all are UAT or UAT-approved.",
            "decisions": decisions,
        }

    # Compute sub-label
    existing_label_names = {lbl["name"] for lbl in github_client.list_labels(repo_name=project)}
    sub_label = _next_sprint_sublabel(sprint_label, existing_label_names)

    # Create the sub-label on GitHub with the same color as the parent sprint label
    parent_color = github_client.get_label_color(sprint_label, repo_name=project) or "0075ca"
    github_client.create_label(
        sub_label, parent_color,
        description=f"Re-run of {sprint_label}",
        repo_name=project,
    )

    errors: list[str] = []
    moved: list[int] = []
    # Audit trail of stale session-state labels stripped on this re-run
    # (issue #811): [{issue_num, removed_labels}], one entry per ticket that
    # actually carried stale labels.
    stripped_labels: list[dict] = []

    for d in to_move:
        issue_num = d["issue_num"]
        stale = _stale_session_labels(issue_labels.get(issue_num, set()))
        try:
            github_client.update_labels(
                issue_num,
                add=[sub_label],
                remove=[sprint_label, *stale],
                repo_name=project,
            )
        except subprocess.CalledProcessError as e:
            errors.append(f"#{issue_num} label swap failed: {e.stderr.strip() if e.stderr else str(e)}")
            continue
        moved.append(issue_num)
        if stale:
            stripped_labels.append({"issue_num": issue_num, "removed_labels": stale})

    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)

    # Rotate each moved ticket's per-issue log so the new run starts clean
    # (logs are keyed by issue number, not sprint, so a re-run would otherwise
    # show the previous run's lines + elapsed). The old log is kept as .prev so
    # the prior run stays recoverable.
    logs_dir = commander / "logs"
    rotated_logs: list[int] = []
    for issue_num in moved:
        old_log = logs_dir / f"sprint-issue-{issue_num}.log"
        if old_log.exists():
            try:
                old_log.replace(old_log.parent / f"{old_log.name}.prev")
                rotated_logs.append(issue_num)
            except OSError:
                pass

    # Create plan.json for sub-label; original plan.json is untouched.
    # New sprints start as `draft` under the unified lifecycle (legacy files
    # may still carry "planning" — readers accept both).
    _write_plan_json(project_root, sub_label, {
        "state": "draft",
        "tickets": moved,
        "parent": sprint_label,
    })

    # Mark parent state file with rerun_into so the board knows where the re-run went
    parent_state_path = sprints_dir / f"{sprint_label}-state.json"
    if parent_state_path.exists():
        try:
            state_data = json.loads(parent_state_path.read_text(encoding="utf-8"))
            state_data["rerun_into"] = sub_label
            parent_state_path.write_text(json.dumps(state_data), encoding="utf-8")
        except Exception:
            pass

    github_client.invalidate("open_issues_body:")
    github_client.invalidate("open_issues:")
    github_client.invalidate("issues:")
    github_client.invalidate("sprint_labels:")

    # Block until GitHub + the local mirror both reflect every moved ticket under
    # the new sub-label, so the running pane and the dispatch never start on a
    # partial set (issue: re-run began before all tickets had moved to N.x).
    confirmed_moved = _await_rerun_relabel(project, sub_label, moved)
    all_moved = set(moved).issubset(confirmed_moved)

    # Scoped activity-log event for the sprint target (issue #721). The
    # stripped_labels payload is the timestamped audit trail of which stale
    # session-state labels were removed from which items on re-run (issue #811).
    _emit_dashboard_event(
        project=project,
        type="sprint_rerun",
        target=sub_label,
        detail={
            "sprint_id": sprint_label,
            "sub_label": sub_label,
            "stripped_labels": stripped_labels,
        },
        action_id=str(uuid.uuid4()),
    )

    if stripped_labels:
        print(
            f"[rerun] {sprint_label} → {sub_label}: stripped stale session "
            f"labels from {len(stripped_labels)} item(s): "
            + ", ".join(
                f"#{e['issue_num']}={'+'.join(e['removed_labels'])}"
                for e in stripped_labels
            ),
            flush=True,
        )

    result: dict = {
        "noop": False,
        "sub_label": sub_label,
        "parent_label": sprint_label,
        "dispatch_count": len(moved),
        "moved": moved,
        "moved_confirmed": sorted(confirmed_moved),
        "all_moved": all_moved,
        "decisions": decisions,
        "stripped_labels": stripped_labels,
        "rotated_logs": rotated_logs,
    }
    if errors:
        result["errors"] = errors

    # auto_run=false: leave the selected tickets queued in the child sprint
    # (plan state stays "draft") for a manual run later.
    if not body.auto_run:
        result["queued"] = True
        return result

    # Auto-run: dispatch sprint_manager for the sub-sprint
    if not SPRINT_MANAGER_PATH.exists():
        return result

    log_dir = commander / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_id = str(uuid.uuid4())
    coder_path = _coder_clone_path(project_root)
    pid_path = sprints_dir / f"{sub_label}-pid"
    pending_path = sprints_dir / f"{sub_label}-pid.pending"
    run_log_path = log_dir / f"sprint-run-{sub_label}-{ts}.log"

    try:
        fd = os.open(str(pending_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.write(fd, b"0")
        os.close(fd)
    except FileExistsError:
        raise HTTPException(409, detail=f"Sprint {sub_label} is already running on {project}")

    try:
        _plan_json_set_state(project_root, sub_label, "running",
                             started_at=datetime.now(timezone.utc).isoformat(),
                             parent=sprint_label)
    except Exception:
        pass

    stripped_env = _build_sprint_subprocess_env()
    goal_path = _sprint_goal_path(project_root, sprint_label)
    if not goal_path.exists():
        goal_path = _sprint_goal_path(project_root, sub_label)
    if goal_path.exists():
        stripped_env["SPRINT_GOAL"] = goal_path.read_text(encoding="utf-8").strip()

    run_log_fh = open(run_log_path, "w")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SPRINT_MANAGER_PATH), sub_label,
             "--alert-mode", _ALERT_MODES],
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

    result["run_id"] = run_id
    result["pid"] = proc.pid
    result["log"] = str(run_log_path)
    return result


@app.delete("/api/sprints/{sprint_label}")
def delete_sprint(sprint_label: str, project: str):
    """Remove a sprint label from GitHub and unlabel all attached tickets.

    Does NOT delete the issues themselves — only the sprint label is removed.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")

    if _is_sprint_running(_project_root_path(project), sprint_label):
        return JSONResponse(
            status_code=409,
            content={"error": "Sprint is currently running.", "suggestion": "Cancel the sprint first, then delete."},
        )

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    from routers import sprint_history_service  # snapshot history BEFORE label-strip (#805)
    sprint_history_service.record_deleted_sprint(sprint_label, project, sprint_issues, commander)
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

    for _ck in ("open_issues_body:", "open_issues:", "issues:", "sprints:"):
        github_client.invalidate(_ck)

    result: dict = {"deleted_label": sprint_label, "unlabelled_count": unlabelled_count, **({"errors": errors} if errors else {})}
    return result


# ── Finish Sprint / Merge Sprint endpoints (issue #511, lifecycle P2) ─────────

_FINISH_SPRINT_STATUS_LABELS = frozenset({
    "backlog", "in-progress", "SIT", "UAT", "UAT-approved",
    "needs-rework", "need-rework", "blocked",
})


def _sprint_label_base(label: str) -> str:
    m = re.match(r"^(sprint-\d+)", label)
    return m.group(1) if m else label


def _sprint_label_sub_index(label: str) -> float:
    m = re.match(r"^sprint-\d+\.(\d+)$", label)
    return float(m.group(1)) if m else 0.0


def _is_child_sprint_label(label: str) -> bool:
    return bool(re.match(r"^sprint-\d+\.\d+", label))


def _sprint_branch_name(label: str) -> str:
    return f"sprint/{label}"


def _child_sprint_labels_from_plans(project_root: Path, base_label: str) -> list[str]:
    """Return child sprint labels under base_label, sorted by sub-index."""
    sprints_dir = _commander_dir(project_root) / "sprints"
    if not sprints_dir.is_dir():
        return []
    m = re.match(r"^sprint-(\d+)$", base_label)
    if not m:
        return []
    n = m.group(1)
    children: list[str] = []
    for path in sprints_dir.glob(f"sprint-{n}.*-plan.json"):
        lbl = path.name.replace("-plan.json", "")
        if _is_child_sprint_label(lbl):
            children.append(lbl)
    return sorted(children, key=_sprint_label_sub_index)


def _gh_branch_exists(repo: str, branch: str) -> bool:
    try:
        from urllib.parse import quote
        ref = quote(branch, safe="")
        res = subprocess.run(
            ["gh", "api", f"repos/{repo}/branches/{ref}", "--jq", ".name"],
            capture_output=True, text=True, timeout=15,
        )
        return res.returncode == 0
    except Exception:
        return False


def _gh_merge_branch_via_pr(
    repo: str,
    head: str,
    base: str,
    title: str,
    delete_branch: bool = True,
) -> tuple[bool, str]:
    """Create (or reuse) a PR head→base and merge it. Returns (ok, detail)."""
    pr_url: Optional[str] = None
    try:
        pr_res = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", head, "--base", base,
             "--state", "open", "--json", "url", "--limit", "1"],
            capture_output=True, text=True, timeout=30,
        )
        if pr_res.returncode == 0 and pr_res.stdout.strip():
            prs = json.loads(pr_res.stdout)
            if prs:
                pr_url = prs[0].get("url")
        if not pr_url:
            create = subprocess.run(
                ["gh", "pr", "create", "--repo", repo, "--base", base, "--head", head,
                 "--title", title, "--body", f"Merge Sprint: `{head}` → `{base}`."],
                capture_output=True, text=True, timeout=60,
            )
            if create.returncode != 0:
                stderr = create.stderr.strip()
                m = re.search(r"https://github\.com/\S+", stderr)
                if m and ("already exists" in stderr or "already have" in stderr.lower()):
                    pr_url = m.group(0)
                else:
                    return False, stderr or "PR create failed"
            else:
                pr_url = create.stdout.strip()
        merge_args = ["gh", "pr", "merge", pr_url, "--repo", repo, "--merge"]
        if delete_branch:
            merge_args.append("--delete-branch")
        merge_res = subprocess.run(merge_args, capture_output=True, text=True, timeout=120)
        if merge_res.returncode != 0:
            return False, merge_res.stderr.strip() or "PR merge failed"
        return True, pr_url or f"{head} → {base}"
    except Exception as exc:
        return False, str(exc)


def _branch_has_unmerged_commits(repo: str, head: str, base: str) -> bool:
    """True when head has commits not reachable from base."""
    if not _gh_branch_exists(repo, head):
        return False
    try:
        from urllib.parse import quote
        ref = quote(f"{base}...{head}", safe="")
        res = subprocess.run(
            ["gh", "api", f"repos/{repo}/compare/{ref}", "--jq", ".ahead_by"],
            capture_output=True, text=True, timeout=30,
        )
        if res.returncode != 0:
            return True
        return int((res.stdout or "0").strip() or "0") > 0
    except Exception:
        return True


def _merge_steps_for_sprint_chain(project_root: Path, repo: str, base_label: str) -> list[dict]:
    """Ordered merge steps: each child → base, then base → develop."""
    steps: list[dict] = []
    base_branch = _sprint_branch_name(base_label)
    for child_label in _child_sprint_labels_from_plans(project_root, base_label):
        child_branch = _sprint_branch_name(child_label)
        if _branch_has_unmerged_commits(repo, child_branch, base_branch):
            steps.append({
                "kind": "merge",
                "head": child_branch,
                "base": base_branch,
                "label": f"{child_label} → {base_label}",
                "delete_branch": True,
                "title": f"Merge Sprint: {child_label} → {base_label}",
            })
    if _branch_has_unmerged_commits(repo, base_branch, "develop"):
        steps.append({
            "kind": "merge",
            "head": base_branch,
            "base": "develop",
            "label": f"{base_label} → develop",
            "delete_branch": False,
            "title": f"Merge Sprint: {base_label} → develop",
        })
    return steps


def _sprint_merge_chain_pending(project_root: Path, repo: str, base_label: str) -> list[str]:
    """Branches that still need merging before bulk-complete settlement."""
    pending: list[str] = []
    base_branch = _sprint_branch_name(base_label)
    for child_label in _child_sprint_labels_from_plans(project_root, base_label):
        child_branch = _sprint_branch_name(child_label)
        if _branch_has_unmerged_commits(repo, child_branch, base_branch):
            pending.append(f"{child_branch} → {base_branch}")
    if _branch_has_unmerged_commits(repo, base_branch, "develop"):
        pending.append(f"{base_branch} → develop")
    return pending


def _merge_sprint_branch_chain(repo: str, base_label: str) -> list[str]:
    """Merge leftover child branches into base, then base → develop. Returns errors."""
    errors: list[str] = []
    project_root = _project_root_path(repo)
    for step in _merge_steps_for_sprint_chain(project_root, repo, base_label):
        ok, detail = _gh_merge_branch_via_pr(
            repo, step["head"], step["base"],
            title=step["title"],
            delete_branch=step["delete_branch"],
        )
        if not ok:
            errors.append(f"{step['head']} → {step['base']}: {detail}")
    return errors


def _next_sprint_number(sprint_label: str) -> int:
    """Return the next sprint number for a given sprint label (sprint-N → N+1)."""
    m = re.match(r"^sprint-(\d+)(?:\.\d+)?$", sprint_label)
    if not m:
        raise ValueError(f"Invalid sprint label: {sprint_label!r}")
    return int(m.group(1)) + 1


@app.get("/api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview")
def get_sprint_finish_preview(owner: str, repo_name: str, label: str):
    """Return preview data for the Merge Sprint dialog.

    Returns: {
      all_tickets: [{number, title, category}],
      uat_tickets: [{number, title}],
      non_uat_tickets: [{number, title, status}],
      sprint_pr: {url, number, title} | null,
      merge_branches: [{head, base}],
      base_label: str,
      next_sprint_label: str,
      next_sprint_exists: bool,
      conflict_error: str | null,
    }
    """
    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    base_label = _sprint_label_base(label)
    project_root = _project_root_path(repo)

    next_num = _next_sprint_number(base_label)
    next_sprint_label = f"sprint-{next_num}"

    try:
        existing_sprints = github_client.list_sprints(repo_name=repo)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    next_sprint_exists = next_num in existing_sprints
    conflict_error: str | None = None

    try:
        sprint_issues = _get_sprint_issues(repo, base_label)
        seen_nums: set[int] = {iss["number"] for iss in sprint_issues}
        for child_label in _child_sprint_labels_from_plans(project_root, base_label):
            try:
                for iss in _get_sprint_issues(repo, child_label):
                    if iss["number"] not in seen_nums:
                        sprint_issues.append(iss)
                        seen_nums.add(iss["number"])
            except subprocess.CalledProcessError:
                pass
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    # Open PRs for child sprint branches targeting the base branch
    sprint_pr: Optional[dict] = None
    merge_branches: list[dict] = []
    base_branch = _sprint_branch_name(base_label)
    for child_label in _child_sprint_labels_from_plans(project_root, base_label):
        child_branch = _sprint_branch_name(child_label)
        if _gh_branch_exists(repo, child_branch):
            merge_branches.append({"head": child_branch, "base": base_branch, "label": child_label})
    if _gh_branch_exists(repo, base_branch):
        merge_branches.append({"head": base_branch, "base": "develop", "label": base_label})
    try:
        branch_name = _sprint_branch_name(label)
        pr_res = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", branch_name,
             "--state", "open", "--json", "number,url,title,baseRefName", "--limit", "1"],
            capture_output=True, text=True, timeout=4,
        )
        if pr_res.returncode == 0 and pr_res.stdout.strip():
            prs = json.loads(pr_res.stdout)
            if prs:
                sprint_pr = {
                    "url": prs[0].get("url"),
                    "number": prs[0].get("number"),
                    "title": prs[0].get("title"),
                    "base": prs[0].get("baseRefName"),
                }
    except Exception:
        pass

    _NON_WORK_LABELS_FP = {"sprint-summary", "docs", "documentation"}
    all_tickets = []
    uat_tickets = []
    non_uat_tickets = []
    for iss in sprint_issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        number = iss["number"]
        title = iss.get("title", "")
        if _NON_WORK_LABELS_FP & label_names:
            all_tickets.append({"number": number, "title": title, "category": "sprint-summary"})
            continue
        if "UAT" in label_names:
            all_tickets.append({"number": number, "title": title, "category": "UAT"})
            uat_tickets.append({"number": number, "title": title})
        else:
            status = next(
                (lbl for lbl in sorted(label_names) if lbl in _FINISH_SPRINT_STATUS_LABELS and lbl != "UAT"),
                "queued",
            )
            all_tickets.append({"number": number, "title": title, "category": status})
            non_uat_tickets.append({"number": number, "title": title, "status": status})

    return {
        "all_tickets": all_tickets,
        "uat_tickets": uat_tickets,
        "non_uat_tickets": non_uat_tickets,
        "sprint_pr": sprint_pr,
        "merge_branches": merge_branches,
        "base_label": base_label,
        "next_sprint_label": next_sprint_label,
        "next_sprint_exists": next_sprint_exists,
        "conflict_error": conflict_error,
    }


class FinishSprintBody(BaseModel):
    confirmed: bool
    move_non_uat_to: str = ""
    selected_ticket_numbers: list[int] = []
    merge_pr: bool = False
    sprint_pr_url: Optional[str] = None


@app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/finish")
async def finish_sprint(owner: str, repo_name: str, label: str, body: FinishSprintBody):
    """Merge Sprint: merge child branches → base → develop, close issues, keep labels.

    Single human UAT sign-off — no per-ticket gate, no label stripping.
    """
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")

    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    project_root = _project_root_path(repo)
    base_label = _sprint_label_base(label)
    child_labels = _child_sprint_labels_from_plans(project_root, base_label)
    all_labels = [base_label, *child_labels]

    for lbl in all_labels:
        if _is_sprint_running(project_root, lbl):
            raise HTTPException(
                409,
                detail=f"Sprint {lbl} is currently running — merge after the run completes",
            )

    try:
        sprint_issues = _get_sprint_issues(repo, base_label)
        seen_nums: set[int] = {iss["number"] for iss in sprint_issues}
        for child_label in child_labels:
            try:
                for iss in _get_sprint_issues(repo, child_label):
                    if iss["number"] not in seen_nums:
                        sprint_issues.append(iss)
                        seen_nums.add(iss["number"])
            except subprocess.CalledProcessError:
                pass
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    closed = 0
    moved = 0
    errors: list[str] = []

    # 1. Merge leftover child branches → base → develop
    errors.extend(_merge_sprint_branch_chain(repo, base_label))

    # Legacy: merge a specific open PR if the client still sends one (child end-of-run PR)
    if body.merge_pr and body.sprint_pr_url:
        try:
            merge_res = subprocess.run(
                ["gh", "pr", "merge", body.sprint_pr_url, "--repo", repo, "--merge", "--delete-branch"],
                capture_output=True, text=True, check=False,
            )
            if merge_res.returncode != 0:
                errors.append(f"PR merge failed: {merge_res.stderr.strip()}")
        except Exception as exc:
            errors.append(f"PR merge error: {exc}")

    _NON_WORK_LABELS_FS2 = {"sprint-summary", "docs", "documentation"}

    # 2. Close sprint issues — labels stay on for GitHub history queries
    for iss in sprint_issues:
        issue_num = iss["number"]
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        if _NON_WORK_LABELS_FS2 & label_names:
            continue
        try:
            github_client.close_issue(issue_num, repo_name=repo, reason="completed")
            closed += 1
        except subprocess.CalledProcessError as exc:
            err_msg = exc.stderr.strip() if exc.stderr else str(exc)
            errors.append(f"#{issue_num}: {err_msg}")

    # 3. Mark base + all children completed in plan.json
    for lbl in all_labels:
        try:
            _plan_json_set_state(
                project_root, lbl, "completed",
                ended_at=datetime.now(timezone.utc).isoformat(),
                end_reason="merge_sprint",
            )
        except Exception as exc:
            errors.append(f"plan.json update failed for {lbl}: {exc}")

    # Invalidate caches so board refreshes
    github_client.invalidate("open_issues_body:")
    github_client.invalidate("open_issues:")
    github_client.invalidate("issues:")
    github_client.invalidate("recent_closed:")

    await broadcast({
        "type": "update",
        "event": {
            "event_type": "sprint_finished",
            "sprint_label": base_label,
        },
    })

    _emit_dashboard_event(
        project=repo,
        type="sprint_finished",
        target=base_label,
        detail={
            "sprint_id": base_label,
            "summary_issue_url": _read_sprint_summary_url(project_root, base_label),
        },
        action_id=str(uuid.uuid4()),
    )

    result: dict = {
        "closed": closed,
        "moved": moved,
        "errors": errors,
        "next_sprint_label": f"sprint-{_next_sprint_number(base_label)}",
        "base_label": base_label,
    }
    status_code = 207 if errors else 200
    return JSONResponse(content=result, status_code=status_code)


_NON_WORK_LABELS_BC = {"sprint-summary", "docs", "documentation"}


def _summary_title_for_label(label: str) -> str:
    m = re.match(r"^sprint-(.+)$", label)
    if not m:
        return ""
    return f"Sprint {m.group(1)} Executive Summary"


def _open_summary_issues_for_labels(repo: str, labels: list[str]) -> list[dict]:
    """Return open sprint-summary issues whose title matches a lineage label."""
    titles_wanted = {_summary_title_for_label(lbl) for lbl in labels}
    titles_wanted.discard("")
    if not titles_wanted:
        return []
    try:
        issues = github_client.list_open_issues_with_body(repo_name=repo, limit=200)
    except Exception:
        return []
    result: list[dict] = []
    seen: set[int] = set()
    for iss in issues:
        title = iss.get("title", "") or ""
        if title not in titles_wanted:
            continue
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        if not (_NON_WORK_LABELS_BC & label_names):
            continue
        num = iss.get("number")
        if num is None or num in seen:
            continue
        seen.add(num)
        sprint_lbl = next(
            (lbl for lbl in labels if title == _summary_title_for_label(lbl)),
            None,
        )
        result.append({
            "number": num,
            "title": title,
            "labels": iss.get("labels", []),
            "sprint_label": sprint_lbl,
        })
    return result


def _bulk_complete_collect_issues(repo: str, project_root: Path, base_label: str) -> tuple[list[str], list[dict]]:
    if not re.match(r"^sprint-\d+$", base_label):
        raise HTTPException(400, detail=f"Bulk complete requires a base sprint label, got {base_label!r}")
    child_labels = _child_sprint_labels_from_plans(project_root, base_label)
    if not child_labels:
        raise HTTPException(400, detail=f"No child sprints found under {base_label}")
    all_labels = [base_label, *child_labels]
    sprint_issues: list[dict] = []
    seen_nums: set[int] = set()
    for lbl in all_labels:
        try:
            for iss in _get_sprint_issues(repo, lbl):
                if iss["number"] not in seen_nums:
                    sprint_issues.append(iss)
                    seen_nums.add(iss["number"])
        except subprocess.CalledProcessError:
            pass
    for iss in _open_summary_issues_for_labels(repo, all_labels):
        if iss["number"] not in seen_nums:
            sprint_issues.append(iss)
            seen_nums.add(iss["number"])
    return all_labels, sprint_issues


def _bulk_complete_ticket_rows(issues: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for iss in issues:
        label_names = {lbl["name"] for lbl in iss.get("labels", [])}
        number = iss["number"]
        title = iss.get("title", "")
        if _NON_WORK_LABELS_BC & label_names:
            rows.append({"number": number, "title": title, "category": "sprint-summary"})
            continue
        if "UAT" in label_names:
            rows.append({"number": number, "title": title, "category": "UAT"})
            continue
        status = next(
            (lbl for lbl in sorted(label_names) if lbl in _FINISH_SPRINT_STATUS_LABELS and lbl != "UAT"),
            "queued",
        )
        rows.append({"number": number, "title": title, "category": status})
    return rows


@app.get("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview")
def get_sprint_bulk_complete_preview(owner: str, repo_name: str, label: str):
    """Preview tickets to close and member sprints to mark completed."""
    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    base_label = _sprint_label_base(label)
    project_root = _project_root_path(repo)

    all_labels, sprint_issues = _bulk_complete_collect_issues(repo, project_root, base_label)

    merge_steps = _merge_steps_for_sprint_chain(project_root, repo, base_label)

    return {
        "all_tickets": _bulk_complete_ticket_rows(sprint_issues),
        "member_labels": all_labels,
        "base_label": base_label,
        "child_count": len(all_labels) - 1,
        "merge_steps": merge_steps,
    }


class SprintBranchMergeBody(BaseModel):
    confirmed: bool
    head: str
    base: str
    title: str = ""
    delete_branch: bool = True


@app.post("/api/projects/{owner}/{repo_name}/sprint-branch-merge")
def sprint_branch_merge(owner: str, repo_name: str, body: SprintBranchMergeBody):
    """Merge one sprint branch into another via PR (used by bulk-complete step progress)."""
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")
    if not body.head or not body.base:
        raise HTTPException(400, detail="head and base are required")

    repo = f"{owner}/{repo_name}"
    title = body.title.strip() or f"Merge `{body.head}` → `{body.base}`"
    ok, detail = _gh_merge_branch_via_pr(
        repo, body.head, body.base, title, body.delete_branch,
    )
    if not ok:
        raise HTTPException(400, detail=detail)
    return {"ok": True, "detail": detail}


class BulkCompleteSprintBody(BaseModel):
    confirmed: bool
    selected_ticket_numbers: list[int] = []


@app.post("/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete")
async def bulk_complete_sprint(owner: str, repo_name: str, label: str, body: BulkCompleteSprintBody):
    """Close UAT + summary issues across a lineage and mark every member completed.

    Requires the sprint branch merge chain to be complete (child → base → develop)
    before closing issues — use merge_steps from bulk-complete-preview first.
    """
    if not body.confirmed:
        raise HTTPException(400, detail="Request must have confirmed=true")

    if not _SPRINT_LABEL_RE.match(label):
        raise HTTPException(400, detail=f"Invalid sprint label: {label!r}")

    repo = f"{owner}/{repo_name}"
    project_root = _project_root_path(repo)
    base_label = _sprint_label_base(label)
    all_labels, sprint_issues = _bulk_complete_collect_issues(repo, project_root, base_label)

    for lbl in all_labels:
        if _is_sprint_running(project_root, lbl):
            raise HTTPException(
                409,
                detail=f"Sprint {lbl} is currently running — bulk complete after the run finishes",
            )

    pending_merges = _sprint_merge_chain_pending(project_root, repo, base_label)
    if pending_merges:
        raise HTTPException(
            409,
            detail=(
                "Sprint branches are not fully merged to develop — complete merge steps first: "
                + "; ".join(pending_merges)
            ),
        )

    selected = set(body.selected_ticket_numbers) if body.selected_ticket_numbers else None
    closed = 0
    errors: list[str] = []

    for iss in sprint_issues:
        issue_num = iss["number"]
        if selected is not None and issue_num not in selected:
            continue
        try:
            github_client.close_issue(issue_num, repo_name=repo, reason="completed")
            closed += 1
        except subprocess.CalledProcessError as exc:
            err_msg = exc.stderr.strip() if exc.stderr else str(exc)
            errors.append(f"#{issue_num}: {err_msg}")

    completed = 0
    now = datetime.now(timezone.utc).isoformat()
    for lbl in all_labels:
        plan = _read_plan_json(project_root, lbl)
        if plan and (plan.get("state") or "").lower() == "deleted":
            continue
        try:
            _plan_json_set_state(
                project_root, lbl, "completed",
                ended_at=now,
                end_reason="bulk_complete",
            )
            _sprint_db_set_state(
                lbl, repo, "completed",
                ended_at=now,
                end_reason="bulk_complete",
            )
            completed += 1
        except Exception as exc:
            errors.append(f"plan.json update failed for {lbl}: {exc}")

    github_client.invalidate("open_issues_body:")
    github_client.invalidate("open_issues:")
    github_client.invalidate("issues:")
    github_client.invalidate("recent_closed:")
    github_client.invalidate("summary_issues:")

    await broadcast({
        "type": "update",
        "event": {
            "event_type": "sprint_bulk_completed",
            "sprint_label": base_label,
        },
    })

    _emit_dashboard_event(
        project=repo,
        type="sprint_bulk_completed",
        target=base_label,
        detail={
            "sprint_id": base_label,
            "member_labels": all_labels,
            "closed": closed,
            "completed": completed,
        },
        action_id=str(uuid.uuid4()),
    )

    result: dict = {
        "closed": closed,
        "completed": completed,
        "errors": errors,
        "base_label": base_label,
        "member_labels": all_labels,
    }
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
# Extensions allowed for bulk create attachments (issue #374 extended from images-only)
_BULK_ATTACH_EXTS = {'.png', '.jpg', '.jpeg', '.md', '.html', '.htm', '.pdf'}

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

    job = _get_bulk_job(job_id)
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


_INLINE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _build_body_with_images(body: str, ticket_index: int, job: dict) -> str:
    """Append attachment links for files assigned to this ticket.

    Images use inline syntax; all other types use plain link syntax so they
    render as clickable links rather than broken-image icons on GitHub.

    Idempotent: returns body unchanged if an Attachments section is already present.
    """
    url_map = job.get("image_url_map") or {}
    if not url_map:
        return body
    if "## Attachments" in body:
        return body
    assignments = job.get("image_assignments") or []
    links: list[str] = []
    for a in assignments:
        assignment = a.get("assignment")
        if assignment == "all" or assignment == ticket_index:
            fname = a.get("filename", "")
            url = url_map.get(fname)
            if url:
                ext = Path(fname).suffix.lower()
                if ext in _INLINE_IMAGE_EXTS:
                    links.append(f"![{fname}]({url})")
                else:
                    links.append(f"[{fname}]({url})")
    if not links:
        return body
    return body + "\n\n## Attachments\n\n" + "\n\n".join(links)


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


def _extract_ba_labels(output: str, allowed: set[str]) -> list[str]:
    """Extract a 'labels' array from BA JSON output, keeping only labels that
    already exist in the repo (`allowed`). Returns [] on any parse failure or
    when no allowed set is provided — BA never invents new repo labels.
    """
    if not allowed:
        return []
    clean = re.sub(r"^```(?:json)?\s*", "", output.strip(), flags=re.MULTILINE)
    clean = re.sub(r"\s*```\s*$", "", clean.strip(), flags=re.MULTILINE)
    clean = clean.strip()

    data = None
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(clean[start : end + 1])
            except json.JSONDecodeError:
                data = None

    if not isinstance(data, dict):
        return []
    raw = data.get("labels", [])
    if not isinstance(raw, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for lbl in raw:
        name = str(lbl).strip()
        if name in allowed and name not in seen:
            seen.add(name)
            result.append(name)
    return result


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

    job = _get_bulk_job(job_id)
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
                job = _get_bulk_job(job_id)
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
        job = _get_bulk_job(job_id)
        if job:
            ticket = job["tickets"][index]
            ticket["state"] = "estimate_failed"
            ticket["estimate_error"] = str(exc)[:200]
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    job = _get_bulk_job(job_id)
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


def _draft_body_hash(title: str, body: str) -> str:
    """Stable hash of a draft's text, used to detect edits that need re-sizing."""
    return hashlib.sha256(f"{title}\n\n{body}".encode("utf-8")).hexdigest()


async def _run_bulk_draft_estimator_for_ticket(job_id: str, index: int) -> None:
    """Estimate one bulk draft from its current title+body, before it is posted.

    Sizes are computed on the draft text (no GitHub issue exists yet), stored on
    the ticket, and surfaced so they can inform sprint assignment. The result is
    materialised onto the real issue (size label + cache) at post time.

    Tracks progress in parallel fields, leaving ticket["state"] == "draft_ready"
    untouched so the post-selected flow keeps working:
      estimate_state: None → "estimating" → "sized" | "estimate_failed"
      estimate_size:  "S"/"M"/"L"/"XL"
      estimate:       full estimator JSON (persisted to the cache at post)
      estimate_body_hash: hash of the text that was sized (stale-edit detection)
    """
    import logging as _logging

    job = _get_bulk_job(job_id)
    if not job or index < 0 or index >= len(job["tickets"]):
        return
    ticket = job["tickets"][index]
    title = ticket.get("title") or ""
    body = ticket.get("body") or ""
    body_hash = _draft_body_hash(title, body)

    # Already sized for this exact text — nothing to do.
    if ticket.get("estimate_state") == "sized" and ticket.get("estimate_body_hash") == body_hash:
        return

    if not _ESTIMATE_ISSUE_SCRIPT.exists():
        ticket["estimate_state"] = "estimate_failed"
        ticket["estimate_error"] = "estimate_issue.py not found"
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    ticket["estimate_state"] = "estimating"
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    semaphore = _get_bulk_estimator_semaphore()
    tmp_path: str | None = None
    stdout_bytes = b""
    stderr_bytes = b""
    returncode = -1
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tf:
            json.dump({"title": title, "body": body}, tf)
            tmp_path = tf.name
        async with semaphore:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(_ESTIMATE_ISSUE_SCRIPT), "--draft-file", tmp_path,
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
                returncode = -1
                stderr_bytes = b"draft estimation timed out after 240s"
    except Exception as exc:
        _logging.warning(f"[bulk-estimator] draft estimation (job {job_id} #{index}) failed: {exc}")
        stderr_bytes = str(exc).encode("utf-8")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    job = _get_bulk_job(job_id)
    if not job or index >= len(job["tickets"]):
        return
    ticket = job["tickets"][index]

    estimate: dict | None = None
    if returncode == 0:
        try:
            estimate = json.loads(stdout_bytes.decode("utf-8", errors="replace").strip())
        except (json.JSONDecodeError, ValueError):
            estimate = None

    if not estimate or not isinstance(estimate, dict):
        reason = stderr_bytes.decode("utf-8", errors="replace").strip()[:300] or "could not parse estimate"
        ticket["estimate_state"] = "estimate_failed"
        ticket["estimate_error"] = reason
        ticket["estimate_body_hash"] = body_hash
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        return

    ticket["estimate_state"] = "sized"
    ticket["estimate_size"] = estimate.get("size")
    ticket["estimate"] = estimate
    ticket["estimate_body_hash"] = body_hash
    ticket.pop("estimate_error", None)
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})


async def _materialise_bulk_estimate(
    job_id: str,
    index: int,
    issue_number: int,
    repo: str,
    estimate: dict,
) -> None:
    """Persist a pre-computed draft estimate onto the freshly-posted issue.

    Applies directly via github_client (no second LLM call): writes the JSON
    cache, adds the size-* + estimated labels, and posts the estimate comment.
    Falls back to a live estimation only if the JSON write fails.
    """
    import logging as _logging

    try:
        estimates_dir = _commander_dir(_project_root_path(repo)) / "estimates"
        estimates_dir.mkdir(parents=True, exist_ok=True)
        record = dict(estimate)
        record["issue_number"] = issue_number
        (estimates_dir / f"issue-{issue_number}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        _logging.warning(f"[bulk-estimator] could not cache estimate for #{issue_number}: {exc}")
        if _ESTIMATE_ISSUE_SCRIPT.exists():
            await _run_bulk_estimator_for_ticket(job_id, index, issue_number, repo)
        return

    # Apply size label + estimated label directly — no subprocess, no model re-run.
    size = estimate.get("size")
    try:
        if size:
            github_client.update_labels(
                issue_number,
                add=[f"size-{size}", "estimated"],
                remove=[],
                repo_name=repo,
            )
    except Exception as exc:
        _logging.warning(f"[bulk-estimator] label apply failed for #{issue_number}: {exc}")

    # Post estimate comment directly.
    try:
        comment_body = _format_estimate_comment(estimate)
        await asyncio.to_thread(github_client.add_comment, issue_number, comment_body, repo)
    except Exception as exc:
        _logging.warning(f"[bulk-estimator] comment post failed for #{issue_number}: {exc}")

    github_client.invalidate("open_issues_body:")
    github_client.invalidate("open_issues:")
    github_client.invalidate("issues:")


def _format_estimate_comment(estimate: dict) -> str:
    """Format an estimate dict as the structured GitHub comment body."""
    size       = estimate.get("size", "?")
    minutes    = estimate.get("minutes") or {"S": 5, "M": 15, "L": 30, "XL": 60}.get(size, "?")
    hours      = estimate.get("estimated_hours", "?")
    confidence = estimate.get("confidence", "?")
    files      = estimate.get("files_likely_affected", [])
    depends_on = estimate.get("depends_on", [])
    blocks     = estimate.get("blocks", [])
    risk_flags = estimate.get("risk_flags", [])
    summary    = estimate.get("summary", "")
    files_str  = "\n".join(f"  - `{f}`" for f in files) if files else "  - (none)"
    risk_str   = ", ".join(f"`{r}`" for r in risk_flags) if risk_flags else "none"
    deps_str   = ", ".join(f"#{d}" for d in depends_on) if depends_on else "none"
    blocks_str = ", ".join(f"#{b}" for b in blocks) if blocks else "none"
    return f"""## Estimate

| Field | Value |
|---|---|
| Size | **{size}** |
| Minutes | {minutes} |
| Estimated hours | {hours}h |
| Confidence | {confidence} |
| Risk flags | {risk_str} |
| Depends on | {deps_str} |
| Blocks | {blocks_str} |

**Files likely affected:**
{files_str}

**Summary:** {summary}

---
*Generated by Issue Estimator (Haiku 4.5)*"""


@app.post("/api/tickets/create", status_code=201)
async def create_ticket_from_draft(
    background_tasks: BackgroundTasks,
    draft_id: str = Form(default=""),
    title: str = Form(...),
    body: str = Form(default=""),
    project: str = Form(default=""),
    sprint_label: str = Form(default=""),
    extra_labels: list[str] = Form(default=[]),
    milestone: str = Form(default=""),
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
            title=title, body=body, labels=labels, repo_name=project or None,
            milestone=(milestone or "").strip() or None)
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


def _get_bulk_job(job_id: str) -> dict | None:
    """Return job from memory, or lazy-load from disk (e.g. after server restart).

    If found on disk and not in memory, the job is restored to _bulk_jobs so
    subsequent calls hit memory. Returns None if not found anywhere.
    """
    job = _bulk_jobs.get(job_id)
    if job is not None:
        return job
    # Disk fallback — covers server reload / restart scenarios
    try:
        jobs_dir = _bulk_jobs_dir()
        path = jobs_dir / f"{job_id}.json"
        if path.exists():
            job = json.loads(path.read_text(encoding="utf-8"))
            # A job that was 'running' when the server restarted can never
            # resume — its in-flight BA processes are gone. Mark it cancelled
            # so the client gets accurate state instead of a perpetual spinner.
            if job.get("status") == "running":
                _bulk_cancel_interrupted(job)
            _bulk_jobs[job_id] = job
            return job
    except Exception:
        pass
    return None


def _bulk_cancel_interrupted(job: dict) -> None:
    """Mark a job interrupted by a server restart as cancelled and persist to disk."""
    _NON_TERMINAL = {"pending", "drafting", "estimating"}
    for ticket in job.get("tickets", []):
        if ticket.get("state") in _NON_TERMINAL:
            ticket["state"] = "cancelled"
            ticket["error"] = "Server restarted — job was interrupted"
    job["status"] = "cancelled"
    _persist_bulk_job(job)


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
    job = _get_bulk_job(job_id)
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

    allowed_labels = list(job.get("allowed_labels") or [])
    if allowed_labels:
        labels_clause = (
            "\nAvailable repo labels (pick ONLY from these, do not invent new ones): "
            + ", ".join(allowed_labels) + "\n"
        )
        json_spec = (
            'Output ONLY valid JSON with these fields: "title" (string), '
            '"body" (string, GitHub-flavored markdown), and "labels" (array of 1-3 '
            "strings chosen ONLY from the available repo labels above that best "
            "categorize this ticket — use [] if none clearly fit). No text outside the JSON."
        )
    else:
        labels_clause = ""
        json_spec = (
            'Output ONLY valid JSON with exactly two string fields: "title" and "body".\n'
            "The body field must be GitHub-flavored markdown. No text outside the JSON."
        )

    prompt_text = (
        "You are a BA (Business Analyst) agent writing a GitHub issue.\n\n"
        f"User description: {prompt}\n\n"
        "Write a complete GitHub issue with these sections:\n"
        "  - Title (short, imperative, 5-10 words)\n"
        "  - ## What & Why (1-3 sentences)\n"
        "  - ## Acceptance Criteria (checkbox list, specific and testable)\n"
        "  - ## UAT Test Steps (numbered, each with Expected: line)\n"
        "  - ## Out of Scope (brief list)\n"
        + labels_clause + "\n"
        + json_spec
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
    ticket["suggested_labels"] = _extract_ba_labels(output, set(allowed_labels))
    ticket["state"] = "draft_ready"  # internal state — flusher picks up from here
    ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
    ticket["_default_labels"] = default_labels
    ticket["_repo"] = repo
    _persist_bulk_job(job)
    # Don't broadcast yet — flusher will broadcast after creating the issue


async def _bulk_flusher(job_id: str) -> None:
    """Flush completed drafts in original index order.

    Issue #374: drafts are now held at draft_ready for user review before GitHub posting.
    The flusher injects image links into the body and broadcasts; it does NOT create
    GitHub issues — that happens via /post-selected after the user reviews.
    """
    job = _get_bulk_job(job_id)
    if not job:
        return

    # Pre-commit attachment files before finalising bodies so URLs can go in the body
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

    while flush_idx < n:
        job = _get_bulk_job(job_id)
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
            # Inject image/attachment links into body
            body_with_attachments = _build_body_with_images(
                ticket["body"] or "", flush_idx, job
            )

            # Body size guard (issue #261): warn if body exceeds GitHub's limit
            if len(body_with_attachments) > _BC_BODY_SIZE_THRESHOLD:
                ticket["state"] = "size_warning"
                ticket["body"] = body_with_attachments
                ticket["body_char_count"] = len(body_with_attachments)
                ticket["body_over_by"] = len(body_with_attachments) - _BC_BODY_SIZE_THRESHOLD
                ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            else:
                # Keep at draft_ready — user will review and choose which to post (issue #374)
                ticket["body"] = body_with_attachments
                ticket["body_preview"] = body_with_attachments[:200]
                ticket["finished_at"] = datetime.now(timezone.utc).isoformat()

            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

            # Estimation is NOT auto-started here. The user triggers it at the
            # Estimate stage (POST /estimate-draft), so drafting never blocks on
            # the estimator and the "continue" button is always available.

            flush_idx += 1

        elif ticket["state"] in ("pending", "drafting"):
            # Not ready yet — wait a bit
            await asyncio.sleep(0.5)

        else:
            flush_idx += 1

    # All drafts processed — check terminal draft states (draft_ready, failed, skipped, size_warning)
    job = _get_bulk_job(job_id)
    if job:
        all_drafted = all(
            t["state"] in ("draft_ready", "failed", "skipped", "size_warning")
            for t in job["tickets"]
        )
        if all_drafted and job.get("status") not in ("done", "stopped", "drafts_ready"):
            job["status"] = "drafts_ready"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_drafts_ready", "job_id": job_id})


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

        job = _get_bulk_job(job_id)
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
    job = _get_bulk_job(job_id)
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
    job = _get_bulk_job(job_id)
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

    # Final status update — flusher handles drafts_ready; fall back if not set
    job = _get_bulk_job(job_id)
    if job and job.get("status") not in ("done", "stopped", "drafts_ready"):
        job["status"] = "drafts_ready"
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "job_drafts_ready", "job_id": job_id})


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
    sprint_label: str = Form(default=""),
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

    # Validate sprint_label: "" (backlog), "NEW" (create next sprint at post
    # time), or an existing "sprint-N" label. Resolved lazily on post so an
    # abandoned draft never leaves a ghost sprint label behind.
    sprint_label = (sprint_label or "").strip()
    if sprint_label and sprint_label != "NEW" and not re.match(r"^sprint-\d+$", sprint_label):
        raise HTTPException(422, detail="'sprint_label' must be empty, 'NEW', or 'sprint-N'")

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

    # Fetch the repo's labels once: used to (a) validate default_labels and
    # (b) give the BA the existing label set so it can suggest labels per ticket
    # without ever inventing new ones.
    try:
        existing_label_names = sorted(lbl["name"] for lbl in github_client.list_labels(repo_name=repo))
    except Exception:
        existing_label_names = []
    existing_label_set = set(existing_label_names)

    # Validate default_labels — each must already exist in the repo
    if default_labels_list and existing_label_set:
        bad = [lbl for lbl in default_labels_list if lbl not in existing_label_set]
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

    # Validate and store uploaded files (issue #374: now accepts .md/.html/.pdf in addition to images)
    valid_upload_files = [f for f in files if f.filename]
    attachment_file_data: list[tuple[str, bytes]] = []  # (original_filename, content)
    if valid_upload_files:
        _accepted_fmt = ".png, .jpg, .jpeg, .md, .html, .htm, .pdf"
        batch_size = 0
        for upload in valid_upload_files:
            ext = Path(upload.filename).suffix.lower()
            if ext not in _BULK_ATTACH_EXTS:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' has unsupported extension '{ext}'. "
                           f"Accepted: {_accepted_fmt}.",
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
            "suggested_labels": None,
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
        "sprint_label": sprint_label,
        "allowed_labels": existing_label_names,
        "concurrency": concurrency,
        "created_at": now,
        "stop_requested": False,
        "has_attachments": len(attachment_file_data) > 0,
        "attachment_filenames": [n for n, _ in attachment_file_data],
        "image_assignments": image_assignments,
        "image_url_map": None,
        "tickets": tickets,
        "_action_id": str(uuid.uuid4()),
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
    job = _get_bulk_job(job_id)
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
        "repo": job.get("repo", ""),
        "status": job["status"],
        "concurrency": job["concurrency"],
        "default_labels": job.get("default_labels", []),
        "tickets": tickets,
    }


@app.get("/api/tickets/bulk/{job_id}/stream")
async def bulk_job_stream(job_id: str, request: Request):
    """SSE stream of state-change events for a bulk job."""
    job = _get_bulk_job(job_id)
    if not job:
        # Rehydrate from disk if the job was persisted but evicted from memory
        # (e.g. server restart). Mirrors bulk_get_job so a reconnecting client
        # doesn't get a fatal 404 for a job that still exists on disk.
        try:
            path = _bulk_jobs_dir() / f"{job_id}.json"
            if path.exists():
                job = json.loads(path.read_text())
                _bulk_jobs[job_id] = job
        except Exception:
            pass
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


class BulkSkipBody(BaseModel):
    index: int


class BulkEstimateDraftBody(BaseModel):
    index: int
    title: str | None = None
    body: str | None = None


@app.post("/api/tickets/bulk/{job_id}/estimate-draft")
async def bulk_estimate_draft(job_id: str, body: BulkEstimateDraftBody):
    """(Re-)estimate a draft from its current text (auto re-estimate on edit).

    Applies any edited title/body, then re-sizes only if the text actually
    changed since the last estimate. Sizing runs in the background; the caller
    gets the new estimate via the SSE ticket_update stream.
    """
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket.get("state") != "draft_ready":
        # Only draft_ready tickets are sizeable pre-post; ignore otherwise.
        return {"ok": True, "estimating": False}

    if body.title is not None:
        ticket["title"] = body.title
    if body.body is not None:
        ticket["body"] = body.body

    new_hash = _draft_body_hash(ticket.get("title") or "", ticket.get("body") or "")
    if ticket.get("estimate_state") == "sized" and ticket.get("estimate_body_hash") == new_hash:
        return {"ok": True, "estimating": False}  # text unchanged — keep current size

    _persist_bulk_job(job)
    asyncio.create_task(_run_bulk_draft_estimator_for_ticket(job_id, body.index))
    return {"ok": True, "estimating": True}


@app.post("/api/tickets/bulk/{job_id}/skip")
async def bulk_skip_ticket(job_id: str, body: BulkSkipBody):
    """Mark a pending ticket as skipped (no-op if already past pending)."""
    job = _get_bulk_job(job_id)
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
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] not in ("failed", "cancelled"):
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
        # After BA, keep at draft_ready for user review (issue #374)
        t = job["tickets"][body.index]
        if t.get("state") == "draft_ready":
            body_with_attachments = _build_body_with_images(
                t.get("body") or "", body.index, job
            )
            if len(body_with_attachments) > _BC_BODY_SIZE_THRESHOLD:
                t["state"] = "size_warning"
                t["body"] = body_with_attachments
                t["body_char_count"] = len(body_with_attachments)
                t["body_over_by"] = len(body_with_attachments) - _BC_BODY_SIZE_THRESHOLD
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            else:
                t["body"] = body_with_attachments
                t["body_preview"] = body_with_attachments[:200]
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

        # Check if all tickets are in terminal draft state
        all_drafted = all(
            tt["state"] in ("draft_ready", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        if all_drafted and job.get("status") not in ("done", "stopped", "drafts_ready"):
            job["status"] = "drafts_ready"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_drafts_ready", "job_id": job_id})

    asyncio.create_task(_retry_task())
    return {"ok": True}


class BulkRedraftBody(BaseModel):
    index: int


@app.post("/api/tickets/bulk/{job_id}/redraft")
async def bulk_redraft_ticket(job_id: str, body: BulkRedraftBody):
    """Re-run BA for a single ticket in place (issue #374 per-ticket recreate).

    Works on draft_ready, failed, or skipped tickets. After BA completes the
    ticket returns to draft_ready for user review — no GitHub issue is created.
    """
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]

    # Only allow redraft when not actively running
    if ticket["state"] in ("drafting", "pending"):
        return {"ok": True, "state": ticket["state"]}

    # Reset ticket state
    ticket["state"] = "pending"
    ticket["error"] = None
    ticket["started_at"] = None
    ticket["finished_at"] = None
    ticket["title"] = None
    ticket["body"] = None
    ticket["body_preview"] = None
    job["status"] = "running"
    _persist_bulk_job(job)
    await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _redraft_task():
        await _run_single_ba_ticket(
            job_id, body.index, ticket["prompt"],
            job["repo"], job["default_labels"]
        )
        t = job["tickets"][body.index]
        if t.get("state") == "draft_ready":
            body_with_attachments = _build_body_with_images(
                t.get("body") or "", body.index, job
            )
            if len(body_with_attachments) > _BC_BODY_SIZE_THRESHOLD:
                t["state"] = "size_warning"
                t["body"] = body_with_attachments
                t["body_char_count"] = len(body_with_attachments)
                t["body_over_by"] = len(body_with_attachments) - _BC_BODY_SIZE_THRESHOLD
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            else:
                t["body"] = body_with_attachments
                t["body_preview"] = body_with_attachments[:200]
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

        all_drafted = all(
            tt["state"] in ("draft_ready", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        if all_drafted and job.get("status") not in ("done", "stopped", "drafts_ready"):
            job["status"] = "drafts_ready"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_drafts_ready", "job_id": job_id})

    asyncio.create_task(_redraft_task())
    return {"ok": True}


class BulkPostSelectedItem(BaseModel):
    index: int
    labels: list[str] = []
    title: str | None = None  # override drafted title (issue #526)
    body: str | None = None   # override drafted body (issue #526)


class BulkPostSelectedBody(BaseModel):
    tickets: list[BulkPostSelectedItem]
    # Target sprint chosen at the Sprint stage: "" (backlog), "NEW", or "sprint-N".
    # Falls back to the job's stored sprint_label when omitted.
    sprint_label: str | None = None
    milestone: str | None = None  # chosen at Sprint stage; None/"" = no milestone (#879)


def _resolve_bulk_sprint_label(sprint_label: str | None, repo: str | None) -> str:
    """Resolve a bulk job's sprint selection to a concrete label.

    "" → "" (backlog). "NEW" → create the next sprint-N label (max existing + 1)
    and return it. A concrete "sprint-N" is returned unchanged. Returns "" if
    sprint creation fails, so a failed lookup falls back to the backlog.
    """
    sprint_label = (sprint_label or "").strip()
    if sprint_label != "NEW":
        return sprint_label
    try:
        next_num = _next_new_sprint_number(repo)
        github_client.ensure_sprint_label(next_num, repo_name=repo)
        return f"sprint-{next_num}"
    except Exception:
        return ""


def _next_new_sprint_number(repo: str | None) -> int:
    """Next sprint number for a brand-new sprint — the SAME value the board's
    "New sprint (Sprint N)" option shows.

    Must take the max over BOTH live sprint labels AND finished-sprint summary
    numbers: a sprint's `sprint-N` label is often deleted once it finishes, so
    counting labels alone resets the next number back to 1 (issue: bulk-create
    "NEW" said 54 but created sprint-1). The finished-summary issues preserve
    the real high-water mark cross-machine.
    """
    nums: list[int] = list(github_client.list_sprints(repo_name=repo) or [])
    for lbl in _finished_sprint_summaries(repo):
        if _SPRINT_LABEL_RE.match(lbl):
            try:
                # lbl is "sprint-N" or "sprint-N.X" — take the base number N.
                nums.append(int(lbl.split("-", 1)[1].split(".")[0]))
            except (IndexError, ValueError):
                pass
    return (max(nums) if nums else 0) + 1


def _compose_ticket_labels(sprint_label: str, item_labels: list[str]) -> list[str]:
    """Build the GitHub label list for one bulk-created ticket.

    A ticket assigned to a sprint gets ``[sprint_label, *extras]`` and skips the
    ``backlog`` label — mirroring assign_sprint(), which removes ``backlog`` when
    moving a ticket into a sprint. Otherwise it gets ``["backlog", *extras]``.
    Any ``sprint-N`` already present in ``item_labels`` is dropped so the chosen
    sprint wins (and we never apply two sprint labels).
    """
    extras = [lbl for lbl in item_labels if lbl and not _SPRINT_LABEL_RE.match(lbl)]
    if sprint_label:
        return [sprint_label] + extras
    return ["backlog"] + extras


@app.post("/api/tickets/bulk/{job_id}/post-selected")
async def bulk_post_selected(job_id: str, body: BulkPostSelectedBody):
    """Post selected draft_ready tickets to GitHub (issue #374 review-then-post flow).

    Each item in `tickets` specifies the ticket index and the labels to apply.
    Only tickets in draft_ready state can be posted; others are ignored.
    """
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")

    # Validate all indices up front
    for item in body.tickets:
        if item.index < 0 or item.index >= len(job["tickets"]):
            raise HTTPException(422, detail=f"Invalid ticket index: {item.index}")
        if job["tickets"][item.index]["state"] != "draft_ready":
            raise HTTPException(
                422,
                detail=f"Ticket {item.index} is not in draft_ready state "
                       f"(current: {job['tickets'][item.index]['state']})",
            )

    if not body.tickets:
        raise HTTPException(422, detail="No tickets selected")

    job["status"] = "running"
    _persist_bulk_job(job)

    async def _post_task():
        estimation_tasks: list[asyncio.Task] = []

        # Retry attachment pre-commit if _run_bulk_job failed to commit earlier
        # (e.g. transient git/network error). image_url_map being falsy while
        # has_attachments is True is the signal that the first attempt failed.
        if job.get("has_attachments") and not job.get("image_url_map"):
            try:
                url_map = await asyncio.to_thread(
                    _do_pre_commit_bulk_images, job["job_id"], job["repo"]
                )
                job["image_url_map"] = url_map
                _persist_bulk_job(job)
            except Exception as _pre_err:
                logger.warning("Bulk image pre-commit retry failed: %s", str(_pre_err)[:200])
                job["image_url_map"] = {}

        # Resolve the batch's target sprint once ("NEW" → create the next
        # sprint-N label), then persist the concrete label back onto the job.
        # The Sprint stage sends the choice in the request body; fall back to the
        # job for resilience (e.g. resumed sessions).
        chosen = body.sprint_label if body.sprint_label is not None else job.get("sprint_label")
        sprint_label = _resolve_bulk_sprint_label(chosen, job.get("repo") or None)
        if sprint_label != (job.get("sprint_label") or ""):
            job["sprint_label"] = sprint_label
            _persist_bulk_job(job)

        # Milestone chosen at the Sprint stage applies to the whole batch; persist
        # it so retries reuse it. Empty = no milestone (issue #879).
        milestone = _resolve_bulk_milestone(body.milestone, job)
        if milestone != job.get("milestone"):
            job["milestone"] = milestone; _persist_bulk_job(job)

        for item in body.tickets:
            idx = item.index
            labels = _compose_ticket_labels(sprint_label, item.labels)
            t = job["tickets"][idx]
            issue_repo = job.get("repo") or None

            # Apply user edits from the frontend (issue #526)
            if item.title is not None:
                stripped_title = item.title.strip()
                if stripped_title:
                    t["title"] = stripped_title
            if item.body is not None:
                t["body"] = item.body

            t["state"] = "drafting"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

            body_with_attachments = _build_body_with_images(t.get("body") or "", idx, job)

            # Body size guard (issue #261)
            if len(body_with_attachments) > _BC_BODY_SIZE_THRESHOLD:
                t["state"] = "size_warning"
                t["body"] = body_with_attachments
                t["body_char_count"] = len(body_with_attachments)
                t["body_over_by"] = len(body_with_attachments) - _BC_BODY_SIZE_THRESHOLD
                t["_default_labels"] = labels
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
                _persist_bulk_job(job)
                await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
                continue

            # Never create a blank issue (defense-in-depth alongside the
            # frontend's title validation).
            if not (t.get("title") or "").strip():
                t["state"] = "failed"
                t["error"] = "Refusing to post a ticket with no title."
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
                _persist_bulk_job(job)
                await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
                continue

            created_issue_number: int | None = None
            pre_estimate = t.get("estimate") if t.get("estimate_state") == "sized" else None
            try:
                number, url = github_client.create_issue(title=t["title"], body=body_with_attachments,
                    labels=labels, repo_name=issue_repo, milestone=job.get("milestone"))
                t["state"] = "created"
                t["issue_num"] = number
                t["issue_url"] = url
                t["body"] = body_with_attachments
                t["body_preview"] = body_with_attachments[:200]
                t["label_pills"] = labels
                created_issue_number = number
            except Exception as e:
                t["state"] = "failed"
                t["error"] = f"GitHub issue creation failed: {str(e)[:200]}"

            t["finished_at"] = datetime.now(timezone.utc).isoformat()
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

            # Materialise the estimate onto the new issue. Tickets sized at the
            # Estimate stage just need the size label + cache written (no model
            # re-run); anything that wasn't sized pre-post falls back to a live
            # estimation of the posted issue.
            if created_issue_number is not None:
                try:
                    resolved_repo = issue_repo or github_client.get_repo_for_operation(None)
                except Exception:
                    resolved_repo = None
                if not resolved_repo:
                    pass  # leave un-estimated; sprint board can size it later
                elif pre_estimate:
                    est_task = asyncio.create_task(
                        _materialise_bulk_estimate(job_id, idx, created_issue_number, resolved_repo, pre_estimate)
                    )
                    estimation_tasks.append(est_task)
                elif _ESTIMATE_ISSUE_SCRIPT.exists():
                    est_task = asyncio.create_task(
                        _run_bulk_estimator_for_ticket(job_id, idx, created_issue_number, resolved_repo)
                    )
                    estimation_tasks.append(est_task)

        if estimation_tasks:
            await asyncio.gather(*estimation_tasks, return_exceptions=True)

        # Job is done when no tickets are still pending/drafting
        # (unselected draft_ready tickets count as done)
        no_active = all(
            t["state"] not in ("pending", "drafting") for t in job["tickets"]
        )
        if no_active and job.get("status") not in ("done", "stopped"):
            job["status"] = "done"
            _persist_bulk_job(job)
            await _broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})
            _created_ids = [
                f"#{t['issue_num']}" for t in job["tickets"]
                if t.get("state") == "created" and t.get("issue_num") is not None
            ]
            _emit_dashboard_event(
                project=job.get("repo") or "dashboard",
                type="bulk_created",
                target=",".join(_created_ids),
                detail={"ticket_ids": _created_ids},
                action_id=job.get("_action_id") or str(uuid.uuid4()),
            )

    asyncio.create_task(_post_task())
    return {"ok": True}


async def _post_ticket_body_to_github(job_id: str, index: int, body_text: str) -> None:
    """Post a ticket body directly to GitHub (no BA drafting) and update job state."""
    job = _get_bulk_job(job_id)
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
        title = first_line.lstrip("# ").strip()[:120]

    # Never create an empty/"Untitled" GitHub issue: a retry on a draft with no
    # body used to silently post a blank ticket. Refuse instead and leave the
    # ticket failed so the user can regenerate it.
    if not (body_text or "").strip() and not title:
        t["state"] = "failed"
        t["error"] = "Refusing to post an empty ticket — regenerate the draft before retrying."
        t["last_error"] = t["error"]
        t["finished_at"] = datetime.now(timezone.utc).isoformat()
        _persist_bulk_job(job)
        await _broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
        return

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
        number, url = github_client.create_issue(title=title, body=body_text,
            labels=labels, repo_name=issue_repo, milestone=job.get("milestone"))
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
    job = _get_bulk_job(job_id)
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
    job = _get_bulk_job(job_id)
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
        if ext not in _BULK_ATTACH_EXTS:
            raise HTTPException(
                422,
                detail=f"Unsupported file type '{ext}'. Accepted: .png, .jpg, .jpeg, .md, .html, .htm, .pdf.",
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
    job = _get_bulk_job(job_id)
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
        if t["state"] not in ("failed", "cancelled"):
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
    job = _get_bulk_job(job_id)
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
    job = _get_bulk_job(job_id)
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

async def _mark_inflight_jobs_failed():
    """On restart, mark any previously-running jobs as failed (state lost).

    Called from the lifespan startup. NOTE: this must NOT use
    @app.on_event("startup") — the app is created with a lifespan handler, and
    FastAPI ignores on_event hooks when a lifespan is set, so the decorator
    silently never ran (left bulk jobs wedged in "running" across restarts).
    """
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


# ── Mis-sizing flag endpoints (issue #578) ───────────────────────────────────


class MisSizingActionBody(BaseModel):
    action: str
    new_size: Optional[str] = None
    note: Optional[str] = None


class MisSizingConfigBody(BaseModel):
    tier_threshold: int
    min_events: int


@app.get("/api/sprints/{sprint_label}/mis-sizing-flags")
def get_mis_sizing_flags(sprint_label: str, project: str):
    """Return the current mis-sizing flags for a sprint.

    Does NOT regenerate; returns whatever is persisted on disk.
    Call POST /generate to regenerate from current sprint issues.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not _MIS_SIZING_AVAILABLE:
        return {"sprint_label": sprint_label, "flags": [], "audit_log": [], "generated_at": None, "config": {}}
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    return _mis_sizing.load_flags(commander, sprint_label)


@app.post("/api/sprints/{sprint_label}/mis-sizing-flags/generate")
def generate_mis_sizing_flags(sprint_label: str, project: str):
    """Regenerate mis-sizing flags for a sprint from current GitHub issue data."""
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not _MIS_SIZING_AVAILABLE:
        return {"sprint_label": sprint_label, "flags": [], "audit_log": [], "generated_at": None, "config": {}}

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    try:
        sprint_issues = _get_sprint_issues(project, sprint_label)
    except subprocess.CalledProcessError as e:
        raise _gh_error(e)

    return _mis_sizing.generate_and_save_flags(commander, sprint_label, sprint_issues)


@app.post("/api/sprints/{sprint_label}/mis-sizing-flags/{issue_id}/action")
def act_on_mis_sizing_flag(sprint_label: str, issue_id: int, body: MisSizingActionBody, project: str):
    """Take an action on a mis-sizing flag: approved, reestimated, or dismissed.

    For reestimated, also updates the estimate file and GitHub size label.
    """
    if not _SPRINT_LABEL_RE.match(sprint_label):
        raise HTTPException(400, detail=f"Invalid sprint label: {sprint_label!r}")
    if not _MIS_SIZING_AVAILABLE:
        raise HTTPException(501, detail="Mis-sizing module not available")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)

    try:
        data = _mis_sizing.record_action(
            commander,
            sprint_label,
            issue_id,
            body.action,
            new_size=body.new_size,
            note=body.note,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except KeyError as e:
        raise HTTPException(404, detail=str(e))

    # If re-estimating, update the estimate file and GitHub label
    if body.action == "reestimated" and body.new_size:
        estimates_dir = commander / "estimates"
        est_path = estimates_dir / f"issue-{issue_id}.json"
        if est_path.exists():
            try:
                est_data = json.loads(est_path.read_text(encoding="utf-8"))
                old_size = est_data.get("size")
                est_data["size"] = body.new_size
                est_data["minutes"] = _mis_sizing.CANONICAL_MINUTES.get(body.new_size, 0)
                est_path.write_text(json.dumps(est_data, indent=2), encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass
        # Update GitHub label: remove old size-*, add new size-*
        try:
            size_labels = [f"size-{s}" for s in _mis_sizing.SIZE_TIERS]
            github_client.update_labels(
                issue_id,
                add=[f"size-{body.new_size}"],
                remove=size_labels,
                repo_name=project,
            )
        except subprocess.CalledProcessError:
            pass  # Label update is best-effort

    return data


@app.get("/api/mis-sizing/history")
def get_mis_sizing_history(project: str):
    """Return the full mis-sizing history for a project."""
    if not _MIS_SIZING_AVAILABLE:
        return {"version": 1, "events_by_label": {}, "last_rebuilt": None}
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    return _mis_sizing.load_history(commander)


@app.post("/api/mis-sizing/rebuild")
def rebuild_mis_sizing_history(project: str):
    """Rebuild mis-sizing history by scanning all sprint state files.

    Fetches issue labels from GitHub for completed tickets (one batch call).
    This is an expensive operation — run once or when history is stale.
    """
    if not _MIS_SIZING_AVAILABLE:
        raise HTTPException(501, detail="Mis-sizing module not available")

    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"

    # Collect all completed tickets from sprint state files
    raw_completed: list[dict] = []
    if sprints_dir.exists():
        for state_path in sorted(sprints_dir.glob("sprint-*-state.json")):
            m = re.search(r"sprint-(\d+)-state", state_path.name)
            sprint_label_str = f"sprint-{m.group(1)}" if m else state_path.stem
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for iss in state_data.get("issues", []):
                if iss.get("status") not in ("done", "passed"):
                    continue
                num = iss.get("number")
                if num is None:
                    continue
                # Only include tickets that have an estimate file
                est_path = estimates_dir / f"issue-{num}.json"
                if not est_path.exists():
                    continue
                try:
                    est_data = json.loads(est_path.read_text(encoding="utf-8"))
                    estimated_size = est_data.get("size") or None
                except (json.JSONDecodeError, OSError):
                    continue
                if not estimated_size:
                    continue
                raw_completed.append({
                    "number": num,
                    "sprint": sprint_label_str,
                    "estimated_size": estimated_size,
                    "coder_started_at": iss.get("coder_started_at"),
                    "tester_finished_at": iss.get("tester_finished_at"),
                    "status_changed_at": iss.get("status_changed_at"),
                })

    if not raw_completed:
        history = _mis_sizing.build_history_from_completed([])
        _mis_sizing.save_history(commander, history)
        return {"message": "No completed estimated tickets found", "total_events": 0, "labels_with_history": 0}

    # Batch-fetch labels for all completed issues from GitHub
    issue_numbers = {c["number"] for c in raw_completed}
    labels_by_num: dict[int, list[str]] = {}
    try:
        repo = github_client.get_repo_for_operation(project)
        result = subprocess.run(
            ["gh", "issue", "list", "--repo", repo,
             "--state", "all", "--json", "number,labels", "--limit", "1000"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            for iss in json.loads(result.stdout or "[]"):
                n = iss.get("number")
                if n in issue_numbers:
                    labels_by_num[n] = [
                        lbl["name"] for lbl in iss.get("labels", [])
                    ]
    except Exception:
        pass  # Proceed without labels — events will be skipped

    # Attach labels to completed tickets
    for rec in raw_completed:
        rec["labels"] = labels_by_num.get(rec["number"], [])

    history = _mis_sizing.build_history_from_completed(raw_completed)
    _mis_sizing.save_history(commander, history)

    total_events = sum(len(v) for v in history.get("events_by_label", {}).values())
    labels_count = len(history.get("events_by_label", {}))
    return {
        "message": f"History rebuilt: {total_events} events across {labels_count} labels",
        "labels_with_history": labels_count,
        "total_events": total_events,
        "last_rebuilt": history.get("last_rebuilt"),
    }


@app.get("/api/mis-sizing/config")
def get_mis_sizing_config(project: str):
    """Return the current mis-sizing detection thresholds."""
    if not _MIS_SIZING_AVAILABLE:
        return {"tier_threshold": 2, "min_events": 2}
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    return _mis_sizing.load_config(commander)


@app.post("/api/mis-sizing/config")
def update_mis_sizing_config(body: MisSizingConfigBody, project: str):
    """Update the mis-sizing detection thresholds."""
    if not _MIS_SIZING_AVAILABLE:
        raise HTTPException(501, detail="Mis-sizing module not available")
    if body.tier_threshold < 1 or body.tier_threshold > 3:
        raise HTTPException(400, detail="tier_threshold must be 1–3")
    if body.min_events < 1:
        raise HTTPException(400, detail="min_events must be >= 1")
    project_root = _project_root_path(project)
    commander = _commander_dir(project_root)
    config = {"tier_threshold": body.tier_threshold, "min_events": body.min_events}
    _mis_sizing.save_config(commander, config)
    return config


# ── Per-project notes (NOTES.md) ──────────────────────────────────────────────

class SaveNotesBody(BaseModel):
    content: str
    expected_mtime: Optional[float] = None


def _notes_path(repo: str) -> Path:
    return _project_root_path(repo) / "NOTES.md"


@app.get("/api/projects/notes")
def get_project_notes(repo: str = ""):
    if not repo:
        raise HTTPException(status_code=400, detail="repo required")
    path = _notes_path(repo)
    if not path.exists():
        return {"content": "", "mtime": None, "exists": False}
    try:
        mtime = path.stat().st_mtime
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail={"error": "failed to read NOTES.md", "reason": str(exc)})
    return {"content": content, "mtime": mtime, "exists": True}


@app.post("/api/projects/notes")
def save_project_notes(repo: str = "", body: SaveNotesBody = ...):
    if not repo:
        raise HTTPException(status_code=400, detail="repo required")
    path = _notes_path(repo)
    if path.exists() and body.expected_mtime is not None:
        current_mtime = path.stat().st_mtime
        if abs(current_mtime - body.expected_mtime) > 0.5:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "conflict",
                    "message": "NOTES.md changed on disk since last load.",
                    "current_mtime": current_mtime,
                },
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.content, encoding="utf-8")
    return {"ok": True, "mtime": path.stat().st_mtime}


# ── Static asset routes with long-lived cache headers (issue #249) ────────────
# Explicit routes for JS and CSS files must appear before the StaticFiles mount.
# Browsers can cache these indefinitely because the build hash in the query
# string changes whenever the file content changes.

@app.get("/static/{filename:path}")
async def static_assets(filename: str):
    """Serve static files with appropriate cache headers.

    JS and CSS files get Cache-Control: public, max-age=31536000, immutable
    because the HTML pages reference them with a versioned query string.
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
