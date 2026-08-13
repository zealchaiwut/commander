"""Model routing helpers shared across bulk-create, split-XL, brief, and other
one-shot agent paths.

Role-dispatch functions (_resolve_coder_model, _effective_coder_backend, etc.)
were removed in issue #2249 when sprint_manager.py/dispatch.py were deleted.
The functions here are the shared subset still used by dashboard routers and
sprint-manager helper scripts.
"""
from __future__ import annotations

import json
from typing import Optional

from services.sprint_manager.paths import _plan_json_path


def _plan_json_llm_provider(
    sprint_label: str,
    cfg: Optional[object] = None,
) -> Optional[str]:
    """Return the per-run LLM provider from the sprint's plan.json, or None."""
    try:
        path = _plan_json_path(sprint_label, cfg)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        value = raw.get("llm_provider") if isinstance(raw, dict) else None
        return str(value) if value else None
    except Exception:
        return None


def get_effective_llm_provider(
    sprint_label: Optional[str],
    cfg: Optional[object],
    repo: Optional[str] = None,
) -> str:
    """Resolve the LLM provider for this run: plan.json > global setting > anthropic.

    The per-run plan.json value (run-modal choice) always wins; the global
    `llmProvider` setting covers CLI-started/legacy sprints; the final
    fallback is direct Anthropic.
    """
    if sprint_label:
        per_run = _plan_json_llm_provider(sprint_label, cfg)
        if per_run:
            return per_run
    try:
        import settings_repo  # noqa: PLC0415

        stored = settings_repo.get_setting("app_config", project=repo) or {}
        if not isinstance(stored, dict):
            stored = {}
        value = stored.get("llmProvider")
        if value:
            return str(value)
    except Exception:
        pass
    return "anthropic"


def apply_ica_agent_env(sub_env: dict, profile_name: Optional[str] = None) -> None:
    """Route an agent subprocess through claude-proxy to ICA (in place).

    Sets on *sub_env*:
    - ANTHROPIC_BASE_URL → the local claude-proxy (COMMANDER_PROXY_URL).
    - ANTHROPIC_CUSTOM_HEADERS → X-CCProxy-Profile, the proxy's per-REQUEST
      routing signal.
    - CCPROXY_PROFILE → telemetry only.
    - Auth: strips ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN; sets a dummy
      ANTHROPIC_AUTH_TOKEN so the CLI uses the proxy path.
    """
    import os  # noqa: PLC0415

    effective_profile = sub_env.get("CCPROXY_PROFILE") or profile_name or "ica"
    proxy_url = os.environ.get("COMMANDER_PROXY_URL", "http://127.0.0.1:8788").rstrip("/")
    sub_env["ANTHROPIC_BASE_URL"] = proxy_url
    sub_env["ANTHROPIC_CUSTOM_HEADERS"] = f"X-CCProxy-Profile: {effective_profile}"
    sub_env["CCPROXY_PROFILE"] = effective_profile
    sub_env.pop("ANTHROPIC_API_KEY", None)
    sub_env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    sub_env["ANTHROPIC_AUTH_TOKEN"] = "commander-ica-proxy"


# The only model the ICA upstream serves.
ICA_FORCED_MODEL = "claude-sonnet-4-6"


def _ica_allowed_roles() -> Optional[set[str]]:
    """Roles allowed to route through ICA (COMMANDER_ICA_ROLES, comma-separated).

    Unset/blank → None, meaning no role restriction.
    """
    import os  # noqa: PLC0415

    raw = os.environ.get("COMMANDER_ICA_ROLES", "").strip()
    if not raw:
        return None
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def apply_provider_env(
    sub_env: dict,
    model: str,
    *,
    sprint_label: Optional[str] = None,
    cfg: Optional[object] = None,
    repo: Optional[str] = None,
    profile_name: Optional[str] = None,
    role: Optional[str] = None,
) -> str:
    """Apply the effective LLM provider to an agent subprocess env (in place).

    Returns the model the dispatch must use: unchanged for direct Anthropic,
    ICA_FORCED_MODEL when the effective provider is 'ica'. Callers with no
    sprint context (dashboard one-shot agents) pass repo only — the global
    llmProvider setting decides.

    When COMMANDER_ICA_ROLES is set, only the listed roles route through ICA;
    other roles stay on direct Anthropic.
    """
    if get_effective_llm_provider(sprint_label, cfg, repo) != "ica":
        return model
    allowed = _ica_allowed_roles()
    if allowed is not None and (role or "").lower() not in allowed:
        return model
    apply_ica_agent_env(sub_env, profile_name)
    return ICA_FORCED_MODEL
