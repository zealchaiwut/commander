""""Copy PRD → UAT" background job (Deploy-tab-copy-prd-to-uat milestone).

Reuses deploy_progress_service's job-key/queue/_run_job engine — same
console-stream SSE endpoint as deploy/restart/stop/start, sharing the uat
card's job key (env="uat") so the copy's progress shows up on the same
console the operator already watches for that environment.

Direction is hard-locked PRD→UAT, matching perf-coach's existing
services/user_copy.py precedent. Three strategies, dispatched by each
project's services.sprint_manager.deploy_config_schema.copy_strategy_for():

  sqlite_file      — viral-radar: stop uat, back up uat's .db, copy prd's .db
                      over it, restart uat.
  json_dir         — asset-studio: same shape, whole directory instead of one file.
  postgres_rowcopy — perf-coach (crux once its uat moves off SQLite): run
                      that project's own copy script (Commander doesn't know
                      the project's schema) with its .env sourced.

Every strategy backs up UAT's current state before overwriting it.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from services.sprint_manager import deploy_config_schema as _copy_schema  # noqa: E402

from . import deploy_progress_service as _progress  # noqa: E402


def _env_module():
    from . import environments as _env  # noqa: PLC0415
    return _env


def _backup_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def run_copy_job(key: str, slug: str) -> None:
    """Copy PRD's data over UAT's for *slug*, per its registered strategy."""
    strategy_cfg = _copy_schema.copy_strategy_for(slug)
    if not strategy_cfg:
        await _progress._emit(key, {
            "status": "error", "mode": "bar", "action": "copy-prd-to-uat",
            "error": f"No copy strategy registered for '{slug}'",
            "log_tail": [f"No copy strategy registered for '{slug}'"],
            "done": 0, "total": 1,
        })
        return

    strategy = strategy_cfg["strategy"]
    if strategy == "sqlite_file":
        await _run_file_copy(key, slug, strategy_cfg, kind="file")
    elif strategy == "json_dir":
        await _run_file_copy(key, slug, strategy_cfg, kind="dir")
    elif strategy == "postgres_rowcopy":
        await _run_postgres_rowcopy(key, slug, strategy_cfg)
    else:
        await _progress._emit(key, {
            "status": "error", "mode": "bar", "action": "copy-prd-to-uat",
            "error": f"Unknown copy strategy '{strategy}' for '{slug}'",
            "log_tail": [f"Unknown copy strategy '{strategy}'"],
            "done": 0, "total": 1,
        })


async def _run_file_copy(key: str, slug: str, strategy_cfg: dict, *, kind: str) -> None:
    """Shared body for sqlite_file and json_dir — same shape, file vs dir ops."""
    env_mod = _env_module()

    def _prep():
        repo = env_mod._resolve_project_slug(slug)
        merged = env_mod._merged_deploy_config(slug, repo)
        env_mod._enrich_local_working_dirs(repo, merged)
        prd_entry = merged.get("prd")
        uat_entry = merged.get("uat")
        if not prd_entry or not uat_entry:
            raise RuntimeError(f"'{slug}' is missing a prd or uat entry in its deploy config")
        prd_dir = prd_entry.get("working_dir")
        uat_dir = uat_entry.get("working_dir")
        if not prd_dir or not uat_dir:
            raise RuntimeError(f"'{slug}' prd/uat working_dir not resolved")
        name = strategy_cfg["db_filename"] if kind == "file" else strategy_cfg["dir_name"]
        return uat_entry, Path(prd_dir) / name, Path(uat_dir) / name

    try:
        uat_entry, source_path, target_path = await asyncio.to_thread(_prep)
    except Exception as exc:  # noqa: BLE001
        await _progress._emit(key, {
            "status": "error", "mode": "bar", "action": "copy-prd-to-uat",
            "error": str(exc), "log_tail": [f"Setup failed: {exc}"], "done": 0, "total": 1,
        })
        return

    def _validate_source():
        if not source_path.exists():
            raise RuntimeError(f"PRD source not found: {source_path}")
        return f"Found PRD source at {source_path}"

    def _backup():
        if not target_path.exists():
            return "No existing UAT data to back up"
        backup_path = target_path.with_name(target_path.name + f".bak-{_backup_suffix()}")
        if kind == "file":
            shutil.copy2(target_path, backup_path)
        else:
            shutil.copytree(target_path, backup_path)
        return f"Backed up current UAT data to {backup_path.name}"

    def _stop_uat():
        try:
            env_mod._stop_environment(uat_entry)
        except Exception as exc:  # noqa: BLE001
            return f"UAT stop skipped/failed (continuing): {exc}"
        return "Stopped UAT service"

    def _copy():
        if kind == "file":
            shutil.copy2(source_path, target_path)
        else:
            if target_path.exists():
                shutil.rmtree(target_path)
            shutil.copytree(source_path, target_path)
        return f"Copied {source_path} → {target_path}"

    def _start_uat():
        # launchd needs a moment to fully release a service after bootout
        # before a bootstrap for the same label succeeds — bootstrapping
        # immediately after _stop_uat's bootout can fail with a transient
        # "Bootstrap failed: 5: Input/output error" (observed in a real
        # supervised dry run against viral-radar). Retry with backoff rather
        # than fail the whole job over a timing race — the copy itself has
        # already succeeded by this point.
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0, 1, 2, 3)):
            if delay:
                time.sleep(delay)
            try:
                env_mod._start_environment(uat_entry)
                return "Started UAT service" if attempt == 0 else (
                    f"Started UAT service (attempt {attempt + 1}, after launchd released the prior instance)"
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        raise RuntimeError(f"UAT service failed to start after retries: {last_exc}")

    await _progress._run_job(key, "copy-prd-to-uat", [
        ("Validating PRD source", _validate_source),
        ("Backing up current UAT data", _backup),
        ("Stopping UAT service", _stop_uat),
        ("Copying PRD data to UAT", _copy),
        ("Starting UAT service", _start_uat),
    ])


async def _run_postgres_rowcopy(key: str, slug: str, strategy_cfg: dict) -> None:
    """Run the project's own copy_script (Commander doesn't know its schema)."""
    working_dir = strategy_cfg["working_dir"]
    copy_script = strategy_cfg["copy_script"]

    def _run():
        env_file = Path(working_dir) / ".env"
        source_prefix = ""
        if env_file.exists():
            source_prefix = "set -a; source .env; set +a; "
        result = subprocess.run(
            ["bash", "-c", source_prefix + copy_script],
            capture_output=True, text=True, cwd=working_dir, timeout=900,
        )
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr.strip() or result.stdout.strip() or "copy script failed")[-2000:]
            )
        tail = (result.stdout.strip() or "copy script completed").splitlines()
        return tail[-1] if tail else "copy script completed"

    await _progress._run_job(key, "copy-prd-to-uat", [
        (f"Running {copy_script}", _run),
    ])
