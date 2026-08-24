"""Sprint-branch lifecycle helpers (issue #2329).

The sprint-branch model:

  sprint/sprint-N          cut from develop when the sprint starts
    └── feature/<N>-<slug> one per ticket, cut from the sprint branch
          └── merged into sprint/sprint-N  by the tester, on pass
  sprint/sprint-N ──> develop  one merge PR, when the sprint is done

These helpers are imported by scripts/start_feature.py,
scripts/finish_feature.py, and dispatch_runner.py.  They are pure
git+gh operations; no dashboard DB or GitHub issues API writes.
"""
from __future__ import annotations

import subprocess
from typing import Optional


def sprint_branch_name(sprint_label: str) -> str:
    """Return the git branch name for a sprint label."""
    return f"sprint/{sprint_label}"


def _run(*cmd: str, cwd: Optional[str] = None) -> str:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=True, cwd=cwd
    ).stdout.strip()


def _try(*cmd: str, cwd: Optional[str] = None) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip()


def branch_exists_on_remote(repo: str, branch: str) -> bool:
    """True when ``branch`` exists on the remote (via gh API, no clone needed)."""
    try:
        from urllib.parse import quote
        ref = quote(branch, safe="")
        ok, _ = _try("gh", "api", f"repos/{repo}/branches/{ref}", "--jq", ".name")
        return ok
    except Exception:
        return False


def _base_label_re():
    """The canonical base sprint-label pattern (``sprint-N``, no child suffix).

    Imported lazily: sprint_label_re lives under apps/dashboard, which is not on
    sys.path for every caller of this module. backlog_triage.py resolves it the
    same way.
    """
    import sys as _sys
    from pathlib import Path as _Path

    dashboard = _Path(__file__).resolve().parent.parent.parent / "apps" / "dashboard"
    if str(dashboard) not in _sys.path:
        _sys.path.insert(0, str(dashboard))
    from sprint_label_re import SPRINT_BASE_LABEL_RE

    return SPRINT_BASE_LABEL_RE


def sprint_label_for_issue(issue_data: dict) -> Optional[str]:
    """Extract the sprint label from an issue's labels list.

    Returns the first ``sprint-N`` label found, or None.

    Uses the canonical pattern from ``apps/dashboard/sprint_label_re.py`` rather
    than compiling its own. #2059 consolidated every sprint-label regex into that
    module precisely so the accepted shape cannot drift between call sites, and
    an invariant test enforces it.
    """
    for lbl in issue_data.get("labels", []):
        name = lbl.get("name", "") if isinstance(lbl, dict) else str(lbl)
        if _base_label_re().match(name):
            return name
    return None


def detect_sprint_branch_for_issue(
    issue_num: int,
    repo_name: Optional[str] = None,
) -> Optional[str]:
    """Return the sprint branch for an issue, or None when none applies.

    Reads issue labels to find the sprint label, then checks whether the
    corresponding ``sprint/sprint-N`` branch exists on origin.  Returns None
    when:
    - the issue has no sprint label
    - the sprint branch does not exist on the remote
    - any lookup fails (best-effort, never raises)

    This is the single lookup that both start_feature.py and finish_feature.py
    use so the auto-detection logic lives in one place.
    """
    try:
        # Deferred import — these scripts set sys.path before importing us.
        import github_client  # noqa: PLC0415

        issue = github_client.get_issue(issue_num, repo_name=repo_name)
        sprint_label = sprint_label_for_issue(issue)
        if not sprint_label:
            return None

        branch = sprint_branch_name(sprint_label)
        repo = repo_name or github_client.repo()
        if branch_exists_on_remote(repo, branch):
            return branch
        return None
    except Exception:
        return None


def ensure_sprint_branch(
    sprint_label: str,
    repo: str,
    *,
    cwd: Optional[str] = None,
) -> str:
    """Create ``sprint/sprint-N`` from develop, idempotently.

    If the branch already exists on the remote, this is a no-op (returns the
    branch name without touching git state).  If it does not, fetches develop
    and pushes a new branch off its tip.

    Returns the branch name.
    Raises ``RuntimeError`` on unrecoverable failure.
    """
    branch = sprint_branch_name(sprint_label)

    if branch_exists_on_remote(repo, branch):
        return branch

    # Fetch latest develop then push sprint branch off it.
    ok, err = _try("git", "fetch", "origin", "develop", cwd=cwd)
    if not ok:
        raise RuntimeError(f"git fetch origin develop failed: {err}")

    ok, err = _try(
        "git", "push", "origin", f"refs/remotes/origin/develop:refs/heads/{branch}",
        cwd=cwd,
    )
    if not ok:
        # Another process may have raced us and already created the branch.
        if branch_exists_on_remote(repo, branch):
            return branch
        raise RuntimeError(
            f"could not create sprint branch {branch} on {repo}: {err}"
        )

    return branch
