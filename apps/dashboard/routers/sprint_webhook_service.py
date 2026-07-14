"""Best-effort webhook delivery for sprint terminal events (issue #1865).

When a sprint run is launched with an optional ``callback_url``, Commander
POSTs a JSON outcome document to that URL when the sprint reaches a terminal
state (finished, needs_rework, killed).  Delivery is best-effort: up to
3 attempts with exponential back-off; failures are logged and never affect
the sprint pipeline.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BACKOFF_SECS = [1, 3]    # waits before retry 2 and retry 3
_TIMEOUT_SECS = 10


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects — a 30x to an internal host must not bypass
    the SSRF screen applied to the original URL (issue #1896)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


# Opener that never follows redirects. Used for all webhook delivery.
_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def screen_callback_url(url: str) -> Optional[str]:
    """Return a rejection reason if *url* is not a safe public webhook target,
    else None (issue #1896 — SSRF guard).

    Rejects non-http(s) schemes and any host that resolves to a loopback,
    link-local (incl. 169.254.169.254 cloud metadata), private/RFC1918, ULA,
    multicast, reserved, or unspecified address. Resolves DNS and screens
    *every* returned address so a name that maps to a mix of public and
    internal IPs is still rejected.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:  # noqa: BLE001
        return f"unparseable url: {exc}"
    if parsed.scheme not in ("http", "https"):
        return f"scheme must be http/https, got {parsed.scheme!r}"
    host = parsed.hostname
    if not host:
        return "empty host"
    try:
        infos = socket.getaddrinfo(host, parsed.port or None)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        return f"cannot resolve host {host!r}: {exc}"
    for info in infos:
        addr = info[4][0]
        # Strip an IPv6 scope id (e.g. 'fe80::1%en0') before parsing.
        addr = addr.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return f"unparseable resolved address {addr!r}"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return f"host {host!r} resolves to blocked non-public address {addr}"
    return None

_OUTCOME_MAP = {
    "ready_to_merge": "finished",
    "completed": "finished",
    "needs_rework": "needs_rework",
    "cancelled": "killed",
}

_KILL_END_REASONS = frozenset({"stopped by user"})


def validate_callback_url(url: str) -> bool:
    """Return True iff url is structurally valid AND passes the SSRF screen.

    Structural check (http/https scheme + non-empty host) plus
    ``screen_callback_url`` so an internal/loopback/metadata target is rejected
    at request time, not just at fire time (issue #1896).
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
    except Exception:
        return False
    return screen_callback_url(url) is None


def check_callback_url_auth(
    *,
    callback_url: Optional[str],
    auth_header: str,
    api_token: str,
) -> bool:
    """Return True if the caller is permitted to set callback_url.

    Rules (AC5):
    - No callback_url set → always allowed (no auth check needed).
    - No COMMANDER_API_TOKEN configured → always allowed.
    - Token configured AND callback_url set → caller must present
      'Authorization: Bearer <token>'.
    """
    if not callback_url:
        return True
    if not api_token:
        return True
    return auth_header == f"Bearer {api_token}"


def fire_sprint_webhook(url: str, payload: dict) -> bool:
    """POST payload as JSON to url.  Returns True on 2xx, False on any error.

    Re-screens the URL against the SSRF guard immediately before connecting
    (authoritative check — DNS may have changed since request time, i.e. a
    rebinding attack) and never follows redirects (issue #1896).
    """
    reason = screen_callback_url(url)
    if reason is not None:
        logger.warning("sprint webhook blocked (SSRF guard): %s — %s", url, reason)
        return False
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Commander-Sprint-Webhook/1.0",
        },
        method="POST",
    )
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=_TIMEOUT_SECS) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def deliver_sprint_webhook(url: str, payload: dict) -> None:
    """Deliver webhook with up to 3 attempts and exponential back-off.

    Logs a warning on final failure; never raises.  Sprint outcome is
    unaffected regardless of delivery result.
    """
    for attempt in range(3):
        if attempt > 0:
            time.sleep(_BACKOFF_SECS[attempt - 1])
        if fire_sprint_webhook(url, payload):
            logger.info(
                "sprint webhook delivered (attempt %d) to %s",
                attempt + 1,
                url,
            )
            return
    logger.warning(
        "sprint webhook delivery failed after 3 attempts to %s — sprint outcome unaffected",
        url,
    )


def _read_plan_json_local(sprints_dir: Path, sprint_label: str) -> dict:
    path = sprints_dir / f"{sprint_label}-plan.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"tickets": raw}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_state_json_local(sprints_dir: Path, sprint_label: str) -> dict:
    # Prefer per-label state file; fall back to base sprint file (legacy).
    import re as _re
    label_path = sprints_dir / f"{sprint_label}-state.json"
    if label_path.exists():
        try:
            return json.loads(label_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    m = _re.match(r"^sprint-(\d+)(?:\.\d+)?$", sprint_label)
    if m:
        base_path = sprints_dir / f"sprint-{m.group(1)}-state.json"
        if base_path.exists():
            try:
                return json.loads(base_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    return {}


def _duration_sec(started_at: Optional[str]) -> int:
    """Compute elapsed seconds from started_at (ISO-8601) to now."""
    if not started_at:
        return 0
    try:
        s = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        return max(0, round((datetime.now(timezone.utc) - s).total_seconds()))
    except (ValueError, TypeError):
        return 0


def build_webhook_payload(
    *,
    sprints_dir: Path,
    sprint_label: str,
    project: str,
    started_at: Optional[str] = None,
) -> dict:
    """Build the outcome JSON document from plan.json + state.json.

    Returned shape::

        {
            "project": "owner/repo",
            "sprint_label": "sprint-N",
            "outcome": "finished" | "needs_rework" | "killed",
            "duration_sec": <int>,
            "tickets": [{"number": <int>, "status": <str>}],
            "summary_url": <str | absent>,
        }
    """
    plan = _read_plan_json_local(sprints_dir, sprint_label)
    state_data = _read_state_json_local(sprints_dir, sprint_label)

    plan_state = plan.get("state", "")
    end_reason = (plan.get("end_reason") or "").lower()

    if end_reason in _KILL_END_REASONS:
        outcome = "killed"
    else:
        outcome = _OUTCOME_MAP.get(plan_state, "needs_rework")

    effective_started_at = started_at or plan.get("started_at")
    tickets = [
        {"number": iss.get("number", iss.get("ticket_id")), "status": iss.get("status", "unknown")}
        for iss in (state_data.get("issues") or [])
        if iss.get("number") or iss.get("ticket_id")
    ]

    payload: dict = {
        "project": project,
        "sprint_label": sprint_label,
        "outcome": outcome,
        "duration_sec": _duration_sec(effective_started_at),
        "tickets": tickets,
    }

    summary_url = state_data.get("summary_issue_url") or plan.get("summary_issue_url")
    if summary_url:
        payload["summary_url"] = summary_url

    return payload


def _monitor_worker(
    proc,
    callback_url: str,
    sprint_label: str,
    project: str,
    sprints_dir: Path,
    started_at: str,
) -> None:
    """Background thread: wait for subprocess exit, then deliver webhook."""
    try:
        proc.wait()
    except Exception as exc:
        logger.debug("sprint webhook monitor: proc.wait() raised %s", exc)

    try:
        payload = build_webhook_payload(
            sprints_dir=sprints_dir,
            sprint_label=sprint_label,
            project=project,
            started_at=started_at,
        )
    except Exception as exc:
        logger.warning("sprint webhook: failed to build payload for %s: %s", sprint_label, exc)
        payload = {
            "project": project,
            "sprint_label": sprint_label,
            "outcome": "unknown",
            "duration_sec": _duration_sec(started_at),
            "tickets": [],
        }

    deliver_sprint_webhook(callback_url, payload)


def start_callback_monitor(
    *,
    proc,
    callback_url: Optional[str],
    sprint_label: str,
    project: str,
    sprints_dir: Path,
    started_at: str,
) -> Optional[threading.Thread]:
    """Start a daemon thread that fires callback_url when the sprint subprocess exits.

    Returns None immediately if callback_url is absent (AC4: zero behavior change).
    """
    if not callback_url:
        return None

    t = threading.Thread(
        target=_monitor_worker,
        args=(proc, callback_url, sprint_label, project, sprints_dir, started_at),
        daemon=True,
        name=f"sprint-webhook-{sprint_label}",
    )
    t.start()
    return t
