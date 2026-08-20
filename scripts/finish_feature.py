#!/usr/bin/env python3
"""Merge a tested feature branch into develop.

Call this after tests pass. It:
  1. Fetches latest from origin
  2. Checks out the feature/<N>-* branch
  3. Merges it into the target branch with --no-ff
  4. Pushes the target branch
  5. Deletes the feature branch locally and on origin

On success, writes to stdout:
    FINISH_FEATURE_OUTCOME merged sha=<sha> branch=<branch>

On merge conflict: aborts cleanly, posts a comment, exits non-zero.
Label transitions are managed exclusively by sprint_manager via state_machine.transition().

Usage:
    python3 ~/commander/scripts/finish_feature.py --issue 42

Run from the git root of the repository (NOT from dashboard/).
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_DIR))
from dotenv import load_dotenv  # noqa: E402  (deferred: sys.path set above)
load_dotenv(_DASHBOARD_DIR / ".env")
import github_client  # noqa: E402  (deferred: sys.path set above)
from services.run_id import mint_run_id  # noqa: E402
from services.logging import log as structured_log  # noqa: E402


def _run(*cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def _is_true_merge_commit(sha: str, run=None) -> bool:
    """Return True when `sha` is a true merge commit (issue #1441).

    A true merge commit records two (or more) `parent` lines in its object;
    a fast-forward or squash commit records exactly one. Detected via
    `git cat-file -p <sha>`.
    """
    run = run or _try
    ok, out = run("git", "cat-file", "-p", sha)
    if not ok:
        return False
    parent_lines = [ln for ln in out.splitlines() if ln.startswith("parent ")]
    return len(parent_lines) >= 2


def _changed_files_for_merge(merge_sha: str, target: str, run=None) -> list[str]:
    """Files a merged ticket changed, robust to ff/squash merges (issue #1441).

    True merge commit (two parents): diff the merge against its first parent —
    this captures the whole feature branch and is the original behavior (AC2).

    ff/squash commit (single parent): `--first-parent` would diff only the tip
    commit against its lone parent, undercounting the branch. Fall back to
    diffing `merge_sha` against its merge-base with `target` so every commit on
    the feature branch is covered (AC3/AC4).
    """
    run = run or _try
    if _is_true_merge_commit(merge_sha, run=run):
        ok, out = run(
            "git", "diff-tree", "-r", "--no-commit-id", "--name-only",
            "--first-parent", merge_sha,
        )
    else:
        sys.stdout.write(
            f"merge_sha {merge_sha[:8]} is single-parent — using merge-base diff\n"
        )
        ok_mb, merge_base = run("git", "merge-base", target, merge_sha)
        if ok_mb and merge_base:
            ok, out = run("git", "diff", "--name-only", f"{merge_base}..{merge_sha}")
        else:
            # No common ancestor with target — diff against the single parent.
            ok, out = run("git", "diff", "--name-only", f"{merge_sha}^..{merge_sha}")
    return [f for f in out.splitlines() if f.strip()] if ok else []


def _record_merge_accuracy(
    issue_num: int, merge_sha: str, repo_root: Path, target: str = "develop"
) -> None:
    """Record estimator file-prediction accuracy for a merged ticket (issue #1417).

    Compares the estimate's files_likely_affected against the files actually
    changed by the merge commit. Best-effort: failures are logged and ignored.
    """
    try:
        from services.sprint_manager.estimate_accuracy import (
            find_commander_dir,
            record_accuracy,
        )

        commander_dir = find_commander_dir(start=repo_root)
        if not commander_dir:
            return

        estimates_json = commander_dir / "estimates" / f"issue-{issue_num}.json"
        if not estimates_json.exists():
            return

        import json
        est = json.loads(estimates_json.read_text(encoding="utf-8"))
        predicted = est.get("files_likely_affected") or []

        # Get files actually changed by the merge. Robust to ff/squash merges:
        # a single-parent merge_sha falls back to a merge-base diff (issue #1441).
        actual = _changed_files_for_merge(merge_sha, target)

        accuracy_dir = commander_dir / "estimates" / "accuracy"
        record_accuracy(issue_num, predicted, actual, accuracy_dir)
        sys.stdout.write(f"Accuracy recorded for issue #{issue_num} → {accuracy_dir / f'issue-{issue_num}.json'}\n")
    except Exception as exc:
        sys.stdout.write(f"Warning: could not record accuracy for #{issue_num}: {exc}\n")


def _try(*cmd) -> tuple[bool, str]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stdout.strip()


def _changed_files_vs_target(branch: str, target: str) -> list[str]:
    """Files the feature branch changes relative to its merge-base with target."""
    ok, out = _try("git", "diff", "--name-only", f"{target}...origin/{branch}")
    if not ok:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _run_suite_on_branch(branch: str, timeout_seconds: int) -> tuple[str, bool]:
    """Run pytest against origin/<branch> in a throwaway worktree.

    A worktree is used rather than a checkout because finish_feature deliberately
    never checks the feature branch out — it may be checked out in the coder
    worktree, where git would refuse.

    Returns (combined_output, timed_out).
    """
    import tempfile

    pytest_args = os.environ.get("COMMANDER_BASELINE_PYTEST_ARGS", "tests/ -q").split()
    tmpdir = tempfile.mkdtemp(prefix="commander-baseline-")
    wt = str(Path(tmpdir) / "wt")
    try:
        ok, out = _try("git", "worktree", "add", "--detach", wt, f"origin/{branch}")
        if not ok:
            return (f"could not create worktree for origin/{branch}: {out}", False)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", *pytest_args],
                cwd=wt, capture_output=True, text=True, timeout=timeout_seconds,
            )
            return ((proc.stdout or "") + (proc.stderr or ""), False)
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or b"")
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            return (partial, True)
    finally:
        _try("git", "worktree", "remove", "--force", wt)
        shutil.rmtree(tmpdir, ignore_errors=True)


def _baseline_check_or_exit(issue_num: int, branch: str, target: str, repo, override: bool) -> None:
    """Refuse the merge when the branch adds failures beyond the recorded baseline.

    The orchestrator's gate pipeline was deleted in the 2026-08 shrink; this is
    the one objective check standing between "tester says pass" and develop.
    """
    from services.sprint_manager.merge_baseline import (
        check_against_baseline,
        load_baseline,
        parse_failed_test_ids,
    )
    from services.sprint_manager.suite_health_gate import _parse_pytest_output

    # Kill switch. The check refuses when no baseline is recorded, which is the
    # right default for an unmeasured project but would block every merge on a
    # project that has not run record_test_baseline.py yet. Set
    # COMMANDER_BASELINE_CHECK=0 to disable during rollout.
    if os.environ.get("COMMANDER_BASELINE_CHECK", "1").strip() in ("0", "false", "no"):
        sys.stdout.write(
            "Baseline-delta check disabled (COMMANDER_BASELINE_CHECK=0) — merging unchecked.\n"
        )
        return

    project = repo or github_client.repo()
    commander_dir = _REPO_ROOT / ".commander"
    # 600s was below commander's own measured suite time (741.96s on develop,
    # 2026-08-20), so the check timed out and refused every merge — a refusal on
    # timeout is indistinguishable from a real regression to whoever reads it.
    # 1800s leaves headroom on a suite of this size without waiting forever on a
    # genuinely hung run; override per-project with COMMANDER_BASELINE_TIMEOUT.
    timeout_seconds = int(os.environ.get("COMMANDER_BASELINE_TIMEOUT", "1800"))

    changed = _changed_files_vs_target(branch, target)
    baseline = load_baseline(project, commander_dir)

    # Docs-only diffs short-circuit before paying for a suite run.
    from services.sprint_manager.merge_baseline import is_docs_only
    if changed and is_docs_only(changed):
        sys.stdout.write("Baseline-delta check skipped: diff touches documentation only.\n")
        return

    # The two sides of the comparison must measure the same thing. A baseline
    # recorded over `tests/ -q` cannot be compared against a scoped branch run
    # (issue #2331); warn loudly rather than compare incomparable numbers.
    branch_args = os.environ.get("COMMANDER_BASELINE_PYTEST_ARGS", "tests/ -q")
    if baseline is not None and baseline.pytest_args and baseline.pytest_args != branch_args:
        sys.stderr.write(
            f"WARNING: baseline was recorded with pytest args {baseline.pytest_args!r} "
            f"but this run uses {branch_args!r}. The counts are not comparable — "
            f"re-record the baseline with the same scope.\n"
        )

    sys.stdout.write(f"Running suite on origin/{branch} for baseline-delta check…\n")
    output, timed_out = _run_suite_on_branch(branch, timeout_seconds)

    if timed_out:
        msg = (
            f"⛔ **Baseline-delta check could not complete** — the suite on `{branch}` "
            f"exceeded {timeout_seconds}s.\n\n"
            "The merge is refused because the branch is unverified, not because it failed. "
            "Scope the run with `COMMANDER_BASELINE_PYTEST_ARGS`, raise "
            "`COMMANDER_BASELINE_TIMEOUT`, or re-run with `--override-baseline` "
            "if you have verified it another way."
        )
        sys.stderr.write(msg + "\n")
        _post_comment(issue_num, msg, repo)
        if not override:
            sys.exit(1)
        _record_override(issue_num, "suite run timed out", repo)
        return

    from services.sprint_manager.merge_baseline import collection_failed

    passed, failed, _skipped = _parse_pytest_output(output)
    # A run that aborted at collection reports zero failures for a suite that
    # never executed. Passing that straight into the delta check compares 0 to 0
    # and waves the merge through, which is what #2331 found. Report both the
    # collection state and the pass count so the check can refuse.
    check = check_against_baseline(
        failed_now=failed,
        failing_test_ids_now=parse_failed_test_ids(output),
        baseline=baseline,
        changed_files=changed or None,
        passed_now=passed,
        collection_error=collection_failed(output),
    )

    if check.allowed:
        sys.stdout.write(check.summary() + "\n")
        return

    body = f"⛔ **Merge refused by the baseline-delta check**\n\n```\n{check.summary()}\n```"
    sys.stderr.write(check.summary() + "\n")
    _post_comment(issue_num, body, repo)

    if not override:
        sys.exit(1)
    _record_override(issue_num, check.reason, repo)


def _post_comment(issue_num: int, body: str, repo) -> None:
    try:
        github_client.add_comment(issue_num, body, repo_name=repo)
    except Exception:
        pass


def _record_override(issue_num: int, reason: str, repo) -> None:
    """An override is a decision, so it is written down where the ticket lives."""
    msg = (
        "⚠️ **Baseline-delta check overridden by the operator.**\n\n"
        f"The check refused this merge: {reason}\n\n"
        "`--override-baseline` was passed, so the merge proceeded anyway."
    )
    sys.stdout.write("Baseline-delta check OVERRIDDEN — proceeding with merge.\n")
    _post_comment(issue_num, msg, repo)


def find_branch(issue_num: int) -> str | None:
    """Find feature/<N>-* locally first, then on remote."""
    ok, out = _try("git", "branch", "--list", f"feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().lstrip("*+ ")

    ok, out = _try("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().removeprefix("origin/")

    return None


def main():
    _run_id = mint_run_id("manual")
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="finish_feature")

    p = argparse.ArgumentParser(description="Merge feature branch into target branch after tests pass")
    p.add_argument("--issue", type=int, required=True, help="Issue number")
    p.add_argument("--repo",  default=None,            help="owner/repo override")
    p.add_argument(
        "--target-branch",
        default=os.environ.get("COMMANDER_MERGE_TARGET", "develop"),
        help="Branch to merge into (default: COMMANDER_MERGE_TARGET env var or 'develop')",
    )
    p.add_argument(
        "--override-baseline",
        action="store_true",
        help="Merge even if the baseline-delta check refuses. The override is recorded on the issue.",
    )
    args = p.parse_args()
    target = args.target_branch

    # When no explicit target was given (neither --target-branch nor
    # COMMANDER_MERGE_TARGET), auto-detect the sprint branch for this issue
    # so the tester merges into sprint/sprint-N rather than develop (#2329).
    # Falls back to develop when no sprint branch exists, preserving the
    # existing behaviour for projects that don't use sprint branches.
    _explicit_target = (
        args.target_branch != "develop"
        or os.environ.get("COMMANDER_MERGE_TARGET", "").strip()
    )
    if not _explicit_target:
        from services.sprint_manager.sprint_branch import (  # noqa: PLC0415
            detect_sprint_branch_for_issue,
        )
        detected = detect_sprint_branch_for_issue(args.issue, repo_name=args.repo)
        if detected:
            target = detected

    structured_log.set_context(issue_num=args.issue)
    structured_log.info("feature_finish_start", f"merging feature branch for issue #{args.issue} into {target}", issue_num=args.issue)

    # Fetch so remote-only branches are visible
    sys.stdout.write(str("Fetching from origin…") + "\n")
    _run("git", "fetch", "origin")

    branch = find_branch(args.issue)
    if not branch:
        sys.stderr.write(str(f"Error: no branch found matching feature/{args.issue}-*\n"
            "Are you in the git root? Has start_feature.py been run?") + "\n")
        sys.exit(1)

    # Do NOT check out the feature branch locally — it may be checked out in
    # another worktree (e.g. the coder worktree), which causes git to refuse.
    # origin/<branch> is already current after the fetch above.

    # Gate the merge on the baseline-delta check (issue #2316). Runs on the
    # feature branch, before any merge commit exists, and exits non-zero on
    # refusal so nothing reaches the target branch.
    _baseline_check_or_exit(args.issue, branch, target, args.repo, args.override_baseline)

    sys.stdout.write(str(f"Merging {branch} → {target}…") + "\n")

    # Ensure target branch is available locally
    ok, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{target}")
    if ok:
        _run("git", "checkout", target)
        _run("git", "pull", "origin", target)
    else:
        _run("git", "checkout", "--track", f"origin/{target}")

    # Clear staged generated binary files (e.g. codedb.snapshot staged by the
    # indexer during test runs) — git refuses to merge if staged files would be
    # overwritten by the merge, even when neither branch tracks the file.
    _try("git", "reset", "HEAD", "--", "codedb.snapshot")

    merge_msg = f"Merge {branch} into {target} (issue #{args.issue})"
    result = subprocess.run(
        ["git", "merge", "--no-ff", f"origin/{branch}", "-m", merge_msg],
        capture_output=True, text=True,
    )

    if result.returncode != 0:
        _try("git", "merge", "--abort")
        conflict_comment = (
            f"❌ **Merge conflict** — could not merge `{branch}` into `{target}` automatically.\n\n"
            f"Resolve manually:\n"
            f"```bash\n"
            f"git checkout {target}\n"
            f"git merge {branch}\n"
            f"# fix conflicts, then:\n"
            f"git add . && git commit\n"
            f"git push origin {target}\n"
            f"git branch -d {branch}\n"
            f"git push origin --delete {branch}\n"
            f"```"
        )
        sys.stderr.write(str(conflict_comment) + "\n")
        try:
            github_client.add_comment(args.issue, conflict_comment, repo_name=args.repo)
        except Exception:
            pass
        sys.exit(1)

    ok, merge_sha = _try("git", "rev-parse", "HEAD")
    if not ok or not merge_sha:
        merge_sha = ""

    # Record estimator accuracy before pushing / deleting the branch (AC1/AC7).
    if merge_sha:
        _record_merge_accuracy(args.issue, merge_sha, _REPO_ROOT, target)

    _run("git", "push", "origin", target)
    sys.stdout.write(str(f"Pushed {target}.") + "\n")

    # Clean up feature branch — use -D (force) since the branch may be checked
    # out in the coder worktree, where -d would be rejected by git.
    _try("git", "branch", "-D", branch)
    _try("git", "push", "origin", "--delete", branch)

    sys.stdout.write(str(f"✅  Merged {branch} into {target}") + "\n")
    sys.stdout.write(str("    Feature branch deleted locally and on origin") + "\n")
    # Signal to sprint_manager that merge succeeded; label transitions are
    # handled exclusively by sprint_manager via state_machine.transition().
    sys.stdout.write(str(f"FINISH_FEATURE_OUTCOME merged sha={merge_sha} branch={branch}") + "\n")

    # Manual /tester path: apply UAT label here.
    # When sprint_manager dispatches finish_feature it sets COMMANDER_SPRINT_RUNNING
    # and handles the UAT transition itself — skip to avoid double-transition.
    _apply_uat_if_manual(args.issue, args.repo)


def _apply_uat_if_manual(issue_num: int, repo) -> None:
    """Transition ticket to UAT unless the dispatch path owns the transition.

    sprint_manager sets COMMANDER_SPRINT_RUNNING when it calls this script;
    a non-empty value means the dispatch path will handle the UAT label.
    When unset (manual /tester run), we apply it here so the ticket is not
    stuck on SIT forever.
    """
    if os.environ.get("COMMANDER_SPRINT_RUNNING", "").strip():
        return

    try:
        from services.sprint_manager.state_machine import TicketState, transition
        transition(issue_num, TicketState.UAT, actor="finish_feature", repo=repo)
        sys.stdout.write(str(f"Label transitioned to UAT for issue #{issue_num}\n"))
    except Exception as exc:
        sys.stdout.write(str(f"Warning: UAT transition failed for #{issue_num}: {exc}\n"))


if __name__ == "__main__":
    main()
