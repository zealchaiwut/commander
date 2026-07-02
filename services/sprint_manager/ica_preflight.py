"""ICA preflight readiness check (issue #1668).

Validates that the claude-proxy is reachable and that its model_map includes
every model Commander dispatch can emit, before any sprint agents are dispatched.

Call check_ica_readiness() before starting a sprint when llmProvider == "ica".
Raises IcaPreflightError with a descriptive message on any failure.
Returns None when all checks pass.
"""
from __future__ import annotations

import os
from typing import Sequence

import httpx

REQUIRED_MODELS: tuple[str, ...] = ("claude-sonnet-4-6", "claude-haiku-4-5")

_DEFAULT_PROXY_URL = "http://localhost:9090"


class IcaPreflightError(RuntimeError):
    """Raised when the ICA preflight check fails before sprint dispatch."""


def _proxy_base_url() -> str:
    return os.environ.get("COMMANDER_PROXY_URL", _DEFAULT_PROXY_URL).rstrip("/")


def check_ica_readiness(
    proxy_url: str | None = None,
    required_models: Sequence[str] = REQUIRED_MODELS,
    profile: str = "ica",
) -> None:
    """Assert ICA/claude-proxy is ready to serve all required models.

    Steps:
    1. GET {proxy}/health?profile={profile} — proxy must be reachable and
       return 2xx. The ?profile= param makes the proxy report the model_map
       of the profile this run will actually use (the per-request
       X-CCProxy-Profile routing), independent of the proxy's global default.
    2. Parse model_map from the health response body.
    3. Verify every entry in required_models is present in model_map.

    Raises IcaPreflightError with a descriptive message on any failure.
    Returns None silently when all checks pass.
    """
    base = (proxy_url or _proxy_base_url()).rstrip("/")
    health_url = f"{base}/health?profile={profile}"

    try:
        resp = httpx.get(health_url, timeout=5.0)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise IcaPreflightError(
            f"ICA not ready: claude-proxy /health check failed — "
            f"proxy unreachable or returned {exc}"
        )
    except httpx.HTTPStatusError as exc:
        raise IcaPreflightError(
            f"ICA not ready: claude-proxy /health check failed — "
            f"proxy unreachable or returned {exc.response.status_code}"
        )

    try:
        data = resp.json()
    except Exception:
        data = {}

    model_map: dict = data.get("model_map", {}) if isinstance(data, dict) else {}
    missing = [m for m in required_models if m not in model_map]

    if len(missing) == 1:
        raise IcaPreflightError(
            f"ICA not ready: model {missing[0]} not mapped in proxy"
        )
    if missing:
        missing_str = ", ".join(missing)
        raise IcaPreflightError(
            f"ICA not ready: models {missing_str} not mapped in proxy"
        )
