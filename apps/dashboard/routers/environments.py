from __future__ import annotations
import os, sys, uuid, subprocess, json
from pathlib import Path
from typing import Optional, Any
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db
import projects as projects_module
from services.logging import log as _slog

_PROJECTS_BASE = Path.home() / "dev"

router = APIRouter()

def _server():
    import server
    return server


# ── Module imports ────────────────────────────────────────────────────────────

from services.sprint_manager import deploy_actions as _deploy_actions
from services.sprint_manager import render_actions as _render_actions
from services.sprint_manager.deploy_config_schema import (
    DEPLOY_CONFIG_KEY,
    seed_for as _deploy_seed_for,
    merge_seed as _deploy_merge_seed,
    enrich_local_working_dirs as _enrich_working_dirs,
)
import services.sprint_manager.settings_repo as _settings_repo


# ── Local helpers ─────────────────────────────────────────────────────────────

def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _resolve_project_slug(slug: str) -> str:
    """Resolve a project slug to the full repo string (owner/repo).

    Matches by last path component or exact match.
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


def _dashboard_listen_port() -> Optional[int]:
    """Port this dashboard process is bound to (from PORT env, default 8000)."""
    raw = os.environ.get("PORT", "8000")
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


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


def _merged_deploy_config(slug: str, repo: str) -> dict:
    """Return seed-merged stored deploy config (raw, secrets intact)."""
    stored = _settings_repo.get_setting_scoped("project", DEPLOY_CONFIG_KEY, project=repo)
    return _deploy_merge_seed(_deploy_seed_for(slug), stored or {})


def _render_deploy_environment(entry: dict, env: str) -> dict:
    """Trigger a new Render deploy for a host=render env (issue #725)."""
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
    # `start` runs. Detach a `sleep; stop; start` helper in a new session so the
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


# ── Pydantic models ───────────────────────────────────────────────────────────

class _EnvEntry(BaseModel):
    env: str
    local_directory: str


class _PutEnvironmentsBody(BaseModel):
    environments: list[_EnvEntry]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/api/projects/{slug}/environments/{env}/deploy")
def deploy_environment(slug: str, env: str):
    """Deploy a local environment: checkout branch, pull, then restart.

    Runs ``git checkout <branch>`` then ``git pull --ff-only origin <branch>``
    inside the configured ``working_dir`` (never merge/push/PR), returns the raw
    pull stdout and the new HEAD sha, then triggers the restart action for the
    same env.
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

    # Sync to the configured branch, then fetch + hard-reset to origin (deploy
    # must not fail on untracked artifacts like package-lock.json blocking pull).
    subprocess.run(
        _deploy_actions.build_stash_dirty_command(),
        capture_output=True, text=True, cwd=working_dir,
    )

    checkout = subprocess.run(
        _deploy_actions.build_checkout_command(branch),
        capture_output=True, text=True, cwd=working_dir,
    )
    if checkout.returncode != 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot checkout '{branch}': "
                f"{checkout.stderr.strip() or checkout.stdout.strip() or 'git checkout failed'}"
            ),
        )

    fetch = subprocess.run(
        _deploy_actions.build_fetch_command(branch),
        capture_output=True, text=True, cwd=working_dir,
    )
    if fetch.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=fetch.stderr.strip() or fetch.stdout.strip() or "git fetch failed",
        )

    reset = subprocess.run(
        _deploy_actions.build_reset_hard_command(branch),
        capture_output=True, text=True, cwd=working_dir,
    )
    if reset.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=reset.stderr.strip() or reset.stdout.strip() or "git reset failed",
        )

    pull_output = (fetch.stdout or "") + (reset.stdout or "")

    head = subprocess.run(
        _deploy_actions.build_head_sha_command(),
        capture_output=True, text=True, cwd=working_dir,
    )
    head_sha = head.stdout.strip()

    # AC: after a successful sync, auto-trigger restart for the same env.
    # Best-effort — a restart-config problem must not mask a successful pull.
    try:
        restart_result = _restart_environment(entry)
    except HTTPException as exc:
        restart_result = {"ok": False, "error": exc.detail}

    _server()._save_deploy_time(slug, env)
    _server()._deploy_times[f"{slug}/{env}"] = datetime.now(timezone.utc).isoformat()

    resp = {
        "ok": True,
        "env": env,
        "branch": branch,
        "working_dir": working_dir,
        "pull_output": pull_output,
        "head": head_sha,
        "restart": restart_result,
    }
    if restart_result.get("detached"):
        return JSONResponse(status_code=202, content=resp)
    return resp


@router.post("/api/projects/{slug}/environments/{env}/restart")
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


@router.post("/api/projects/{slug}/environments/{env}/stop")
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


@router.post("/api/projects/{slug}/environments/{env}/start")
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


@router.get("/api/projects/{slug}/environments/{env}/run-state")
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
    if label:
        result = subprocess.run(
            _deploy_actions.build_print_command(label), capture_output=True, text=True
        )
        state = _deploy_actions.interpret_run_state(result.returncode)
        return {"ok": True, "env": env, "host": "local", "state": state, "probe": "launchd"}

    port_state = _deploy_actions.interpret_script_run_state(entry)
    if port_state != "idle":
        return {"ok": True, "env": env, "host": "local", "state": port_state, "probe": "port"}
    return {"ok": True, "env": env, "host": "local", "state": "idle"}


@router.get("/api/projects/{slug}/environments/{env}/deploy-status")
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


@router.get("/api/projects/{slug}/environments")
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


@router.put("/api/projects/{slug}/environments")
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
