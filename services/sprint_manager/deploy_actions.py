"""Local deploy / restart action helpers (issue #723).

Pure, side-effect-free builders and validators that the dashboard's deploy and
restart endpoints use to drive a ``git pull`` + service restart for
Mac-mini-hosted (``host=local``) environments. All authority stays pull-only:
no merge, push, PR, checkout, or branch switching is ever performed here.

Design split:
  - The functions in this module build commands and validate config. They never
    shell out themselves, so they are trivially unit-testable.
  - The server endpoints own the actual ``subprocess`` calls and HTTP shape.

Restart strategy per environment:
  - ``launchd_label`` configured  → ``launchctl kickstart -k gui/<uid>/<label>``.
    kickstart restarts the service in place; it does NOT unload/bootout, so a
    ``KeepAlive`` policy keeps respawning the service after a later crash.
  - no ``launchd_label``           → run the configured ``stop`` then ``start``
    scripts (keys ``stop_script``/``start_script``, or ``stop``/``start``).
  - target is the dashboard's own process (``com.commander.dashboard``) → spawn
    a DETACHED helper that sleeps ~1s then kickstarts, so the endpoint can
    return before launchd kills the worker mid-request.
"""
from __future__ import annotations

import os
from typing import Any, Optional

# The launchd label of the dashboard process itself. Restarting this env can't
# run kickstart inline — it would kill the worker before the response is sent —
# so it routes through the detached-helper path instead.
DASHBOARD_LAUNCHD_LABEL = "com.commander.dashboard"


class DeployActionError(ValueError):
    """Invalid or missing deploy/restart config. Maps to an HTTP 4xx."""


def get_env_entry(merged_config: Any, env: str) -> Optional[dict]:
    """Return a shallow copy of the config entry for *env*, or None."""
    if not isinstance(merged_config, dict):
        return None
    entry = merged_config.get(env)
    return dict(entry) if isinstance(entry, dict) else None


def require_deploy_target(entry: Optional[dict]) -> tuple[str, str]:
    """Return ``(working_dir, branch)`` for a deployable local env.

    Raises :class:`DeployActionError` when the env is missing, is not a local
    host, or lacks ``working_dir`` / ``branch`` — so the caller rejects before
    any shell command runs.
    """
    if not entry:
        raise DeployActionError("No deploy config for this environment")
    host = entry.get("host")
    if host not in (None, "local"):
        raise DeployActionError(
            f"Deploy is only supported for host=local environments (got host={host!r})"
        )
    working_dir = (entry.get("working_dir") or "").strip()
    branch = (entry.get("branch") or "").strip()
    if not working_dir:
        raise DeployActionError("working_dir is not configured for this environment")
    if not branch:
        raise DeployActionError("branch is not configured for this environment")
    return working_dir, branch


def build_pull_command(branch: str) -> list[str]:
    """Build the pull-only command. Fast-forward only; never merges or pushes."""
    return ["git", "pull", "--ff-only", "origin", branch]


def build_head_sha_command() -> list[str]:
    """Command that prints the current HEAD sha."""
    return ["git", "rev-parse", "HEAD"]


def restart_label(entry: dict) -> Optional[str]:
    """Return the configured ``launchd_label`` (non-empty) or None."""
    return (entry.get("launchd_label") or "").strip() or None


def stop_start_scripts(entry: dict) -> tuple[Optional[str], Optional[str]]:
    """Return ``(stop, start)`` scripts from the env config (None when absent).

    Accepts ``stop_script``/``start_script`` (canonical) or ``stop``/``start``.
    """
    stop = (entry.get("stop_script") or entry.get("stop") or "").strip() or None
    start = (entry.get("start_script") or entry.get("start") or "").strip() or None
    return stop, start


def restart_port(entry: dict) -> Optional[int]:
    """Return the configured bind ``port`` (int) for *entry*, or None (issue #769).

    A non-integer or empty value yields None so the start command falls back to
    its own default rather than exporting a bad PORT.
    """
    raw = entry.get("port")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def build_restart_env(
    entry: dict, base_env: Optional[dict] = None
) -> Optional[dict]:
    """Build the subprocess env for a restart, exporting ``PORT`` when configured.

    Returns None when no port is configured (the subprocess then inherits the
    ambient environment unchanged). Otherwise returns a copy of *base_env* (the
    process environment by default) with ``PORT=<configured>`` so the start/stop
    scripts bind the configured port instead of a hardcoded default (issue #769).
    """
    port = restart_port(entry)
    if port is None:
        return None
    env = dict(base_env if base_env is not None else os.environ)
    env["PORT"] = str(port)
    return env


def require_restart_target(entry: Optional[dict]) -> None:
    """Validate that *entry* has a usable restart strategy.

    Raises :class:`DeployActionError` when neither a ``launchd_label`` nor both
    ``stop`` and ``start`` scripts are present.
    """
    if not entry:
        raise DeployActionError("No deploy config for this environment")
    if restart_label(entry):
        return
    stop, start = stop_start_scripts(entry)
    if stop and start:
        return
    raise DeployActionError(
        "Restart requires either a launchd_label or both stop and start scripts"
    )


def service_target(label: str, uid: Optional[int] = None) -> str:
    """Build the launchd gui-domain service target ``gui/<uid>/<label>``."""
    if uid is None:
        uid = os.getuid()
    return f"gui/{uid}/{label}"


def build_kickstart_command(label: str, uid: Optional[int] = None) -> list[str]:
    """Build ``launchctl kickstart -k gui/<uid>/<label>``.

    ``-k`` restarts the service in place without unloading it, so a ``KeepAlive``
    policy is untouched.
    """
    return ["launchctl", "kickstart", "-k", service_target(label, uid)]


def is_self_restart(entry: dict) -> bool:
    """True when *entry* targets the dashboard's own launchd process."""
    return restart_label(entry) == DASHBOARD_LAUNCHD_LABEL


def build_self_restart_command(
    label: str, uid: Optional[int] = None, delay_seconds: float = 1.0
) -> list[str]:
    """Build a detached helper command: sleep, then kickstart the dashboard.

    The sleep lets the HTTP response flush (202 Accepted) before launchd kills
    the worker.
    """
    kick = " ".join(build_kickstart_command(label, uid))
    return ["sh", "-c", f"sleep {delay_seconds}; {kick}"]
