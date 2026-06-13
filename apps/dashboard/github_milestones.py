"""GitHub Milestones REST layer + local mirror sync (issue #877).

The dashboard read model is the local DB, so repo milestones are mirrored into
the `milestones` table via ETag-conditional polling — unchanged polls return
HTTP 304 and consume zero rate-limit quota, changed polls return 200 and upsert
the mirror. Write operations (create/edit/close) hit GitHub live and upsert the
mirror in the same request cycle so the change is reflected immediately.

GitHub Milestones REST reference:
    GET    /repos/{owner}/{repo}/milestones
    POST   /repos/{owner}/{repo}/milestones
    PATCH  /repos/{owner}/{repo}/milestones/{number}
    DELETE /repos/{owner}/{repo}/milestones/{number}
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from github_events_sync import _get_gh_token  # shared gh-token resolver

logger = logging.getLogger(__name__)

_API = "https://api.github.com"
MILESTONES_PER_PAGE = 100


def _split_repo(repo: str) -> tuple[str, str]:
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return owner, name
    return repo, repo


def _headers(etag: Optional[str] = None) -> dict:
    headers = {
        "Authorization": f"Bearer {_get_gh_token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if etag:
        headers["If-None-Match"] = etag
    return headers


def _normalise_milestone(item: dict) -> dict:
    """Map a GitHub REST milestone object to the dict the mirror stores."""
    return {
        "number": item.get("number"),
        "title": item.get("title", ""),
        "description": item.get("description", "") or "",
        "state": item.get("state", ""),
        "due_on": item.get("due_on", "") or "",
        "url": item.get("html_url", ""),
        "open_issues": item.get("open_issues", 0),
        "closed_issues": item.get("closed_issues", 0),
        "createdAt": item.get("created_at", ""),
        "updatedAt": item.get("updated_at", ""),
    }


def fetch_milestones_live(repo: str) -> list[dict]:
    """Fetch all milestones for *repo* straight from GitHub (fallback path).

    Used before the first mirror sync. Returns normalised milestone dicts.
    """
    owner, name = _split_repo(repo)
    resp = httpx.get(
        f"{_API}/repos/{owner}/{name}/milestones",
        headers=_headers(),
        params={"state": "all", "per_page": MILESTONES_PER_PAGE},
        timeout=15.0,
    )
    resp.raise_for_status()
    return [_normalise_milestone(it) for it in resp.json()]


def sync_milestones_mirror(repo: str, db_module=None) -> dict:
    """Poll repo milestones with an ETag and upsert the local mirror (issue #877).

    200 -> upsert mirror and store the new ETag. 304 -> no-op (mirror and ETag
    preserved). Returns a dict with keys: status, synced, optionally error.
    """
    if db_module is None:
        import db as db_module  # type: ignore[assignment]

    owner, name = _split_repo(repo)
    etag_key = f"milestones:{repo}"
    etag = db_module.get_sync_etag(etag_key)

    try:
        resp = httpx.get(
            f"{_API}/repos/{owner}/{name}/milestones",
            headers=_headers(etag),
            params={"state": "all", "per_page": MILESTONES_PER_PAGE},
            timeout=15.0,
        )
    except Exception as exc:
        logger.warning("milestones mirror fetch failed for %s: %s", repo, exc)
        return {"status": 0, "synced": 0, "error": str(exc)}

    new_etag = resp.headers.get("ETag") or etag

    if resp.status_code == 304:
        logger.info("milestones mirror %s: 304 Not Modified (zero quota)", repo)
        return {"status": 304, "synced": 0}

    resp.raise_for_status()
    normalised = [_normalise_milestone(it) for it in resp.json()]
    db_module.upsert_milestones(repo, normalised)
    if new_etag:
        db_module.set_sync_etag(etag_key, new_etag)

    logger.info("milestones mirror %s: 200, upserted %d", repo, len(normalised))
    return {"status": 200, "synced": len(normalised)}


def create_milestone(
    repo: str,
    title: str,
    description: Optional[str] = None,
    due_on: Optional[str] = None,
    db_module=None,
) -> dict:
    """Create a milestone on GitHub and mirror it. `title` is required."""
    if not title:
        raise ValueError("title is required to create a milestone")
    if db_module is None:
        import db as db_module  # type: ignore[assignment]

    owner, name = _split_repo(repo)
    payload: dict = {"title": title}
    if description is not None:
        payload["description"] = description
    if due_on is not None:
        payload["due_on"] = due_on

    resp = httpx.post(
        f"{_API}/repos/{owner}/{name}/milestones",
        headers=_headers(),
        json=payload,
        timeout=15.0,
    )
    resp.raise_for_status()
    milestone = _normalise_milestone(resp.json())
    db_module.upsert_milestones(repo, [milestone])
    return milestone


def update_milestone(repo: str, number: int, db_module=None, **fields) -> dict:
    """Edit a milestone on GitHub (title/description/due_on/state) and mirror it.

    Only the provided fields are sent; None values are dropped.
    """
    if db_module is None:
        import db as db_module  # type: ignore[assignment]

    owner, name = _split_repo(repo)
    payload = {k: v for k, v in fields.items() if v is not None}

    resp = httpx.patch(
        f"{_API}/repos/{owner}/{name}/milestones/{number}",
        headers=_headers(),
        json=payload,
        timeout=15.0,
    )
    resp.raise_for_status()
    milestone = _normalise_milestone(resp.json())
    db_module.upsert_milestones(repo, [milestone])
    return milestone


def close_milestone(repo: str, number: int, db_module=None) -> dict:
    """Close a milestone on GitHub (PATCH state=closed) and mirror the change."""
    return update_milestone(repo, number, db_module=db_module, state="closed")
