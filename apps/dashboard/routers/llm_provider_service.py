"""Service logic for the LLM provider toggle (issue #1667).

Mechanism: Commander calls POST {COMMANDER_PROXY_URL}/profile with
{"name": "<provider>"} to instruct the claude-proxy to activate the named
profile. COMMANDER_PROXY_URL defaults to http://localhost:9090.

If the proxy endpoint is unreachable or returns non-2xx, Commander raises
HTTP 503 and does NOT persist the change — no silent fallback.

In-flight agent sessions are unaffected because they already hold open HTTP
connections through the proxy; only newly dispatched agents inherit the new
profile.

Valid providers: "anthropic", "ica"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

_SM_DIR = Path(__file__).resolve().parent.parent.parent.parent / "services" / "sprint_manager"
if str(_SM_DIR) not in sys.path:
    sys.path.insert(0, str(_SM_DIR))

import settings_repo as _settings_repo  # noqa: E402
from settings_schema import APP_CONFIG_KEY  # noqa: E402

_VALID_PROVIDERS: frozenset[str] = frozenset({"anthropic", "ica"})
_DEFAULT_PROVIDER = "anthropic"
_DEFAULT_PROXY_URL = "http://localhost:9090"


def _proxy_base_url() -> str:
    return os.environ.get("COMMANDER_PROXY_URL", _DEFAULT_PROXY_URL).rstrip("/")


def validate_provider(provider: str) -> None:
    """Raise HTTPException(400) if provider is not a known value."""
    if not provider or provider not in _VALID_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid llmProvider {provider!r}; "
                f"must be one of: {', '.join(sorted(_VALID_PROVIDERS))}"
            ),
        )


def _call_proxy_profile(provider: str) -> None:
    """POST to the claude-proxy profile endpoint.

    Raises HTTPException(503) if the proxy is unreachable or returns non-2xx.
    This is the documented ccswitch mechanism for issue #1667.
    """
    url = f"{_proxy_base_url()}/profile"
    try:
        resp = httpx.post(url, json={"name": provider}, timeout=5.0)
        resp.raise_for_status()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"claude-proxy unreachable at {url}: {exc}. "
                "Ensure the proxy is running before switching providers."
            ),
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"claude-proxy returned {exc.response.status_code} "
                f"when switching to profile '{provider}': {exc}"
            ),
        )


def get_provider() -> dict[str, Any]:
    """Return the currently active LLM provider."""
    stored = _settings_repo.get_setting_scoped("global", APP_CONFIG_KEY)
    provider = stored.get("llmProvider", _DEFAULT_PROVIDER)
    return {"provider": provider}


def set_provider(provider: str) -> dict[str, Any]:
    """Switch the active LLM provider.

    1. Validates the provider value.
    2. Calls the claude-proxy profile endpoint (raises 503 if unavailable).
    3. Persists the new value in the global settings store.
    4. Returns the updated provider state.

    In-flight agent sessions are NOT affected — only newly dispatched agents
    inherit the changed profile from the proxy.
    """
    validate_provider(provider)
    _call_proxy_profile(provider)
    current = _settings_repo.get_setting_scoped("global", APP_CONFIG_KEY)
    merged = {**current, "llmProvider": provider}
    _settings_repo.set_setting("global", APP_CONFIG_KEY, merged)
    return {"provider": provider, "ok": True}
