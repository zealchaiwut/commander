"""Model routing helpers for the sprint manager.

These functions were extracted from sprint_manager.py (issue #1276) to
isolate the coder model/backend selection concern. sprint_manager.py
re-imports and re-exports them so all existing call sites remain unmodified.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig

from services.sprint_manager.paths import _plan_json_path
from services.logging import log as structured_log

# Default coder-model routing by ticket size (issue #789).
# S → haiku (fast/cheap for small changes); M/L/XL → sonnet.
_DEFAULT_CODER_BY_SIZE: dict = {
    "S":  "claude-haiku-4-5",
    "M":  "claude-sonnet-4-6",
    "L":  "claude-sonnet-4-6",
    "XL": "claude-sonnet-4-6",
}

# Extensions that mark a path as docs/config (issue #1403, #1428).
_DOCS_PATH_EXTENSIONS = frozenset({".md", ".yaml", ".yml", ".json"})
# Extensions that mark a path as code — their presence disqualifies docs-only routing.
_CODE_PATH_EXTENSIONS = frozenset({".py", ".js", ".ts"})


def get_role_profile(role: str, cfg: Optional["SprintConfig"]) -> Optional[str]:
    """Return the CCPROXY_PROFILE override for a given agent role (issue #1671).

    Returns None when cfg is None or when no per-role profile is configured —
    the caller should then leave CCPROXY_PROFILE unset so the global value
    from the parent environment is inherited.
    """
    if cfg is None:
        return None
    if role == "coder":
        return cfg.coder_profile or None
    if role == "tester":
        return cfg.tester_profile or None
    return None


def _plan_json_use_cline_followups(
    sprint_label: str,
    cfg: Optional["SprintConfig"] = None,
) -> bool:
    """Return True when the sprint's plan.json has use_cline_followups=true (issue #919)."""
    try:
        path = _plan_json_path(sprint_label, cfg)
        if not path.exists():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        return bool(raw.get("use_cline_followups", False) if isinstance(raw, dict) else False)
    except Exception:
        return False


def _plan_json_llm_provider(
    sprint_label: str,
    cfg: Optional["SprintConfig"] = None,
) -> Optional[str]:
    """Return the per-run LLM provider from the sprint's plan.json, or None.

    Written by the run modal (POST /api/sprints/run body.llm_provider) and
    copied to rerun children — mirrors _plan_json_use_cline_followups.
    """
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
    cfg: Optional["SprintConfig"],
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
        import settings_repo  # noqa: PLC0415 — lazy, mirrors _get_llm_provider

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
      routing signal (a client-side CCPROXY_PROFILE env var never reaches the
      proxy — only this header or the proxy's own global state do).
    - CCPROXY_PROFILE → telemetry only: the post-tool hook forwards its own
      env value as ccproxy_profile, which is what flags the run is_ica.
    - Auth: ANTHROPIC_API_KEY stays stripped (never send the metered key at
      the proxy); a dummy ANTHROPIC_AUTH_TOKEN keeps the CLI off subscription
      OAuth for this subprocess — the proxy's openai-kind profile ignores
      inbound auth and attaches its own ICA key upstream.

    When a per-role profile (issue #1671) already set CCPROXY_PROFILE, that
    name wins for both the header and the env var.
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


# The only model the ICA upstream serves (proxy maps it to
# global/anthropic.claude-sonnet-4-6). Any other model id 404s upstream,
# so every ICA-routed dispatch must be pinned to this.
ICA_FORCED_MODEL = "claude-sonnet-4-6"


def apply_provider_env(
    sub_env: dict,
    model: str,
    *,
    sprint_label: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    repo: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> str:
    """Apply the effective LLM provider to an agent subprocess env (in place).

    Returns the model the dispatch must use: unchanged for direct Anthropic,
    ICA_FORCED_MODEL when the effective provider is 'ica' (the ICA upstream
    only serves claude-sonnet). Callers with no sprint context (dashboard
    one-shot agents) pass repo only — the global llmProvider setting decides.
    """
    if get_effective_llm_provider(sprint_label, cfg, repo) != "ica":
        return model
    apply_ica_agent_env(sub_env, profile_name)
    return ICA_FORCED_MODEL


def _effective_coder_backend(
    sprint_label: Optional[str],
    cfg: Optional["SprintConfig"],
    prior_failures: Optional[list],
) -> str:
    """Return the effective coder backend for a dispatch (issue #919).

    Follow-up dispatches (prior_failures non-empty) route to Cline when the
    sprint's plan.json has use_cline_followups=true.  Initial dispatches and
    all dispatches when the flag is absent/false use cfg.coder_backend.
    """
    base = cfg.coder_backend if cfg is not None else "claude-code"
    if prior_failures and sprint_label and _plan_json_use_cline_followups(sprint_label, cfg):
        return "cline"
    return base


def _is_docs_only(estimate: Optional[dict]) -> tuple[bool, str]:
    """Determine whether a ticket qualifies for docs-only Haiku routing (issue #1403).

    Returns (True, reason) when the ticket is docs/config-only; (False, "") otherwise.
    reason is one of "docs-only:flag" or "docs-only:paths".
    """
    if not estimate:
        return False, ""

    risk_flags = estimate.get("risk_flags") or []
    if "docs-only" in risk_flags:
        return True, "docs-only:flag"

    paths = estimate.get("files_likely_affected") or []
    if not paths:
        return False, ""

    def _is_doc_path(p: str) -> bool:
        suffix = PurePosixPath(p).suffix.lower()
        return suffix in _DOCS_PATH_EXTENSIONS or p.startswith("docs/")

    has_code = any(PurePosixPath(p).suffix.lower() in _CODE_PATH_EXTENSIONS for p in paths)
    if has_code:
        return False, ""

    if all(_is_doc_path(p) for p in paths):
        return True, "docs-only:paths"

    return False, ""


def _resolve_coder_model(
    issue_num: int,
    cfg: Optional["SprintConfig"],
    estimate: Optional[dict] = None,
) -> tuple[str, str]:
    """Return (model, routing_reason) for a coder dispatch.

    Routing precedence:
      0. docs-only (flag or paths heuristic) + non-XL → Haiku via _is_docs_only
      1. If estimate has a known size (S/M/L/XL) → look up cfg.coder_by_size
      2. Otherwise → cfg.coder_model flat default (or hardcoded sonnet fallback)

    routing_reason examples: "docs-only:flag", "docs-only:paths", "size=S",
    "unestimated:default".
    """
    flat_default = cfg.coder_model if cfg is not None else "claude-sonnet-4-6"
    by_size = (cfg.coder_by_size if cfg is not None else None) or _DEFAULT_CODER_BY_SIZE

    if estimate:
        size = str(estimate.get("size") or "").upper().strip()

        if size != "XL":
            docs_only, docs_reason = _is_docs_only(estimate)
            if docs_only:
                haiku_model = by_size.get("S") or _DEFAULT_CODER_BY_SIZE["S"]
                return haiku_model, docs_reason

        if size in ("S", "M", "L", "XL"):
            model = by_size.get(size) or flat_default
            return model, f"size={size}"

    return flat_default, "unestimated:default"


def _resolve_cline_model(cfg: Optional["SprintConfig"], coder_model: str) -> tuple[str, str]:
    """Return (model, routing_reason) for a Cline headless dispatch.

    Cline uses its own model namespace (e.g. Bedrock ARNs). agent_config.cline.model
    takes precedence; falling back to coder_model is a misconfiguration risk.
    """
    if cfg is not None and cfg.cline_model:
        return cfg.cline_model, "cline:configured"
    return coder_model, "cline:fallback-coder_model"


def _select_coder_backend(
    issue_num: int,
    cfg: Optional["SprintConfig"],
    repo_name: Optional[str] = None,
) -> str:
    """Resolve the effective coder backend for this dispatch (issue #918).

    Returns 'cline' when all three conditions hold:
      1. sprint.use_cline_followups is True
      2. The ticket carries the 'follow-up' label
      3. .clinerules exists in cfg.worktree_coder

    Falls back to 'claude-code' (with a warning) when the first two conditions
    are met but .clinerules is absent — so the sprint is never blocked.

    For all other cases (explicit coder_backend override, no flag, no label)
    returns cfg.coder_backend unchanged.
    """
    if cfg is None:
        return "claude-code"

    if getattr(cfg, "use_cline_followups", False) is not True:
        return cfg.coder_backend

    # Lazy import to avoid a circular dependency at module load time.
    import services.sprint_manager.sprint_manager as _sm  # noqa: PLC0415
    labels = _sm._get_issue_labels(issue_num, repo_name=repo_name)

    if "follow-up" not in labels:
        return cfg.coder_backend

    cwd_path: Path = cfg.worktree_coder
    if not (cwd_path / ".clinerules").exists():
        structured_log.warn(
            "cline_followup_fallback",
            f"[coder] follow-up routing to Cline requested but .clinerules absent in "
            f"{cwd_path} — falling back to claude-code (issue #918)",
            issue_num=issue_num,
            worktree=str(cwd_path),
        )
        return "claude-code"

    return "cline"
