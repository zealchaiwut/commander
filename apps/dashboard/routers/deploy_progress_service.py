"""Deploy/Restart/Stop/Start background progress service.

Runs each action as a background asyncio task and emits normalized
ProgressActivity snapshots to SSE subscribers via asyncio.Queue — the same
job-key/queue/emit shape as finish_progress_service.py (issue #929), reused
here rather than re-invented.

Deliberately wraps the existing, already-tested synchronous action functions
in routers/environments.py (`_restart_environment`, `_stop_environment`,
`_start_environment`, and deploy's stash/checkout/fetch/reset chain) via
`asyncio.to_thread`, narrating progress between phases — it does not rewrite
that logic (self-restart detachment, script-vs-launchd branching, per-phase
timeouts are all easy to get subtly wrong, and this is live infrastructure
the operator restarts this very dashboard through). This is also what
finish_progress_service.py itself actually does for its own multi-step
operation — discrete human-readable log lines per phase, not a raw stdout
pipe — not the byte-level `asyncio.create_subprocess_exec` + readline()
pattern used for project init, which suits a single long-running command
better than this multi-phase, mostly-sub-second chain.

Job store:
  _JOBS: dict[job_key -> latest snapshot]
  _SUBS: dict[job_key -> list[asyncio.Queue]]

A snapshot has the ProgressActivity shape:
  { status, mode, done, total, current, log_tail, result?, error? }
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Optional

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from services.sprint_manager import deploy_actions as _deploy_actions  # noqa: E402

# ── Job store ─────────────────────────────────────────────────────────────────

_JOBS: dict[str, dict] = {}
_SUBS: dict[str, list[asyncio.Queue]] = {}


def job_key(slug: str, env: str) -> str:
    """Canonical key for a deploy-action job: one per project+environment.

    Shared across deploy/restart/stop/start — you would not run two of these
    concurrently against the same environment card anyway, and a shared key
    means reopening the console always shows the most recent action.
    """
    return f"{env}@{slug}"


def get_snapshot(key: str) -> Optional[dict]:
    return _JOBS.get(key)


def is_running(key: str) -> bool:
    snap = _JOBS.get(key)
    return snap is not None and snap.get("status") == "running"


def subscribe(key: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _SUBS.setdefault(key, []).append(q)
    return q


def unsubscribe(key: str, q: asyncio.Queue) -> None:
    subs = _SUBS.get(key, [])
    if q in subs:
        subs.remove(q)


async def _emit(key: str, snapshot: dict) -> None:
    _JOBS[key] = snapshot
    for q in list(_SUBS.get(key, [])):
        try:
            q.put_nowait(snapshot)
        except asyncio.QueueFull:
            pass
    await asyncio.sleep(0)  # yield to event loop


def _env_module():
    """Deferred import of routers.environments — avoids import-time cycles."""
    from . import environments as _env  # noqa: PLC0415
    return _env


async def _run_job(key: str, action: str, steps: list) -> None:
    """Drive one job through *steps*, emitting a snapshot before/after each.

    Each step is ``(label, fn)`` where ``fn`` is a zero-arg callable (run via
    ``asyncio.to_thread``) returning a short human-readable result string, or
    raising to fail the job. ``fn`` may itself raise the wrapped action's
    ``HTTPException`` (from the reused environments.py helpers) — its
    ``detail`` becomes the error message.
    """
    from fastapi import HTTPException  # noqa: PLC0415

    total = len(steps)
    log_tail: list[str] = []

    def _log(msg: str) -> None:
        log_tail.append(msg)
        if len(log_tail) > 150:
            log_tail.pop(0)

    snapshot: dict = {
        "status": "running", "mode": "bar", "action": action,
        "done": 0, "total": total, "current": "Starting…", "log_tail": [],
    }
    await _emit(key, snapshot)

    for i, (label, fn) in enumerate(steps):
        _log(label + "…")
        snapshot = {**snapshot, "current": label + "…", "log_tail": list(log_tail)}
        await _emit(key, snapshot)
        try:
            result_msg = await asyncio.to_thread(fn)
            _log(result_msg or f"{label}: done")
        except HTTPException as exc:
            _log(f"{label} failed: {exc.detail}")
            await _emit(key, {
                "status": "error", "mode": "bar", "action": action,
                "error": str(exc.detail), "log_tail": list(log_tail),
                "done": i, "total": total,
            })
            return
        except Exception as exc:  # noqa: BLE001
            _log(f"{label} failed: {exc}")
            await _emit(key, {
                "status": "error", "mode": "bar", "action": action,
                "error": str(exc), "log_tail": list(log_tail),
                "done": i, "total": total,
            })
            return
        snapshot = {**snapshot, "done": i + 1, "log_tail": list(log_tail)}
        await _emit(key, snapshot)

    _log("Done.")
    await _emit(key, {
        "status": "done", "mode": "bar", "action": action,
        "done": total, "total": total, "current": "Done",
        "result": f"{action} complete", "log_tail": list(log_tail),
    })


async def run_deploy_job(key: str, slug: str, env: str) -> None:
    """Deploy: stash -> checkout -> fetch -> reset -> restart (issue #723).

    Same chain as the existing synchronous /deploy route, narrated phase by
    phase. Re-derives the merged config fresh at run time (not passed in) so
    a slow-to-start job reflects the config as of when each phase actually
    runs, matching the existing route's behaviour.
    """
    env_mod = _env_module()

    def _prep():
        repo = env_mod._resolve_project_slug(slug)
        merged = env_mod._merged_deploy_config(slug, repo)
        env_mod._enrich_local_working_dirs(repo, merged)
        entry = _deploy_actions.get_env_entry(merged, env)
        working_dir, branch = _deploy_actions.require_deploy_target(entry)
        return entry, working_dir, branch

    try:
        entry, working_dir, branch = await asyncio.to_thread(_prep)
    except Exception as exc:  # noqa: BLE001
        await _emit(key, {
            "status": "error", "mode": "bar", "action": "deploy",
            "error": str(exc), "log_tail": [f"Setup failed: {exc}"], "done": 0, "total": 1,
        })
        return

    def _stash():
        subprocess.run(
            _deploy_actions.build_stash_dirty_command(),
            capture_output=True, text=True, cwd=working_dir,
        )
        return "Stashed local changes (if any)"

    def _checkout():
        r = subprocess.run(
            _deploy_actions.build_checkout_command(branch),
            capture_output=True, text=True, cwd=working_dir,
        )
        if r.returncode != 0:
            raise RuntimeError(f"checkout '{branch}' failed: {r.stderr.strip() or r.stdout.strip()}")
        return f"Checked out {branch}"

    def _fetch():
        r = subprocess.run(
            _deploy_actions.build_fetch_command(branch),
            capture_output=True, text=True, cwd=working_dir, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(f"fetch failed: {r.stderr.strip() or r.stdout.strip()}")
        return "Fetched origin"

    def _reset():
        r = subprocess.run(
            _deploy_actions.build_reset_hard_command(branch),
            capture_output=True, text=True, cwd=working_dir, timeout=30,
        )
        if r.returncode != 0:
            raise RuntimeError(f"reset failed: {r.stderr.strip() or r.stdout.strip()}")
        head = subprocess.run(
            _deploy_actions.build_head_sha_command(),
            capture_output=True, text=True, cwd=working_dir, timeout=10,
        )
        sha = head.stdout.strip()[:8] if head.returncode == 0 else "?"
        return f"Reset to origin/{branch} ({sha})"

    def _restart():
        env_mod._restart_environment(entry)
        return "Restart triggered"

    await _run_job(key, "deploy", [
        ("Stashing local changes", _stash),
        ("Checking out " + branch, _checkout),
        ("Fetching origin", _fetch),
        ("Resetting to origin/" + branch, _reset),
        ("Restarting service", _restart),
    ])


async def _run_simple_action_job(key: str, slug: str, env: str, action: str) -> None:
    """Shared body for restart/stop/start — each is a single existing call."""
    env_mod = _env_module()
    fn_by_action = {
        "restart": env_mod._restart_environment,
        "stop": env_mod._stop_environment,
        "start": env_mod._start_environment,
    }
    label_by_action = {
        "restart": "Restarting service",
        "stop": "Stopping service",
        "start": "Starting service",
    }

    def _prep_and_run():
        repo = env_mod._resolve_project_slug(slug)
        merged = env_mod._merged_deploy_config(slug, repo)
        env_mod._enrich_local_working_dirs(repo, merged)
        entry = _deploy_actions.get_env_entry(merged, env)
        if entry is None:
            raise RuntimeError(f"No deploy config for environment '{env}'")
        result = fn_by_action[action](entry)
        return result.get("method", action)

    await _run_job(key, action, [(label_by_action[action], _prep_and_run)])


async def run_restart_job(key: str, slug: str, env: str) -> None:
    await _run_simple_action_job(key, slug, env, "restart")


async def run_stop_job(key: str, slug: str, env: str) -> None:
    await _run_simple_action_job(key, slug, env, "stop")


async def run_start_job(key: str, slug: str, env: str) -> None:
    await _run_simple_action_job(key, slug, env, "start")
