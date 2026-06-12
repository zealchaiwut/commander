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
import re
from pathlib import Path
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


def script_path_in_workdir(working_dir: str, script: str) -> Optional[Path]:
    """Return the first ``.sh`` path referenced by *script*, resolved under *working_dir*."""
    for match in re.finditer(r"(\S+\.sh)", script or ""):
        rel = match.group(1)
        path = Path(rel)
        if not path.is_absolute():
            path = Path(working_dir) / path
        return path
    return None


def check_deploy_readiness(entry: Optional[dict]) -> tuple[bool, list[str]]:
    """Return ``(ready, errors)`` for Deploy (git pull + restart).

    Render envs are always ready here (API key checks happen at action time).
    Local script-based envs require working_dir plus stop/start scripts on disk.
    An optional ``deploy_not_ready_message`` forces not-ready (vector-search-demo).
    Launchd-managed envs skip script checks once the optional block message passes.
    """
    if not entry:
        return False, ["No deploy config for this environment"]
    host = entry.get("host")
    if host == "render":
        return True, []
    if host != "local":
        return False, [f"Unsupported host: {host!r}"]

    errors: list[str] = []
    blocked = (entry.get("deploy_not_ready_message") or "").strip()
    if blocked:
        errors.append(blocked)

    if restart_label(entry):
        return len(errors) == 0, errors

    errors.extend(_deploy_script_errors(entry))
    return len(errors) == 0, errors


def _deploy_script_errors(entry: dict) -> list[str]:
    """Validate working_dir + stop/start scripts for script-based lifecycle."""
    errors: list[str] = []
    working_dir = (entry.get("working_dir") or "").strip()
    if not working_dir:
        errors.append("working_dir is not configured")
    elif not os.path.isdir(working_dir):
        stop_probe, start_probe = stop_start_scripts(entry)
        rel_scripts = [
            script_path_in_workdir(working_dir, s)
            for s in (stop_probe, start_probe) if s
        ]
        if any(p and not p.is_absolute() for p in rel_scripts):
            errors.append(f"Folder does not exist: {working_dir}")

    stop, start = stop_start_scripts(entry)
    if not stop or not start:
        errors.append("Missing stop_script / start_script — deploy lifecycle not configured")
    elif working_dir:
        errors.extend(_missing_script_file_errors(working_dir, stop, start))
    return errors


def _missing_script_file_errors(
    working_dir: str, stop: str, start: str
) -> list[str]:
    errors: list[str] = []
    for phase, script in (("Stop", stop), ("Start", start)):
        match = re.search(r"(?:^|\s)((?:\.?/)?[\w./-]+\.sh)\b", script or "")
        if not match:
            continue
        rel = match.group(1)
        sh_path = Path(rel)
        if sh_path.is_absolute():
            continue
        resolved = Path(working_dir) / sh_path
        if not resolved.is_file():
            errors.append(f"{phase} script not found: {resolved}")
    return errors


def _script_uses_relative_sh(script: Optional[str]) -> bool:
    """True when *script* references a non-absolute ``.sh`` path."""
    match = re.search(r"(?:^|\s)((?:\.?/)?[\w./-]+\.sh)\b", script or "")
    if not match:
        return False
    return not Path(match.group(1)).is_absolute()


def _working_dir_readiness(entry: dict, script: Optional[str]) -> tuple[bool, list[str]]:
    """Validate working_dir only when *script* uses a relative ``.sh`` path."""
    if not _script_uses_relative_sh(script):
        return True, []
    working_dir = (entry.get("working_dir") or "").strip()
    if not working_dir:
        return False, ["working_dir is not configured"]
    if not os.path.isdir(working_dir):
        return False, [f"Folder does not exist: {working_dir}"]
    return True, []


def check_restart_readiness(entry: Optional[dict]) -> tuple[bool, list[str]]:
    """Return ``(ready, errors)`` for Restart (no git pull).

    Does not apply ``deploy_not_ready_message`` — that gate is Deploy-only.
    """
    if not entry:
        return False, ["No deploy config for this environment"]
    if entry.get("host") == "render":
        return True, []
    if entry.get("host") != "local":
        return False, [f"Unsupported host: {host!r}"]
    try:
        require_restart_target(entry)
    except DeployActionError as exc:
        return False, [str(exc)]
    if restart_label(entry):
        return True, []
    return _check_script_lifecycle_readiness(entry)


def check_stop_readiness(entry: Optional[dict]) -> tuple[bool, list[str]]:
    """Return ``(ready, errors)`` for Stop."""
    if not entry:
        return False, ["No deploy config for this environment"]
    if entry.get("host") == "render":
        return False, ["Stop is not supported for host=render environments"]
    if entry.get("host") != "local":
        return False, [f"Unsupported host: {host!r}"]
    try:
        require_stop_target(entry)
    except DeployActionError as exc:
        return False, [str(exc)]
    if restart_label(entry):
        return True, []
    stop, _start = stop_start_scripts(entry)
    ok, errors = _working_dir_readiness(entry, stop)
    if not ok:
        return False, errors
    working_dir = (entry.get("working_dir") or "").strip()
    if working_dir and stop and _script_uses_relative_sh(stop):
        file_errors = _missing_script_file_errors(working_dir, stop, stop)
        return len(file_errors) == 0, file_errors
    return True, []


def check_start_readiness(entry: Optional[dict]) -> tuple[bool, list[str]]:
    """Return ``(ready, errors)`` for Start."""
    if not entry:
        return False, ["No deploy config for this environment"]
    if entry.get("host") == "render":
        return False, ["Start is not supported for host=render environments"]
    if entry.get("host") != "local":
        return False, [f"Unsupported host: {host!r}"]
    try:
        require_start_target(entry)
    except DeployActionError as exc:
        return False, [str(exc)]
    if restart_label(entry) and launchd_plist(entry):
        return True, []
    _stop, start = stop_start_scripts(entry)
    ok, errors = _working_dir_readiness(entry, start)
    if not ok:
        return False, errors
    working_dir = (entry.get("working_dir") or "").strip()
    if working_dir and start and _script_uses_relative_sh(start):
        file_errors = _missing_script_file_errors(working_dir, start, start)
        return len(file_errors) == 0, file_errors
    return True, []


def _check_script_lifecycle_readiness(entry: dict) -> tuple[bool, list[str]]:
    errors = _deploy_script_errors(entry)
    return len(errors) == 0, errors


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


def launchd_plist(entry: dict) -> Optional[str]:
    """Return the configured ``launchd_plist`` path (non-empty) or None (issue #771).

    ``launchctl bootstrap`` needs the plist path to load a service back into the
    domain after a ``bootout``; a launchd_label alone is not enough to Start.
    """
    return (entry.get("launchd_plist") or "").strip() or None


def require_stop_target(entry: Optional[dict]) -> None:
    """Validate that *entry* has a usable Stop strategy (issue #771).

    Stop is valid with either a ``launchd_label`` (→ ``launchctl bootout``) or a
    ``stop`` script. Raises :class:`DeployActionError` (→ HTTP 400) otherwise, so
    the caller rejects before any shell command runs.
    """
    if not entry:
        raise DeployActionError("No deploy config for this environment")
    if restart_label(entry):
        return
    stop, _ = stop_start_scripts(entry)
    if stop:
        return
    raise DeployActionError(
        "Stop requires either a launchd_label or a stop script"
    )


def require_start_target(entry: Optional[dict]) -> None:
    """Validate that *entry* has a usable Start strategy (issue #771).

    Start is valid with either a ``launchd_label`` AND ``launchd_plist`` (→
    ``launchctl bootstrap``) or a ``start`` script. A launchd_label without a
    plist is rejected because ``bootstrap`` cannot run without the plist path.
    """
    if not entry:
        raise DeployActionError("No deploy config for this environment")
    if restart_label(entry) and launchd_plist(entry):
        return
    _, start = stop_start_scripts(entry)
    if start:
        return
    raise DeployActionError(
        "Start requires either a launchd_label with launchd_plist, or a start script"
    )


def service_target(label: str, uid: Optional[int] = None) -> str:
    """Build the launchd gui-domain service target ``gui/<uid>/<label>``."""
    if uid is None:
        uid = os.getuid()
    return f"gui/{uid}/{label}"


def gui_domain(uid: Optional[int] = None) -> str:
    """Build the launchd gui-domain target ``gui/<uid>`` (issue #771).

    ``bootstrap`` is addressed at the domain (``gui/<uid>``) plus a plist path,
    unlike ``bootout``/``print`` which address an individual service.
    """
    if uid is None:
        uid = os.getuid()
    return f"gui/{uid}"


def build_bootout_command(label: str, uid: Optional[int] = None) -> list[str]:
    """Build ``launchctl bootout gui/<uid>/<label>`` for Stop (issue #771).

    Unlike ``kickstart -k`` (restart in place), ``bootout`` removes the service
    from the domain so it stays down until a later ``bootstrap`` brings it back.
    """
    return ["launchctl", "bootout", service_target(label, uid)]


def build_bootstrap_command(plist: str, uid: Optional[int] = None) -> list[str]:
    """Build ``launchctl bootstrap gui/<uid> <plist>`` for Start (issue #771)."""
    return ["launchctl", "bootstrap", gui_domain(uid), plist]


def build_print_command(label: str, uid: Optional[int] = None) -> list[str]:
    """Build ``launchctl print gui/<uid>/<label>`` to probe run state (issue #771)."""
    return ["launchctl", "print", service_target(label, uid)]


def interpret_run_state(returncode: int) -> str:
    """Map a ``launchctl print`` return code to a run state (issue #771).

    ``print`` returns 0 when the service is bootstrapped into the domain
    (running/loaded) and non-zero once it has been booted out (stopped).
    """
    return "running" if returncode == 0 else "stopped"


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
