# startup.py — helper functions extracted from server.py (issue #1267).
#
# server.py imports this module and injects all its names into the server
# namespace so router files can still do:  srv = _server(); srv.<name>
#
# Circular-import safety: helpers call _server() only inside function bodies,
# never at module level.

def _server():
    """Deferred import of the server module — safe at call time."""
    import server  # noqa: PLC0415
    return server


# Re-import service objects removed with the app factory block so that
# helper functions (e.g. _cache_refresh_loop) can still call broadcast().
try:
    from routers.logs_service import broadcast  # noqa: E402
    from routers.bulk_tickets import _get_bulk_job  # noqa: E402
except Exception:
    pass  # available after server.py finishes importing its routers

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import importlib.util as _importlib_util  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402


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

from dotenv import load_dotenv  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

# Load .env before importing local modules so that DB_PATH and other env vars
# are available when db.py executes its module-level startup checks.
load_dotenv(Path(__file__).parent / ".env")

import db  # noqa: E402
import github_client  # noqa: E402
import sprint_state  # noqa: E402
import github_events_sync  # noqa: E402
import projects as projects_module  # noqa: E402

# Structured event logging (services/logging.py at repo root)
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from services.logging import log as _slog, setup_logging as _setup_logging  # noqa: E402

# Install size-rotating prd.log handler at import time so the uvicorn worker
# (which imports server:app) is covered without disk-exhaustion risk (issue #762).
try:
    _setup_logging()
except Exception:  # logging must never break startup
    pass

# Backup module lives in services/sprint_manager/ — add it to sys.path
import sys as _sys  # noqa: E402
_SERVICES_DIR = Path(__file__).parent.parent.parent / "services" / "sprint_manager"
if str(_SERVICES_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SERVICES_DIR))
try:
    import backup as _backup_module
    _BACKUP_AVAILABLE = True
except ImportError:
    _backup_module = None  # type: ignore[assignment]
    _BACKUP_AVAILABLE = False

# Local rolling backup (issue #1901) — apps/dashboard/backup.py, separate from
# the gist/repo authority-DB backup above. Loaded by file path so it is not
# shadowed by the services/sprint_manager/backup.py already on sys.path.
try:
    _LOCAL_BAK_PATH = Path(__file__).parent / "backup.py"
    if _LOCAL_BAK_PATH.exists():
        _spec = _importlib_util.spec_from_file_location("_dashboard_backup_local", _LOCAL_BAK_PATH)
        _local_backup_module = _importlib_util.module_from_spec(_spec)
        _spec.loader.exec_module(_local_backup_module)
        _local_backup_module.start_local_backup_scheduler()
except Exception:
    pass  # local backup is best-effort; never block startup

# Neon dual-write was removed in issue #758 — SQLite + local JSON is the primary
# (and only live) store. Neon is now an optional export target reached solely via
# scripts/export_to_neon.py, so there is no startup sync or per-flow Neon write to
# disable here.

from sizing import SIZE_TO_MINUTES as _SIZE_TO_MINUTES  # noqa: E402

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
    import prune_test_files as _prune_test_files
    _PRUNE_TESTS_AVAILABLE = True
except ImportError:
    _prune_test_files = None  # type: ignore[assignment]
    _PRUNE_TESTS_AVAILABLE = False

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
COMMANDER_SWEEP_GRACE_SECONDS: int = int(os.environ.get("COMMANDER_SWEEP_GRACE_SECONDS", "30"))
_TIMEOUT_CHECK_INTERVAL: int = 60  # run the check every 60 seconds

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
    sweep_start = time.monotonic()
    scanned = 0
    cleaned = 0

    def _remove_orphan(pid_file: Path, pid: int | None, reason: str) -> None:
        nonlocal cleaned
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
        _server()._orphans_removed_total += 1

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
    """Three-condition gate before settling running plan.json to needs_rework (issue #1089).
    Writes go through db.transition_sprint_state(actor="reconcile") — never direct (AC2).
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
                # Condition 1: PID file absent (orphan sweep already cleaned dead PIDs)
                if (sprints_dir / f"{label}-pid").exists() or (sprints_dir / f"{label}-pid.pending").exists():
                    continue
                # Condition 2: no live manager process (guards startup race before PID written)
                if _live_manager_pid(project_root, label) is not None:
                    continue
                # Condition 3: grace window must have elapsed
                row = db.get_sprint(label, project=proj["repo"])
                raw_ts = (row or {}).get("started_at") or data.get("started_at")
                if not raw_ts:
                    print(f"[startup-sweep] {label}: no started_at — grace assumed, skip")
                    continue
                try:
                    started = datetime.fromisoformat(raw_ts)
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - started).total_seconds()
                except (ValueError, TypeError):
                    age = COMMANDER_SWEEP_GRACE_SECONDS + 1
                if age < COMMANDER_SWEEP_GRACE_SECONDS:
                    print(f"[startup-sweep] {label}: grace {age:.0f}s < {COMMANDER_SWEEP_GRACE_SECONDS}s — skip")
                    continue
                # All three conditions met — route through the guarded writer (AC2).
                # transition_sprint_state returns a TransitionResult (not a tuple);
                # read .accepted/.reason — unpacking it raised TypeError and the
                # sweep silently skipped every stale sprint (caught below).
                _tr = db.transition_sprint_state(label, "needs_rework", actor="reconcile", end_reason="process lost")
                ok, rejection = _tr.accepted, _tr.reason
                if ok:
                    _plan_json_set_state(project_root, label, "needs_rework", end_reason="process lost")
                    reconciled += 1
                    print(f"[startup-sweep] reconciled {label}: running→needs_rework")
                else:
                    print(f"[startup-sweep] {label}: guard rejected — {rejection}")  # AC5
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
    _srv = _server()

    if not shutil.which("gh"):
        _srv._GH_AUTH_STATUS = {
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
            _srv._GH_AUTH_STATUS = {
                "ok": False,
                "event": "gh_auth_check_failed",
                "message": "GitHub CLI is not authenticated",
                # Plain `gh auth login` doesn't propagate the new token to the
                # headless dashboard (.env + launchd plist). The helper does all
                # three; pass a long-lived PAT to stop the recurring expiry.
                "remediation": "Run: scripts/gh_reauth.sh --token <long-lived PAT>",
            }
            _slog.warn(
                "gh_auth_check_failed",
                "gh CLI not authenticated",
                scope_required="repo",
                scope_present=False,
                remediation="scripts/gh_reauth.sh --token <PAT>",
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
            _srv._GH_AUTH_STATUS = {
                "ok": False,
                "event": "gh_auth_check_failed",
                "message": "GitHub CLI token is missing the 'repo' scope",
                "remediation": "Run: scripts/gh_reauth.sh --token <PAT with repo scope>",
            }
            _slog.warn(
                "gh_auth_check_failed",
                "gh CLI token missing 'repo' scope",
                scope_required="repo",
                scope_present=False,
                remediation="scripts/gh_reauth.sh --token <PAT with repo scope>",
            )
            return

        _srv._GH_AUTH_STATUS = {"ok": True, "message": ""}
        _slog.info(
            "gh_auth_check_passed",
            "gh CLI authenticated with repo scope",
            scope_required="repo",
            scope_present=True,
        )

    except subprocess.TimeoutExpired:
        _srv._GH_AUTH_STATUS = {
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
        _srv._GH_AUTH_STATUS = {
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


def _sweep_orphan_db_running_rows(max_age_minutes: int = 30) -> list[str]:
    """Reconcile DB rows stuck at state='running' with no live local sprint.

    ``_all_sprints_running`` only sees sprints that still have a local
    ``*-plan.json`` with state=running. A ``sprints`` row left at 'running' with
    no such plan.json — a row copied from another machine, a deleted plan, or a
    crash before the terminal write — is otherwise never cleared and lingers on
    the board / pill forever (e.g. a perf-coach sprint showing 'running' for days).

    Sweep such rows to needs_rework when ALL hold:
      * the (project, label) is not reported live by _all_sprints_running, and
      * the row's started_at is older than ``max_age_minutes`` (so a sprint
        mid-startup, before its plan.json lands, is never killed).

    Returns the labels swept. Best-effort; never raises.
    """
    swept: list[str] = []
    try:
        rows = db.list_sprints_lifecycle()
    except Exception:
        return swept
    try:
        live = {(r.get("project"), r.get("sprint_label")) for r in _all_sprints_running()}
    except Exception:
        live = set()
    now = datetime.now(timezone.utc)
    for row in rows:
        if (row.get("state") or "") != "running":
            continue
        label = row.get("label") or ""
        project = row.get("project") or ""
        if not label or (project, label) in live:
            continue
        # AC1 (#1887): verify the manager PID is dead before settling a running
        # row.  During post-sprint phase (documenter/reviewer dispatched) the
        # plan.json leaves state=running so the sprint drops from _all_sprints_running,
        # but the manager process is still alive — do not orphan it.
        try:
            _proj_root = _project_root_path(project) if project else None
            if _proj_root is not None and _live_manager_pid(_proj_root, label) is not None:
                continue
        except Exception:
            pass
        # (#2031): If plan.json already carries a terminal (non-running) state,
        # the sprint manager wrote its outcome before the process exited.  The DB
        # row is stale — a failed or not-yet-applied DB write, or a restart race.
        # Do NOT overwrite with "orphaned (no live process)"; that would lie about
        # a sprint that completed normally or crashed with a known end_reason (e.g.
        # "hard-crash" from #2030's crash handler).
        # Genuinely orphaned sprints still have plan.json=running (manager crashed
        # before writing terminal state) and fall through to the existing orphan path.
        try:
            if project:
                _plan_root = _project_root_path(project)
                _plan_file = (
                    _commander_dir(_plan_root) / "sprints" / f"{label}-plan.json"
                )
                if _plan_file.exists():
                    _plan_data = json.loads(_plan_file.read_text(encoding="utf-8"))
                    if isinstance(_plan_data, dict):
                        _plan_state = _plan_data.get("state") or ""
                        if _plan_state and _plan_state != "running":
                            # Sprint is already terminal — DB row is stale.
                            # Sync DB silently so the board shows the right state.
                            _plan_end_reason = _plan_data.get("end_reason")
                            _plan_ended_at = _plan_data.get("ended_at")
                            try:
                                if _plan_state in ("ready_to_merge", "completed"):
                                    db.record_sprint_ready_to_merge(
                                        label,
                                        end_reason=_plan_end_reason,
                                        ended_at=_plan_ended_at,
                                        project=project,
                                    )
                                elif _plan_state in ("needs_rework", "cancelled", "failed"):
                                    db.record_sprint_needs_rework(
                                        label,
                                        end_reason=_plan_end_reason,
                                        ended_at=_plan_ended_at,
                                        project=project,
                                    )
                            except Exception:
                                pass
                            logger.info(
                                "[orphan-db-sweep] sprint %s: plan.json=%s — "
                                "DB was stale-running; synced, not orphaned",
                                label, _plan_state,
                            )
                            continue  # not an orphan — do not add to swept
        except Exception:
            pass
        started = (row.get("started_at") or "").strip()
        ts = None
        if started:
            try:
                ts = datetime.fromisoformat(started.replace("Z", "+00:00"))
            except ValueError:
                ts = None
            if ts is not None and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        # Skip rows that are too fresh (a real sprint may not have written its
        # plan.json yet). A row with no/garbled started_at is treated as stale.
        if ts is not None and (now - ts).total_seconds() < max_age_minutes * 60:
            continue
        # Reconcile-aware settle: a sprint whose process died is NOT automatically
        # a failure. If its sprint branch already merged, the work shipped — settle
        # it `completed` rather than `needs_rework`, so a mid-run dashboard restart
        # can't false-fail a finished sprint (e.g. perf-coach sprint-83: 5/5 passed
        # + PR merged, orphaned by a restart). A merged branch is the one
        # unambiguous positive signal; for everything else stay conservative
        # (needs_rework), and the reconcile sweep promotes to ready_to_merge if the
        # tickets actually settled (a reconcile-only edge, never guarded).
        merged = False
        try:
            merged = f"sprint/{label}" in github_client.list_merged_sprint_branches(project)
        except Exception:
            merged = False
        try:
            if merged:
                db.record_sprint_finish(
                    label, end_reason="orphaned (work merged)", project=project,
                )
            else:
                db.record_sprint_needs_rework(
                    label, end_reason="orphaned (no live process)", project=project,
                )
            swept.append(label)
        except Exception:
            continue
    if swept:
        logger.info("[orphan-db-sweep] reconciled stale running rows: %s", swept)
    return swept


async def _periodic_orphan_sweep_loop() -> None:
    """Sweep orphan PID files every 5 minutes while the dashboard is running."""
    while True:
        await asyncio.sleep(300)
        try:
            _sweep_orphan_pid_files()
        except Exception as exc:
            print(f"[periodic-sweep] unexpected error: {exc}")
        try:
            _sweep_orphan_db_running_rows()
        except Exception as exc:
            print(f"[periodic-sweep] db-running sweep error: {exc}")


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




logger = logging.getLogger(__name__)


# ── API no-cache middleware (issue #249) ──────────────────────────────────────
# Ensure all /api/* responses carry Cache-Control: no-cache so browsers and
# proxies never serve stale API data on auto-refresh or manual refresh.





# ── request models ────────────────────────────────────────────────────────────

# RejectBody moved to routers/issues.py (issue #1267)


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


# ── agent endpoints ───────────────────────────────────────────────────────────

# GET /, /brief, /home, /overview, /projects/*, /project/* moved to routers/pages.py (issue #1248)
# /diagnostics moved to routers/system.py (issue #794)
# /api/health, /api/environment moved to routers/system.py (issue #1247)
# /api/version, /api/gh-auth-status moved to routers/system.py (issue #794)
# /api/agent-event, /api/token-usage, /api/events/test, /events moved to routers/logs.py


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


# /api/repo/config, /api/github/labels (GET+POST) moved to routers/system.py (issue #1247)
# /api/sprint-nav-status, /api/sprint-progress, /api/sprint-nav-summary moved to routers/sprint_nav.py (issue #1267)

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


def _settled_done_from_columns(total: int, columns: dict) -> int:
    """Canonical GitHub-derived "done" = settled work past SIT
    (uat + done + needs-rework) = total minus the not-yet-settled columns
    (backlog + in-progress + sit).

    Single source of the GitHub-side count: mirrors the frontend
    ``_snavSettledDone()`` and the live tier's ``done+skipped+failed`` so the nav
    pill, sidebar badge, and board running badge can never disagree. The old
    ``done + uat`` formula undercounted needs-rework tickets; ``total - backlog``
    (frontend) overcounted by treating in-progress + SIT as done.
    """
    columns = columns or {}
    return max(0, (total or 0) - (columns.get("backlog") or 0)
               - (columns.get("in-progress") or 0) - (columns.get("sit") or 0))


def _sprint_progress_file_path(project: str) -> Optional[Path]:
    """Return the path to the persisted sprint-progress JSON file for a project."""
    if not project:
        return None
    project_root = _project_root_path(project)
    return _commander_dir(project_root) / "runtime" / "sprint-progress.json"


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


# /api/issues, /api/issues/{issue_id}/approve, /api/issues/{issue_id}/reject,
# /api/issues/{issue_id}/close, /api/issues/{issue_id}/test-report moved to
# routers/issues.py (issue #1267)


# ── project endpoints ─────────────────────────────────────────────────────────









# /api/projects/{slug}/events (and _VALID_EVENT_SOURCES) moved to
# routers/activity.py (issue #794)


# ── Settings API (issue #639) ────────────────────────────────────────────────

import services.sprint_manager.settings_repo as _settings_repo  # noqa: E402
from services.sprint_manager.settings_schema import (  # noqa: E402
    APP_CONFIG_KEY,
    build_effective_response,
)
from services.sprint_manager.deploy_config_schema import (  # noqa: E402
    DEPLOY_CONFIG_KEY,
    seed_for as _deploy_seed_for,
    merge_seed as _deploy_merge_seed,
    # Re-exported for routers/system_misc.py's GET /api/deploy/overview, which
    # calls srv._deploy_known_slugs / srv._deploy_overview_entries_for. The
    # #1267 server.py slim-down dropped these two aliases — restore them. They
    # reach the router via server's globals().update(vars(startup)); unused here.
    known_deploy_slugs as _deploy_known_slugs,  # noqa: F401
    overview_entries_for as _deploy_overview_entries_for,  # noqa: F401
)

# Re-exported for routers/system_misc.py's POST /api/issues/{id}/estimate, which
# calls srv._ei_* and srv._minutes_from_letter. Same #1267 slim-down drop.
from services.sprint_manager.estimate_issue import (  # noqa: E402
    fetch_issue as _ei_fetch_issue,  # noqa: F401
    run_estimator as _ei_run_estimator,  # noqa: F401
    apply_label as _ei_apply_label,  # noqa: F401
    apply_estimated_status as _ei_apply_estimated_status,  # noqa: F401
)
from sizing import minutes_from_letter as _minutes_from_letter  # noqa: E402,F401


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


# Settings/deploy-config/fs/env-vars/scaffold/notes endpoints moved to
# routers/settings.py + routers/settings_service.py


from services.sprint_manager.deploy_config_schema import enrich_local_working_dirs as _enrich_working_dirs  # noqa: E402

from services.sprint_manager import deploy_actions as _deploy_actions  # noqa: E402


def _dashboard_listen_port() -> Optional[int]:
    """Port this dashboard process is bound to (from PORT env, default 8000)."""
    raw = os.environ.get("PORT", "8000")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _enrich_local_working_dirs(repo: str, resp: dict) -> None:
    """Fill working_dir for local entries (honours ``shared_working_dir`` seeds)."""
    envs = projects_module.get_project_environments(repo)
    if not envs:
        envs = _derive_project_environments(repo)
    _enrich_working_dirs(resp, envs)


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


from services.sprint_manager import render_actions as _render_actions  # noqa: E402


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
    listen_port = _dashboard_listen_port()
    if _deploy_actions.is_script_self_restart(entry, str(_REPO_ROOT), listen_port):
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










# ── Settings sync (issue #644) ───────────────────────────────────────────────




class _SyncDiffBody(BaseModel):
    direction: str  # "upload" or "fetch"




class _SyncCommitBody(BaseModel):
    direction: str  # "upload" or "fetch"




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




class _EnvEntry(BaseModel):
    env: str
    local_directory: str


class _PutEnvironmentsBody(BaseModel):
    environments: list[_EnvEntry]





class _TestCleanupBody(BaseModel):
    project: str
    keep: int = 100
    dry_run: bool = True


def _maintenance_repo_root(project: str) -> Path:
    """Resolve the git clone that holds tests/ for maintenance actions."""
    slug = _resolve_project_slug(project.strip())
    project_root = _project_root_path(slug)
    for candidate in (project_root / "uat", project_root, _REPO_ROOT):
        if (candidate / "tests").is_dir():
            return candidate.resolve()
    return project_root.resolve()










# ── sprint status endpoint (AC-6 from #24) ───────────────────────────────────

# Keyed by (project, sprint_label); populated by POST /api/sprint-status from sprint_manager.py
_sprint_statuses: dict[tuple, dict] = {}



SPRINTS_DIR = Path(__file__).parent / "sprints"


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


# ── home aggregated endpoint (#216) ── delegated to home_service (issue #1786)

def _invalidate_home_cache(slug: str) -> None:
    """Drop cached /api/home payload for *slug* — delegates to home_service."""
    try:
        from home_service import invalidate_home_by_slug  # noqa: PLC0415
        invalidate_home_by_slug(slug)
    except Exception:
        pass


def _home_project_data(proj: dict, running_sprints: list[dict]) -> dict:
    """Per-project home payload — delegates to home_service (issue #1786)."""
    from home_service import home_project_data  # noqa: PLC0415
    return home_project_data(proj, running_sprints, _sprint_statuses)


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
            labels = {lbl["name"] for lbl in issue.get("labels", [])}
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






# ── Plan-sprint endpoints (AC-14, AC-15, AC-16) ───────────────────────────────




# SprintLabelBody and POST /api/issues/{issue_id}/sprint-label moved to
# routers/issues.py (issue #1267)


class BatchLabelChange(BaseModel):
    issue_num: int
    sprint_label: str  # e.g. "sprint-3" or "backlog"


class BatchLabelsBody(BaseModel):
    changes: list[BatchLabelChange]
    project: Optional[str] = None




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


def _read_sprint_summary_url(project_root: Path, sprint_label: str, project: str = "") -> Optional[str]:
    """Return the summary-issue URL from the ingested DB row or state file."""
    row = db.get_sprint(sprint_label, project=project or None)
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


_RERUN_REUSABLE_PLAN_STATES = frozenset({"draft", "planned", "planning"})


def _reusable_rerun_child_label(
    project_root: Optional[Path],
    parent_label: str,
) -> Optional[str]:
    """Return a draft child plan already spawned from parent_label, if any."""
    if project_root is None:
        return None
    m = re.match(r"^(sprint-\d+)(?:\.(\d+))?$", parent_label)
    if not m:
        return None
    base = m.group(1)
    current_suffix = int(m.group(2)) if m.group(2) else 0
    child = f"{base}.{current_suffix + 1}"
    plan = _read_plan_json(project_root, child)
    if not plan or plan.get("parent") != parent_label:
        return None
    state = (plan.get("state") or "").lower()
    if state not in _RERUN_REUSABLE_PLAN_STATES:
        return None
    return child


def _next_sprint_sublabel(
    sprint_label: str,
    existing_label_names: set[str],
    project_root: Optional[Path] = None,
) -> str:
    """Compute the next sibling sub-label for a sprint re-run.

    sprint-25   → sprint-25.1 (or sprint-25.2 if sprint-25.1 already exists)
    sprint-25.1 → sprint-25.2 (next sibling, not a child)
    sprint-25.2 → sprint-25.3

    Reuses an existing draft/planned child plan (e.g. sprint-73.3 after a failed
    sprint-73.2) even when its GitHub label already exists.
    """
    reusable = _reusable_rerun_child_label(project_root, sprint_label)
    if reusable:
        return reusable

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


def _tester_clone_path(project_root: Path) -> Path:
    """Return the tester clone path for a project root (nested or flat layout)."""
    nested = project_root / "tester"
    if nested.exists():
        return nested
    flat = project_root.parent / f"{project_root.name}-tester"
    if flat.exists():
        return flat
    return nested


def _sprint_yaml_path(project_root: Path) -> Path:
    return _commander_dir(project_root) / "sprint.yaml"


def _ensure_sprint_yaml(project_root: Path, repo: str) -> Optional[Path]:
    """Ensure project-root sprint.yaml exists for sprint_manager dispatch.

    Without it sprint_manager falls back to stale ~/commander/work-* paths and
    design-doc guards check the wrong worktree. Generated from the nested/flat
    clone layout when missing; never overwrites an existing file.
    """
    path = _sprint_yaml_path(project_root)
    if path.exists():
        return path
    commander = _commander_dir(project_root)
    commander.mkdir(parents=True, exist_ok=True)
    api_url = (os.environ.get("DASHBOARD_API_URL") or "http://localhost:8000").strip()
    content = (
        f"repo_name: {repo}\n\n"
        "worktrees:\n"
        f"  coder: { _coder_clone_path(project_root) }\n"
        f"  tester: { _tester_clone_path(project_root) }\n"
        "  tester_app_subdir: apps/dashboard\n\n"
        "paths:\n"
        f"  scripts_dir: { _REPO_ROOT / 'scripts' }\n"
        f"  logs_dir: { commander / 'logs' }\n"
        f"  sprints_dir: { commander / 'sprints' }\n"
        f"  alerts_dir: { commander / 'alerts' }\n\n"
        "dashboard:\n"
        f"  api_url: {api_url}\n"
    )
    try:
        path.write_text(content, encoding="utf-8")
        return path
    except OSError:
        return None


def _sprint_manager_argv(sprint_label: str, repo: str, project_root: Path) -> list[str]:
    """Build sprint_manager CLI argv with repo + config so dispatch is project-scoped."""
    argv = [
        sys.executable,
        str(SPRINT_MANAGER_PATH),
        sprint_label,
        "--alert-mode",
        _ALERT_MODES,
        "--repo",
        repo,
    ]
    cfg = _ensure_sprint_yaml(project_root, repo)
    if cfg is not None:
        argv.extend(["--config", str(cfg)])
    return argv


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
# keep working unchanged. The actual re-export was dropped in the refactor, which
# broke run/finish-sprint with AttributeError at dispatch time — restored here.
def _build_sprint_subprocess_env() -> dict:
    # Lazy import: routers.dispatch_service resolves server/startup at request
    # time, so importing it at module load would be circular.
    from routers.dispatch_service import build_sprint_subprocess_env
    return build_sprint_subprocess_env()



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


def _reject_terminal_label_redispatch(project_root: Path, sprint_label: str, project: str = "") -> None:
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
        # Scope by project — labels are unique only per repo, so an unscoped lookup
        # blocks (e.g.) crux sprint-9 on commander's completed sprint-9 row.
        row = db.get_sprint(sprint_label, project=project or None)
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
        # end_reason 'queued' = the child was created (carrying its planned
        # tickets) but never dispatched — no run, no DB row, no state.json. That
        # is NOT a real run, even though tickets are present, so Run must be
        # allowed to dispatch it for the first time. Blocking here forced the
        # operator to Re-run into yet another never-dispatched child — the
        # 90.3 / 91.1 / 99.3 zombie loop.
        never_dispatched = (plan.get("end_reason") or "").lower() == "queued"
        if state in _TERMINAL_PLAN_STATES and plan.get("tickets") and not never_dispatched:
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




def _locally_signed_off_sprint_labels(project_root: Path) -> set[str]:
    """Sprint labels signed off via Merge Sprint without a GitHub Executive Summary.

    Board panes hide when ticket count is zero AND the sprint is finished.
    ``finished_sprints`` normally comes from the Executive Summary issue; this
    set covers merge_sprint / bulk_complete sign-offs that closed tickets but
    never posted (or failed to post) the summary issue.
    """
    labels: set[str] = set()
    sprints_dir = _commander_dir(project_root) / "sprints"
    if not sprints_dir.exists():
        return labels
    for plan_file in sprints_dir.glob("*-plan.json"):
        label = plan_file.name[: -len("-plan.json")]
        if not _SPRINT_LABEL_RE.match(label):
            continue
        plan = _read_plan_json(project_root, label)
        if not plan:
            continue
        if (plan.get("state") or "").lower() != "completed":
            continue
        er = (plan.get("end_reason") or "").lower()
        if er in ("merge_sprint", "bulk_complete"):
            labels.add(label)
    return labels


def _parse_pr_number_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = re.search(r"/pull/(\d+)", str(url))
    return int(m.group(1)) if m else None

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

    Ensures the lifecycle state is the sanctioned post-#1686 "draft" value so
    the sprint reads as ready-to-run once the gate is cleared.  Writing
    "planned" here is forbidden — it was deprecated in #1686 and nothing may
    emit it anew (#1773).
    """
    existing = _read_plan_json(project_root, sprint_label) or {}
    existing["signoff"] = {
        "state": "approved",
        "approver": approver,
        "approved_at": approved_at,
    }
    if existing.get("state") in (None, "draft"):
        existing["state"] = "draft"
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

    No-op when sign-off is disabled globally (COMMANDER_DISABLE_SIGNOFF /
    disable_sprint_signoff): every sprint must be runnable without approval.
    This mirrors the guard in routers/sprint_dispatch._sprint_signoff_state;
    the run gate uses this (startup) copy, so it needs its own check.
    """
    import config  # noqa: PLC0415
    if config.sprint_signoff_disabled():
        return
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
) -> bool:
    """Mirror a sprint lifecycle transition into the `sprints` table (issue #757).

    Best-effort on DB *errors*: any exception is swallowed so it never breaks the
    request — plan.json dual-write remains the cache and the sweep paths reconcile
    drift. But a state-machine *rejection* (illegal edge → accepted=False) is NOT
    an exception — it returns False here so callers can surface a silent no-op
    instead of assuming success. Pass actor="reconcile" via extra_fields to
    complete a `needs_rework` lineage that has merged (the B2 edge is
    reconcile-only). Returns True on success, False on rejection/error.
    """
    actor = extra_fields.get("actor", "manager")
    try:
        if state == "running":
            db.record_sprint_start(
                sprint_label, project=project or "",
                started_at=extra_fields.get("started_at"),
            )
        elif state == "completed":
            res = db.record_sprint_finish(
                sprint_label,
                ended_at=extra_fields.get("ended_at"),
                end_reason=extra_fields.get("end_reason"),
                project=project or "",
                actor=actor,
            )
            return bool(getattr(res, "accepted", True))
        elif state == "ready_to_merge":
            db.record_sprint_ready_to_merge(
                sprint_label,
                end_reason=extra_fields.get("end_reason"),
                ended_at=extra_fields.get("ended_at"),
                project=project or "",
            )
        elif state in ("needs_rework", "cancelled", "failed"):
            # cancelled/failed are legacy callers — all bad endings land in
            # needs_rework under the unified lifecycle.
            db.record_sprint_needs_rework(
                sprint_label,
                end_reason=extra_fields.get("end_reason"),
                ended_at=extra_fields.get("ended_at"),
                project=project or "",
            )
    except Exception:
        return False
    return True


def _sprint_db_mark_merged_completed(
    sprint_label: str,
    project: str,
    **extra_fields,
) -> bool:
    """Mark a sprint completed after its branch merged, using the right actor.

    ``needs_rework`` superseded ancestors need actor=reconcile (B2 edge).
    ``running`` / ``ready_to_merge`` orphans need actor=manager. Try both when
    unsure so bulk-complete resume does not wedge on an illegal edge.

    Sprints that ran before per-project DB rows existed (or never got a lifecycle
    write) read as ``draft`` — ``draft→completed`` is illegal. After the git
    merge already landed, bootstrap through ``ready_to_merge`` first.
    """
    try:
        row = db.get_sprint(sprint_label, project=project or None)
        current = db.canonical_lifecycle((row or {}).get("state") or "draft")
    except Exception:
        current = "unknown"
    # partial_finished is derived-only but may appear on legacy rows; treat like
    # ready_to_merge for post-merge settlement.
    if current in ("draft", "planned", "unknown", "partial_finished"):
        _sprint_db_set_state(
            sprint_label,
            project,
            "ready_to_merge",
            actor="manager",
            end_reason=extra_fields.get("end_reason") or "merge_sprint",
            ended_at=extra_fields.get("ended_at"),
        )
        try:
            row = db.get_sprint(sprint_label, project=project or None)
            current = db.canonical_lifecycle((row or {}).get("state") or "draft")
        except Exception:
            current = "unknown"
    if current == "needs_rework":
        actors = ("reconcile",)
    else:
        actors = ("manager", "reconcile")
    for actor in actors:
        ok = _sprint_db_set_state(
            sprint_label, project, "completed", actor=actor, **extra_fields,
        )
        if ok is not False:
            return True
    return False


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


def _is_sprint_running(project_root: Path, sprint_label: str) -> bool:
    """Check if a sprint is running. Pure read — zero DB writes.

    The durable `sprints` table is the authoritative source (issue #757): a
    sprint is running only when DB state='running' AND its PID is alive. Any
    terminal DB state is treated as definitive — the reconcile service handles
    state corrections, not this function. Falls back to plan.json + PID-file
    scanning only for legacy sprints that have no DB row yet.
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
            # Terminal DB state is authoritative — never flip it back to running.
            # The reconcile service and startup sweep are now correct; a live PID
            # alongside a terminal row is not a sign of flip-flop — it is a race
            # where the manager is shutting down. Report not running.
            return False
        if _sprint_pid_alive(project_root, sprint_label):
            return True
        # DB=running but PID dead — report not running (reconciler will fix state).
        _log.warning(
            "Sprint %s: DB state=running but no alive PID — reporting not running",
            sprint_label,
        )
        return False

    plan = _read_plan_json(project_root, sprint_label)
    if plan is not None:
        plan_state = plan.get("state")
        if plan_state in _NOT_RUNNING_PLAN_STATES:
            # Terminal plan.json state is authoritative — never flip it back.
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
            # plan.json=running but no alive PID — report not running (reconciler will fix state).
            _log.warning(
                "Sprint %s: plan.json=running but no alive PID — reporting not running",
                sprint_label,
            )
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
                # Terminal plan.json state is authoritative — skip; no heal-back.
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
    use_cline_followups: bool = False




# READ-only: recognises both spellings so migration removes whichever is present
_MIGRATION_STATUS_LABELS = {"UAT", "UAT-approved", "SIT", "in-progress", "needs-rework", "need-rework"}


# ── Sprint-issues helpers ─────────────────────────────────────────────────────

def _primary_sprint_label(iss: dict) -> str | None:
    """Return the sprint label used for board column grouping (first sprint-* label)."""
    for lbl in iss.get("labels", []):
        if _SPRINT_LABEL_RE.match(lbl["name"]):
            return lbl["name"]
    return None


def _get_sprint_issues(project: str, sprint_label: str) -> list[dict]:
    """Fetch open issues whose primary sprint label matches sprint_label.

    Matches the sprint-management board: when an issue carries multiple sprint-*
    labels (e.g. after a partial move), only the first sprint label in GitHub's
    label order determines its column — not every attached sprint label.
    """
    issues = github_client.list_open_issues_with_body(repo_name=project, limit=200)
    return [iss for iss in issues if _primary_sprint_label(iss) == sprint_label]


# ── Estimate-summary helpers (issue #211) ────────────────────────────────────

def _size_to_minutes(size: str) -> int:
    """Map a T-shirt size label to agent-effort minutes via SIZE_TO_MINUTES."""
    return _SIZE_TO_MINUTES.get(size, 0)




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




def _rerun_policy(labels: set[str]) -> tuple[str, list[str]]:
    """Return (action, labels_to_strip) for a sprint ticket based on its current labels.

    action:
        'skip'             — UAT / UAT-approved / blocked; leave ticket untouched
        'dispatch_tester'  — SIT ticket; send to tester directly; SIT label preserved
        'dispatch_coder'   — all other states; send to coder; strip appropriate labels

    'blocked' tickets are skipped even when they also carry needs-rework or SIT:
    the operator must manually reset the label before the ticket re-enters auto-rerun
    (issue #2033, AC-2).
    """
    if "blocked" in labels:
        return "skip", []
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
    # A ticket left mid-review from a prior attempt carries a stale session state;
    # re-run starts a fresh attempt, so reset it to clean backlog rather than carry
    # the prior `code-review` label forward (it would mislead the board and the
    # next reviewer about where the ticket actually stands).
    "code-review",
})


def _stale_session_labels(labels) -> list[str]:
    """Return the session-state labels present in `labels`, sorted.

    Used on sprint re-run to strip stale status labels (issue #811). Only
    labels in the `_SESSION_STATE_LABELS` taxonomy are returned; everything
    else is preserved. Returns an empty list when no stale labels are present,
    so callers can re-run cleanly when there is nothing to strip.
    """
    return sorted(set(labels) & _SESSION_STATE_LABELS)







def _has_rework_tickets(sprint_label: str, project: str) -> bool:
    """Pure read-only signal: True when the sprint has open work tickets with rework labels.

    This function is a pure GitHub-label-derived signal. It is read-only and
    must never trigger a sprint state write directly. Its return value is valid
    only as input to reconcile proposals (e.g. fed into
    ``_github_reconcile_row`` which feeds ``transition_sprint_state`` via the
    reconcile actor). Call sites that consume this signal may only set local
    display variables — they must not pass the result directly to a
    state-writing function.

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


def _state_data_is_dry_run_only(state_data: dict) -> bool:
    """True when state.json reflects a --dry-run pass (no coder/tester dispatch)."""
    issues = state_data.get("issues") or []
    if not issues:
        return False
    if any(i.get("coder_started_at") or i.get("tester_started_at") for i in issues):
        return False
    return any((i.get("skip_reason") or "").lower() == "dry-run" for i in issues)


def _sprint_has_own_run_outcome(project_root: Path, sprint_label: str, project: str = "") -> bool:
    """True when outcome data for *this* label exists (not a sibling/base run)."""
    plan = _read_plan_json(project_root, sprint_label)
    if plan and plan.get("state") in ("planning", "draft", "planned"):
        return False

    # Scope by project — sprint labels are unique only per repo (cross-project leak).
    row = db.get_sprint(sprint_label, project=project or None)
    if row and row.get("run_ingested_at"):
        return True

    from routers import sprint_artifact_service  # noqa: PLC0415
    sprints_dir = _commander_dir(project_root) / "sprints"
    resolved = sprint_artifact_service.resolve_state_path(sprints_dir, sprint_label)
    if resolved is None:
        return False
    try:
        state_data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    return not _state_data_is_dry_run_only(state_data)


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
    if lifecycle == "needs_rework" and (end_reason or "") == "natural":
        try:
            _raw = json.loads(row.get("issues_json") or "[]")
            if _raw and all(
                (i.get("state") or "").lower() == "merged"
                or (i.get("agent_status") or "").lower() in ("completed", "done")
                for i in _raw
            ):
                lifecycle = "ready_to_merge"
        except (json.JSONDecodeError, TypeError):
            pass
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

    # Rec 2c — union agent_runs so the outcome band agrees with the History
    # ledger. History (sprint_history_service._finalize_records) unions tickets
    # recorded in agent_runs but absent from the ingested issues_json snapshot
    # (e.g. merged in an EARLIER run of this sprint). Without the same union the
    # outcome band and the History row showed different counts for one sprint.
    # Additive only — never rewrites a ticket already in result_issues.
    try:
        from routers import sprint_history_service  # noqa: PLC0415
        _seen = {str(i["number"]) for i in result_issues if i.get("number") is not None}
        for _extra in sprint_history_service._issues_from_agent_runs(sprint_label):
            _tid = _extra.get("ticket_id")
            if _tid is None:
                _tid = _extra.get("number")
            if _tid is None or int(_tid) <= 0:
                continue
            _eid = str(_tid)
            if _eid in _seen:
                continue
            _st = (_extra.get("state") or "").lower()  # merged | closed | open
            _oc = "done" if _st == "merged" else ("failed" if _st == "closed" else "skipped")
            result_issues.append({
                "number": int(_tid),
                "title": _extra.get("title", ""),
                "outcome": _oc,
                "elapsed_secs": None,
                "failure_reason": None,
            })
            _seen.add(_eid)
    except Exception:
        pass

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
    pr_number = row.get("pr_number") if row.get("pr_number") is not None else enrich.get("pr_number")
    pr_url = None
    if pr_number:
        try:
            pr_repo = github_client.get_repo_for_operation(project)
            pr_url = f"https://github.com/{pr_repo}/pull/{int(pr_number)}"
        except Exception:
            pr_url = None

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
        "pr_number": pr_number,
        "pr_url": pr_url,
    }


# Merge Sprint (completed) closes the chain; ready_to_merge is still open work.
_CHILD_SETTLED_STATES = frozenset({"completed", "deleted"})
_SPRINT_WORK_EXCLUDE_LABELS = frozenset({"sprint-summary", "docs", "documentation"})
_SPRINT_UAT_LABELS = frozenset({"UAT", "UAT-approved", "released"})
# Canonical lifecycle states that mean the sprint has finished (issue #1093).
_OUTCOME_TERMINAL_STATES = frozenset({"completed", "needs_rework", "ready_to_merge", "deleted"})


def _sprint_work_tickets_all_uat(project: str, sprint_label: str) -> bool:
    """True when every non-summary open issue on the label is UAT (or label is empty)."""
    try:
        issues = _get_sprint_issues(project, sprint_label)
    except Exception:
        return False
    work = [
        iss for iss in issues
        if not ({lbl["name"] for lbl in iss.get("labels", [])} & _SPRINT_WORK_EXCLUDE_LABELS)
    ]
    if not work:
        return True
    return all(
        bool({lbl["name"] for lbl in iss.get("labels", [])} & _SPRINT_UAT_LABELS)
        for iss in work
    )


def _child_sprint_settled(project_root: Path, project: str, child_label: str) -> bool:
    """Child is settled when plan says so or all its work tickets are UAT on GitHub."""
    plan = _read_plan_json(project_root, child_label)
    plan_state = (plan.get("state") or "").lower() if plan else ""
    if plan_state in _CHILD_SETTLED_STATES:
        return True
    return _sprint_work_tickets_all_uat(project, child_label)


def _derive_outcome_lifecycle(
    sprint_label: str,
    project_root: Path,
    project: str,
    plan_state: str,
    pane_state: str,
    failed_count: int,
) -> str:
    """Board/history lifecycle — DB-only: derives partial_finished when a child is unsettled.

    Reads parent canonical state and child rows exclusively from the sprints DB
    table (issue #1093). No GitHub label lookups, no disk globs.
    """
    row = db.get_sprint(sprint_label, project=project or None)
    if row is None:
        return db.canonical_lifecycle(pane_state)
    parent_state = db.canonical_lifecycle(row["state"])
    if parent_state not in _OUTCOME_TERMINAL_STATES:
        return parent_state
    children = db.get_sprint_children(sprint_label, project=project or None)
    if not children:
        return parent_state
    unsettled = [
        c for c in children
        if db.canonical_lifecycle(c["state"]) not in _CHILD_SETTLED_STATES
    ]
    if unsettled:
        return "partial_finished"
    return parent_state




# ── Estimate-vs-Actual report (issue #575) ───────────────────────────────────



# ── Estimator calibration view (issue #576) ──────────────────────────────────



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
    datetime.now(tz=timezone.utc).date()

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
    by_sprint: list[dict] = []
    issue_rejections: list[dict] = []

    # Token counts are sourced from the status files (model_name is joined from
    # the token_usage table below for pricing only).
    status_tokens_in = 0
    status_tokens_out = 0

    estimates_dir = _commander_dir(project_root) / "estimates"

    if sprints_dir.exists():
        def _sprint_num_key(p: Path) -> int:
            m = re.search(r"sprint-(\d+)-state", p.name)
            return int(m.group(1)) if m else 0

        for state_file in sorted(sprints_dir.glob("sprint-*-state.json"), key=_sprint_num_key):
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

                if rejections > 0:
                    issue_num = issue.get("number")
                    if issue_num is not None:
                        issue_rejections.append({"number": issue_num, "rejections": rejections})

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

            # Per-sprint summary entry
            sp_total = len(sprint_done)
            sp_fp = 0
            sp_rw = 0
            sp_coder_mins: list[float] = []
            for issue in sprint_done:
                rej = _count_tester_rejections(issue)
                if rej == 0:
                    sp_fp += 1
                else:
                    sp_rw += 1
                cs = issue.get("coder_started_at")
                ce = issue.get("coder_finished_at")
                if cs and ce:
                    try:
                        _s = datetime.fromisoformat(cs.rstrip("Z")).replace(tzinfo=timezone.utc)
                        _e = datetime.fromisoformat(ce.rstrip("Z")).replace(tzinfo=timezone.utc)
                        sp_coder_mins.append((_e - _s).total_seconds() / 60.0)
                    except Exception:
                        pass
            by_sprint.append({
                "sprint_label": sprint_label_val,
                "first_pass_rate": round(sp_fp / sp_total, 4) if sp_total else 0.0,
                "rework_rate": round(sp_rw / sp_total, 4) if sp_total else 0.0,
                "avg_coder_minutes": round(sum(sp_coder_mins) / len(sp_coder_mins), 2) if sp_coder_mins else 0.0,
                "wall_clock_minutes": round(wall_clock_secs / 60.0, 2),
                "tickets_done": sp_total,
            })

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

    most_reworked = sorted(issue_rejections, key=lambda x: x["rejections"], reverse=True)[:5]

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
        "most_reworked": most_reworked,
        "by_sprint": by_sprint,
    }




# ── Calibration analytics endpoint (issue #649) ──────────────────────────────

_CALIBRATION_SIZES = ("S", "M", "L", "XL")
_CALIBRATION_SIZE_SETTING_KEYS = {
    "S": "estimation_s_minutes",
    "M": "estimation_m_minutes",
    "L": "estimation_l_minutes",
    "XL": "estimation_xl_minutes",
}
_CALIBRATION_CACHE_VERSION = 1
_CALIBRATION_DONE_STATUSES = frozenset({"done", "uat", "merged", "passed"})


def _calibration_cache_path(commander_dir: Path) -> Path:
    return commander_dir / "calibration_cache.json"


def _calibration_empty_by_size() -> dict[str, dict]:
    return {
        sz: {
            "count": 0,
            "min_minutes": None,
            "avg_minutes": None,
            "max_minutes": None,
        }
        for sz in _CALIBRATION_SIZES
    }


def _calibration_empty_cache() -> dict:
    return {
        "version": _CALIBRATION_CACHE_VERSION,
        "archive_bootstrap_done": False,
        "by_size": _calibration_empty_by_size(),
        "processed": [],
        "points": [],
    }


def _load_calibration_cache(commander_dir: Path) -> dict:
    path = _calibration_cache_path(commander_dir)
    if not path.is_file():
        return _calibration_empty_cache()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _calibration_empty_cache()
    if data.get("version") != _CALIBRATION_CACHE_VERSION:
        return _calibration_empty_cache()
    by_size = data.get("by_size") or {}
    for sz in _CALIBRATION_SIZES:
        if sz not in by_size:
            by_size[sz] = _calibration_empty_by_size()[sz]
    data["by_size"] = by_size
    if not isinstance(data.get("processed"), list):
        data["processed"] = []
    if not isinstance(data.get("points"), list):
        data["points"] = []
    return data


def _save_calibration_cache(commander_dir: Path, cache: dict) -> None:
    path = _calibration_cache_path(commander_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _calibration_add_sample(
    cache: dict,
    size: str,
    actual_minutes: float,
    point: dict | None = None,
) -> None:
    """Incrementally update per-size count/min/avg/max (no full rescan)."""
    bucket = cache["by_size"][size]
    val = round(actual_minutes, 2)
    count = int(bucket["count"] or 0)
    if count == 0:
        bucket["count"] = 1
        bucket["min_minutes"] = val
        bucket["avg_minutes"] = val
        bucket["max_minutes"] = val
    else:
        old_avg = float(bucket["avg_minutes"])
        new_count = count + 1
        bucket["avg_minutes"] = round((old_avg * count + val) / new_count, 2)
        bucket["count"] = new_count
        bucket["min_minutes"] = round(min(float(bucket["min_minutes"]), val), 2)
        bucket["max_minutes"] = round(max(float(bucket["max_minutes"]), val), 2)
    if point is not None:
        cache["points"].append(point)


def _calibration_issue_sample(
    issue: dict,
    estimates_dir: Path,
    configured_minutes: dict[str, int],
) -> tuple[str, float, dict] | None:
    """Return (size, actual_minutes, point_dict) for one completed ticket."""
    if issue.get("status") not in _CALIBRATION_DONE_STATUSES:
        return None
    issue_num = issue.get("number")
    size = None
    if issue_num is not None and estimates_dir.is_dir():
        est_file = estimates_dir / f"issue-{issue_num}.json"
        if est_file.is_file():
            try:
                est = json.loads(est_file.read_text(encoding="utf-8"))
                size = est.get("size")
            except (json.JSONDecodeError, OSError):
                size = None
    if size not in _CALIBRATION_SIZES:
        return None

    coder_min = _analytics_elapsed_minutes(
        issue.get("coder_started_at"), issue.get("coder_finished_at"))
    tester_min = _analytics_elapsed_minutes(
        issue.get("tester_started_at"), issue.get("tester_finished_at"))
    if coder_min is None and tester_min is None:
        return None

    actual_minutes = (coder_min or 0.0) + (tester_min or 0.0)
    point = {
        "issue_number": issue_num,
        "estimated_size": size,
        "estimated_minutes": configured_minutes[size],
        "actual_minutes": round(actual_minutes, 2),
    }
    return size, actual_minutes, point


def _calibration_state_key(state_file: Path, sprints_dir: Path, issue_num: int) -> str:
    rel = state_file.relative_to(sprints_dir)
    return f"{rel.as_posix()}/{issue_num}"


def _calibration_absorb_state_file(
    cache: dict,
    state_file: Path,
    sprints_dir: Path,
    estimates_dir: Path,
    configured_minutes: dict[str, int],
    processed: set[str],
) -> bool:
    """Merge new tickets from one state file into cache; return True if anything added."""
    try:
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    changed = False
    for issue in state_data.get("issues", []):
        issue_num = issue.get("number")
        if issue_num is None:
            continue
        key = _calibration_state_key(state_file, sprints_dir, issue_num)
        if key in processed:
            continue
        sample = _calibration_issue_sample(issue, estimates_dir, configured_minutes)
        if sample is None:
            continue
        size, actual_minutes, point = sample
        _calibration_add_sample(cache, size, actual_minutes, point)
        cache["processed"].append(key)
        processed.add(key)
        changed = True
    return changed


def _refresh_calibration_cache(
    project_root: Path,
    configured_minutes: dict[str, int],
) -> dict:
    """Merge sprint state files into calibration_cache.json (durable local store).

    Live ``sprint-*-state.json`` files and ``archive/`` copies are scanned every
    refresh; the ``processed`` list prevents double-counting. Aggregates in
    ``by_size`` persist even after state files are archived or deleted.
    """
    commander = _commander_dir(project_root)
    sprints_dir = commander / "sprints"
    estimates_dir = commander / "estimates"
    cache = _load_calibration_cache(commander)
    processed = set(cache.get("processed") or [])
    changed = False

    if not sprints_dir.is_dir():
        return cache

    archive_dir = sprints_dir / "archive"
    if archive_dir.is_dir():
        for state_file in sorted(archive_dir.glob("sprint-*-state.json")):
            if _calibration_absorb_state_file(
                cache, state_file, sprints_dir, estimates_dir,
                configured_minutes, processed,
            ):
                changed = True

    for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
        if _calibration_absorb_state_file(
            cache, state_file, sprints_dir, estimates_dir,
            configured_minutes, processed,
        ):
            changed = True

    if not cache.get("archive_bootstrap_done"):
        cache["archive_bootstrap_done"] = True
        changed = True

    if changed:
        _save_calibration_cache(commander, cache)
    return cache


def _iter_calibration_state_files(
    sprints_dir: Path,
    *,
    include_archive: bool = False,
) -> list[Path]:
    """State files for a full scan (optionally including archive/)."""
    if not sprints_dir.is_dir():
        return []
    roots = [sprints_dir]
    if include_archive:
        archive = sprints_dir / "archive"
        if archive.is_dir():
            roots.append(archive)
    files: list[Path] = []
    for root in roots:
        files.extend(sorted(root.glob("sprint-*-state.json")))
    return files


def _compute_calibration_from_files(
    project_root: Path,
    configured_minutes: dict[str, int],
    since_dt: datetime | None,
    until_dt: datetime | None,
    sprint_filter: str | None,
    *,
    include_archive: bool,
) -> dict:
    """Full scan for scoped queries (since/until/sprint filters)."""
    sprints_dir = _commander_dir(project_root) / "sprints"
    estimates_dir = _commander_dir(project_root) / "estimates"

    by_size = {
        sz: {
            "configured_minutes": configured_minutes[sz],
            **(_calibration_empty_by_size()[sz]),
        }
        for sz in _CALIBRATION_SIZES
    }
    buckets: dict[str, list[float]] = {sz: [] for sz in _CALIBRATION_SIZES}
    points: list[dict] = []

    for state_file in _iter_calibration_state_files(
        sprints_dir, include_archive=include_archive,
    ):
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
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
            sample = _calibration_issue_sample(issue, estimates_dir, configured_minutes)
            if sample is None:
                continue
            size, actual_minutes, point = sample
            buckets[size].append(actual_minutes)
            points.append(point)

    for sz in _CALIBRATION_SIZES:
        vals = buckets[sz]
        if vals:
            by_size[sz]["count"] = len(vals)
            by_size[sz]["min_minutes"] = round(min(vals), 2)
            by_size[sz]["avg_minutes"] = round(sum(vals) / len(vals), 2)
            by_size[sz]["max_minutes"] = round(max(vals), 2)

    return {"by_size": by_size, "points": points}


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

    project_root = _project_root_path(repo)

    # Scoped queries need a per-request full scan (archive included).
    if since or until or sprint_filter:
        return _compute_calibration_from_files(
            project_root,
            configured_minutes,
            since_dt,
            until_dt,
            sprint_filter,
            include_archive=True,
        )

    cache = _refresh_calibration_cache(project_root, configured_minutes)
    by_size = {
        sz: {
            "configured_minutes": configured_minutes[sz],
            "count": cache["by_size"][sz]["count"],
            "min_minutes": cache["by_size"][sz]["min_minutes"],
            "avg_minutes": cache["by_size"][sz]["avg_minutes"],
            "max_minutes": cache["by_size"][sz]["max_minutes"],
        }
        for sz in _CALIBRATION_SIZES
    }
    return {"by_size": by_size, "points": list(cache.get("points") or [])}




# ── Daily report endpoint (issue #478) ───────────────────────────────────────





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


def children_of(parent_label: str, project_root: Path | None = None, project: str | None = None) -> list[str]:
    """Return child sprint labels whose parent is parent_label, sorted by sub-index.

    Primary: queries sprints DB WHERE parent_label matches.
    When project is provided, scoped to that project (issue #1464).
    Fallback (rows predating parent-linkage tracking): plan.json disk glob.
    """
    try:
        with db.get_conn() as conn:
            db._create_sprint_lifecycle_tables(conn)
            if project:
                rows = conn.execute(
                    "SELECT label FROM sprints WHERE parent_label = ? AND project = ?",
                    (parent_label, project),
                ).fetchall()
            else:
                logger.warning(
                    "children_of called without project for parent %r — label-only fallback",
                    parent_label,
                )
                rows = conn.execute(
                    "SELECT label FROM sprints WHERE parent_label = ?",
                    (parent_label,),
                ).fetchall()
        if rows:
            return sorted([r["label"] for r in rows], key=_sprint_label_sub_index)
    except Exception:
        pass
    if project_root is None:
        return []
    # Fallback: disk glob for sprints predating DB parent-linkage tracking
    sprints_dir = _commander_dir(project_root) / "sprints"
    if not sprints_dir.is_dir():
        return []
    base_m = re.match(r"^sprint-(\d+)$", parent_label)
    if base_m:
        # Fast path: base sprint → glob sprint-N.* directly
        n = base_m.group(1)
        children: list[str] = []
        for path in sprints_dir.glob(f"sprint-{n}.*-plan.json"):
            lbl = path.name.replace("-plan.json", "")
            if _is_child_sprint_label(lbl):
                children.append(lbl)
        return sorted(children, key=_sprint_label_sub_index)
    # General path: child sprint as parent — scan all plan files for parent match
    children = []
    for plan_file in sprints_dir.glob("sprint-*-plan.json"):
        lbl = plan_file.name[: -len("-plan.json")]
        plan = _read_plan_json(project_root, lbl)
        if plan and plan.get("parent") == parent_label and lbl not in children:
            children.append(lbl)
    return sorted(children, key=_sprint_label_sub_index)


def _sprint_merge_parent_label(project_root: Path, label: str) -> str:
    """Immediate parent for a sprint branch merge (plan.json parent, else base)."""
    if not _is_child_sprint_label(label):
        return _sprint_label_base(label)
    plan = _read_plan_json(project_root, label)
    parent = (plan.get("parent") or "").strip() if plan else ""
    if parent and _SPRINT_LABEL_RE.match(parent):
        return parent
    return _sprint_label_base(label)


_BULK_COMPLETE_CHILD_READY_STATES: frozenset[str] = frozenset({
    "completed", "deleted", "ready_to_merge",
})


def _bulk_complete_child_state(project_root: Path, sprint_label: str, project: str | None = None) -> str:
    """Lifecycle state for bulk-complete gating (canonical accessor only)."""
    return sprint_state.current(sprint_label, project)


def _bulk_complete_lineage_settled(project_root: Path, sprint_label: str, project: str | None = None) -> bool:
    """True when this label or a rerun child under it finished its run."""
    if _bulk_complete_child_state(project_root, sprint_label, project) in _BULK_COMPLETE_CHILD_READY_STATES:
        return True
    sprints_dir = _commander_dir(project_root) / "sprints"
    if not sprints_dir.is_dir():
        return False
    for path in sprints_dir.glob("*-plan.json"):
        lbl = path.name.replace("-plan.json", "")
        if lbl == sprint_label:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (data.get("parent") or "") != sprint_label:
            continue
        if _bulk_complete_lineage_settled(project_root, lbl, project):
            return True
    return False


def _bulk_complete_unsettled_children(project_root: Path, base_label: str, project: str | None = None) -> list[str]:
    """Child sprint labels whose run (or rerun chain) is not yet settled."""
    unsettled: list[str] = []
    for child_label in children_of(base_label, project_root, project=project):
        if not _bulk_complete_lineage_settled(project_root, child_label, project):
            unsettled.append(child_label)
    return unsettled


def _bulk_complete_assert_children_completed(project_root: Path, base_label: str, project: str | None = None) -> None:
    unsettled = _bulk_complete_unsettled_children(project_root, base_label, project=project)
    if unsettled:
        raise HTTPException(
            409,
            detail=(
                "Bulk complete requires every child sprint run to finish — "
                f"still open: {', '.join(unsettled)}"
            ),
        )


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


# Doc paths auto-resolved from develop before sprint→develop merge (CHANGELOG/README drift).
_DEVELOP_MERGE_DOC_PATHS: frozenset[str] = frozenset({
    "CHANGELOG.md", "README.md", "CLAUDE.md", "AGENTS.md",
})

_CHANGELOG_SPRINT_HEADER_RE = re.compile(r"^## Sprint (\d+(?:\.\d+)?)\s*$", re.MULTILINE)


def _is_merge_conflict_detail(detail: str) -> bool:
    """True when a merge/PR failure message indicates a merge conflict."""
    d = (detail or "").lower()
    return any(
        token in d
        for token in (
            "conflict", "conflicting", "mergeable", "dirty",
            "can't be merged", "cannot be merged", "not mergeable",
        )
    )


def _git_repo_for_merge(repo: str) -> Path | None:
    """Best-effort local clone for pre-merge git operations."""
    project_root = _project_root_path(repo)
    for candidate in (_coder_clone_path(project_root), project_root):
        if (candidate / ".git").exists():
            return candidate
    return None


def _is_doc_merge_path(path: str) -> bool:
    if path in _DEVELOP_MERGE_DOC_PATHS:
        return True
    return path.startswith("docs/") and path.endswith(".md")


def _is_union_merge_safe_path(path: str) -> bool:
    """Files where append-only additions can be auto-resolved via union merge (issue #1898).

    Covers SCHEMA.md and any models.py (at any directory depth). Never auto-resolves
    files that carry executable logic beyond model definitions.
    """
    name = Path(path).name.lower()
    return name == "schema.md" or name == "models.py"


def _has_overlapping_conflict_in_diff3(diff3_text: str) -> bool:
    """True when any conflict region in diff3 output has non-empty base content.

    Append-only conflicts have an empty ||||||| base section (both sides added
    new content at the same spot). Overlapping modifications have non-empty base
    sections (both sides changed the same existing lines) — these are not safe to
    auto-resolve via union (issue #1898).
    """
    in_base = False
    base_lines: list = []
    for line in diff3_text.splitlines():
        if line.startswith("<<<<<<<"):
            in_base = False
            base_lines = []
        elif line.startswith("|||||||"):
            in_base = True
        elif in_base and line.startswith("======="):
            if any(ln.strip() for ln in base_lines):
                return True
            in_base = False
            base_lines = []
        elif in_base:
            base_lines.append(line)
    return False


def _resolve_union_merge_conflicts(cwd: Path, paths: list) -> tuple:
    """Attempt union merge on each path. Returns (all_resolved, still_conflicting).

    Strategy (issue #1898):
    1. Run git merge-file --diff3 to inspect conflict regions.
    2. If any conflict region has non-empty base content (both sides modified the
       same existing lines), treat the file as NOT auto-resolvable.
    3. Otherwise all conflicts are append-only — apply git merge-file --union which
       cleanly includes both sides' additions without conflict markers.
    Resolved files are written in-place and staged with git add.
    """
    import tempfile as _tempfile
    still_conflicting: list = []
    for path in paths:
        def _git_show(stage: str) -> Optional[str]:
            r = subprocess.run(
                ["git", "show", f":{stage}:{path}"],
                cwd=cwd, capture_output=True, text=True, timeout=30,
            )
            return r.stdout if r.returncode == 0 else None

        ours_text = _git_show("2")
        base_text = _git_show("1") or ""
        theirs_text = _git_show("3")

        if ours_text is None or theirs_text is None:
            still_conflicting.append(path)
            continue

        ours_tmp = base_tmp = theirs_tmp = diff3_tmp = None
        try:
            with _tempfile.NamedTemporaryFile(
                mode="w", suffix=".ours", delete=False, encoding="utf-8"
            ) as f:
                ours_tmp = f.name
                f.write(ours_text)
            with _tempfile.NamedTemporaryFile(
                mode="w", suffix=".base", delete=False, encoding="utf-8"
            ) as f:
                base_tmp = f.name
                f.write(base_text)
            with _tempfile.NamedTemporaryFile(
                mode="w", suffix=".theirs", delete=False, encoding="utf-8"
            ) as f:
                theirs_tmp = f.name
                f.write(theirs_text)

            # Step 1: diff3 check — detect overlapping (non-append-only) conflicts
            import shutil as _shutil
            diff3_tmp = ours_tmp + ".diff3"
            _shutil.copy(ours_tmp, diff3_tmp)
            subprocess.run(
                ["git", "merge-file", "--diff3", diff3_tmp, base_tmp, theirs_tmp],
                capture_output=True, text=True,
            )
            with open(diff3_tmp, encoding="utf-8") as f:
                diff3_result = f.read()
            try:
                os.unlink(diff3_tmp)
            except OSError:
                pass
            diff3_tmp = None

            if _has_overlapping_conflict_in_diff3(diff3_result):
                still_conflicting.append(path)
                continue

            # Step 2: all conflicts are append-only — union merge is safe
            subprocess.run(
                ["git", "merge-file", "--union", ours_tmp, base_tmp, theirs_tmp],
                capture_output=True, text=True,
            )
            with open(ours_tmp, encoding="utf-8") as f:
                merged = f.read()
        finally:
            for tmp in (base_tmp, theirs_tmp, ours_tmp, diff3_tmp):
                if tmp:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

        (cwd / path).write_text(merged, encoding="utf-8")
        subprocess.run(
            ["git", "add", path], cwd=cwd, capture_output=True, text=True, timeout=30,
        )

    return len(still_conflicting) == 0, still_conflicting


# ── Conflict-blocked state (issue #1898) ─────────────────────────────────────
# Persisted in plan.json under "conflict_blocked": {"files": [...], "at": "<iso>"}
# so the GET /conflict-status endpoint can surface it to autonomous callers.

def _sprint_set_conflict_blocked(project_root: Path, sprint_label: str, files: list) -> None:
    existing = _read_plan_json(project_root, sprint_label) or {}
    existing["conflict_blocked"] = {
        "files": files,
        "at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    _write_plan_json(project_root, sprint_label, existing)


def _sprint_clear_conflict_blocked(project_root: Path, sprint_label: str) -> None:
    existing = _read_plan_json(project_root, sprint_label)
    if existing and "conflict_blocked" in existing:
        del existing["conflict_blocked"]
        _write_plan_json(project_root, sprint_label, existing)


def _sprint_get_conflict_blocked(project_root: Path, sprint_label: str) -> Optional[dict]:
    plan = _read_plan_json(project_root, sprint_label)
    if not plan:
        return None
    return plan.get("conflict_blocked") or None


def _changelog_sprint_sections(text: str) -> dict[str, str]:
    """Map sprint label (e.g. '91', '85.5') to its full ## Sprint section text."""
    matches = list(_CHANGELOG_SPRINT_HEADER_RE.finditer(text))
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        label = match.group(1)
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[label] = text[start:end].rstrip() + "\n"
    return sections


def _merge_changelog_conflict(cwd: Path) -> bool:
    """Resolve CHANGELOG.md by prepending sprint-only sections, keeping develop body."""
    def _stage_text(stage: str) -> str | None:
        res = subprocess.run(
            ["git", "show", f":{stage}:CHANGELOG.md"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        return res.stdout if res.returncode == 0 else None

    sprint_text = _stage_text("2")  # HEAD (sprint branch)
    develop_text = _stage_text("3")  # MERGE_HEAD (origin/develop)
    if not sprint_text or not develop_text:
        return False

    sprint_sections = _changelog_sprint_sections(sprint_text)
    develop_sections = _changelog_sprint_sections(develop_text)
    new_labels = [lbl for lbl in sprint_sections if lbl not in develop_sections]
    if not new_labels:
        merged_body = develop_text
    else:
        prepend = "\n".join(sprint_sections[lbl].rstrip() for lbl in new_labels)
        develop_body = develop_text
        if develop_body.startswith("# Changelog"):
            rest = develop_body.split("\n", 1)
            tail = rest[1].lstrip("\n") if len(rest) > 1 else ""
            merged_body = f"# Changelog\n\n{prepend}\n\n{tail}" if tail else f"# Changelog\n\n{prepend}\n"
        else:
            merged_body = prepend + "\n" + develop_body

    (cwd / "CHANGELOG.md").write_text(merged_body, encoding="utf-8")
    return True


def _resolve_doc_merge_conflicts(cwd: Path, unmerged: list[str]) -> bool:
    """Auto-resolve doc-path merge conflicts. Returns False on unsupported paths."""
    for path in unmerged:
        if path == "CHANGELOG.md":
            if not _merge_changelog_conflict(cwd):
                return False
            subprocess.run(["git", "add", path], cwd=cwd, capture_output=True, text=True, timeout=30)
        elif _is_doc_merge_path(path):
            subprocess.run(["git", "checkout", "--theirs", path], cwd=cwd, capture_output=True, text=True, timeout=30)
            subprocess.run(["git", "add", path], cwd=cwd, capture_output=True, text=True, timeout=30)
        else:
            return False
    return True


def _prepare_sprint_branch_for_develop_merge(repo: str, head_branch: str) -> tuple[bool, str]:
    """Merge origin/develop into the sprint branch; resolve doc conflicts before PR."""
    cwd = _git_repo_for_merge(repo)
    if cwd is None:
        return True, "skipped (no local clone for doc sync)"

    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            list(args), cwd=cwd, capture_output=True, text=True, timeout=180,
        )

    try:
        fetch = _run("git", "fetch", "origin", head_branch, "develop")
        if fetch.returncode != 0:
            return False, fetch.stderr.strip() or "git fetch failed"

        # Hard-sync the local sprint branch to its remote tip before merging.
        # This clone is the coder clone, whose sprint branch can be STALE/diverged
        # from earlier dispatch hygiene; a plain `checkout <branch>` left it behind
        # origin, so develop merged onto the old tip and the post-merge push was
        # rejected as non-fast-forward ("tip is behind its remote counterpart").
        # Discard any dirty working state, then point the branch at origin/<branch>
        # so the merge lands on the true tip and the push fast-forwards. The coder
        # clone's working tree is transient (reset on every dispatch), so this is
        # safe.
        _run("git", "reset", "--hard")
        _run("git", "clean", "-fd")
        co = _run("git", "checkout", "-B", head_branch, f"origin/{head_branch}")
        if co.returncode != 0:
            return False, co.stderr.strip() or "git checkout failed"

        merge = _run(
            "git", "merge", "origin/develop",
            "-m", f"sync develop into {head_branch} before sprint merge",
        )
        if merge.returncode != 0:
            status = _run("git", "diff", "--name-only", "--diff-filter=U")
            unmerged = [ln.strip() for ln in (status.stdout or "").splitlines() if ln.strip()]
            if not unmerged:
                return False, merge.stderr.strip() or "git merge develop failed"
            doc_files = [p for p in unmerged if _is_doc_merge_path(p)]
            non_doc = [p for p in unmerged if not _is_doc_merge_path(p)]
            safe_non_doc = [p for p in non_doc if _is_union_merge_safe_path(p)]
            unsafe = [p for p in non_doc if not _is_union_merge_safe_path(p)]
            if unsafe:
                _run("git", "merge", "--abort")
                # Prefix signals "needs human" to _gh_merge_branch_via_pr and complete-step
                return False, f"merge_conflict_needs_human: {', '.join(unsafe)}"
            if doc_files:
                if not _resolve_doc_merge_conflicts(cwd, doc_files):
                    _run("git", "merge", "--abort")
                    return False, "failed to auto-resolve doc conflicts"
            if safe_non_doc:
                resolved, still_bad = _resolve_union_merge_conflicts(cwd, safe_non_doc)
                if not resolved:
                    _run("git", "merge", "--abort")
                    return False, f"merge_conflict_needs_human: {', '.join(still_bad)}"
            commit = _run(
                "git", "commit", "-m",
                f"sync develop into {head_branch} (resolve doc and union conflicts)",
            )
            if commit.returncode != 0:
                _run("git", "merge", "--abort")
                return False, commit.stderr.strip() or "failed to commit resolved conflicts"

        push = _run("git", "push", "origin", head_branch)
        if push.returncode != 0:
            return False, push.stderr.strip() or "git push failed"
        return True, "synced with develop"
    except Exception as exc:
        try:
            _run("git", "merge", "--abort")
        except Exception:
            pass
        return False, str(exc)


def _check_branch_merge_conflict(
    repo: str, head: str, base: str,
) -> tuple[bool, str, list[str]]:
    """Return (has_conflict, message, conflicting_file_paths) for head→base."""
    if not _branch_has_unmerged_commits(repo, head, base):
        return False, "", []
    try:
        pr_res = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--head", head, "--base", base,
             "--state", "open", "--json", "number", "--limit", "1"],
            capture_output=True, text=True, timeout=30,
        )
        pr_number: int | None = None
        if pr_res.returncode == 0 and pr_res.stdout.strip():
            prs = json.loads(pr_res.stdout)
            if prs:
                pr_number = prs[0].get("number")
        if pr_number is None:
            create = subprocess.run(
                ["gh", "pr", "create", "--repo", repo, "--base", base, "--head", head,
                 "--title", f"Merge check: {head} → {base}",
                 "--body", "Ephemeral merge-conflict check (Commander)."],
                capture_output=True, text=True, timeout=60,
            )
            if create.returncode != 0:
                stderr = create.stderr.strip()
                if _is_merge_conflict_detail(stderr):
                    return True, stderr, []
                return False, "", []
            m = re.search(r"/pull/(\d+)", create.stdout or "")
            if m:
                pr_number = int(m.group(1))
        if pr_number is None:
            return False, "", []
        view = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "mergeable,mergeStateStatus,files"],
            capture_output=True, text=True, timeout=30,
        )
        if view.returncode != 0 or not view.stdout.strip():
            return False, "", []
        pr = json.loads(view.stdout)
        conflicting = (
            pr.get("mergeable") == "CONFLICTING"
            or pr.get("mergeStateStatus") == "DIRTY"
        )
        files = [
            f.get("path") for f in (pr.get("files") or [])
            if isinstance(f, dict) and f.get("path")
        ]
        if conflicting:
            return True, f"PR #{pr_number} has merge conflicts", files
        return False, "", []
    except Exception:
        return False, "", []


def _gh_merge_branch_via_pr(
    repo: str,
    head: str,
    base: str,
    title: str,
    delete_branch: bool = True,
    conflict_detail_out: Optional[dict] = None,
) -> tuple[bool, str, int | None]:
    """Create (or reuse) a PR head→base and merge it. Returns (ok, detail).

    When conflict_detail_out (a mutable dict) is provided, fills it with
    {"code": "merge_conflict_needs_human", "files": [...]} on a needs-human
    conflict so callers can distinguish it from other failures (issue #1898).
    """
    if base == "develop":
        prep_ok, prep_detail = _prepare_sprint_branch_for_develop_merge(repo, head)
        if not prep_ok:
            if conflict_detail_out is not None and prep_detail.startswith("merge_conflict_needs_human:"):
                files_str = prep_detail.split(":", 1)[1].strip()
                conflict_detail_out["code"] = "merge_conflict_needs_human"
                conflict_detail_out["files"] = [f.strip() for f in files_str.split(",") if f.strip()]
            return False, f"prepare for develop merge failed: {prep_detail}", None

    has_conflict, conflict_msg, conflict_files = _check_branch_merge_conflict(repo, head, base)
    if has_conflict:
        if conflict_detail_out is not None:
            conflict_detail_out["code"] = "merge_conflict_needs_human"
            conflict_detail_out["files"] = conflict_files
        suffix = f" ({', '.join(conflict_files)})" if conflict_files else ""
        return False, f"merge conflict: {conflict_msg}{suffix}", None

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
                    return False, stderr or "PR create failed", None
            else:
                pr_url = create.stdout.strip()
        merge_args = ["gh", "pr", "merge", pr_url, "--repo", repo, "--merge"]
        if delete_branch:
            merge_args.append("--delete-branch")
        merge_res = subprocess.run(merge_args, capture_output=True, text=True, timeout=120)
        if merge_res.returncode != 0:
            return False, merge_res.stderr.strip() or "PR merge failed", None
        return True, pr_url or f"{head} → {base}", _parse_pr_number_from_url(pr_url)
    except Exception as exc:
        return False, str(exc), None


def _branch_has_unmerged_commits(repo: str, head: str, base: str) -> bool:
    """True when head has commits not reachable from base."""
    if not _gh_branch_exists(repo, head):
        return False
    # A deleted base branch means there is nothing to merge into — the chain
    # already settled (base merged up and was pruned). Without this guard the
    # compare below 404s, falls through to `return True`, and bulk-complete then
    # tries `gh pr create --base <deleted>`, which fails and breaks the whole
    # merge run. Treat a missing base as "no unmerged commits".
    if not _gh_branch_exists(repo, base):
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
    """Ordered merge steps: each child → its parent (deepest first), then base → develop."""
    steps: list[dict] = []
    base_branch = _sprint_branch_name(base_label)
    children = children_of(base_label, project_root, project=repo or None)
    for child_label in sorted(children, key=_sprint_label_sub_index, reverse=True):
        parent_label = _sprint_merge_parent_label(project_root, child_label)
        child_branch = _sprint_branch_name(child_label)
        parent_branch = _sprint_branch_name(parent_label)
        if _branch_has_unmerged_commits(repo, child_branch, parent_branch):
            steps.append({
                "kind": "merge",
                "head": child_branch,
                "base": parent_branch,
                "label": f"{child_label} → {parent_label}",
                "delete_branch": True,
                "title": f"Merge Sprint: {child_label} → {parent_label}",
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


def _bulk_complete_merge_steps(project_root: Path, repo: str, base_label: str) -> list[dict]:
    """Bulk complete runs the full lineage merge chain: each child → base, then base → develop."""
    return _merge_steps_for_sprint_chain(project_root, repo, base_label)


def _bulk_complete_merge_pending(project_root: Path, repo: str, base_label: str) -> list[str]:
    """Branches still needing merge before bulk-complete settlement (full lineage chain)."""
    return _sprint_merge_chain_pending(project_root, repo, base_label)


def _sprint_merge_chain_pending(project_root: Path, repo: str, base_label: str) -> list[str]:
    """Branches that still need merging before base-sprint finish (full child → parent → develop chain)."""
    pending: list[str] = []
    base_branch = _sprint_branch_name(base_label)
    children = children_of(base_label, project_root, project=repo or None)
    for child_label in sorted(children, key=_sprint_label_sub_index, reverse=True):
        parent_label = _sprint_merge_parent_label(project_root, child_label)
        child_branch = _sprint_branch_name(child_label)
        parent_branch = _sprint_branch_name(parent_label)
        if _branch_has_unmerged_commits(repo, child_branch, parent_branch):
            pending.append(f"{child_branch} → {parent_branch}")
    if _branch_has_unmerged_commits(repo, base_branch, "develop"):
        pending.append(f"{base_branch} → develop")
    return pending


def _merge_sprint_branch_chain(repo: str, base_label: str) -> list[str]:
    """Merge leftover child branches into base, then base → develop. Returns errors."""
    errors: list[str] = []
    project_root = _project_root_path(repo)
    for step in _merge_steps_for_sprint_chain(project_root, repo, base_label):
        ok, detail, _pr_num = _gh_merge_branch_via_pr(
            repo, step["head"], step["base"],
            title=step["title"],
            delete_branch=step["delete_branch"],
        )
        if not ok:
            errors.append(f"{step['head']} → {step['base']}: {detail}")
    return errors


def _finish_merge_steps(project_root: Path, repo: str, label: str) -> list[dict]:
    """Merge steps for Merge Sprint on one label.

    Base label → full child→parent→develop chain. A child label normally merges
    just into its parent; but when the rest of the lineage is already settled (no
    OTHER child still in flight), run the full base chain so the work continues up
    to develop instead of stranding on the parent branch. The chain is idempotent
    — `_branch_has_unmerged_commits` skips branches that already merged.
    """
    base_label = _sprint_label_base(label)
    if _is_child_sprint_label(label):
        others_unsettled = [
            c for c in _bulk_complete_unsettled_children(project_root, base_label, project=repo)
            if c != label
        ]
        if not others_unsettled:
            # Lineage otherwise settled — settle the whole chain to develop.
            return _merge_steps_for_sprint_chain(project_root, repo, base_label)
        # Other children still in flight — only fold this child up to its parent.
        steps: list[dict] = []
        parent_label = _sprint_merge_parent_label(project_root, label)
        child_branch = _sprint_branch_name(label)
        parent_branch = _sprint_branch_name(parent_label)
        if _branch_has_unmerged_commits(repo, child_branch, parent_branch):
            steps.append({
                "kind": "merge",
                "head": child_branch,
                "base": parent_branch,
                "label": f"{label} → {parent_label}",
                "delete_branch": True,
                "title": f"Merge Sprint: {label} → {parent_label}",
            })
        return steps
    return _merge_steps_for_sprint_chain(project_root, repo, base_label)


def _merge_sprint_branches_for_label(repo: str, label: str) -> tuple[list[str], int | None]:
    """Execute merge steps for Merge Sprint on the requested label.

    Returns (errors, develop_pr_number) where develop_pr_number is parsed from
    the sprint branch → develop merge PR (for History / outcome links).
    """
    errors: list[str] = []
    develop_pr: int | None = None
    project_root = _project_root_path(repo)
    for step in _finish_merge_steps(project_root, repo, label):
        ok, detail, pr_num = _gh_merge_branch_via_pr(
            repo, step["head"], step["base"],
            title=step["title"],
            delete_branch=step["delete_branch"],
        )
        if not ok:
            errors.append(f"{step['head']} → {step['base']}: {detail}")
        elif step.get("base") == "develop" and pr_num:
            develop_pr = pr_num
    return errors, develop_pr


def _next_sprint_number(sprint_label: str) -> int:
    """Return the next sprint number for a given sprint label (sprint-N → N+1)."""
    m = re.match(r"^sprint-(\d+)(?:\.\d+)?$", sprint_label)
    if not m:
        raise ValueError(f"Invalid sprint label: {sprint_label!r}")
    return int(m.group(1)) + 1


# POST /finish and POST /bulk-complete extracted to routers/sprint_finish.py (issue #1261)


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
    # A base sprint with zero DB children is a clean, single-attempt sprint
    # (no rework ever needed) — not an error state (issue #1758).
    child_labels = children_of(base_label, project_root)
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


class SprintBranchMergeBody(BaseModel):
    confirmed: bool
    head: str
    base: str
    title: str = ""
    delete_branch: bool = True




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
        _sync_attachments_branch_ref(cache_dir)
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


def _sync_attachments_branch_ref(cache_dir: Path) -> None:
    """Fetch origin/attachments and align local HEAD with the remote tip.

    Bare-cache refs/heads/attachments can lag behind origin after another
  machine pushed; committing on a stale tip causes non-fast-forward push failures.
    """
    subprocess.run(
        ["git", "fetch", "origin", _ATTACHMENTS_BRANCH],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    remote_ref = f"refs/remotes/origin/{_ATTACHMENTS_BRANCH}"
    check = subprocess.run(
        ["git", "rev-parse", "--verify", remote_ref],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if check.returncode == 0:
        subprocess.run(
            ["git", "update-ref", f"refs/heads/{_ATTACHMENTS_BRANCH}", remote_ref],
            capture_output=True, text=True, cwd=str(cache_dir),
        )


_BULK_ATTACHMENT_WARN = (
    "Attachments were not uploaded to GitHub — add them manually on the attachments branch."
)


def _ticket_has_attachment_assignment(assignments: list, ticket_index: int) -> bool:
    """True when image_assignments include at least one file for this ticket."""
    for a in assignments:
        assignment = a.get("assignment")
        if assignment == "all" or assignment == ticket_index:
            if a.get("filename"):
                return True
    return False


def _apply_bulk_attachment_warning(job: dict, message: str) -> None:
    """Record a job-level attachment failure and per-ticket warnings where relevant."""
    job["attachment_error"] = message
    assignments = job.get("image_assignments") or []
    for t in job.get("tickets", []):
        idx = t.get("index")
        if idx is None:
            continue
        if _ticket_has_attachment_assignment(assignments, idx):
            t["attachment_warning"] = message


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

    _sync_attachments_branch_ref(cache_dir)
    _do_commit()

    # Push to remote
    push_result = subprocess.run(
        ["git", "push", "origin", f"refs/heads/{_ATTACHMENTS_BRANCH}:refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if push_result.returncode == 0:
        return

    # Push failed — sync remote tip and retry commit once
    _sync_attachments_branch_ref(cache_dir)
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

    _sync_attachments_branch_ref(cache_dir)
    _do_commit()

    push_result = subprocess.run(
        ["git", "push", "origin",
         f"refs/heads/{_ATTACHMENTS_BRANCH}:refs/heads/{_ATTACHMENTS_BRANCH}"],
        capture_output=True, text=True, cwd=str(cache_dir),
    )
    if push_result.returncode == 0:
        return

    # Push failed — sync remote tip and retry commit once
    _sync_attachments_branch_ref(cache_dir)
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
    github_client.invalidate("open_issues_body:")
    github_client.invalidate("open_issues:")
    github_client.invalidate("issues:")


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
        # Bulk-create fires one estimate-draft task per ticket; the cap bounds how
        # many estimator subprocesses (cheap Haiku) run at once. 3 made a batch
        # fill in slow waves (felt one-by-one); 8 lets a typical batch estimate in
        # parallel while staying well within subprocess/rate-limit headroom.
        _bulk_estimator_semaphore = asyncio.Semaphore(8)
    return _bulk_estimator_semaphore


def _extract_size_from_estimator_stdout(stdout: str) -> str | None:
    """Parse size (S/M/L/XL) from estimate_issue.py stdout.

    The script prints the JSON estimate after the 'Saved:' line.
    """
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
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    return time.time()


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
        "  - ## Files to touch (optional stub — include the heading with no paths pre-filled)\n"
        "  - ## Out of Scope (brief list)\n"
        + labels_clause + "\n"
        + json_spec
    )

    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    from services.sprint_manager.model_routing import apply_provider_env
    _ba_model = apply_provider_env(
        sub_env, "claude-sonnet-4-6", repo=os.environ.get("COMMANDER_PROJECT"),
        role="ba",
    )
    cmd = [
        "claude",
        "--model", _ba_model,
        "--dangerously-skip-permissions",
        "-p", prompt_text,
    ]
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
            if url_map:
                job.pop("attachment_error", None)
            else:
                _apply_bulk_attachment_warning(job, _BULK_ATTACHMENT_WARN)
            _persist_bulk_job(job)
        except Exception as pre_err:
            logger.warning("Bulk image pre-commit failed: %s", str(pre_err)[:200])
            job["image_url_map"] = {}
            _apply_bulk_attachment_warning(
                job,
                f"{_BULK_ATTACHMENT_WARN} ({str(pre_err)[:120]})",
            )
            _persist_bulk_job(job)

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




# GET /api/tickets/bulk/{job_id} and GET /api/tickets/bulk/{job_id}/stream
# extracted to routers/bulk_tickets.py (issue #1264).


class BulkStopBody(BaseModel):
    pass


class BulkSkipBody(BaseModel):
    index: int


class BulkEstimateDraftBody(BaseModel):
    index: int
    title: str | None = None
    body: str | None = None






class BulkRetryBody(BaseModel):
    index: int




class BulkRedraftBody(BaseModel):
    index: int




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


def _sprint_base_number(label: str) -> int | None:
    """Parse the integer N from ``sprint-N`` or ``sprint-N.M``."""
    if not _SPRINT_LABEL_RE.match(label or ""):
        return None
    try:
        return int((label or "").split("-", 1)[1].split(".")[0])
    except (IndexError, ValueError):
        return None


def _sprint_row_matches_project(row_project: str | None, repo: str | None) -> bool:
    """True when a DB/history row belongs to ``repo`` (or is legacy unscoped)."""
    proj = (row_project or "").strip()
    want = (repo or "").strip()
    if not want:
        return True
    return not proj or proj == want


def _used_sprint_numbers(repo: str | None) -> set[int]:
    """Base sprint numbers already recorded for this project.

    GitHub labels alone are insufficient: finished sprints often drop their
    ``sprint-N`` label, and an old sprint-99 ledger row must block reusing 99
    even when the label is gone (issue: History showed ancient #1/#11 tickets
    beside a new sprint-99 board card).
    """
    used: set[int] = set()
    for n in github_client.list_sprints(repo_name=repo) or []:
        try:
            used.add(int(n))
        except (TypeError, ValueError):
            pass
    for lbl in _finished_sprint_summaries(repo):
        base = _sprint_base_number(lbl)
        if base is not None:
            used.add(base)
    try:
        for row in db.list_sprints_lifecycle():
            if not _sprint_row_matches_project(row.get("project"), repo):
                continue
            base = _sprint_base_number(row.get("label") or "")
            if base is not None:
                used.add(base)
        for rec in db.list_sprint_history():
            if not _sprint_row_matches_project(rec.get("project"), repo):
                continue
            base = _sprint_base_number(rec.get("label") or "")
            if base is not None:
                used.add(base)
    except Exception:
        pass
    if repo:
        try:
            project_root = _project_root_path(repo)
            sprints_dir = _commander_dir(project_root) / "sprints"
            if sprints_dir.is_dir():
                from routers import sprint_history_service as shs  # noqa: PLC0415
                for lbl in shs._discover_file_labels(sprints_dir):
                    base = _sprint_base_number(lbl)
                    if base is not None:
                        used.add(base)
        except Exception:
            pass
    return used


def _next_new_sprint_number(repo: str | None) -> int:
    """Next sprint number for a brand-new sprint — the SAME value the board's
    "New sprint (Sprint N)" option shows.

    High-water mark over live labels, finished summaries, lifecycle DB,
    sprint_history snapshots, and on-disk sprint artifacts for the project.
    """
    used = _used_sprint_numbers(repo)
    return (max(used) if used else 0) + 1


def _sprint_number_reserved(repo: str | None, sprint_number: int) -> bool:
    """Return True when sprint_number is already recorded for this project."""
    try:
        n = int(sprint_number)
    except (TypeError, ValueError):
        return True
    return n in _used_sprint_numbers(repo)


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






class BulkRetryAllBody(BaseModel):
    bodies: dict[str, str]  # str(index) -> body text




# ── Body-size remediation endpoints (issue #261) ─────────────────────────────

class SizeRemedyCommentBody(BaseModel):
    index: int




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




# ── Mis-sizing flag endpoints (issue #578) ───────────────────────────────────


class MisSizingActionBody(BaseModel):
    action: str
    new_size: Optional[str] = None
    note: Optional[str] = None


class MisSizingConfigBody(BaseModel):
    tier_threshold: int
    min_events: int
















# Static files are mounted in server.py after the app is created.
