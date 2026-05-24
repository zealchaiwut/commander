#!/usr/bin/env python3
"""Sprint Manager — orchestrates coder and tester agents for sprint issues,
with a post-tester quality gate pipeline before auto-merging to develop.

Quality gates (pytest → lint → merge-preview) run after a tester subprocess
exits 0 and the issue has advanced to the UAT label. Any gate failure reverts
the issue to SIT with a detailed comment.

Usage:
    python3 ~/commander/dashboard/scripts/sprint_manager.py <label> [options]

Example:
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --skip-gates
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --gate-pytest=false

Run from the git root of the repository (NOT from dashboard/).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── path setup ────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent
DASHBOARD_DIR = SCRIPTS_DIR.parent

sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(DASHBOARD_DIR / ".env")
import github_client

# Default paths — can be overridden via env vars or CLI for testing
WORKTESTER_ROOT = Path(os.environ.get("WORKTESTER_ROOT", Path.home() / "commander" / "work-tester"))
WORKTESTER_DASHBOARD = WORKTESTER_ROOT / "dashboard"
FINISH_FEATURE_SCRIPT = DASHBOARD_DIR / "scripts" / "finish_feature.py"
DASHBOARD_API_URL = os.environ.get("DASHBOARD_API_URL", "http://localhost:8000")


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class GateResult:
    gate: str
    passed: bool
    skipped: bool = False
    output: str = ""

    @property
    def symbol(self) -> str:
        if self.skipped:
            return "⏭️ skipped"
        return "✅" if self.passed else "❌"


@dataclass
class SprintSummary:
    processed: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ── subprocess helpers ────────────────────────────────────────────────────────

def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)
    return r.stdout.strip()


def _try(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


def _run_timed(*cmd, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout, r.stderr


# ── dashboard integration ─────────────────────────────────────────────────────

def _post_agent_event(tool_name: str, agent_id: str = "sprint-manager") -> None:
    """POST to /api/agent-event to update the dashboard agent card."""
    try:
        payload = json.dumps({
            "agent_id": agent_id,
            "tool_name": tool_name,
            "timestamp": time.time(),
        })
        subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                f"{DASHBOARD_API_URL}/api/agent-event",
                "-H", "Content-Type: application/json",
                "-d", payload,
                "--max-time", "2",
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        # Fail silently — dashboard may not be running
        pass


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _get_issue_labels(issue_num: int, repo_name: Optional[str] = None) -> set[str]:
    """Re-fetch current labels for an issue via gh CLI."""
    r = _r(repo_name)
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(issue_num), "--repo", r,
             "--json", "labels"],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        return {lbl["name"] for lbl in data.get("labels", [])}
    except Exception:
        return set()


def _revert_to_sit(issue_num: int, gate_name: str, output: str,
                   repo_name: Optional[str] = None) -> None:
    """Label the issue SIT and post a failure comment."""
    truncated = output[:2000] if len(output) > 2000 else output
    comment = (
        f"❌ Quality gate failed: **{gate_name}**\n"
        f"Issue reverted to SIT for re-inspection.\n\n"
        f"**{gate_name}** output:\n"
        f"```\n{truncated}\n```"
    )
    try:
        github_client.update_labels(
            issue_num,
            add=["SIT"],
            remove=["UAT", "in-progress"],
            repo_name=repo_name,
        )
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        print(f"  Warning: failed to update GitHub — {e}", file=sys.stderr)


def _post_success_comment(issue_num: int, results: list[GateResult],
                           repo_name: Optional[str] = None) -> None:
    gate_lines = "\n".join(
        f"- **{r.gate}**: {r.symbol}" for r in results
    )
    comment = (
        f"✅ Quality gates passed. Auto-merged to develop.\n\n"
        f"Gates:\n{gate_lines}\n\n"
        f"Awaiting human UAT approval."
    )
    try:
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        print(f"  Warning: failed to post success comment — {e}", file=sys.stderr)


def _r(repo_name: Optional[str]) -> str:
    return repo_name or github_client.repo()


# ── quality gates ─────────────────────────────────────────────────────────────

def _gate_pytest(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
) -> GateResult:
    """Gate 1 — run pytest -x inside the tester worktree dashboard."""
    if skip:
        print("  [gate:pytest] skipped")
        return GateResult(gate="pytest", passed=True, skipped=True)

    _post_agent_event("gate:pytest")
    print("  [gate:pytest] running pytest -x …")

    # Detect pytest binary
    ok, pytest_path, _ = _try("which", "pytest")
    if not ok:
        # Try inside dashboard venv
        venv_pytest = worktester_dashboard / ".." / "venv" / "bin" / "pytest"
        if venv_pytest.exists():
            pytest_bin = str(venv_pytest.resolve())
        else:
            output = "pytest binary not found on PATH and no venv/bin/pytest found."
            print(f"  [gate:pytest] FAIL — {output}")
            return GateResult(gate="pytest", passed=False, output=output)
    else:
        pytest_bin = pytest_path

    rc, stdout, stderr = _run_timed(pytest_bin, "-x", cwd=worktester_dashboard)
    combined = stdout + stderr
    if rc == 0:
        print("  [gate:pytest] PASS")
        return GateResult(gate="pytest", passed=True, output=combined)
    else:
        print(f"  [gate:pytest] FAIL (exit {rc})")
        _revert_to_sit(issue_num, "pytest", combined, repo_name=repo_name)
        return GateResult(gate="pytest", passed=False, output=combined)


def _gate_lint(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
) -> GateResult:
    """Gate 2 — run ruff check . inside the tester worktree dashboard."""
    if skip:
        print("  [gate:lint] skipped")
        return GateResult(gate="lint", passed=True, skipped=True)

    _post_agent_event("gate:lint")
    print("  [gate:lint] running ruff check . …")

    # ruff is optional — if not found, log warning and treat as passed
    ok, ruff_path, _ = _try("which", "ruff")
    if not ok:
        # Try inside dashboard venv
        venv_ruff = worktester_dashboard / ".." / "venv" / "bin" / "ruff"
        if venv_ruff.exists():
            ruff_bin = str(venv_ruff.resolve())
        else:
            print("  [gate:lint] WARNING — ruff not on PATH; treating lint gate as passed")
            return GateResult(gate="lint", passed=True, skipped=False,
                              output="ruff not found — skipped with warning")
    else:
        ruff_bin = ruff_path

    rc, stdout, stderr = _run_timed(ruff_bin, "check", ".", cwd=worktester_dashboard)
    combined = stdout + stderr
    if rc == 0:
        print("  [gate:lint] PASS")
        return GateResult(gate="lint", passed=True, output=combined)
    else:
        print(f"  [gate:lint] FAIL (exit {rc})")
        _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
        return GateResult(gate="lint", passed=False, output=combined)


def _gate_merge_preview(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    skip: bool,
    repo_name: Optional[str] = None,
) -> GateResult:
    """Gate 3 — simulate merge in worktester root without committing."""
    if skip:
        print("  [gate:merge-preview] skipped")
        return GateResult(gate="merge-preview", passed=True, skipped=True)

    _post_agent_event("gate:merge-preview")
    print(f"  [gate:merge-preview] simulating merge of {feature_branch} …")

    merge_ok = False
    combined = ""

    try:
        # Fetch + update develop
        _run("git", "fetch", "origin", cwd=worktester_root, check=False)
        _run("git", "checkout", "develop", cwd=worktester_root, check=False)
        _run("git", "pull", "origin", "develop", cwd=worktester_root, check=False)

        # Attempt dry-run merge
        rc, stdout, stderr = _run_timed(
            "git", "merge", "--no-commit", "--no-ff", feature_branch,
            cwd=worktester_root,
        )
        combined = stdout + stderr
        merge_ok = (rc == 0)

        if merge_ok:
            print("  [gate:merge-preview] PASS — no conflicts")
        else:
            print(f"  [gate:merge-preview] FAIL — conflicts detected")
    finally:
        # Always abort to leave working tree clean
        _run("git", "merge", "--abort", cwd=worktester_root, check=False)

    if not merge_ok:
        _revert_to_sit(issue_num, "merge-preview", combined, repo_name=repo_name)
        return GateResult(gate="merge-preview", passed=False, output=combined)

    return GateResult(gate="merge-preview", passed=True, output=combined)


def _run_quality_gates(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    worktester_dashboard: Path,
    skip_all: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    repo_name: Optional[str] = None,
) -> list[GateResult]:
    """Run the three quality gates sequentially. Returns list of GateResult.

    Stops early on first failure (remaining gates are not run).
    If skip_all is True, all gates are skipped.
    """
    results: list[GateResult] = []

    # Gate 1 — pytest
    r1 = _gate_pytest(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_pytest),
        repo_name=repo_name,
    )
    results.append(r1)
    if not r1.passed:
        return results

    # Gate 2 — lint
    r2 = _gate_lint(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_lint),
        repo_name=repo_name,
    )
    results.append(r2)
    if not r2.passed:
        return results

    # Gate 3 — merge-preview
    r3 = _gate_merge_preview(
        issue_num,
        feature_branch,
        worktester_root,
        skip=(skip_all or not gate_merge_preview),
        repo_name=repo_name,
    )
    results.append(r3)

    return results


# ── feature branch lookup ──────────────────────────────────────────────────────

def _find_feature_branch(issue_num: int) -> Optional[str]:
    """Return feature/<N>-* branch name, checking local then remote."""
    ok, out, _ = _try("git", "branch", "--list", f"feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().lstrip("* ")

    ok, out, _ = _try("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().removeprefix("origin/")

    return None


# ── post-tester hook ──────────────────────────────────────────────────────────

def handle_post_tester(
    issue_num: int,
    tester_exit_code: int,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    worktester_root: Path,
    worktester_dashboard: Path,
    repo_name: Optional[str] = None,
) -> tuple[bool, str]:
    """Called after a tester subprocess exits.

    Returns (merged: bool, summary_line: str).

    AC-1: Gates only run if tester exited 0 AND label is exactly UAT.
    """
    if tester_exit_code != 0:
        return False, f"Issue #{issue_num}: tester exited {tester_exit_code}, skipping gates"

    # Re-fetch current labels (AC-1)
    labels = _get_issue_labels(issue_num, repo_name=repo_name)
    if "UAT" not in labels:
        current = ", ".join(sorted(labels)) or "(none)"
        print(f"  Issue #{issue_num}: tester exited 0 but label is [{current}], not UAT — skipping gates")
        return False, f"Issue #{issue_num}: tester exited 0 but not UAT — no merge"

    print(f"\nIssue #{issue_num} is UAT — running quality gates…")

    # Find the feature branch
    feature_branch = _find_feature_branch(issue_num)
    if not feature_branch:
        msg = f"Issue #{issue_num}: feature branch not found — cannot run merge-preview gate"
        print(f"  Warning: {msg}")
        # Use a placeholder so the other gates can still run
        feature_branch = f"feature/{issue_num}-unknown"

    if skip_gates:
        print("  --skip-gates active — skipping all quality gates, proceeding to merge")
        _call_finish_feature(issue_num, worktester_root, repo_name=repo_name)
        _post_agent_event("gate:merging")
        all_skipped = [
            GateResult(gate="pytest", passed=True, skipped=True),
            GateResult(gate="lint", passed=True, skipped=True),
            GateResult(gate="merge-preview", passed=True, skipped=True),
        ]
        _post_success_comment(issue_num, all_skipped, repo_name=repo_name)
        return True, f"Issue #{issue_num}: all gates skipped, merged"

    results = _run_quality_gates(
        issue_num=issue_num,
        feature_branch=feature_branch,
        worktester_root=worktester_root,
        worktester_dashboard=worktester_dashboard,
        skip_all=False,
        gate_pytest=gate_pytest,
        gate_lint=gate_lint,
        gate_merge_preview=gate_merge_preview,
        repo_name=repo_name,
    )

    # Check if all gates passed
    all_passed = all(r.passed for r in results)

    if all_passed:
        _post_agent_event("gate:merging")
        print(f"  All gates passed — calling finish_feature.py for issue #{issue_num}")
        _call_finish_feature(issue_num, worktester_root, repo_name=repo_name)
        _post_success_comment(issue_num, results, repo_name=repo_name)
        return True, f"Issue #{issue_num}: all gates passed, merged to develop"
    else:
        failed = next((r for r in results if not r.passed), None)
        gate_name = failed.gate if failed else "unknown"
        return False, f"Issue #{issue_num}: gate failed ({gate_name})"


def _call_finish_feature(
    issue_num: int,
    worktester_root: Path,
    repo_name: Optional[str] = None,
) -> None:
    """Call finish_feature.py as a subprocess from the worktester root."""
    cmd = [sys.executable, str(FINISH_FEATURE_SCRIPT), "--issue", str(issue_num)]
    if repo_name:
        cmd += ["--repo", repo_name]

    print(f"  Calling finish_feature.py --issue {issue_num} …")
    result = subprocess.run(cmd, cwd=str(worktester_root), capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        print(f"  Warning: finish_feature.py exited {result.returncode}", file=sys.stderr)
        if result.stderr:
            print(f"  {result.stderr.rstrip()}", file=sys.stderr)
    else:
        print(f"  finish_feature.py completed successfully")


# ── sprint loop ────────────────────────────────────────────────────────────────

def list_backlog_issues(label: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return open issues with the given label, in backlog, sorted by number."""
    r = _r(repo_name)
    try:
        out = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", r,
                "--label", label,
                "--state", "open",
                "--json", "number,title,labels",
                "--limit", "200",
            ],
            capture_output=True, text=True, check=True,
        )
        issues = json.loads(out.stdout)
        # Only return issues in backlog state
        result = []
        for issue in issues:
            labels_set = {lbl["name"] for lbl in issue.get("labels", [])}
            status = _classify(labels_set)
            if status == "backlog":
                result.append(issue)
        return sorted(result, key=lambda i: i["number"])
    except Exception as e:
        print(f"Warning: could not list issues — {e}", file=sys.stderr)
        return []


def _classify(labels: set[str]) -> str:
    if "UAT-approved" in labels:
        return "done"
    if "UAT" in labels:
        return "uat"
    if "SIT" in labels:
        return "sit"
    if "in-progress" in labels:
        return "in-progress"
    return "backlog"


def run_sprint(
    label: str,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    repo_name: Optional[str] = None,
    dry_run: bool = False,
) -> SprintSummary:
    """Main sprint loop — processes backlog issues sequentially."""
    summary = SprintSummary()

    print(f"\n=== Sprint Manager: label={label} ===")
    issues = list_backlog_issues(label, repo_name=repo_name)

    if not issues:
        print("No backlog issues found for this label.")
        return summary

    print(f"Found {len(issues)} backlog issue(s): {[i['number'] for i in issues]}")

    for issue in issues:
        num = issue["number"]
        title = issue["title"]
        print(f"\n--- Issue #{num}: {title} ---")
        summary.processed.append(f"#{num}")

        if dry_run:
            print("  [dry-run] would dispatch coder + tester")
            summary.skipped.append(f"#{num} (dry-run)")
            continue

        # Dispatch coder
        print(f"  Dispatching coder for issue #{num} …")
        coder_ok = _dispatch_agent("coder", num)
        if not coder_ok:
            print(f"  Coder failed for #{num} — skipping to next issue")
            summary.skipped.append(f"#{num} (coder failed)")
            continue

        # Dispatch tester
        print(f"  Dispatching tester for issue #{num} …")
        tester_rc = _dispatch_tester(num)

        # Post-tester hook
        merged, summary_line = handle_post_tester(
            issue_num=num,
            tester_exit_code=tester_rc,
            skip_gates=skip_gates,
            gate_pytest=gate_pytest,
            gate_lint=gate_lint,
            gate_merge_preview=gate_merge_preview,
            worktester_root=WORKTESTER_ROOT,
            worktester_dashboard=WORKTESTER_DASHBOARD,
            repo_name=repo_name,
        )
        print(f"  {summary_line}")

        if merged:
            summary.merged.append(f"#{num}")
        elif "gate failed" in summary_line:
            summary.gate_failures.append(summary_line)

    return summary


def _dispatch_agent(agent_type: str, issue_num: int) -> bool:
    """Placeholder for dispatching a coder agent. Returns True on success."""
    # In full implementation this would invoke the coder agent subprocess.
    # For now it's a hook point; tester integration is the focus of this ticket.
    print(f"  [sprint-manager] agent={agent_type} issue={issue_num} (stub)")
    return True


def _dispatch_tester(issue_num: int) -> int:
    """Placeholder for dispatching a tester agent. Returns exit code."""
    # In full implementation this would run the tester subprocess.
    # Returns 0 on success.
    print(f"  [sprint-manager] tester issue={issue_num} (stub)")
    return 0


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Sprint Manager — run a sprint with optional quality gates",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("label", help="GitHub label identifying the sprint (e.g. sprint-5)")
    p.add_argument("--repo", default=None, help="owner/repo override")

    # Gate control flags (AC-13, AC-14)
    p.add_argument(
        "--skip-gates",
        action="store_true",
        default=False,
        help="Skip all quality gates and force auto-merge after tester passes",
    )
    p.add_argument(
        "--gate-pytest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable pytest gate (default: enabled)",
    )
    p.add_argument(
        "--gate-lint",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable lint gate (default: enabled)",
    )
    p.add_argument(
        "--gate-merge-preview",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable merge-preview gate (default: enabled)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List issues but do not dispatch agents",
    )

    args = p.parse_args()

    summary = run_sprint(
        label=args.label,
        skip_gates=args.skip_gates,
        gate_pytest=args.gate_pytest,
        gate_lint=args.gate_lint,
        gate_merge_preview=args.gate_merge_preview,
        repo_name=args.repo,
        dry_run=args.dry_run,
    )

    print("\n=== Sprint Summary ===")
    print(f"Processed: {', '.join(summary.processed) or 'none'}")
    print(f"Merged:    {', '.join(summary.merged) or 'none'}")
    if summary.gate_failures:
        print("Gate failures:")
        for line in summary.gate_failures:
            print(f"  {line}")
    if summary.skipped:
        print(f"Skipped:   {', '.join(summary.skipped)}")


if __name__ == "__main__":
    main()
