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

CACHE_TTL = 30.0
SPRINT_RE = re.compile(r"^sprint-(\d+)$")
STATUS_LABELS = {"in-progress", "SIT", "UAT", "UAT-approved", "needs-rework", "blocked"}

_cache: dict[str, tuple[float, object]] = {}
_detected_repo: str | None = None


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
        return os.environ.get("COMMANDER_TEST_REPO", "zealchaiwut/commander-issue-test")
    return resolved


def _r(repo_name: str | None) -> str:
    return get_repo_for_operation(repo_name)


# ── cache ─────────────────────────────────────────────────────────────────────

def _cached(key: str, fn):
    now = time.monotonic()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
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
    key = f"issues:{r}:{sprint}"
    def fetch():
        issues = _json(
            "issue", "list", "--repo", r,
            "--label", f"sprint-{sprint}",
            "--state", "all",
            "--json", "number,title,labels,assignees,state,url,createdAt,updatedAt",
            "--limit", "200",
        )
        return [{"column": classify_issue(i), **i} for i in issues]
    return _cached(key, fetch)


def list_open_issues(repo_name: str | None = None, limit: int = 20) -> list[dict]:
    """List open issues for a repo, regardless of sprint label."""
    r = _r(repo_name)
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
    return _json(
        "issue", "view", str(issue_number), "--repo", r,
        "--json", "number,title,labels,assignees,state,url,body,createdAt,updatedAt",
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


def delete_label(label_name: str, repo_name: str | None = None) -> None:
    """Delete a GitHub label by name."""
    r = _r(repo_name)
    _run("label", "delete", label_name, "--repo", r, "--yes")
    invalidate(f"sprints:{r}")
    invalidate(f"open_issues:{r}")
    invalidate(f"open_issues_body:{r}")
    invalidate(f"issues:{r}:")


def assign_sprint(issue_id: int, sprint_num: int | None, repo_name: str | None = None) -> None:
    """Assign (or remove) a sprint-N label on an issue.

    If sprint_num is None, removes all sprint-* labels.
    If sprint_num is set, ensures the label exists, removes other sprint-* labels,
    then adds sprint-N.
    """
    r = _r(repo_name)

    # Find existing sprint labels on the issue
    issue = get_issue(issue_id, repo_name=repo_name)
    current_sprint_labels = [
        lbl["name"] for lbl in issue.get("labels", [])
        if SPRINT_RE.match(lbl["name"])
    ]

    if sprint_num is None:
        # Remove all sprint labels
        if current_sprint_labels:
            cmd = ["issue", "edit", str(issue_id), "--repo", r]
            for lbl in current_sprint_labels:
                cmd += ["--remove-label", lbl]
            _run(*cmd)
    else:
        ensure_sprint_label(sprint_num, repo_name=repo_name)
        target_label = f"sprint-{sprint_num}"
        add_labels = [target_label]
        remove_labels = [lbl for lbl in current_sprint_labels if lbl != target_label]
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


def get_pr(pr_number: int, repo_name: str | None = None) -> dict:
    return _json(
        "pr", "view", str(pr_number), "--repo", _r(repo_name),
        "--json", "number,title,state,url,body,headRefName",
    )


def repo_config() -> dict:
    return {
        "repo": repo(),
        "default_branch": os.environ.get("GITHUB_DEFAULT_BRANCH", "main"),
    }


def list_open_uat_issues(repo_name: str | None = None, sprint: int | None = None) -> list[dict]:
    """List open issues labelled UAT, optionally filtered to sprint-N."""
    r = _r(repo_name)
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
         "--add-label", "in-progress",
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


def close_issue(issue_id: int, repo_name: str | None = None):
    r = _r(repo_name)
    _run("issue", "close", str(issue_id), "--repo", r)
    invalidate(f"issues:{r}:")
    invalidate(f"latest_sprint:{r}")


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
