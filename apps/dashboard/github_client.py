"""
GitHub integration via the `gh` CLI.
Repo auto-detected from `git remote get-url origin`; set GITHUB_REPO in .env to override.
All read functions accept an optional repo_name to support multi-repo setups.
Results cached for CACHE_TTL seconds.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from config import TEST_GITHUB_REPO

CACHE_TTL = 30.0
SPRINT_RE = re.compile(r"^sprint-(\d+)$")
SPRINT_LABEL_RE_ALL = re.compile(r"^sprint-(\d+)(?:\.(\d+))?$")
# READ-only: recognises both spellings for backward compat with old tickets
STATUS_LABELS = {"in-progress", "SIT", "UAT", "UAT-approved", "needs-rework", "need-rework", "blocked"}

_cache: dict[str, tuple[float, object]] = {}
_detected_repo: str | None = None


class SprintRunLockError(RuntimeError):
    """Raised when a non-status label mutation is attempted during a sprint run."""


def _refuse_if_sprint_running(action: str) -> None:
    """Refuse non-status label mutations while a sprint run holds the lock.

    During an active sprint run (COMMANDER_SPRINT_RUNNING=1) only status labels
    may change; sprint-N assignment and sprint-label strips must be frozen
    (issue #754). Mirrors the existing "refuse if running" guards elsewhere.
    When the lock is not held this is a no-op, so existing behavior is unchanged.
    """
    if os.environ.get("COMMANDER_SPRINT_RUNNING") == "1":
        raise SprintRunLockError(
            f"Sprint run active (COMMANDER_SPRINT_RUNNING=1): refusing to {action}. "
            f"Sprint and other non-status labels are frozen until the run ends."
        )


# ── repo resolution ───────────────────────────────────────────────────────────

def repo() -> str:
    env_repo = os.environ.get("GITHUB_REPO", "").strip()
    if env_repo:
        return env_repo
    global _detected_repo
    if _detected_repo:
        return _detected_repo
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
            cwd=Path(__file__).parent,
        )
        url = r.stdout.strip()
        m = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$", url)
        if m:
            _detected_repo = m.group(1)
            return _detected_repo
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    raise ValueError(
        "Cannot determine GitHub repo. "
        "Set GITHUB_REPO=owner/repo in .env, "
        "or ensure the 'origin' remote points to GitHub."
    )


def get_repo_for_operation(repo_name: str | None = None) -> str:
    """Resolve target repo, redirecting to the sandbox repo only in EXPLICIT test mode.

    HOTFIX: previously redirected ALL commander-self operations including dashboard
    READ operations, which broke the dashboard (sandbox was empty → 0 tickets shown).

    Redirect ONLY when COMMANDER_TEST_MODE=1 is explicitly set (e.g. by agents).
    Dashboard reads against commander itself now correctly hit the real repo.

    Override the sandbox target with COMMANDER_TEST_REPO env var.
    """
    resolved = repo_name or repo()
    test_mode = os.environ.get("COMMANDER_TEST_MODE", "").strip() == "1"
    if test_mode:
        return os.environ.get("COMMANDER_TEST_REPO", TEST_GITHUB_REPO)
    return resolved


def _r(repo_name: str | None) -> str:
    return get_repo_for_operation(repo_name)


# ── DB mirror read path (issue #756) ──────────────────────────────────────────
# The dashboard read model is the local DB. Issue reads are served from the
# `issues` mirror table (kept fresh by github_events_sync.sync_issues_mirror)
# so renders/polls consume zero GitHub rate-limit quota. When the mirror is
# empty or unavailable (e.g. before the first sync) callers fall back to gh.


def _mirror_issues(repo_name: str) -> list[dict] | None:
    """Return mirrored issues for *repo_name*, or None to signal gh fallback.

    Returns None when DB access is unavailable or the mirror has no rows yet, so
    the caller can fall back to a live `gh` fetch during bootstrap.
    """
    if not os.environ.get("DB_PATH"):
        return None
    try:
        import db  # lazy: db.py exits if DB_PATH is unset at import time
        rows = db.get_mirrored_issues(repo_name)
    except Exception:
        return None
    return rows or None


def _mirror_issue(repo_name: str, issue_number: int) -> dict | None:
    """Return a single mirrored issue, or None to signal gh fallback."""
    if not os.environ.get("DB_PATH"):
        return None
    try:
        import db
        return db.get_mirrored_issue(repo_name, issue_number)
    except Exception:
        return None


def _mirror_labels(repo_name: str) -> list[dict] | None:
    """Return {name, color} for every label on any mirrored issue, or None.

    Derived from the issues mirror, so it costs zero GitHub quota — unlike
    `gh label list`, which goes through GraphQL. Labels that exist in the repo
    but are attached to no issue won't appear; callers that need the complete
    label registry (e.g. the ticket-creation label picker) keep using gh.
    """
    mirror = _mirror_issues(repo_name)
    if mirror is None:
        return None
    seen: dict[str, str] = {}
    for iss in mirror:
        for lbl in iss.get("labels") or []:
            name = lbl.get("name") if isinstance(lbl, dict) else lbl
            if name and name not in seen:
                seen[name] = (lbl.get("color") or "") if isinstance(lbl, dict) else ""
    return [{"name": n, "color": c} for n, c in seen.items()]


# ── cache ─────────────────────────────────────────────────────────────────────

# Label registries change rarely (label create/delete, not issue activity), so
# their gh fallback gets a longer TTL than issue data. Write paths invalidate
# these keys explicitly (create_label), so fresh creates still appear at once.
_TTL_BY_PREFIX = {
    "labels:": 300.0,
    "sprint_labels:": 300.0,
    "sprints:": 300.0,
    # Summary issues change only when a sprint finishes; 120s staleness is fine
    # and cuts the board/nav polling burn on this (GraphQL) query ~4x further.
    "summary_issues:": 120.0,
}


def _ttl_for(key: str) -> float:
    for prefix, ttl in _TTL_BY_PREFIX.items():
        if key.startswith(prefix):
            return ttl
    return CACHE_TTL


def _cached(key: str, fn):
    now = time.monotonic()
    if key in _cache and now - _cache[key][0] < _ttl_for(key):
        return _cache[key][1]
    val = fn()
    _cache[key] = (now, val)
    return val


def invalidate(prefix: str = ""):
    for k in [k for k in _cache if k.startswith(prefix)]:
        del _cache[k]


def _json(*args) -> object:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _run(*args) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return r.stdout.strip()


# ── classification ────────────────────────────────────────────────────────────

def classify_issue(issue: dict) -> str:
    labels = {l["name"] for l in issue.get("labels", [])}
    if issue.get("state") == "closed" or "UAT-approved" in labels:
        return "done"
    if "UAT" in labels:
        return "uat"
    if "SIT" in labels:
        return "sit"
    if "in-progress" in labels:
        return "in-progress"
    return "backlog"


# ── read operations ───────────────────────────────────────────────────────────

def list_issues(sprint: int, repo_name: str | None = None) -> list[dict]:
    r = _r(repo_name)
    sprint_label = f"sprint-{sprint}"
    mirror = _mirror_issues(r)
    if mirror is not None:
        issues = [
            i for i in mirror
            if any(l.get("name") == sprint_label for l in i.get("labels", []))
        ]
        return [{"column": classify_issue(i), **i} for i in issues]
    key = f"issues:{r}:{sprint}"
    def fetch():
        issues = _json(
            "issue", "list", "--repo", r,
            "--label", sprint_label,
            "--state", "all",
            "--json", "number,title,labels,assignees,state,url,createdAt,updatedAt",
            "--limit", "200",
        )
        return [{"column": classify_issue(i), **i} for i in issues]
    return _cached(key, fetch)


def list_open_issues(repo_name: str | None = None, limit: int = 20) -> list[dict]:
    """List open issues for a repo, regardless of sprint label."""
    r = _r(repo_name)
    mirror = _mirror_issues(r)
    if mirror is not None:
        return [i for i in mirror if i.get("state") == "open"][:limit]
    key = f"open_issues:{r}"
    def fetch():
        return _json(
            "issue", "list", "--repo", r,
            "--state", "open",
            "--json", "number,title,labels,assignees,state,url,createdAt,updatedAt",
            "--limit", str(limit),
        )
    return _cached(key, fetch)


def list_all_open_issues(repo_name: str | None = None, limit: int = 200) -> list[dict]:
    """List all open issues with column classification (no sprint filter)."""
    r = _r(repo_name)
    mirror = _mirror_issues(r)
    if mirror is not None:
        issues = [i for i in mirror if i.get("state") == "open"][:limit]
        return [{"column": classify_issue(i), **i} for i in issues]
    key = f"all_open_issues:{r}"
    def fetch():
        issues = _json(
            "issue", "list", "--repo", r,
            "--state", "open",
            "--json", "number,title,labels,assignees,state,url,createdAt,updatedAt",
            "--limit", str(limit),
        )
        return [{"column": classify_issue(i), **i} for i in issues]
    return _cached(key, fetch)


def list_open_issues_with_body(repo_name: str | None = None, limit: int = 200) -> list[dict]:
    """List open issues including body field — needed for size estimation."""
    r = _r(repo_name)
    mirror = _mirror_issues(r)
    if mirror is not None and all("body" in i for i in mirror):
        return [i for i in mirror if i.get("state") == "open"][:limit]
    key = f"open_issues_body:{r}"
    def fetch():
        return _json(
            "issue", "list", "--repo", r,
            "--state", "open",
            "--json", "number,title,labels,assignees,state,url,body,createdAt,updatedAt",
            "--limit", str(limit),
        )
    return _cached(key, fetch)


def get_issue(issue_number: int, repo_name: str | None = None) -> dict:
    """Fetch a single issue by number including body."""
    r = _r(repo_name)
    mirror = _mirror_issue(r, issue_number)
    if mirror is not None and "body" in mirror:
        return mirror
    return _json(
        "issue", "view", str(issue_number), "--repo", r,
        "--json", "number,title,labels,assignees,state,stateReason,url,body,createdAt,updatedAt",
    )


def ensure_sprint_label(sprint_num: int, repo_name: str | None = None) -> None:
    """Create sprint-N label if it doesn't exist (colour #0075ca)."""
    r = _r(repo_name)
    label_name = f"sprint-{sprint_num}"
    try:
        _run("label", "create", label_name, "--repo", r,
             "--color", "0075ca", "--description", f"Sprint {sprint_num} issues")
    except subprocess.CalledProcessError:
        # Label already exists — that's fine
        pass
    invalidate(f"sprints:{r}")
    invalidate(f"sprint_labels:{r}")


def delete_label(label_name: str, repo_name: str | None = None) -> None:
    """Delete a GitHub label by name."""
    r = _r(repo_name)
    _run("label", "delete", label_name, "--repo", r, "--yes")
    invalidate(f"sprints:{r}")
    invalidate(f"open_issues:{r}")
    invalidate(f"open_issues_body:{r}")
    invalidate(f"issues:{r}:")


def edit_label(old_name: str, new_name: str, description: str | None = None,
               repo_name: str | None = None) -> None:
    """Rename a GitHub label in-place. GitHub updates all issues automatically."""
    r = _r(repo_name)
    cmd = ["label", "edit", old_name, "--repo", r, "--name", new_name]
    if description is not None:
        cmd += ["--description", description]
    _run(*cmd)
    invalidate(f"sprints:{r}")
    invalidate(f"open_issues:{r}")
    invalidate(f"open_issues_body:{r}")
    invalidate(f"issues:{r}:")
    invalidate(f"labels:{r}")


def assign_sprint(issue_id: int, sprint_num: int | None, repo_name: str | None = None) -> None:
    """Assign (or remove) a sprint-N label on an issue.

    If sprint_num is None, removes all sprint-* labels (move to backlog).
    If sprint_num is set, ensures the label exists, removes other sprint-* labels
    and removes the 'backlog' label if present, then adds sprint-N.
    """
    _refuse_if_sprint_running("change sprint assignment")
    r = _r(repo_name)

    # Find existing sprint labels on the issue
    issue = get_issue(issue_id, repo_name=repo_name)
    current_labels = [lbl["name"] for lbl in issue.get("labels", [])]
    current_sprint_labels = [lbl for lbl in current_labels if SPRINT_RE.match(lbl)]

    if sprint_num is None:
        # Remove all sprint labels (ticket moves to backlog)
        if current_sprint_labels:
            cmd = ["issue", "edit", str(issue_id), "--repo", r]
            for lbl in current_sprint_labels:
                cmd += ["--remove-label", lbl]
            _run(*cmd)
    else:
        ensure_sprint_label(sprint_num, repo_name=repo_name)
        target_label = f"sprint-{sprint_num}"
        add_labels = [target_label]
        # Remove other sprint labels and also remove 'backlog' label if present
        remove_labels = [lbl for lbl in current_sprint_labels if lbl != target_label]
        if "backlog" in current_labels:
            remove_labels.append("backlog")
        update_labels(issue_id, add=add_labels, remove=remove_labels, repo_name=repo_name)

    invalidate(f"issues:{r}:")
    invalidate(f"open_issues:{r}")
    invalidate(f"open_issues_body:{r}")
    invalidate(f"sprints:{r}")
    invalidate(f"latest_sprint:{r}")


def list_feature_branches(repo_name: str | None = None) -> dict[int, str]:
    """Return a mapping of issue_number -> branch_name for feature branches."""
    r = _r(repo_name)
    key = f"feature_branches:{r}"
    def fetch():
        result = subprocess.run(
            ["git", "branch", "-r"],
            capture_output=True, text=True,
        )
        mapping: dict[int, str] = {}
        import re as _re
        for line in result.stdout.splitlines():
            line = line.strip().replace("origin/", "")
            m = _re.match(r"feature/(\d+)-", line)
            if m:
                mapping[int(m.group(1))] = line
        return mapping
    return _cached(key, fetch)


def list_recent_closed(repo_name: str | None = None, limit: int = 5) -> list[dict]:
    r = _r(repo_name)
    mirror = _mirror_issues(r)
    if mirror is not None:
        closed = [i for i in mirror if i.get("state") == "closed"]
        if closed:
            closed.sort(key=lambda i: i.get("updatedAt") or "", reverse=True)
            return closed[:limit]
    key = f"recent_closed:{r}"
    def fetch():
        return _json(
            "issue", "list", "--repo", r,
            "--state", "closed",
            "--json", "number,title,labels,assignees,state,url,updatedAt",
            "--limit", str(limit),
        )
    return _cached(key, fetch)


def list_sprints(repo_name: str | None = None) -> list[int]:
    r = _r(repo_name)
    labels = _mirror_labels(r)
    if labels is not None:
        nums = sorted({int(m.group(1)) for lbl in labels
                       if (m := SPRINT_RE.match(lbl["name"]))})
        if nums:
            return nums
    key = f"sprints:{r}"
    def fetch():
        labels = _json("label", "list", "--repo", r, "--json", "name", "--limit", "200")
        nums = []
        for lbl in labels:
            m = SPRINT_RE.match(lbl["name"])
            if m:
                nums.append(int(m.group(1)))
        return sorted(nums)
    return _cached(key, fetch)


def _sprint_label_sort_key_gc(label: str) -> tuple[int, int]:
    """Return (base, suffix) sort key for sprint labels (plain or dotted)."""
    m = SPRINT_LABEL_RE_ALL.match(label)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else 0)


def list_sprint_labels(repo_name: str | None = None) -> list[str]:
    """Return all sprint labels (sprint-N and sprint-N.X) sorted naturally."""
    r = _r(repo_name)
    labels = _mirror_labels(r)
    if labels is not None:
        result = [lbl["name"] for lbl in labels if SPRINT_LABEL_RE_ALL.match(lbl["name"])]
        if result:
            return sorted(result, key=_sprint_label_sort_key_gc)
    key = f"sprint_labels:{r}"
    def fetch():
        labels = _json("label", "list", "--repo", r, "--json", "name", "--limit", "200")
        result = []
        for lbl in labels:
            if SPRINT_LABEL_RE_ALL.match(lbl["name"]):
                result.append(lbl["name"])
        return sorted(result, key=_sprint_label_sort_key_gc)
    return _cached(key, fetch)


def get_label_color(label_name: str, repo_name: str | None = None) -> str | None:
    """Return the hex color (without #) of a GitHub label, or None if not found."""
    r = _r(repo_name)
    mirrored = _mirror_labels(r)
    if mirrored is not None:
        for lbl in mirrored:
            if lbl["name"] == label_name and lbl.get("color"):
                return lbl["color"]
    try:
        labels = _json("label", "list", "--repo", r, "--json", "name,color", "--limit", "200")
        for lbl in labels:
            if lbl["name"] == label_name:
                return lbl["color"]
    except Exception:
        pass
    return None


def create_label(name: str, color: str, description: str = "", repo_name: str | None = None) -> None:
    """Create a GitHub label with the given color. Ignores if it already exists."""
    r = _r(repo_name)
    try:
        _run("label", "create", name, "--repo", r,
             "--color", color, "--description", description)
    except subprocess.CalledProcessError:
        pass
    invalidate(f"sprints:{r}")
    invalidate(f"sprint_labels:{r}")
    invalidate(f"labels:{r}")


def assign_sprint_by_label(issue_id: int, sprint_label: str | None,
                           repo_name: str | None = None) -> None:
    """Assign (or remove) a sprint label on an issue by label string.

    Handles both plain sprint-N and dotted sprint-N.X labels.
    If sprint_label is None, removes all sprint-* labels.
    """
    _refuse_if_sprint_running("change sprint assignment")
    r = _r(repo_name)
    issue = get_issue(issue_id, repo_name=repo_name)
    current_labels = [lbl["name"] for lbl in issue.get("labels", [])]
    current_sprint_labels = [lbl for lbl in current_labels if SPRINT_LABEL_RE_ALL.match(lbl)]

    if sprint_label is None:
        if current_sprint_labels:
            cmd = ["issue", "edit", str(issue_id), "--repo", r]
            for lbl in current_sprint_labels:
                cmd += ["--remove-label", lbl]
            _run(*cmd)
        invalidate(f"issues:{r}:")
        return

    to_add = [sprint_label] if sprint_label not in current_labels else []
    to_remove = [lbl for lbl in current_sprint_labels if lbl != sprint_label]
    to_remove += [lbl for lbl in ["backlog"] if lbl in current_labels]

    if to_add or to_remove:
        cmd = ["issue", "edit", str(issue_id), "--repo", r]
        for lbl in to_add:
            cmd += ["--add-label", lbl]
        for lbl in to_remove:
            cmd += ["--remove-label", lbl]
        _run(*cmd)
    invalidate(f"issues:{r}:")
    invalidate(f"open_issues_body:{r}")
    invalidate(f"open_issues:{r}")
    invalidate(f"sprints:{r}")
    invalidate(f"sprint_labels:{r}")
    invalidate(f"latest_sprint:{r}")


def latest_active_sprint(repo_name: str | None = None) -> Optional[int]:
    r = _r(repo_name)
    key = f"latest_sprint:{r}"
    def fetch():
        open_issues = _json(
            "issue", "list", "--repo", r,
            "--state", "open", "--json", "labels", "--limit", "500",
        )
        active: set[int] = set()
        for issue in open_issues:
            for lbl in issue.get("labels", []):
                m = SPRINT_RE.match(lbl["name"])
                if m:
                    active.add(int(m.group(1)))
        if active:
            return max(active)
        sprints = list_sprints(r)
        return sprints[-1] if sprints else None
    return _cached(key, fetch)


def _pr_from_rest(pr: dict) -> dict:
    """Map a REST pull-request object to the gh-CLI shape callers expect."""
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "state": (pr.get("state") or "").upper(),
        "url": pr.get("html_url", ""),
        "body": pr.get("body") or "",
        "headRefName": (pr.get("head") or {}).get("ref", ""),
        "baseRefName": (pr.get("base") or {}).get("ref", ""),
    }


def get_pr(pr_number: int, repo_name: str | None = None) -> dict:
    # REST (gh api) instead of `gh pr view` — pr view goes through GraphQL,
    # whose 5000/hr budget is the scarce one; REST has its own.
    pr = _json("api", f"repos/{_r(repo_name)}/pulls/{pr_number}")
    return _pr_from_rest(pr)


def find_open_pr_for_head(head_branch: str, repo_name: str | None = None) -> dict | None:
    """Return the first open PR whose head branch matches head_branch, or None."""
    r = _r(repo_name)
    owner = r.split("/")[0]
    try:
        # REST instead of `gh pr list` (GraphQL). head filter needs owner:branch.
        prs = _json("api", f"repos/{r}/pulls?state=open&head={owner}:{head_branch}")
        return _pr_from_rest(prs[0]) if prs else None
    except subprocess.CalledProcessError:
        return None


def merge_pr(pr_number: int, repo_name: str | None = None) -> None:
    """Merge a PR by number using a merge commit (preserves history)."""
    r = _r(repo_name)
    _run("pr", "merge", str(pr_number), "--repo", r, "--merge")


def repo_config() -> dict:
    return {
        "repo": repo(),
        "default_branch": os.environ.get("GITHUB_DEFAULT_BRANCH", "main"),
    }


def _mirror_is_complete() -> bool:
    """True when the paginated full-history bootstrap has run (marker v2).

    Reads that need the COMPLETE issue history (e.g. old sprint summaries) may
    only trust the mirror after the paginated crawl; a partial (pre-v2) mirror
    would make finished sprints "unfinish".
    """
    if not os.environ.get("DB_PATH"):
        return False
    try:
        import db
        return db.is_bootstrap_complete()
    except Exception:
        return False


def list_summary_issues(repo_name: str | None = None) -> list[dict]:
    """All sprint-summary-labeled issues, any state, as {number,title,url}.

    Mirror-backed once the full-history bootstrap has completed (zero quota);
    until then falls back to a cached gh call (see _TTL_BY_PREFIX) — this was
    previously an uncached GraphQL query on the board/nav hot path.
    """
    r = _r(repo_name)
    if _mirror_is_complete():
        mirror = _mirror_issues(r)
        if mirror is not None:
            return [
                {"number": i["number"], "title": i.get("title", ""), "url": i.get("url", "")}
                for i in mirror
                if any(isinstance(l, dict) and l.get("name") == "sprint-summary"
                       for l in i.get("labels") or [])
            ]
    key = f"summary_issues:{r}"
    def fetch():
        return _json(
            "issue", "list", "--repo", r,
            "--label", "sprint-summary", "--state", "all",
            "--json", "number,title,url", "--limit", "200",
        )
    return _cached(key, fetch)


def list_open_uat_issues(repo_name: str | None = None, sprint: int | None = None) -> list[dict]:
    """List open issues labelled UAT, optionally filtered to sprint-N."""
    r = _r(repo_name)
    mirror = _mirror_issues(r)
    if mirror is not None:
        out = []
        for iss in mirror:
            if iss.get("state") != "open":
                continue
            names = {l.get("name") for l in iss.get("labels") or [] if isinstance(l, dict)}
            if "UAT" not in names:
                continue
            if sprint is not None and f"sprint-{sprint}" not in names:
                continue
            out.append({"number": iss["number"], "title": iss.get("title", ""),
                        "url": iss.get("url", "")})
        return out
    args = [
        "issue", "list", "--repo", r,
        "--label", "UAT",
        "--state", "open",
        "--json", "number,title,url",
        "--limit", "200",
    ]
    if sprint is not None:
        args += ["--label", f"sprint-{sprint}"]
    return _json(*args)


# ── write operations ──────────────────────────────────────────────────────────

def approve_issue(issue_id: int, repo_name: str | None = None):
    r = _r(repo_name)
    _run("issue", "edit", str(issue_id), "--repo", r,
         "--add-label", "UAT-approved", "--remove-label", "UAT")
    _run("issue", "close", str(issue_id), "--repo", r)
    invalidate(f"issues:{r}:")
    invalidate(f"latest_sprint:{r}")
    invalidate(f"recent_closed:{r}")
    invalidate(f"open_issues:{r}")


def reject_issue(issue_id: int, reason: str, repo_name: str | None = None):
    r = _r(repo_name)
    _run("issue", "edit", str(issue_id), "--repo", r,
         "--add-label", "needs-rework",
         "--remove-label", "UAT")
    _run("issue", "comment", str(issue_id), "--repo", r,
         "--body", f"❌ **Rejected:** {reason}")
    invalidate(f"issues:{r}:")
    invalidate(f"recent_closed:{r}")
    invalidate(f"open_issues:{r}")


def search_issues_by_title(title: str, repo_name: str | None = None) -> list[dict]:
    """Search for issues matching *title* exactly (open or closed).

    Uses ``gh issue list --search <title> --state all`` as specified by AC-6.
    Returns a (possibly empty) list of matching issue dicts.
    """
    r = _r(repo_name)
    try:
        results = _json(
            "issue", "list", "--repo", r,
            "--search", title,
            "--state", "all",
            "--json", "number,title,url,state",
            "--limit", "50",
        )
        # Exact-title filter: gh --search is fuzzy, so we narrow to exact matches
        return [i for i in results if i.get("title") == title]
    except subprocess.CalledProcessError:
        return []


def create_issue(title: str, body: str, labels: list[str],
                 repo_name: str | None = None) -> tuple[int, str]:
    r = _r(repo_name)
    url = _run(
        "issue", "create", "--repo", r,
        "--title", title, "--body", body,
        "--label", ",".join(labels),
    )
    number = int(url.rstrip("/").split("/")[-1])
    invalidate(f"issues:{r}:")
    invalidate(f"latest_sprint:{r}")
    return number, url


def list_labels(repo_name: str | None = None) -> list[dict]:
    """Return all labels in the repo as [{"name": ..., "color": ...}, ...]."""
    r = _r(repo_name)
    key = f"labels:{r}"

    def fetch():
        raw = _json("label", "list", "--repo", r, "--json", "name,color", "--limit", "100")
        return [{"name": lbl["name"], "color": lbl["color"]} for lbl in raw]

    return _cached(key, fetch)


def add_comment(issue_id: int, body: str, repo_name: str | None = None):
    r = _r(repo_name)
    _run("issue", "comment", str(issue_id), "--repo", r, "--body", body)
    invalidate(f"test_report:{r}:{issue_id}")


def update_labels(issue_id: int, add: list[str], remove: list[str],
                  repo_name: str | None = None):
    r = _r(repo_name)
    cmd = ["issue", "edit", str(issue_id), "--repo", r]
    for lbl in add:
        cmd += ["--add-label", lbl]
    for lbl in remove:
        cmd += ["--remove-label", lbl]
    _run(*cmd)
    invalidate(f"issues:{r}:")


def close_issue(issue_id: int, repo_name: str | None = None, reason: str | None = None):
    r = _r(repo_name)
    cmd = ["issue", "close", str(issue_id), "--repo", r]
    if reason:
        cmd += ["--reason", reason]
    _run(*cmd)
    invalidate(f"issues:{r}:")
    invalidate(f"latest_sprint:{r}")


def reopen_issue(issue_id: int, repo_name: str | None = None):
    r = _r(repo_name)
    _run("issue", "reopen", str(issue_id), "--repo", r)
    invalidate(f"issues:{r}:")
    invalidate(f"latest_sprint:{r}")


def update_issue_body(issue_id: int, body: str, repo_name: str | None = None):
    import tempfile
    r = _r(repo_name)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                     encoding="utf-8") as tf:
        tmp = Path(tf.name)
        tf.write(body)
    try:
        _run("issue", "edit", str(issue_id), "--repo", r, "--body-file", str(tmp))
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    invalidate(f"issues:{r}:")


# ── test report ───────────────────────────────────────────────────────────────

def get_test_report(issue_id: int, repo_name: str | None = None) -> dict:
    """Return parsed test report from the most recent tester comment, plus UAT steps from the ticket body."""
    r = _r(repo_name)
    key = f"test_report:{r}:{issue_id}"

    def fetch():
        data     = _json("issue", "view", str(issue_id), "--repo", r,
                         "--json", "number,body,comments")
        body     = data.get("body") or ""
        comments = data.get("comments") or []

        report_comment = None
        for c in reversed(comments):
            if "## Acceptance Criteria Results" in (c.get("body") or ""):
                report_comment = c
                break

        uat_steps = _parse_uat_steps(body)

        if report_comment is None:
            return {"found": False, "uat_steps": uat_steps}

        parsed = _parse_report_body(report_comment["body"])
        parsed["found"]       = True
        parsed["uat_steps"]   = uat_steps
        parsed["comment_url"] = report_comment.get("url", "")
        parsed["created_at"]  = report_comment.get("createdAt", "")
        return parsed

    return _cached(key, fetch)


def _parse_uat_steps(issue_body: str) -> list[dict]:
    steps: list[dict] = []
    in_section = False
    step_re    = re.compile(r"^\s*(\d+)\.\s+(.+)")
    for line in issue_body.splitlines():
        if re.match(r"^#+\s+UAT Test Steps", line):
            in_section = True
            continue
        if in_section and re.match(r"^#+\s+", line):
            break
        if in_section:
            m = step_re.match(line)
            if m:
                steps.append({"num": int(m.group(1)), "text": m.group(2).strip()})
    return steps


def _parse_report_body(body: str) -> dict:
    """Split a test report comment on ## headers and parse each section."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    for line in body.splitlines():
        h2 = re.match(r"^## (.+)", line)
        if h2:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = h2.group(1).strip()
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()

    criteria = _parse_criteria_section(sections.get("Acceptance Criteria Results", ""))
    uat_results = _parse_uat_results_section(sections.get("UAT Step Results", ""))
    overall_status, counts = _parse_summary_section(sections.get("Summary", ""))

    return {
        "criteria":       criteria,
        "uat_results":    uat_results,
        "overall_status": overall_status,
        "counts":         counts,
    }


def _parse_criteria_section(text: str) -> list[dict]:
    items = []
    for line in text.splitlines():
        m = re.match(r"^\s*-\s+\[([ x])\]\s+(.+?)\s+[—\-–]\s+(.+)", line)
        if not m:
            continue
        result = m.group(3).strip()
        status = "pass" if "✅" in result else "fail" if "❌" in result else "manual"
        items.append({"text": m.group(2).strip(), "status": status, "result": result})
    return items


def _parse_uat_results_section(text: str) -> list[dict]:
    items = []
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\.\s+(.+?)\s+[—\-–]\s+(.+)", line)
        if not m:
            continue
        result = m.group(3).strip()
        status = "pass" if "✅" in result else "fail" if "❌" in result else "manual"
        items.append({"num": int(m.group(1)), "text": m.group(2).strip(),
                      "status": status, "result": result})
    return items


def _parse_summary_section(text: str) -> tuple[str, dict]:
    status_m = re.search(r"Status:\s*(\S+)", text)
    counts_m = re.search(r"Passed:\s*(\d+)\s*/\s*Failed:\s*(\d+)\s*/\s*Manual:\s*(\d+)", text)
    status = status_m.group(1) if status_m else "UNKNOWN"
    counts: dict = {}
    if counts_m:
        counts = {
            "passed": int(counts_m.group(1)),
            "failed": int(counts_m.group(2)),
            "manual": int(counts_m.group(3)),
        }
    return status, counts
