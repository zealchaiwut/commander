"""Render API deploy / restart action helpers (issue #725).

Pure, side-effect-free builders, validators, and response normalizers for the
dashboard's deploy/restart endpoints when an environment is hosted on Render
(``host=render``). Mirrors the design split used by ``deploy_actions`` for
``host=local``:

  - The functions here build request specs (URL, headers), validate config, and
    normalize Render's responses. They never shell out or open sockets, so they
    are trivially unit-testable.
  - The single ``call_render`` executor owns the actual HTTP round-trip; the
    server endpoints call it and own the FastAPI HTTP shape. Tests mock
    ``call_render`` (or ``urllib.request.urlopen`` underneath it).

Secret handling: ``render_api_key`` is read from the stored env config and used
only to build the ``Authorization: Bearer`` header. It is never returned to the
caller, logged, or placed in any response body.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

RENDER_API_BASE = "https://api.render.com/v1"


class RenderActionError(ValueError):
    """Invalid or missing Render deploy/restart config. Maps to HTTP 400."""


class RenderApiError(Exception):
    """A non-2xx response from the Render API.

    Carries the upstream HTTP status so the caller can map it (401/404 → 502
    with a specific message; everything else → a generic 502).
    """

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def is_render_host(entry: Optional[dict]) -> bool:
    """True when *entry* is configured for a Render host."""
    return bool(entry) and entry.get("host") == "render"


def require_render_target(entry: Optional[dict]) -> tuple[str, str]:
    """Return ``(render_service_id, render_api_key)`` for a Render env.

    Raises :class:`RenderActionError` (→ HTTP 400) when the env is missing, is
    not a render host, or lacks ``render_service_id`` / ``render_api_key`` — so
    the caller rejects BEFORE any Render API call is made.
    """
    if not entry:
        raise RenderActionError("No deploy config for this environment")
    if entry.get("host") != "render":
        raise RenderActionError(
            f"Render deploy is only supported for host=render environments "
            f"(got host={entry.get('host')!r})"
        )
    service_id = (entry.get("render_service_id") or "").strip()
    api_key = (entry.get("render_api_key") or "").strip()
    if not service_id:
        raise RenderActionError(
            "render_service_id is not configured for this environment"
        )
    if not api_key:
        raise RenderActionError(
            "render_api_key is not configured for this environment"
        )
    return service_id, api_key


def deploy_url(service_id: str) -> str:
    """Render endpoint that triggers a new deploy for *service_id*."""
    return f"{RENDER_API_BASE}/services/{service_id}/deploys"


def restart_url(service_id: str) -> str:
    """Render endpoint that restarts the service *service_id*."""
    return f"{RENDER_API_BASE}/services/{service_id}/restart"


def status_url(service_id: str) -> str:
    """Render endpoint returning the latest deploy (``?limit=1``)."""
    return f"{RENDER_API_BASE}/services/{service_id}/deploys?limit=1"


def auth_headers(api_key: str) -> dict[str, str]:
    """Build the Render auth + JSON headers for *api_key*."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# Render's deploy ``status`` values → the four normalized states the UI polls.
# Reference: Render API deploy lifecycle statuses.
_STATUS_MAP: dict[str, str] = {
    "created": "queued",
    "queued": "queued",
    "build_in_progress": "building",
    "update_in_progress": "building",
    "pre_deploy_in_progress": "building",
    "live": "live",
    "build_failed": "failed",
    "update_failed": "failed",
    "pre_deploy_failed": "failed",
    "canceled": "failed",
    "deactivated": "failed",
}


def normalize_status(render_status: Optional[str]) -> str:
    """Map a raw Render deploy status to ``queued|building|live|failed``.

    Unknown/absent statuses fall back to ``building`` — a deploy that exists but
    whose status we don't recognize is most likely still in progress.
    """
    if not render_status:
        return "building"
    return _STATUS_MAP.get(str(render_status), "building")


def latest_status_from_payload(payload: Any) -> str:
    """Extract & normalize the latest deploy status from a ``deploys?limit=1`` body.

    Render returns a list of ``{"deploy": {...}}`` wrappers; tolerates a bare
    list of deploy objects too. Returns a normalized status string.
    """
    items = payload if isinstance(payload, list) else []
    if not items:
        return "building"
    first = items[0]
    deploy = first.get("deploy", first) if isinstance(first, dict) else {}
    return normalize_status(deploy.get("status") if isinstance(deploy, dict) else None)


def latest_deploy_from_payload(payload: Any) -> dict:
    """Extract status, commit SHA, and last-deploy timestamp from a deploys body.

    Render returns a list of ``{"deploy": {...}}`` wrappers (tolerates a bare
    list of deploy objects too). Returns a dict with:

      - ``status``      → normalized ``queued|building|live|failed``
      - ``commit``      → the deploy's commit SHA (``deploy.commit.id``) or None
      - ``finished_at`` → ``finishedAt``/``updatedAt``/``createdAt`` or None

    An empty/absent payload yields a safe default (building, no commit/ts).
    """
    items = payload if isinstance(payload, list) else []
    if not items:
        return {"status": "building", "commit": None, "finished_at": None}
    first = items[0]
    deploy = first.get("deploy", first) if isinstance(first, dict) else {}
    if not isinstance(deploy, dict):
        deploy = {}
    commit = deploy.get("commit")
    commit_sha = commit.get("id") if isinstance(commit, dict) else None
    finished_at = (
        deploy.get("finishedAt")
        or deploy.get("updatedAt")
        or deploy.get("createdAt")
    )
    return {
        "status": normalize_status(deploy.get("status")),
        "commit": commit_sha,
        "finished_at": finished_at,
    }


def map_api_error(status_code: int) -> tuple[int, str]:
    """Map an upstream Render HTTP status to a ``(http_status, detail)`` pair.

    - 401 → 502 "Invalid Render API key"
    - 404 → 502 "Render service not found — check render_service_id"
    - anything else → 502 with a generic upstream message
    """
    if status_code == 401:
        return 502, "Invalid Render API key"
    if status_code == 404:
        return 502, "Render service not found — check render_service_id"
    return 502, f"Render API error (upstream status {status_code})"


def call_render(
    method: str,
    url: str,
    api_key: str,
    *,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    """Perform a Render API request and return ``(status_code, parsed_json)``.

    Raises :class:`RenderApiError` for any non-2xx response, with the upstream
    status attached so the endpoint can map 401/404 to specific 502 messages.
    Network/parse failures raise :class:`RenderApiError` with status 502.
    """
    req = urllib.request.Request(url, method=method, headers=auth_headers(api_key))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "null"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        status, detail = map_api_error(exc.code)
        raise RenderApiError(status, detail) from exc
    except urllib.error.URLError as exc:
        raise RenderApiError(502, f"Could not reach Render API: {exc.reason}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise RenderApiError(502, f"Invalid Render API response: {exc}") from exc
