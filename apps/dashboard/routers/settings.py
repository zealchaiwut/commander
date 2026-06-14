"""Settings, deploy-config, filesystem browser, env-vars, scaffold and notes
endpoints — extracted from server.py.

Routes in this module:
  DELETE /api/projects/{slug}/settings
  GET    /api/settings
  PUT    /api/settings
  GET    /api/projects/{slug}/settings
  PUT    /api/projects/{slug}/settings
  GET    /api/projects/{slug}/deploy-config
  PUT    /api/projects/{slug}/deploy-config
  POST   /api/projects/{slug}/environments/{env}/deploy-config/validate
  GET    /api/fs/list
  GET    /api/projects/{slug}/environments/{env}/env-vars
  PUT    /api/projects/{slug}/environments/{env}/env-vars
  GET    /api/projects/{slug}/docs/scaffold/check
  POST   /api/projects/{slug}/docs/scaffold/apply
  GET    /api/projects/notes
  POST   /api/projects/notes

Out of this wave (pinned to backup cluster):
  /api/settings/sync/* — belongs to the backup router.
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import settings_service

router = APIRouter(tags=["settings"])


# ── Request body models ───────────────────────────────────────────────────────

class _ScaffoldApplyBody(BaseModel):
    confirm: bool = False


class _EnvVarItem(BaseModel):
    key: str
    value: str = ""


class _PutEnvVarsBody(BaseModel):
    vars: list[_EnvVarItem]


class SaveNotesBody(BaseModel):
    content: str
    expected_mtime: Optional[float] = None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.delete("/api/projects/{slug}/settings")
def delete_project_settings(slug: str):
    """Clear the project-level settings override.

    After deletion GET /api/projects/{slug}/settings returns global defaults.
    Returns 404 if the project slug is not found.
    Must be registered before DELETE /api/projects/{owner}/{repo_name} to avoid
    first-match routing collision on two-segment paths.
    """
    return settings_service.delete_project_settings(slug)


@router.get("/api/settings")
def get_global_settings():
    """Return effective global settings."""
    return settings_service.get_global_settings()


@router.put("/api/settings")
def put_global_settings(body: dict):
    """Persist a global settings override."""
    return settings_service.put_global_settings(body)


@router.get("/api/projects/{slug}/settings")
def get_project_settings(slug: str):
    """Return effective project settings (project overrides merged over global)."""
    return settings_service.get_project_settings(slug)


@router.put("/api/projects/{slug}/settings")
def put_project_settings(slug: str, body: dict):
    """Persist a project-level settings override."""
    return settings_service.put_project_settings(slug, body)


@router.get("/api/projects/{slug}/deploy-config")
def get_project_deploy_config(slug: str):
    """Return per-environment deploy config."""
    return settings_service.get_project_deploy_config(slug)


@router.put("/api/projects/{slug}/deploy-config")
def put_project_deploy_config(slug: str, body: dict):
    """Persist a per-environment deploy config override."""
    return settings_service.put_project_deploy_config(slug, body)


@router.post("/api/projects/{slug}/environments/{env}/deploy-config/validate")
def validate_deploy_config_field(slug: str, env: str, body: dict):
    """Validate an inline working_dir / port edit before it is persisted."""
    return settings_service.validate_deploy_config_field(slug, env, body)


@router.get("/api/fs/list")
def fs_list(path: str = ""):
    """Return immediate subdirectories of the given path."""
    return settings_service.fs_list(path)


@router.get("/api/projects/{slug}/environments/{env}/env-vars")
def get_env_vars(slug: str, env: str):
    """Read the environment's .env file and return parsed key/value pairs."""
    return settings_service.get_env_vars(slug, env)


@router.put("/api/projects/{slug}/environments/{env}/env-vars")
def put_env_vars(slug: str, env: str, body: _PutEnvVarsBody):
    """Write env-var changes back to the environment's .env file."""
    pairs: list[tuple[str, str]] = []
    for item in body.vars:
        from fastapi import HTTPException
        key = item.key.strip()
        if not key:
            raise HTTPException(
                status_code=400,
                detail="Each variable must have a non-empty KEY.",
            )
        pairs.append((key, item.value))
    return settings_service.put_env_vars(slug, env, pairs)


@router.get("/api/projects/{slug}/docs/scaffold/check")
def get_scaffold_check(slug: str):
    """Check whether the project's working clone has the standard docs structure."""
    return settings_service.get_scaffold_check(slug)


@router.post("/api/projects/{slug}/docs/scaffold/apply")
def post_scaffold_apply(slug: str, body: _ScaffoldApplyBody):
    """Apply the standard docs scaffold to the project's working clone."""
    return settings_service.post_scaffold_apply(slug, body.confirm)


@router.get("/api/projects/notes")
def get_project_notes(repo: str = ""):
    """Read the project's NOTES.md."""
    return settings_service.get_project_notes(repo)


@router.post("/api/projects/notes")
def save_project_notes(repo: str = "", body: SaveNotesBody = ...):
    """Write the project's NOTES.md."""
    return settings_service.save_project_notes(repo, body.content, body.expected_mtime)
