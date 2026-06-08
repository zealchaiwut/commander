"""settings_sync.py — bidirectional settings sync between local files and Neon.

Supports two directions:
  upload  — local file-based settings → Neon settings store
  fetch   — Neon settings store → local files

Syncable data:
  - projects.json display fields: name, icon, color, tracked (per project)
  - sprint.yaml agent_config section: non-secret, non-path fields
  - Neon global settings: non-secret, non-path fields from app_config

Excluded at serialization time (never appear in diff or payload):
  - Secret fields (github_token, database_url)
  - Machine-specific paths (environments values, sprint.yaml worktrees/paths)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _yaml = None  # type: ignore[assignment]
    _YAML_AVAILABLE = False

from services.sprint_manager.settings_schema import NON_SECRET_FIELDS, SECRET_FIELDS

# Project display fields that are safe to sync (machine-agnostic).
# Excluded: environments (machine-specific paths), active_sprints (runtime state).
PROJECT_DISPLAY_FIELDS: tuple[str, ...] = ("name", "icon", "color", "tracked")

# Sprint.yaml key that holds agent/model config
SPRINT_YAML_AGENT_CONFIG_KEY = "agent_config"


def load_local_snapshot(
    projects_file: Path,
    sprint_yaml_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Load syncable settings from local files.

    Returns a dict with shape:
        {
            "projects": {"owner/repo": {"name": ..., "icon": ..., ...}},
            "app_config": {"default_model": ..., ...}  # from sprint.yaml if present
        }

    Secrets and machine-specific paths are never included.
    """
    snapshot: dict[str, Any] = {"projects": {}, "app_config": {}}

    # ── projects.json display fields ─────────────────────────────────────────
    if projects_file.exists():
        try:
            projects = json.loads(projects_file.read_text(encoding="utf-8"))
            for proj in projects:
                repo = (proj.get("repo") or "").strip()
                if not repo:
                    continue
                display: dict[str, Any] = {}
                for field in PROJECT_DISPLAY_FIELDS:
                    if field in proj:
                        display[field] = proj[field]
                if display:
                    snapshot["projects"][repo] = display
        except Exception:
            pass

    # ── sprint.yaml agent_config section ─────────────────────────────────────
    if sprint_yaml_path and sprint_yaml_path.exists() and _YAML_AVAILABLE:
        try:
            data = _yaml.safe_load(sprint_yaml_path.read_text(encoding="utf-8"))
            if data and isinstance(data, dict):
                agent_config = data.get(SPRINT_YAML_AGENT_CONFIG_KEY) or {}
                if isinstance(agent_config, dict):
                    for k, v in agent_config.items():
                        # Only include known non-secret, non-path fields
                        if k in NON_SECRET_FIELDS and k not in SECRET_FIELDS:
                            snapshot["app_config"][k] = v
        except Exception:
            pass

    return snapshot


def load_neon_snapshot(
    settings_repo: Any,
    app_config_key: str = "app_config",
) -> dict[str, Any]:
    """Load syncable settings from the Neon settings store.

    Returns same shape as load_local_snapshot().
    Excludes all secret fields at read time.
    """
    snapshot: dict[str, Any] = {"projects": {}, "app_config": {}}

    try:
        stored = settings_repo.get_setting_scoped("global", app_config_key)
        if isinstance(stored, dict):
            for k, v in stored.items():
                if k in NON_SECRET_FIELDS and k not in SECRET_FIELDS:
                    snapshot["app_config"][k] = v
    except Exception:
        pass

    return snapshot


def compute_diff(
    local: dict[str, Any],
    neon: dict[str, Any],
    direction: str,
) -> list[dict[str, Any]]:
    """Compute diff between local and Neon snapshots.

    Args:
        local:     Local snapshot (from load_local_snapshot).
        neon:      Neon snapshot (from load_neon_snapshot).
        direction: "upload" (local→Neon) or "fetch" (Neon→local).

    Returns a list of diff items with keys: status, key, value.
      status="added"     — value present in source, absent or different in dest
      status="removed"   — old dest value being replaced (paired with an added item)
      status="unchanged" — same in both source and dest
    """
    if direction not in ("upload", "fetch"):
        raise ValueError(f"direction must be 'upload' or 'fetch'; got {direction!r}")

    if direction == "upload":
        source_cfg = local.get("app_config", {})
        dest_cfg = neon.get("app_config", {})
        source_projs = local.get("projects", {})
        dest_projs = neon.get("projects", {})
    else:
        source_cfg = neon.get("app_config", {})
        dest_cfg = local.get("app_config", {})
        source_projs = neon.get("projects", {})
        dest_projs = local.get("projects", {})

    diff: list[dict[str, Any]] = []

    # ── app_config diff ───────────────────────────────────────────────────────
    all_keys = sorted(set(source_cfg) | set(dest_cfg))
    for key in all_keys:
        # Double-check exclusions (defence in depth)
        if key in SECRET_FIELDS:
            continue
        if key not in NON_SECRET_FIELDS:
            continue

        src_val = source_cfg.get(key)
        dst_val = dest_cfg.get(key)
        ns_key = f"app_config.{key}"

        if src_val == dst_val:
            if src_val is not None:
                diff.append({"status": "unchanged", "key": ns_key, "value": src_val})
        elif src_val is not None and dst_val is None:
            diff.append({"status": "added", "key": ns_key, "value": src_val})
        elif src_val is None and dst_val is not None:
            # In dest but not source — will be left alone (not removed)
            pass
        else:
            # Both exist but different → removed (old) + added (new)
            diff.append({"status": "removed", "key": ns_key, "value": dst_val})
            diff.append({"status": "added", "key": ns_key, "value": src_val})

    # ── projects diff ─────────────────────────────────────────────────────────
    all_repos = sorted(set(source_projs) | set(dest_projs))
    for repo in all_repos:
        src_proj = source_projs.get(repo, {})
        dst_proj = dest_projs.get(repo, {})
        all_fields = sorted(set(src_proj) | set(dst_proj))

        for field in all_fields:
            if field not in PROJECT_DISPLAY_FIELDS:
                continue

            src_val = src_proj.get(field)
            dst_val = dst_proj.get(field)
            ns_key = f"project:{repo}:{field}"

            if src_val == dst_val:
                if src_val is not None:
                    diff.append({"status": "unchanged", "key": ns_key, "value": src_val})
            elif src_val is not None and dst_val is None:
                diff.append({"status": "added", "key": ns_key, "value": src_val})
            elif src_val is None and dst_val is not None:
                pass
            else:
                diff.append({"status": "removed", "key": ns_key, "value": dst_val})
                diff.append({"status": "added", "key": ns_key, "value": src_val})

    return diff


def is_already_in_sync(diff: list[dict[str, Any]]) -> bool:
    """Return True when diff contains no added or removed lines."""
    return all(item["status"] == "unchanged" for item in diff)


def apply_upload(
    diff: list[dict[str, Any]],
    settings_repo: Any,
    app_config_key: str = "app_config",
) -> None:
    """Apply upload commit: write added/changed values to Neon.

    Only processes "added" items — these represent new source values.
    Paired "removed" items are the old dest values being replaced.
    Local files are never touched.
    """
    new_cfg: dict[str, Any] = {}

    for item in diff:
        if item["status"] != "added":
            continue
        key: str = item["key"]

        if key.startswith("app_config."):
            field = key[len("app_config."):]
            if field in NON_SECRET_FIELDS and field not in SECRET_FIELDS:
                new_cfg[field] = item["value"]

    if new_cfg:
        current = settings_repo.get_setting_scoped("global", app_config_key)
        if not isinstance(current, dict):
            current = {}
        merged = {**current, **new_cfg}
        settings_repo.set_setting("global", app_config_key, merged)


def apply_fetch(
    diff: list[dict[str, Any]],
    projects_file: Path,
    sprint_yaml_path: Optional[Path],
    app_config_key: str = "app_config",
) -> None:
    """Apply fetch commit: write added/changed values to local files.

    Only processes "added" items — these represent incoming Neon values.
    Neon is never touched.
    """
    new_cfg: dict[str, Any] = {}
    project_updates: dict[str, dict[str, Any]] = {}

    for item in diff:
        if item["status"] != "added":
            continue
        key: str = item["key"]

        if key.startswith("app_config."):
            field = key[len("app_config."):]
            if field in NON_SECRET_FIELDS and field not in SECRET_FIELDS:
                new_cfg[field] = item["value"]

        elif key.startswith("project:"):
            parts = key.split(":", 2)
            if len(parts) == 3:
                repo = parts[1]
                field = parts[2]
                if field in PROJECT_DISPLAY_FIELDS:
                    project_updates.setdefault(repo, {})[field] = item["value"]

    if new_cfg and sprint_yaml_path is not None and _YAML_AVAILABLE:
        _update_sprint_yaml_agent_config(sprint_yaml_path, new_cfg)

    if project_updates and projects_file.exists():
        _update_projects_json(projects_file, project_updates)


def _update_sprint_yaml_agent_config(sprint_yaml_path: Path, new_cfg: dict[str, Any]) -> None:
    """Update (or create) the agent_config section in sprint.yaml."""
    data: dict = {}
    if sprint_yaml_path.exists() and _YAML_AVAILABLE:
        try:
            data = _yaml.safe_load(sprint_yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}

    existing: dict = data.get(SPRINT_YAML_AGENT_CONFIG_KEY) or {}
    existing.update(new_cfg)
    data[SPRINT_YAML_AGENT_CONFIG_KEY] = existing

    if _YAML_AVAILABLE:
        sprint_yaml_path.write_text(
            _yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )


def _update_projects_json(
    projects_file: Path,
    project_updates: dict[str, dict[str, Any]],
) -> None:
    """Update display fields in projects.json for the given repos."""
    try:
        projects = json.loads(projects_file.read_text(encoding="utf-8"))
    except Exception:
        return

    repo_index = {
        (p.get("repo") or "").strip(): i
        for i, p in enumerate(projects)
    }

    for repo, fields in project_updates.items():
        idx = repo_index.get(repo)
        if idx is not None:
            for field, value in fields.items():
                if field in PROJECT_DISPLAY_FIELDS:
                    projects[idx][field] = value

    tmp = projects_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(projects, indent=2), encoding="utf-8")
    tmp.replace(projects_file)


def get_sync_status(status_file: Path) -> Optional[str]:
    """Return last-synced ISO 8601 timestamp, or None if never synced."""
    if not status_file.exists():
        return None
    try:
        return status_file.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def save_sync_status(status_file: Path, ts: str) -> None:
    """Persist a last-synced timestamp to the status file."""
    try:
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(ts, encoding="utf-8")
    except Exception:
        pass
