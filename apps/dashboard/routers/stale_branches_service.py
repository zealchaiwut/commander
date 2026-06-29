"""Stale-branch scan + cleanup for the History view (issue #808).

Feature branches from past sprints linger on the remote after their PR merges,
cluttering branch listings. This module backs two endpoints (mounted on the
already-wired ``sprint_history`` router, so no new route lands in ``server.py`` —
COMMANDER_GATE_MONOLITH):

* ``GET  /scan-stale-branches``    — list every ``feature/<N>-*`` remote branch,
  map ``N`` to a sprint via the local history enrichment feed, and flag whether
  each is merged into the target branch.
* ``POST /cleanup-stale-branches`` — dry-run first; on confirm, delete ONLY
  merged branches and report any ``skipped_unmerged`` ones (never auto-deleted).

Merged status is computed against the *target branch* (``develop`` by default)
with the GitHub compare API: a branch with zero commits ahead of the target is
fully contained in it, i.e. merged. All GitHub access goes through the ``gh``
CLI, mirroring the existing branch endpoints in ``server.py`` (issue #634).
"""
from __future__ import annotations

import re
import subprocess

from . import sprint_history_service

# Only ``feature/<N>-...`` branches are in scope (AC; Out of Scope excludes other
# naming conventions). The capture group is the issue number used for mapping.
_FEATURE_BRANCH_RE = re.compile(r"^feature/(\d+)-")

# Never deletable, regardless of anything the caller asks for.
_PROTECTED_BRANCHES: frozenset[str] = frozenset({"develop", "master", "main", "attachments"})

# The integration branch a feature is considered "merged" into for History.
_DEFAULT_TARGET = "develop"


# ── gh helpers (thin, individually patchable in tests) ────────────────────────

# A timeout keeps a hung ``gh`` call from blocking a History scan indefinitely.
_GH_TIMEOUT_SECONDS = 30


def _run_gh(args: list[str]) -> str:
    """Run a ``gh`` command, returning stdout (empty string on any failure).

    This is the *single* place a ``subprocess.TimeoutExpired`` is handled: a hung
    ``gh`` call is swallowed here and reported as an empty string, exactly like
    any other failure. Callers (``_is_merged``, ``_list_remote_feature_branches``)
    therefore never see a timeout and must not re-catch one (issue #1587).
    """
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GH_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return ""
        return result.stdout or ""
    except subprocess.TimeoutExpired:
        return ""
    except Exception:
        return ""


def _list_remote_feature_branches(repo: str) -> list[str]:
    """Sorted list of ``feature/<N>-*`` branch names that exist on the remote."""
    out = _run_gh(["api", f"repos/{repo}/branches", "--paginate", "--jq", ".[].name"])
    branches = []
    for name in out.splitlines():
        name = name.strip()
        if name and _FEATURE_BRANCH_RE.match(name) and name not in _PROTECTED_BRANCHES:
            branches.append(name)
    return sorted(set(branches))


def _is_merged(repo: str, branch: str, target: str) -> bool:
    """True when ``branch`` is fully contained in ``target`` (0 commits ahead).

    Timeouts and any other ``gh`` failure are already absorbed by ``_run_gh``,
    which returns ``""`` in that case — the ``if not out`` guard below treats
    that as "not merged". No local ``except subprocess.TimeoutExpired`` is needed
    here; one would be unreachable dead code (issue #1587).
    """
    out = _run_gh(["api", f"repos/{repo}/compare/{target}...{branch}", "--jq", ".ahead_by"])
    out = out.strip()
    if not out:
        return False
    try:
        return int(out) == 0
    except (TypeError, ValueError):
        return False


def _delete_branch(repo: str, branch: str) -> bool:
    """Delete a remote branch ref. Returns True on success."""
    if branch in _PROTECTED_BRANCHES or not _FEATURE_BRANCH_RE.match(branch):
        return False
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "DELETE", f"repos/{repo}/git/refs/heads/{branch}"],
            capture_output=True, text=True, check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── pure helpers ──────────────────────────────────────────────────────────────

def _branch_issue(branch: str) -> int | None:
    """Issue number embedded in a ``feature/<N>-...`` branch name, or None."""
    m = _FEATURE_BRANCH_RE.match(branch or "")
    return int(m.group(1)) if m else None


def _ticket_sprint_map() -> dict[int, str]:
    """Map ticket_id -> sprint label from the local history enrichment feed.

    Uses :func:`sprint_history_service.get_sprint_history` (no GitHub call),
    keeping the first sprint that claims a ticket so a later rerun child does
    not override the original mapping.
    """
    data = sprint_history_service.get_sprint_history(limit=0)
    mapping: dict[int, str] = {}
    for sprint in data.get("sprints", []):
        label = sprint.get("label")
        if not label:
            continue
        for iss in sprint.get("issues", []):
            tid = iss.get("ticket_id")
            if tid is None:
                continue
            try:
                tid = int(tid)
            except (TypeError, ValueError):
                continue
            mapping.setdefault(tid, label)
    return mapping


# ── public API ────────────────────────────────────────────────────────────────

def scan_stale_branches(repo: str, target: str | None = None) -> dict:
    """List ``feature/<N>-*`` remote branches, map each to a sprint, flag merged.

    Returns ``branches`` (every leftover feature branch with its issue, sprint
    and merged flag) plus ``by_sprint`` (per-sprint grouping that drives the
    amber History-row chips: total count + merged/unmerged splits).
    """
    target = target or _DEFAULT_TARGET
    tsm = _ticket_sprint_map()

    out_branches: list[dict] = []
    by_sprint: dict[str, dict] = {}
    for branch in _list_remote_feature_branches(repo):
        issue = _branch_issue(branch)
        sprint = tsm.get(issue) if issue is not None else None
        merged = _is_merged(repo, branch, target)
        out_branches.append(
            {"branch": branch, "issue": issue, "sprint": sprint, "merged": merged}
        )
        if sprint:
            grp = by_sprint.setdefault(
                sprint,
                {"sprint": sprint, "count": 0, "branches": [], "merged": [], "unmerged": []},
            )
            grp["branches"].append(branch)
            (grp["merged"] if merged else grp["unmerged"]).append(branch)
            grp["count"] = len(grp["branches"])

    return {
        "repo": repo,
        "target_branch": target,
        "branches": out_branches,
        "by_sprint": by_sprint,
    }


def cleanup_stale_branches(
    repo: str,
    branches: list[str],
    target: str | None = None,
    confirm: bool = False,
) -> dict:
    """Dry-run, then (on confirm) delete only merged branches.

    Re-checks merged status server-side for every requested branch so an
    unmerged branch is never deleted even if the caller asks for it (AC8).
    Without ``confirm`` returns the plan (``to_delete`` / ``skipped_unmerged``);
    with ``confirm`` performs the deletes and returns ``deleted`` / ``failed``
    alongside the same ``skipped_unmerged`` list.
    """
    target = target or _DEFAULT_TARGET

    to_delete: list[str] = []
    skipped_unmerged: list[str] = []
    for branch in branches or []:
        # Out-of-scope or protected branches are never touched and never counted
        # as "unmerged" — they are simply not eligible for cleanup.
        if not _FEATURE_BRANCH_RE.match(branch or "") or branch in _PROTECTED_BRANCHES:
            continue
        if _is_merged(repo, branch, target):
            to_delete.append(branch)
        else:
            skipped_unmerged.append(branch)

    if not confirm:
        return {
            "dry_run": True,
            "target_branch": target,
            "to_delete": to_delete,
            "skipped_unmerged": skipped_unmerged,
        }

    deleted: list[str] = []
    failed: list[str] = []
    for branch in to_delete:
        if _delete_branch(repo, branch):
            deleted.append(branch)
        else:
            failed.append(branch)

    return {
        "dry_run": False,
        "target_branch": target,
        "deleted": deleted,
        "skipped_unmerged": skipped_unmerged,
        "failed": failed,
    }
