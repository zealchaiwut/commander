"""Commander configuration constants.

Values can be overridden via environment variables.
"""
import os

# Sandbox GitHub repo used for all tester-side GitHub operations in test mode.
# Override with COMMANDER_TEST_REPO env var.
# Falls back to GITHUB_ISSUE_TEST_REPO (the canonical env var for the throwaway
# issue/label test repo).  If neither is set, no default is assumed — callers
# must handle the empty-string case explicitly.
TEST_GITHUB_REPO: str = (
    os.environ.get("COMMANDER_TEST_REPO", "")
    or os.environ.get("GITHUB_ISSUE_TEST_REPO", "")
)

# Issues-mirror sync cadence (issue #756). The dashboard reads issue state from
# the local DB mirror, refreshed every SYNC_INTERVAL_SECONDS via ETag-conditional
# polling. Default is 60 s; override per-machine in apps/dashboard/.env.
# The authoritative resolver lives in github_events_sync.get_sync_interval().
SYNC_INTERVAL_SECONDS: int = int(os.environ.get("SYNC_INTERVAL_SECONDS", "60") or "60")


def _env_bool_from_value(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes")


def _env_bool(name: str, default: str = "0") -> bool:
    return _env_bool_from_value(os.environ.get(name, default))


def _read_global_app_config() -> dict:
    """Best-effort read of persisted global settings (JSON fallback or Neon)."""
    try:
        import settings_repo  # noqa: PLC0415
        from settings_schema import APP_CONFIG_KEY  # noqa: PLC0415

        return settings_repo.get_setting_scoped("global", APP_CONFIG_KEY) or {}
    except Exception:
        return {}


def _resolve_disable_flag(env_name: str, settings_key: str, *, default: str = "1") -> bool:
    """True when a product surface is disabled.

    Precedence: explicit env var → global settings store → default (disabled).
    """
    raw_env = os.environ.get(env_name)
    if raw_env is not None and str(raw_env).strip() != "":
        return _env_bool_from_value(raw_env)
    stored = _read_global_app_config()
    if settings_key in stored:
        return bool(stored[settings_key])
    return _env_bool(name=env_name, default=default)


def sprint_signoff_disabled() -> bool:
    return _resolve_disable_flag("COMMANDER_DISABLE_SIGNOFF", "disable_sprint_signoff")


def advisor_disabled() -> bool:
    return _resolve_disable_flag("COMMANDER_DISABLE_ADVISOR", "disable_advisor")


def brief_disabled() -> bool:
    """Daily/per-sprint brief (issue #839/#860) — parked (#1687), default off."""
    return _resolve_disable_flag("COMMANDER_DISABLE_BRIEF", "disable_brief")


def sprint_planning_disabled() -> bool:
    return _resolve_disable_flag("COMMANDER_DISABLE_PLANNING", "disable_sprint_planning")


def sprint_goal_required_disabled() -> bool:
    return _resolve_disable_flag(
        "COMMANDER_DISABLE_SPRINT_GOAL_REQUIRED", "disable_sprint_goal_required",
    )


def history_aggregate_enabled() -> bool:
    """True when COMMANDER_HISTORY_AGGREGATE=1.

    When enabled the frontend reads inline run_stats already present on each
    history row (issue #1639 backend) and skips per-card
    GET /api/sprints/{label}/run-stats fetches, reducing latency and server
    load. Default OFF so the old per-card fetch path remains until the flag
    is explicitly set (issue #1640).
    """
    return _env_bool("COMMANDER_HISTORY_AGGREGATE", default="0")


def running_aggregate_enabled() -> bool:
    """True when COMMANDER_RUNNING_AGGREGATE=1.

    When enabled the frontend uses the consolidated GET /api/running endpoint
    (issue #1645 backend) instead of fanning out per-agent requests, reducing
    latency and server load. Default OFF so the legacy fan-out code path runs
    as the safe fallback until the flag is explicitly set (issue #1646).
    """
    return _env_bool("COMMANDER_RUNNING_AGGREGATE", default="0")


def definition_of_ready_mode() -> str:
    """Return the DOR gate mode: 'block', 'warn', or 'off' (default 'off').

    Precedence: COMMANDER_DOR_MODE env var → global settings store → 'off'.
    """
    raw_env = os.environ.get("COMMANDER_DOR_MODE", "").strip().lower()
    if raw_env in ("block", "warn", "off"):
        return raw_env
    stored = _read_global_app_config()
    val = str(stored.get("definition_of_ready_mode", "off")).strip().lower()
    return val if val in ("block", "warn", "off") else "off"


def commander_features() -> dict:
    """Feature flags exposed to the dashboard UI (/api/environment)."""
    return {
        "signoff": not sprint_signoff_disabled(),
        "advisor": not advisor_disabled(),
        "brief": not brief_disabled(),
        "planning": not sprint_planning_disabled(),
        "goal_required": not sprint_goal_required_disabled(),
        "definition_of_ready_mode": definition_of_ready_mode(),
        # When True the History view reads inline run_stats from the history
        # feed instead of firing per-card /api/sprints/{label}/run-stats calls
        # (issue #1640).  Set COMMANDER_HISTORY_AGGREGATE=1 in .env to enable.
        "history_aggregate": history_aggregate_enabled(),
        # When True the Running tab uses the consolidated /api/running endpoint
        # instead of fanning out per-agent requests (issue #1646).
        # Set COMMANDER_RUNNING_AGGREGATE=1 in .env to enable.
        "running_aggregate": running_aggregate_enabled(),
    }
