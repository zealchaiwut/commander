"""GitHub Events API poller — syncs recent repo events into the events table."""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

import httpx

if TYPE_CHECKING:
    import db as _DbModule

logger = logging.getLogger(__name__)

POLL_PAGE_SIZE = 300       # upper bound on events processed per poll call
RATE_LIMIT_THRESHOLD = 10  # skip poll when remaining API calls drop below this
MATCH_WINDOW_SECS = 300    # ±5 min window for action_id matching

_SUPPORTED_TYPES = {"IssuesEvent", "CreateEvent", "PullRequestEvent"}


def _get_gh_token() -> str:
    r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _fetch_events(owner: str, repo: str, per_page: int = 100) -> tuple[list[dict], dict]:
    """Fetch repo events from GitHub API. Returns (events_list, rate_info_dict)."""
    token = _get_gh_token()
    url = f"https://api.github.com/repos/{owner}/{repo}/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = httpx.get(url, headers=headers, params={"per_page": per_page}, timeout=15.0)

    rate_info = {
        "remaining": int(resp.headers.get("X-RateLimit-Remaining", 999)),
        "reset": resp.headers.get("X-RateLimit-Reset", ""),
    }

    resp.raise_for_status()
    return resp.json(), rate_info


def _parse_event(gh_event: dict) -> Optional[dict]:
    """Map a GitHub event to our events schema dict. Returns None if unsupported."""
    etype = gh_event.get("type", "")
    actor = (gh_event.get("actor") or {}).get("login", "unknown")
    payload = gh_event.get("payload") or {}
    gh_id = str(gh_event.get("id", ""))
    created_at = gh_event.get("created_at", "")

    if etype == "IssuesEvent":
        action = payload.get("action", "")
        issue = payload.get("issue") or {}
        issue_num = issue.get("number")
        target = f"#{issue_num}" if issue_num is not None else ""
        detail: dict = {"github_event_id": gh_id, "action": action}

        if action == "opened":
            ev_type = "issue_opened"
        elif action == "closed":
            ev_type = "issue_closed"
        elif action in ("labeled", "unlabeled"):
            ev_type = "label_added" if action == "labeled" else "label_removed"
            label = payload.get("label") or {}
            detail["label_name"] = label.get("name", "")
            detail["label_color"] = label.get("color", "")
        else:
            return None

        return {"type": ev_type, "actor": actor, "target": target,
                "detail": detail, "timestamp": created_at}

    if etype == "CreateEvent":
        if payload.get("ref_type") != "branch":
            return None
        ref = payload.get("ref", "")
        detail = {"github_event_id": gh_id, "ref": ref}
        return {"type": "branch_created", "actor": actor, "target": ref,
                "detail": detail, "timestamp": created_at}

    if etype == "PullRequestEvent":
        action = payload.get("action", "")
        pr = payload.get("pull_request") or {}
        head_ref = (pr.get("head") or {}).get("ref", "")
        base_ref = (pr.get("base") or {}).get("ref", "")
        merged = bool(pr.get("merged", False))
        pr_num = pr.get("number")
        target = f"PR#{pr_num}" if pr_num is not None else ""
        detail = {"github_event_id": gh_id, "head": head_ref, "base": base_ref}

        if action == "opened":
            ev_type = "pr_opened"
        elif action == "closed" and merged:
            ev_type = "pr_merged"
            detail["merged"] = True
        else:
            return None

        return {"type": ev_type, "actor": actor, "target": target,
                "detail": detail, "timestamp": created_at}

    return None


def _find_action_id(
    conn,
    project: str,
    target: str,
    timestamp_str: str,
    window_secs: int = MATCH_WINDOW_SECS,
) -> Optional[str]:
    """Find action_id from an existing Commander event matching target within time window."""
    try:
        ts = datetime.fromisoformat(timestamp_str.rstrip("Z")).replace(tzinfo=timezone.utc)
        lo = (ts - timedelta(seconds=window_secs)).strftime("%Y-%m-%dT%H:%M:%S")
        hi = (ts + timedelta(seconds=window_secs)).strftime("%Y-%m-%dT%H:%M:%S")
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None

    row = conn.execute(
        """SELECT action_id FROM events
           WHERE project = ? AND target = ? AND source != 'github'
             AND action_id IS NOT NULL
             AND timestamp BETWEEN ? AND ?
           ORDER BY ABS(CAST(strftime('%s', timestamp) AS INTEGER) - CAST(strftime('%s', ?) AS INTEGER))
           LIMIT 1""",
        (project, target, lo, hi, ts_str),
    ).fetchone()

    return row["action_id"] if row else None


def _normalise_ts(ts: str) -> str:
    """Strip Z suffix and return YYYY-MM-DDTHH:MM:SS."""
    try:
        return datetime.fromisoformat(ts.rstrip("Z")).strftime("%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def sync_github_events(
    project: str,
    repo: str,
    db_module=None,
) -> dict:
    """Poll GitHub Events API and upsert into events table.

    Returns dict with keys: synced, skipped, rate_limited, and optionally error.
    Does not paginate beyond one page to respect rate limits.
    """
    if db_module is None:
        import db as db_module  # type: ignore[assignment]

    if "/" in repo:
        owner, repo_name = repo.split("/", 1)
    else:
        owner, repo_name = repo, repo

    try:
        events, rate_info = _fetch_events(owner, repo_name, per_page=100)
    except Exception as exc:
        logger.warning("GitHub events fetch failed for %s: %s", repo, exc)
        return {"synced": 0, "skipped": 0, "rate_limited": False, "error": str(exc)}

    remaining = rate_info.get("remaining", 999)
    if remaining < RATE_LIMIT_THRESHOLD:
        reset_ts = rate_info.get("reset", "unknown")
        logger.warning(
            "GitHub rate limit low (%d remaining), skipping poll. Reset at %s",
            remaining,
            reset_ts,
        )
        return {"synced": 0, "skipped": 0, "rate_limited": True}

    synced = 0
    skipped = 0

    with db_module.get_conn() as conn:
        for gh_event in events[:POLL_PAGE_SIZE]:
            parsed = _parse_event(gh_event)
            if parsed is None:
                continue

            gh_id = parsed["detail"]["github_event_id"]

            existing = conn.execute(
                "SELECT id FROM events "
                "WHERE project = ? AND source = 'github' "
                "AND json_extract(detail, '$.github_event_id') = ?",
                (project, gh_id),
            ).fetchone()
            if existing:
                skipped += 1
                continue

            action_id = _find_action_id(conn, project, parsed["target"], parsed["timestamp"])
            ts = _normalise_ts(parsed["timestamp"])

            # TODO: Bot-identity attribution is not yet handled — when agents act under
            # the user's GitHub token, actor.login reflects the user's identity rather
            # than the agent. Deferred for a future ticket.
            conn.execute(
                """INSERT INTO events
                   (project, timestamp, source, actor, type, target, action_id, detail)
                   VALUES (?, ?, 'github', ?, ?, ?, ?, ?)""",
                (
                    project,
                    ts,
                    parsed["actor"],
                    parsed["type"],
                    parsed["target"],
                    action_id,
                    json.dumps(parsed["detail"]),
                ),
            )
            synced += 1

        conn.commit()

    return {"synced": synced, "skipped": skipped, "rate_limited": False}
