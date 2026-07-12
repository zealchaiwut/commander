"""Service logic for the settings router (extracted from server.py).

Business logic for the Settings, Deploy Config, Filesystem Browser,
Env-var Editor, Docs Scaffold, and Project Notes API surfaces.
No FastAPI imports — keeps this module independently testable.

Shared server state accessed via a deferred import of ``server`` (the
``_server()`` helper) to avoid the import-time circular dependency that
arises because server.py imports routers/ at startup.
"""
from __future__ import annotations

import logging
import os
import sys as _sys
from pathlib import Path
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_DIR = _REPO_ROOT / "services" / "sprint_manager"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_DIR), str(_SCRIPTS_DIR)):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

# ── Service imports ───────────────────────────────────────────────────────────

import projects as projects_module  # noqa: E402
import settings_repo as _settings_repo  # noqa: E402
from settings_schema import (  # noqa: E402
    APP_CONFIG_KEY,
    SECRET_FIELDS,
    KNOWN_FIELDS,
    build_effective_response,
)
from deploy_config_schema import (  # noqa: E402
    DEPLOY_CONFIG_KEY,
    SUPPORTED_ENVS as _DEPLOY_SUPPORTED_ENVS,
    SUPPORTED_HOSTS as _DEPLOY_SUPPORTED_HOSTS,
    seed_for as _deploy_seed_for,
    merge_seed as _deploy_merge_seed,
    merge_for_put as _deploy_merge_for_put,
    build_deploy_config_response as _build_deploy_config_response,
    enrich_local_working_dirs as _deploy_enrich_working_dirs,
    known_deploy_slugs as _known_deploy_slugs,
)
from services.sprint_manager import deploy_actions as _deploy_actions  # noqa: E402
from services.sprint_manager import deploy_validation as _deploy_validation  # noqa: E402

import env_file as _env_file  # noqa: E402

try:
    from scaffold_project import scaffold_data as _scaffold_data  # noqa: E402
    SCAFFOLD_AVAILABLE = True
except ImportError:
    _scaffold_data = None  # type: ignore[assignment]
    SCAFFOLD_AVAILABLE = False

# ── Module-level constants ────────────────────────────────────────────────────

_PROJECTS_BASE = Path.home() / "dev"
_FS_BROWSE_ROOT: Path = Path.home()
_SYNC_STATUS_FILE = (
    Path(__file__).resolve().parent.parent.parent.parent
    / ".commander"
    / "settings.last_synced"
)

_AGENT_MODEL_KEYS = (
    "default_model", "coder_model", "tester_model",
    "estimator_model", "documentor_model",
)
_VALID_CODER_BACKENDS = frozenset({"cline", "claude-code"})

_PROJECTS_FILE: Path = projects_module.PROJECTS_FILE

logger = logging.getLogger(__name__)


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


# ── Deferred server import (shared state: _home_cache, _slog) ─────────────────

def _server():
    """Deferred import of the monolith — safe at request time."""
    import server  # noqa: PLC0415
    return server


# ── Shared helpers (mirrored from server.py to avoid circular import) ─────────

def _resolve_project_slug(slug: str) -> str:
    """Resolve slug → owner/repo; raise HTTPException 404 if not found."""
    from fastapi import HTTPException
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []
    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return matched["repo"]


def _resolve_with_seed_fallback(slug: str) -> str:
    """Resolve slug → owner/repo with a seed-only-project fallback.

    Projects in projects.json resolve normally. Slugs with a seed deploy config
    but absent from projects.json resolve as 'zealchaiwut/{slug}' (same fallback
    used in /api/deploy/overview). Any other slug raises HTTPException 404.
    """
    from fastapi import HTTPException
    try:
        all_projects = projects_module.load_projects()
    except Exception:
        all_projects = []
    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is not None:
        return matched["repo"]
    if slug in _known_deploy_slugs():
        return f"zealchaiwut/{slug}"
    raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")


def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _main_clone_path(project_root: Path) -> Path:
    nested = project_root / "main"
    if nested.is_dir() and (nested / ".git").exists():
        return nested
    return project_root


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _invalidate_home_cache(slug: str) -> None:
    """Drop cached /api/home payload for slug after identity-changing settings writes."""
    try:
        from home_service import invalidate_home_by_slug  # noqa: PLC0415
        invalidate_home_by_slug(slug)
    except Exception:
        pass


# ── Settings validation helpers ───────────────────────────────────────────────

_PROXY_CONTROLLED_FIELDS: frozenset[str] = frozenset({"llmProvider"})


def _validate_settings_body(body: dict) -> None:
    """Validate a PUT settings body. Raises HTTPException 422/400."""
    from fastapi import HTTPException
    for key in body:
        if key in SECRET_FIELDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Field '{key}' is a secret and cannot be written via this endpoint. "
                    "Use the dedicated secret management endpoint."
                ),
            )
        if key in _PROXY_CONTROLLED_FIELDS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Field '{key}' must be changed via POST /api/settings/provider — "
                    "it instructs the claude-proxy to switch profiles and cannot be "
                    "written directly through PUT /api/settings."
                ),
            )
    unknown = [k for k in body if k not in KNOWN_FIELDS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown settings field(s): {', '.join(sorted(unknown))}. "
                f"Allowed fields: {', '.join(sorted(KNOWN_FIELDS))}"
            ),
        )
    for key, value in body.items():
        meta = KNOWN_FIELDS.get(key)
        if meta is None or meta.get("secret"):
            continue
        default = meta["default"]
        if type(default) is int and value is not None and type(value) is not int:
            raise HTTPException(
                status_code=400,
                detail=f"Field '{key}' must be an integer, got {type(value).__name__!r}.",
            )
    if "coder_backend" in body and body["coder_backend"] not in _VALID_CODER_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid coder_backend {body['coder_backend']!r}; "
                "must be 'claude-code' or 'cline'."
            ),
        )


def _propagate_models_to_sprint_yaml(body: dict) -> list[str]:
    """Write agent-model and coder-backend fields from settings into sprint.yaml."""
    model_cfg = {k: v for k, v in body.items() if k in _AGENT_MODEL_KEYS and v}
    coder_backend = body.get("coder_backend") if "coder_backend" in body else None
    if not model_cfg and coder_backend is None:
        return []
    try:
        from services.sprint_manager.settings_sync import (
            _set_sprint_yaml_coder_backend,
            _update_sprint_yaml_agent_config,
        )
    except Exception:
        return []
    updated: list[str] = []
    for proj in projects_module.load_projects():
        repo = (proj.get("repo") or "").strip()
        if not repo:
            continue
        try:
            sy = _commander_dir(_project_root_path(repo)) / "sprint.yaml"
            if not sy.exists():
                continue
            if model_cfg:
                _update_sprint_yaml_agent_config(sy, model_cfg)
            if coder_backend is not None:
                _set_sprint_yaml_coder_backend(sy, str(coder_backend))
            updated.append(repo)
        except Exception:
            continue
    return updated


def _project_json_for_repo(repo: str) -> dict | None:
    return next(
        (p for p in projects_module.load_projects() if p.get("repo") == repo),
        None,
    )


def _apply_project_identity_defaults(resp: dict, repo: str, proj_override: dict) -> dict:
    """Use projects.json identity when the project override has no explicit value."""
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


# ── Settings endpoints ────────────────────────────────────────────────────────

def delete_project_settings(slug: str) -> dict:
    """Clear the project-level settings override; returns effective settings."""
    repo = _resolve_project_slug(slug)
    _settings_repo.delete_setting("project", APP_CONFIG_KEY, project=repo)
    _invalidate_home_cache(slug)
    effective = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    return build_effective_response(effective)


def get_global_settings() -> dict:
    """Return effective global settings."""
    stored = _settings_repo.get_setting_scoped("global", APP_CONFIG_KEY)
    return build_effective_response(stored)


def put_global_settings(body: dict) -> dict:
    """Persist a global settings override."""
    _validate_settings_body(body)
    current = _settings_repo.get_setting_scoped("global", APP_CONFIG_KEY)
    merged = {**current, **body}
    _settings_repo.set_setting("global", APP_CONFIG_KEY, merged)
    _propagate_models_to_sprint_yaml(body)
    return build_effective_response(merged)


def get_project_settings(slug: str) -> dict:
    """Return effective project settings (project overrides merged over global)."""
    repo = _resolve_project_slug(slug)
    proj_override = _settings_repo.get_setting_scoped("project", APP_CONFIG_KEY, project=repo)
    stored = _settings_repo.get_setting(APP_CONFIG_KEY, project=repo)
    resp = build_effective_response(stored)
    return _apply_project_identity_defaults(resp, repo, proj_override)


def put_project_settings(slug: str, body: dict) -> dict:
    """Persist a project-level settings override."""
    repo = _resolve_project_slug(slug)
    _validate_settings_body(body)
    current_project_override = _settings_repo.get_setting_scoped("project", APP_CONFIG_KEY, project=repo)
    merged = {**current_project_override, **body}
    _settings_repo.set_setting("project", APP_CONFIG_KEY, merged, project=repo)
    model_cfg = {k: v for k, v in body.items() if k in _AGENT_MODEL_KEYS and v}
    coder_backend = body.get("coder_backend") if "coder_backend" in body else None
    if model_cfg or coder_backend is not None:
        try:
            from services.sprint_manager.settings_sync import (
                _set_sprint_yaml_coder_backend,
                _update_sprint_yaml_agent_config,
            )
            sy = _commander_dir(_project_root_path(repo)) / "sprint.yaml"
            if sy.exists():
                if model_cfg:
                    _update_sprint_yaml_agent_config(sy, model_cfg)
                if coder_backend is not None:
                    _set_sprint_yaml_coder_backend(sy, str(coder_backend))
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


# ── Deploy config helpers ─────────────────────────────────────────────────────

def _validate_deploy_config_body(body: dict) -> None:
    """Validate a PUT deploy-config body. Raises HTTPException 400."""
    from fastapi import HTTPException
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


def _derive_project_environments(repo: str) -> dict[str, str]:
    """Best-effort guess of env paths from the on-disk project layout."""
    name = repo.split("/")[-1]
    dev = _PROJECTS_BASE
    found: dict[str, str] = {}
    nested = dev / name
    for env in ("prd", "uat", "coder", "tester"):
        cand = nested / env
        if cand.is_dir():
            found[env] = str(cand)
    if found:
        return found
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
    _deploy_enrich_working_dirs(resp, envs)


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


def _deploy_config_response(slug: str, repo: str, stored: dict) -> dict:
    """Build the GET-shaped response: seed defaults merged with stored, masked."""
    merged = _deploy_merge_seed(_deploy_seed_for(slug), stored or {})
    resp = _build_deploy_config_response(merged)
    _enrich_local_working_dirs(repo, resp)
    return resp


# ── Deploy config endpoints ───────────────────────────────────────────────────

def get_project_deploy_config(slug: str) -> dict:
    """Return per-environment deploy config."""
    repo = _resolve_with_seed_fallback(slug)
    stored = _settings_repo.get_setting_scoped("project", DEPLOY_CONFIG_KEY, project=repo)
    resp = _deploy_config_response(slug, repo, stored)
    _enrich_deploy_readiness(resp)
    return resp


def put_project_deploy_config(slug: str, body: dict) -> dict:
    """Persist a per-environment deploy config override."""
    repo = _resolve_with_seed_fallback(slug)
    _validate_deploy_config_body(body)
    current = _settings_repo.get_setting_scoped("project", DEPLOY_CONFIG_KEY, project=repo)
    merged = _deploy_merge_for_put(current or {}, body)
    _settings_repo.set_setting("project", DEPLOY_CONFIG_KEY, merged, project=repo)
    return _deploy_config_response(slug, repo, merged)


def validate_deploy_config_field(slug: str, env: str, body: dict) -> dict:
    """Validate an inline working_dir / port edit before persistence."""
    from fastapi import HTTPException
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


# ── Filesystem browser ────────────────────────────────────────────────────────

def fs_list(path: str = "") -> dict:
    """Return immediate subdirectories of the given path."""
    from fastapi import HTTPException
    root = _FS_BROWSE_ROOT.resolve()

    if not path or path.strip() in ("~", ""):
        target = root
    else:
        if path.startswith("~/"):
            path = str(root / path[2:])
        elif path == "~":
            path = str(root)

        candidate = Path(path)
        normalized = Path(os.path.normpath(str(candidate)))

        if not normalized.is_relative_to(root):
            raise HTTPException(status_code=403, detail="Forbidden")

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
                continue
            if not item.is_dir():
                continue
            if item.name.startswith("."):
                continue
            entries.append({"name": item.name, "path": str(item)})
    except OSError as exc:
        logger.info("[fs_list] error listing %s: %s", target, exc)

    return {"entries": entries, "current": str(target)}


# ── Env-var editor ────────────────────────────────────────────────────────────

def _env_working_dir(slug: str, repo: str, env: str) -> str | None:
    """Resolve the on-disk directory holding an environment's .env file."""
    envs = projects_module.get_project_environments(repo)
    if not envs:
        envs = _derive_project_environments(repo)
    if env in envs and envs[env]:
        return envs[env]
    merged = _merged_deploy_config(slug, repo)
    entry = (merged or {}).get(env) or {}
    return entry.get("working_dir") or None


def get_env_vars(slug: str, env: str) -> dict:
    """Read the environment's .env file and return parsed key/value pairs."""
    from fastapi import HTTPException
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


def put_env_vars(slug: str, env: str, pairs: list[tuple[str, str]]) -> dict:
    """Write env-var changes back to the environment's .env file."""
    from fastapi import HTTPException
    repo = _resolve_project_slug(slug)
    working_dir = _env_working_dir(slug, repo, env)
    if not working_dir:
        raise HTTPException(
            status_code=404,
            detail=f"No directory configured for environment '{env}'",
        )
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


# ── Scaffold docs ─────────────────────────────────────────────────────────────

def _scaffold_resolve_working_clone(slug: str) -> tuple[str, Path]:
    """Resolve project slug → (repo, working_clone_path) with traversal guard."""
    from fastapi import HTTPException
    repo = _resolve_project_slug(slug)
    project_root = _project_root_path(repo)
    working_clone = _main_clone_path(project_root)
    resolved = working_clone.resolve()
    base_resolved = _PROJECTS_BASE.resolve()
    if not str(resolved).startswith(str(base_resolved) + "/") and resolved != base_resolved:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Resolved project root '{resolved}' is outside the configured "
                f"projects directory '{base_resolved}'"
            ),
        )
    return repo, working_clone


def get_scaffold_check(slug: str) -> dict:
    """Check whether the project's working clone has the standard docs structure."""
    from fastapi import HTTPException
    if not SCAFFOLD_AVAILABLE:
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


def post_scaffold_apply(slug: str, confirm: bool) -> dict:
    """Apply the standard docs scaffold to the project's working clone."""
    from fastapi import HTTPException
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to apply scaffold")
    if not SCAFFOLD_AVAILABLE:
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


# ── Project notes ─────────────────────────────────────────────────────────────

def _notes_path(repo: str) -> Path:
    return _project_root_path(repo) / "NOTES.md"


def get_project_notes(repo: str) -> dict:
    """Read the project's NOTES.md file."""
    from fastapi import HTTPException
    if not repo:
        raise HTTPException(status_code=400, detail="repo required")
    path = _notes_path(repo)
    if not path.exists():
        return {"content": "", "mtime": None, "exists": False}
    try:
        mtime = path.stat().st_mtime
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "failed to read NOTES.md", "reason": str(exc)},
        )
    return {"content": content, "mtime": mtime, "exists": True}


def save_project_notes(repo: str, content: str, expected_mtime: Optional[float]) -> dict:
    """Write the project's NOTES.md file."""
    from fastapi import HTTPException
    if not repo:
        raise HTTPException(status_code=400, detail="repo required")
    path = _notes_path(repo)
    if path.exists() and expected_mtime is not None:
        current_mtime = path.stat().st_mtime
        if abs(current_mtime - expected_mtime) > 0.5:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "conflict",
                    "message": "NOTES.md changed on disk since last load.",
                    "current_mtime": current_mtime,
                },
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "mtime": path.stat().st_mtime}
