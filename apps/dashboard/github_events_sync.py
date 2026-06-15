"""GitHub Events API poller — syncs recent repo events into the events table.

Also hosts the issues-mirror sync loop (issue #756): the dashboard read model is
the local DB, so repo issues are mirrored into the `issues` table via
ETag-conditional polling.  Unchanged polls return HTTP 304 and consume zero
rate-limit quota; changed polls return 200 and upsert the mirror.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

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


# ── Issues mirror sync (issue #756) ───────────────────────────────────────────

ISSUES_PER_PAGE = 100
DEFAULT_SYNC_INTERVAL = 60  # seconds; AC requires default <= 60


def get_sync_interval() -> int:
    """Resolve the issues-mirror sync interval from SYNC_INTERVAL_SECONDS.

    Defaults to DEFAULT_SYNC_INTERVAL (<= 60 s). A non-positive or non-integer
    value falls back to the default.
    """
    raw = os.environ.get("SYNC_INTERVAL_SECONDS", "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SYNC_INTERVAL
    if value <= 0:
        return DEFAULT_SYNC_INTERVAL
    return value


def _normalise_issue(item: dict) -> dict:
    """Map a GitHub REST issue object to the gh-CLI-shaped dict the mirror stores."""
    return {
        "number": item.get("number"),
        "title": item.get("title", ""),
        "state": item.get("state", ""),
        "labels": [
            {"name": lbl.get("name", ""), "color": lbl.get("color", "")}
            for lbl in (item.get("labels") or [])
        ],
        "assignees": [
            {"login": a.get("login", "")} for a in (item.get("assignees") or [])
        ],
        "url": item.get("html_url", ""),
        "createdAt": item.get("created_at", ""),
        "updatedAt": item.get("updated_at", ""),
        "body": item.get("body", "") or "",
        # Milestone (issue #877): capture number + title from the REST payload so
        # the mirrored issue carries it without a follow-up GitHub call.
        "milestone": _milestone_ref(item.get("milestone")),
    }


def _milestone_ref(ms: Optional[dict]) -> Optional[dict]:
    """Reduce a REST milestone object to the {number, title} ref the mirror stores."""
    if not ms:
        return None
    return {"number": ms.get("number"), "title": ms.get("title", "")}


def _fetch_issues_conditional(
    owner: str,
    repo: str,
    etag: Optional[str] = None,
    per_page: int = ISSUES_PER_PAGE,
) -> tuple[int, Optional[list[dict]], Optional[str], dict]:
    """Conditionally fetch repo issues using an ETag.

    Returns (status_code, issues_or_None, new_etag, rate_info). On 304 the issues
    list is None (no body) and no quota is charged by GitHub.
    """
    token = _get_gh_token()
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if etag:
        headers["If-None-Match"] = etag

    # sort=updated so ANY recently-touched issue (a label change on an old
    # ticket included) surfaces in page 1; the default created-sort never shows
    # updates to issues older than the newest `per_page`.
    resp = httpx.get(
        url,
        headers=headers,
        params={"state": "all", "per_page": per_page,
                "sort": "updated", "direction": "desc"},
        timeout=15.0,
    )

    rate_info = {
        "remaining": int(resp.headers.get("X-RateLimit-Remaining", 999)),
        "reset": resp.headers.get("X-RateLimit-Reset", ""),
    }
    new_etag = resp.headers.get("ETag") or etag

    if resp.status_code == 304:
        return 304, None, new_etag, rate_info

    resp.raise_for_status()
    # The issues endpoint also returns PRs; exclude them from the mirror.
    items = [it for it in resp.json() if "pull_request" not in it]
    return 200, items, new_etag, rate_info


def sync_issues_mirror(repo: str, db_module=None) -> dict:
    """Poll repo issues with an ETag and upsert the local mirror (issue #756).

    200 -> upsert mirror and store the new ETag. 304 -> no-op (mirror and ETag
    preserved). Returns a dict with keys: status, synced, rate_limited, and
    optionally error.
    """
    if db_module is None:
        import db as db_module  # type: ignore[assignment]

    if "/" in repo:
        owner, repo_name = repo.split("/", 1)
    else:
        owner, repo_name = repo, repo

    etag_key = f"issues:{repo}"
    etag = db_module.get_sync_etag(etag_key)

    try:
        status, items, new_etag, rate_info = _fetch_issues_conditional(
            owner, repo_name, etag
        )
    except Exception as exc:
        logger.warning("issues mirror fetch failed for %s: %s", repo, exc)
        return {"status": 0, "synced": 0, "rate_limited": False, "error": str(exc)}

    if status == 304:
        logger.info("issues mirror %s: 304 Not Modified (zero quota)", repo)
        return {"status": 304, "synced": 0, "rate_limited": False}

    normalised = [_normalise_issue(it) for it in (items or [])]
    db_module.upsert_issues(repo, normalised)
    if new_etag:
        db_module.set_sync_etag(etag_key, new_etag)

    logger.info("issues mirror %s: 200, upserted %d issues", repo, len(normalised))
    return {"status": 200, "synced": len(normalised), "rate_limited": False}


# Hard cap on full-sync pagination: 50 pages × 100 = 5,000 issues per repo.
FULL_SYNC_MAX_PAGES = 50


def full_sync_issues_mirror(repo: str, db_module=None) -> dict:
    """Crawl ALL pages of a repo's issues into the mirror (bootstrap only).

    The incremental ETag poll covers one page of the most recently updated
    issues, so the bootstrap must walk the complete history once — otherwise
    old issues (e.g. early sprint summaries) never enter the mirror. REST only;
    stops early if the rate budget runs low.
    """
    if db_module is None:
        import db as db_module  # type: ignore[assignment]

    if "/" in repo:
        owner, repo_name = repo.split("/", 1)
    else:
        owner, repo_name = repo, repo

    token = _get_gh_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"https://api.github.com/repos/{owner}/{repo_name}/issues"

    total = 0
    for page in range(1, FULL_SYNC_MAX_PAGES + 1):
        resp = httpx.get(
            url, headers=headers,
            params={"state": "all", "per_page": ISSUES_PER_PAGE, "page": page,
                    "sort": "created", "direction": "asc"},
            timeout=15.0,
        )
        resp.raise_for_status()
        remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
        items = [it for it in resp.json() if "pull_request" not in it]
        if items:
            db_module.upsert_issues(repo, [_normalise_issue(it) for it in items])
            total += len(items)
        if len(resp.json()) < ISSUES_PER_PAGE:
            break
        if remaining < RATE_LIMIT_THRESHOLD:
            logger.warning(
                "full sync %s: stopping at page %d — rate budget low (%d left)",
                repo, page, remaining,
            )
            return {"status": 200, "synced": total, "rate_limited": True}

    logger.info("issues full sync %s: %d issue(s) mirrored", repo, total)
    return {"status": 200, "synced": total, "rate_limited": False}


BOOTSTRAP_RESTORE_NOTICE = (
    "history/order/settings start empty — restore from backup if migrating "
    "(backup restore / restore-db)"
)


def bootstrap_full_sync(repos: list[str], db_module=None) -> dict:
    """Run a one-time full GitHub sync on first run from an empty DB (issue #760).

    Detects an empty/absent mirror via the absent schema-marker row. If the marker
    is already present this is a no-op (skipped) and the caller proceeds straight
    to the ETag loop. Otherwise it syncs every repo synchronously, logs progress
    to stdout/server log, logs the local-authority restore notice, and — only when
    every repo synced without error — writes the schema-marker row so the next
    start skips the bootstrap. A failed repo leaves the marker unwritten so the
    bootstrap retries on the next start.

    Never raises: an empty or absent DB at startup must not crash the server.
    """
    if db_module is None:
        import db as db_module  # type: ignore[assignment]

    try:
        if db_module.is_bootstrap_complete():
            logger.info("[bootstrap] schema marker present — skipping full sync")
            return {"bootstrapped": False, "skipped": True, "synced": 0, "errors": 0}

        total = len(repos)
        msg = f"[bootstrap] empty DB detected — full GitHub sync starting for {total} repo(s)"
        logger.info(msg)
        print(msg, flush=True)

        synced = 0
        errors = 0
        for i, repo in enumerate(repos, 1):
            progress = f"[bootstrap] syncing {repo} ({i}/{total})"
            logger.info(progress)
            print(progress, flush=True)
            try:
                # Full paginated crawl, not the single-page ETag poll — the
                # mirror must hold the complete history (old sprint summaries
                # included) for mirror-backed reads to be trustworthy.
                result = full_sync_issues_mirror(repo, db_module=db_module)
            except Exception as exc:  # never let one repo abort the bootstrap
                logger.warning("[bootstrap] sync failed for %s: %s", repo, exc)
                errors += 1
                continue
            if (result.get("status", 0) in (0, None) or result.get("error")
                    or result.get("rate_limited")):
                # A rate-limited crawl is PARTIAL — leave the marker unwritten
                # so the bootstrap retries and completes on a later start.
                errors += 1
            else:
                synced += result.get("synced", 0)

        logger.info(BOOTSTRAP_RESTORE_NOTICE)
        print(BOOTSTRAP_RESTORE_NOTICE, flush=True)

        if errors == 0:
            db_module.mark_bootstrap_complete()
            done = f"[bootstrap] complete — {synced} issue(s) synced, schema marker written"
        else:
            done = (
                f"[bootstrap] {errors} repo(s) failed — schema marker NOT written; "
                "bootstrap will retry on next start"
            )
        logger.info(done)
        print(done, flush=True)
        return {
            "bootstrapped": errors == 0,
            "skipped": False,
            "synced": synced,
            "errors": errors,
        }
    except Exception as exc:  # defensive: bootstrap must never crash startup
        logger.warning("[bootstrap] unexpected error, continuing to ETag loop: %s", exc)
        return {"bootstrapped": False, "skipped": False, "synced": 0, "errors": 1}


async def run_issues_sync_loop(
    repos: list[str],
    db_module=None,
    iterations: Optional[int] = None,
) -> None:
    """Periodic background loop that mirrors issues for each repo (issue #756).

    Sleeps get_sync_interval() seconds between sweeps. Re-reads the interval each
    iteration so SYNC_INTERVAL_SECONDS picks up on restart. `iterations` bounds
    the loop for tests; None runs until cancelled.
    """
    count = 0
    while True:
        for repo in repos:
            try:
                sync_issues_mirror(repo, db_module=db_module)
            except Exception as exc:  # never let the background task die
                logger.warning("issues mirror sync error for %s: %s", repo, exc)
            # Milestones mirror (issue #877): kept fresh on the same sweep so the
            # GET milestones endpoint serves from the local DB at zero quota.
            try:
                import github_milestones
                github_milestones.sync_milestones_mirror(repo, db_module=db_module)
            except Exception as exc:  # never let the background task die
                logger.warning("milestones mirror sync error for %s: %s", repo, exc)
        count += 1
        if iterations is not None and count >= iterations:
            return
        await asyncio.sleep(get_sync_interval())
