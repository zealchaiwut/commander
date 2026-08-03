"""Best-effort webhook delivery for sprint terminal events (issue #1865).

When a sprint run is launched with an optional ``callback_url``, Commander
POSTs a JSON outcome document to that URL when the sprint reaches a terminal
state (finished, needs_rework, killed).  Delivery is best-effort: up to
3 attempts with exponential back-off; failures are logged and never affect
the sprint pipeline.

Issue #1945: also writes commander_report.latest.json atomically to
COMMANDER_REPORT_PATH at sprint end, using the same payload builder as the
webhook so both outputs are always structurally identical.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import tempfile
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


_DEFAULT_REPORT_PATH = "/var/run/commander/commander_report.latest.json"


def _get_report_path() -> Path:
    """Return the path for commander_report.latest.json (COMMANDER_REPORT_PATH env var)."""
    return Path(os.environ.get("COMMANDER_REPORT_PATH") or _DEFAULT_REPORT_PATH)


def build_commander_report(
    *,
    sprints_dir: Path,
    sprint_label: str,
    project: str,
    started_at: Optional[str] = None,
) -> dict:
    """Build the rich commander report payload from plan.json + state.json (issue #1945).

    Returned shape::

        {
            "run_id": <str>,
            "trigger": {"by": <str>, "confirmed_at": <ISO-8601>, "mode": <str>},
            "branch": <str>,
            "summary": {"attempted": <int>, "completed": <int>, "failed": <int>, "skipped": <int>},
            "completed": [{"ticket_id", "title", "commits", "tests", "merged_to", "pr_url"}],
            "needs_review": [{"ticket_id", "title", "commits", "tests"}],
            "dead_letter": [{"ticket_id", "title", "attempts", "last_error"}],
            "cost": {"tokens": <int>, "usd": <float>, "ceiling_hit": <bool>},
            "actions": [{"type": <str>, "ticket_id": <str>}],
        }
    """
    plan = _read_plan_json_local(sprints_dir, sprint_label)
    state_data = _read_state_json_local(sprints_dir, sprint_label)

    confirmed_at = plan.get("started_at") or started_at or datetime.now(timezone.utc).isoformat()

    # Trigger-owner metadata (issue #1946): read from plan.json if stored at run start.
    _triggered_by: Optional[str] = plan.get("triggered_by") or None
    _run_mode: str = str(plan.get("run_mode") or "auto")

    issues = state_data.get("issues") or []
    completed_list: list = []
    needs_review_list: list = []
    _status_dead_letter_list: list = []
    n_completed = n_failed = n_skipped = 0

    for iss in issues:
        ticket_id = str(iss.get("number") or iss.get("ticket_id") or "")
        title = str(iss.get("title") or "")
        status = str(iss.get("status") or "")

        if status == "done":
            n_completed += 1
            completed_list.append({
                "ticket_id": ticket_id,
                "title": title,
                "commits": list(iss.get("feature_commits") or []),
                "tests": list(iss.get("tester_test_files") or []),
                "merged_to": f"sprint/{sprint_label}",
                "pr_url": str(iss.get("pr_url") or ""),
            })
        elif status in ("failed", "error"):
            n_failed += 1
            _status_dead_letter_list.append({
                "ticket_id": ticket_id,
                "title": title,
                "attempts": int(iss.get("tester_attempt_count") or 1),
                "last_error": str(iss.get("failure_reason") or ""),
            })
        elif status == "skipped":
            n_skipped += 1
        elif status == "needs_review":
            needs_review_list.append({
                "ticket_id": ticket_id,
                "title": title,
                "commits": list(iss.get("feature_commits") or []),
                "tests": list(iss.get("tester_test_files") or []),
            })
        # pending / queued / other statuses are not yet attempted — excluded from counts

    # Use persisted dead_letter registry (#1942) as source of truth when available;
    # fall back to status-derived reconstruction only when the field is absent or empty.
    _persisted_dead_letter = state_data.get("dead_letter")
    dead_letter_list: list = (
        list(_persisted_dead_letter)
        if _persisted_dead_letter
        else _status_dead_letter_list
    )

    n_attempted = n_completed + n_failed + n_skipped + len(needs_review_list)

    # Token totals: prefer top-level state fields; fall back to per-issue sum.
    total_in = int(state_data.get("total_tokens_in") or 0)
    total_out = int(state_data.get("total_tokens_out") or 0)
    if total_in == 0 and total_out == 0:
        total_in = sum(int(iss.get("tokens_in") or 0) for iss in issues)
        total_out = sum(int(iss.get("tokens_out") or 0) for iss in issues)

    actions = [
        {"type": "rerun", "ticket_id": str(iss.get("number") or iss.get("ticket_id") or "")}
        for iss in issues
        if str(iss.get("status") or "") in ("failed", "error")
    ]

    return {
        "run_id": confirmed_at,
        "triggered_by": _triggered_by,
        "trigger": {
            "by": _triggered_by or "sprint_manager",
            "confirmed_at": confirmed_at,
            "mode": _run_mode,
        },
        "branch": f"sprint/{sprint_label}",
        "summary": {
            "attempted": n_attempted,
            "completed": n_completed,
            "failed": n_failed,
            "skipped": n_skipped,
        },
        "completed": completed_list,
        "needs_review": needs_review_list,
        "dead_letter": dead_letter_list,
        "cost": {
            "tokens": total_in + total_out,
            "usd": 0.0,
            "ceiling_hit": bool(
                plan.get("token_budget_exceeded") or state_data.get("ceiling_hit")
            ),
        },
        "actions": actions,
    }


def build_webhook_payload(
    *,
    sprints_dir: Path,
    sprint_label: str,
    project: str,
    started_at: Optional[str] = None,
) -> dict:
    """Build the outcome JSON document — delegates to build_commander_report (issue #1945).

    Webhook and file payloads share a single builder so they are always
    structurally identical with no field drift between the two.
    """
    return build_commander_report(
        sprints_dir=sprints_dir,
        sprint_label=sprint_label,
        project=project,
        started_at=started_at,
    )


def write_commander_report(payload: dict, path: Path) -> None:
    """Atomically write payload as JSON to path (write-to-temp + rename).

    If the parent directory does not exist, logs a clear error and returns
    without crashing — the sprint run is unaffected (AC5, issue #1945).
    """
    if not path.parent.exists():
        logger.error(
            "commander report: parent directory %s does not exist — report not written",
            path.parent,
        )
        return
    fd, tmp_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2))
        tmp.rename(path)
        logger.info("commander report written to %s", path)
    except Exception as exc:
        logger.error("commander report: failed to write %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _report_monitor_worker(
    proc,
    sprint_label: str,
    project: str,
    sprints_dir: Path,
    started_at: str,
) -> None:
    """Background thread: wait for subprocess exit, then write commander report file."""
    try:
        proc.wait()
    except Exception as exc:
        logger.debug("sprint report monitor: proc.wait() raised %s", exc)

    try:
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label=sprint_label,
            project=project,
            started_at=started_at,
        )
    except Exception as exc:
        logger.warning(
            "sprint report: failed to build payload for %s: %s", sprint_label, exc
        )
        return

    write_commander_report(payload, _get_report_path())


def start_report_monitor(
    *,
    proc,
    sprint_label: str,
    project: str,
    sprints_dir: Path,
    started_at: str,
) -> threading.Thread:
    """Start a daemon thread that writes commander_report.latest.json when the sprint subprocess exits.

    Always starts a thread regardless of callback_url configuration (issue #1945).
    """
    t = threading.Thread(
        target=_report_monitor_worker,
        args=(proc, sprint_label, project, sprints_dir, started_at),
        daemon=True,
        name=f"sprint-report-{sprint_label}",
    )
    t.start()
    return t


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
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label=sprint_label,
            project=project,
            started_at=started_at,
        )
    except Exception as exc:
        logger.warning("sprint webhook: failed to build payload for %s: %s", sprint_label, exc)
        payload = {
            "run_id": started_at or sprint_label,
            "trigger": {"by": "sprint_manager", "confirmed_at": started_at or "", "mode": "auto"},
            "branch": f"sprint/{sprint_label}",
            "summary": {"attempted": 0, "completed": 0, "failed": 0, "skipped": 0},
            "completed": [],
            "needs_review": [],
            "dead_letter": [],
            "cost": {"tokens": 0, "usd": 0.0, "ceiling_hit": False},
            "actions": [],
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
