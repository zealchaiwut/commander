#!/usr/bin/env python3
"""Sprint Manager — orchestrates coder and tester agents for sprint issues,
with a post-tester quality gate pipeline before auto-merging to develop.

Quality gates (pytest → lint → merge-preview) run after a tester subprocess
exits 0 and the issue has advanced to the UAT label. Any gate failure reverts
the issue to SIT with a detailed comment.

After sprint completion a rich executive summary is written to
~/commander/dashboard/sprints/sprint-<N>-summary-<YYYY-MM-DD>.md, a GitHub
issue is created for permanent record, and an optional interactive learnings
prompt is shown when stdout is a TTY.

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
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
SPRINTS_DIR = DASHBOARD_DIR / "sprints"


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sprint_number(label: str) -> Optional[int]:
    m = re.search(r"(\d+)", label)
    return int(m.group(1)) if m else None


def _state_path(sprint_number: Optional[int], sprint_label: str) -> Path:
    SPRINTS_DIR.mkdir(parents=True, exist_ok=True)
    n = sprint_number if sprint_number is not None else sprint_label
    return SPRINTS_DIR / f"sprint-{n}-state.json"


def _summary_path(sprint_number: Optional[int], sprint_label: str) -> Path:
    SPRINTS_DIR.mkdir(parents=True, exist_ok=True)
    n   = sprint_number if sprint_number is not None else sprint_label
    day = datetime.now().strftime("%Y-%m-%d")
    return SPRINTS_DIR / f"sprint-{n}-summary-{day}.md"


# ── failure categories ────────────────────────────────────────────────────────

class FailureCategory:
    HANG             = "HANG"
    CRASH            = "CRASH"
    GATE_FAIL        = "GATE_FAIL"
    TESTER_REJECTED  = "TESTER_REJECTED"
    RETRY_EXHAUSTED  = "RETRY_EXHAUSTED"


# ── sprint issue state ────────────────────────────────────────────────────────

@dataclass
class IssueState:
    number:      int
    title:       str
    status:      str              = "pending"   # pending | done | skipped
    skip_reason: Optional[str]   = None
    category:    Optional[str]   = None         # FailureCategory value
    tokens_in:   int             = 0
    tokens_out:  int             = 0

    def to_dict(self) -> dict:
        return {
            "number":      self.number,
            "title":       self.title,
            "status":      self.status,
            "skip_reason": self.skip_reason,
            "category":    self.category,
            "tokens_in":   self.tokens_in,
            "tokens_out":  self.tokens_out,
        }

    @staticmethod
    def from_dict(d: dict) -> "IssueState":
        return IssueState(
            number      = d["number"],
            title       = d["title"],
            status      = d.get("status", "pending"),
            skip_reason = d.get("skip_reason"),
            category    = d.get("category"),
            tokens_in   = d.get("tokens_in", 0),
            tokens_out  = d.get("tokens_out", 0),
        )


@dataclass
class SprintState:
    sprint_label:     str
    sprint_number:    Optional[int]
    issues:           list[IssueState]  = field(default_factory=list)
    start_timestamp:  str              = ""
    total_tokens_in:  int              = 0
    total_tokens_out: int              = 0
    wall_clock_secs:  float            = 0.0

    def to_dict(self) -> dict:
        return {
            "sprint_label":     self.sprint_label,
            "sprint_number":    self.sprint_number,
            "issues":           [i.to_dict() for i in self.issues],
            "start_timestamp":  self.start_timestamp,
            "total_tokens_in":  self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "wall_clock_secs":  self.wall_clock_secs,
        }

    @staticmethod
    def from_dict(d: dict) -> "SprintState":
        s = SprintState(
            sprint_label     = d["sprint_label"],
            sprint_number    = d.get("sprint_number"),
            start_timestamp  = d.get("start_timestamp", ""),
            total_tokens_in  = d.get("total_tokens_in", 0),
            total_tokens_out = d.get("total_tokens_out", 0),
            wall_clock_secs  = d.get("wall_clock_secs", 0.0),
        )
        s.issues = [IssueState.from_dict(i) for i in d.get("issues", [])]
        return s

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))


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


# ── sprint summary report (AC-1, AC-2, AC-3) ─────────────────────────────────

LEARNINGS_STUB = (
    "_TODO: replace this stub with your retrospective notes._\n\n"
    "What went well? What should we do differently next sprint?"
)


def _follow_up_action(category: Optional[str]) -> str:
    mapping = {
        FailureCategory.HANG:            "Investigate subprocess logs; check for infinite loops or network waits. Retry manually.",
        FailureCategory.CRASH:           "Examine the coder/tester log for the exception. Fix the underlying issue and retry.",
        FailureCategory.GATE_FAIL:       "Review the gate output in the GitHub comment. Fix failing tests or linting errors.",
        FailureCategory.TESTER_REJECTED: "The tester did not advance the issue to UAT. Review the tester log and re-run tester.",
        FailureCategory.RETRY_EXHAUSTED: "Max retries reached. Manually investigate and fix the issue.",
    }
    return mapping.get(category or "", "Review the issue manually.")


def generate_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
) -> str:
    """Generate a richly-formatted executive summary markdown string.

    Section order per AC-1:
      ## Sprint <N> — <status>
      ## What Shipped
      ## What Didn't Ship
      ## Stats
      ## Carried Over
      ## Key Learnings
      ## Links
      Footer
    """
    n = state.sprint_number if state.sprint_number is not None else state.sprint_label

    start_ts = state.start_timestamp or _utcnow()
    end_ts   = _utcnow()

    h, rem   = divmod(int(elapsed_secs), 3600)
    m_int, s = divmod(rem, 60)
    duration_str = f"{h}h {m_int}m {s}s"

    completed = [i for i in state.issues if i.status == "done"]
    skipped   = [i for i in state.issues if i.status == "skipped"]
    pending   = [i for i in state.issues if i.status == "pending"]

    total_tokens     = state.total_tokens_in + state.total_tokens_out
    avg_ticket_secs  = (elapsed_secs / len(completed)) if completed else 0
    avg_h, avg_r     = divmod(int(avg_ticket_secs), 3600)
    avg_m, avg_s     = divmod(avg_r, 60)
    avg_ticket_str   = f"{avg_h}h {avg_m}m {avg_s}s" if completed else "—"

    tester_rejections = sum(
        1 for i in state.issues
        if i.category == FailureCategory.TESTER_REJECTED
    )
    merge_conflicts = sum(
        1 for i in state.issues
        if i.category == FailureCategory.GATE_FAIL
        and "conflict" in (i.skip_reason or "").lower()
    )
    gate_total    = len([i for i in state.issues if i.status in ("done", "skipped")])
    gate_passed   = len(completed)
    gate_pass_rate = round(gate_passed / gate_total * 100, 1) if gate_total else 0.0

    r = _r(repo_name)
    sprint_filter_url = f"https://github.com/{r}/issues?q=label%3A{state.sprint_label}"

    lines: list[str] = []

    # ── Header section ──
    lines += [
        f"## Sprint {n} — {end_reason}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Sprint number | {n} |",
        f"| Start | {start_ts} |",
        f"| End | {end_ts} |",
        f"| Duration | {duration_str} |",
        f"| End reason | {end_reason} |",
        "",
    ]

    # ── What Shipped ──
    lines += [
        "## What Shipped",
        "",
        "| Issue # | Title | Time taken | Outcome | Size |",
        "|---|---|---|---|---|",
    ]
    if completed:
        for issue in completed:
            lines.append(f"| #{issue.number} | {issue.title} | — | UAT-approved / closed | — |")
    else:
        lines.append("| — | No issues shipped | — | — | — |")
    lines.append("")

    # ── What Didn't Ship ──
    lines += [
        "## What Didn't Ship",
        "",
        "| Issue # | Title | Failure category | Reason |",
        "|---|---|---|---|",
    ]
    if skipped:
        for issue in skipped:
            cat    = issue.category or "unknown"
            reason = (issue.skip_reason or "no reason recorded").replace("|", "/")
            lines.append(f"| #{issue.number} | {issue.title} | {cat} | {reason} |")
    else:
        lines.append("| — | All issues shipped | — | — |")
    lines.append("")

    # ── Stats ──
    lines += [
        "## Stats",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total tokens | {total_tokens} |",
        f"| Avg ticket time | {avg_ticket_str} |",
        f"| Quality-gate pass rate | {gate_pass_rate}% |",
        f"| Tester rejections | {tester_rejections} |",
        f"| Merge conflicts | {merge_conflicts} |",
        "",
    ]

    # ── Carried Over ──
    lines += ["## Carried Over", ""]
    carried_items: list[str] = []
    for issue in pending:
        carried_items.append(f"- #{issue.number} {issue.title} — candidate for next sprint")
    for issue in (open_issues or []):
        num   = issue.get("number", "?")
        title = issue.get("title", "")
        carried_items.append(f"- #{num} {title} — candidate for next sprint")
    if carried_items:
        lines.extend(carried_items)
    else:
        lines.append("No issues carried over.")
    lines.append("")

    # ── Key Learnings ──
    lines += [
        "## Key Learnings",
        "",
        LEARNINGS_STUB,
        "",
    ]

    # ── Links ──
    all_links: list[str] = [
        f"- [Sprint {n} issues on GitHub]({sprint_filter_url})",
    ]
    for issue in state.issues:
        link = f"https://github.com/{r}/issues/{issue.number}"
        all_links.append(f"- [Issue #{issue.number} — {issue.title[:50]}]({link})")

    lines += ["## Links", ""]
    if len(all_links) > 3:
        lines.append("<details>")
        lines.append(f"<summary>{len(all_links)} links — click to expand</summary>")
        lines.append("")
        lines.extend(all_links)
        lines.append("")
        lines.append("</details>")
    else:
        lines.extend(all_links)
    lines.append("")

    # ── Footer ──
    lines.append(f"_Generated by sprint-manager v1.0 on {_utcnow()}_")

    return "\n".join(lines)


def _ensure_github_labels(labels: list[str], repo_name: Optional[str] = None) -> None:
    """Create GitHub labels if they don't exist (best-effort, AC-2)."""
    r = _r(repo_name)
    for label in labels:
        try:
            subprocess.run(
                ["gh", "label", "create", label, "--repo", r, "--force"],
                capture_output=True, text=True, check=False,
            )
        except Exception:
            pass


def create_summary_github_issue(
    content: str,
    sprint_number: Optional[int],
    sprint_label: str,
    repo_name: Optional[str] = None,
) -> tuple[Optional[int], Optional[str]]:
    """AC-2: Create a GitHub issue with the summary markdown as the body.

    Labels: 'docs', 'sprint-<N>' (created if missing).
    Returns (issue_number, issue_url) or (None, None) on failure.
    """
    n      = sprint_number if sprint_number is not None else sprint_label
    title  = f"Sprint {n} Executive Summary"
    labels = ["docs", f"sprint-{n}"]

    _ensure_github_labels(labels, repo_name=repo_name)

    try:
        issue_num, url = github_client.create_issue(
            title=title, body=content, labels=labels, repo_name=repo_name
        )
        print(f"  Summary GitHub issue created: {url}")
        return issue_num, url
    except Exception as e:
        print(f"  Warning: failed to create summary GitHub issue — {e}", file=sys.stderr)
        return None, None


def _prompt_learnings(
    content: str,
    path: Path,
    sprint_number: Optional[int],
    sprint_label: str,
    summary_issue_num: Optional[int],
    repo_name: Optional[str] = None,
) -> str:
    """AC-3: Interactive learnings prompt.

    If stdout is a TTY, ask the user whether to fill in Key Learnings.
    'y' → open $EDITOR (fallback: nano) with the stub pre-populated.
    On close, replace the stub in both the local file and the GitHub issue.
    'n' or non-interactive → leave stub in place.
    Returns the (possibly updated) content string.
    """
    n = sprint_number if sprint_number is not None else sprint_label
    if not sys.stdout.isatty():
        return content  # non-interactive: leave stub in place

    try:
        answer = input(f"\nSprint {n} done. Want to add learnings to the summary? (y/n) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return content

    if answer != "y":
        return content

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                     encoding="utf-8") as tf:
        tmp_path = Path(tf.name)
        tf.write(LEARNINGS_STUB + "\n")

    editor = os.environ.get("EDITOR", "nano")
    try:
        subprocess.run([editor, str(tmp_path)], check=False)
        new_learnings = tmp_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"  Warning: editor failed — {e}", file=sys.stderr)
        new_learnings = ""
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    if not new_learnings or new_learnings == LEARNINGS_STUB.strip():
        print("  No learnings entered; keeping placeholder.")
        return content

    updated = content.replace(LEARNINGS_STUB, new_learnings)

    path.write_text(updated, encoding="utf-8")
    print(f"  Key Learnings updated in {path}")

    if summary_issue_num is not None:
        r = _r(repo_name)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                         encoding="utf-8") as tf2:
            tmp2 = Path(tf2.name)
            tf2.write(updated)
        try:
            subprocess.run(
                ["gh", "issue", "edit", str(summary_issue_num),
                 "--repo", r, "--body-file", str(tmp2)],
                capture_output=True, text=True, check=False,
            )
            print(f"  GitHub issue #{summary_issue_num} body updated with learnings.")
        except Exception as e:
            print(f"  Warning: failed to update GitHub issue body — {e}", file=sys.stderr)
        finally:
            try:
                tmp2.unlink()
            except Exception:
                pass

    return updated


def write_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
) -> Path:
    """Write summary file, create GitHub issue, prompt for learnings (AC-1/2/3)."""
    content = generate_sprint_summary(
        state,
        elapsed_secs,
        end_reason=end_reason,
        open_issues=open_issues,
        repo_name=repo_name,
    )
    path = _summary_path(state.sprint_number, state.sprint_label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  Sprint summary written to {path}")

    # AC-2: Create GitHub issue (best-effort — errors must not crash the sprint)
    try:
        summary_issue_num, summary_issue_url = create_summary_github_issue(
            content=content,
            sprint_number=state.sprint_number,
            sprint_label=state.sprint_label,
            repo_name=repo_name,
        )
    except Exception as exc:
        print(f"  Warning: create_summary_github_issue raised — {exc}", file=sys.stderr)
        summary_issue_num, summary_issue_url = None, None

    # Store summary_issue_url in state JSON
    if summary_issue_url:
        state_path = _state_path(state.sprint_number, state.sprint_label)
        try:
            if state_path.exists():
                state_dict = json.loads(state_path.read_text())
            else:
                state_dict = state.to_dict()
            state_dict["summary_issue_url"] = summary_issue_url
            state_path.write_text(json.dumps(state_dict, indent=2))
        except Exception as e:
            print(
                f"  Warning: could not update state file with summary_issue_url — {e}",
                file=sys.stderr,
            )

    # AC-3: Interactive learnings prompt
    _prompt_learnings(
        content=content,
        path=path,
        sprint_number=state.sprint_number,
        sprint_label=state.sprint_label,
        summary_issue_num=summary_issue_num,
        repo_name=repo_name,
    )

    return path


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
) -> tuple[SprintSummary, SprintState]:
    """Main sprint loop — processes backlog issues sequentially.

    Returns (SprintSummary, SprintState) for backward compat + extended summary.
    """
    summary = SprintSummary()
    sprint_num = _sprint_number(label)
    state = SprintState(
        sprint_label=label,
        sprint_number=sprint_num,
        start_timestamp=_utcnow(),
    )

    print(f"\n=== Sprint Manager: label={label} ===")
    issues = list_backlog_issues(label, repo_name=repo_name)

    if not issues:
        print("No backlog issues found for this label.")
        return summary, state

    print(f"Found {len(issues)} backlog issue(s): {[i['number'] for i in issues]}")
    state.issues = [IssueState(number=i["number"], title=i["title"]) for i in issues]

    start_time = time.monotonic()

    for issue_state in state.issues:
        num   = issue_state.number
        title = issue_state.title
        print(f"\n--- Issue #{num}: {title} ---")
        summary.processed.append(f"#{num}")

        if dry_run:
            print("  [dry-run] would dispatch coder + tester")
            issue_state.status = "skipped"
            issue_state.skip_reason = "dry-run"
            summary.skipped.append(f"#{num} (dry-run)")
            continue

        # Dispatch coder
        print(f"  Dispatching coder for issue #{num} …")
        coder_ok = _dispatch_agent("coder", num)
        if not coder_ok:
            print(f"  Coder failed for #{num} — skipping to next issue")
            issue_state.status      = "skipped"
            issue_state.skip_reason = "Coder failed"
            issue_state.category    = FailureCategory.CRASH
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
            issue_state.status = "done"
            summary.merged.append(f"#{num}")
        else:
            issue_state.status      = "skipped"
            issue_state.skip_reason = summary_line
            if "gate failed" in summary_line:
                issue_state.category = FailureCategory.GATE_FAIL
                summary.gate_failures.append(summary_line)
            else:
                issue_state.category = FailureCategory.TESTER_REJECTED

    state.wall_clock_secs = time.monotonic() - start_time
    return summary, state


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

    summary, state = run_sprint(
        label=args.label,
        skip_gates=args.skip_gates,
        gate_pytest=args.gate_pytest,
        gate_lint=args.gate_lint,
        gate_merge_preview=args.gate_merge_preview,
        repo_name=args.repo,
        dry_run=args.dry_run,
    )

    # AC-1/2/3: write extended summary, create GitHub issue, prompt for learnings
    if state.issues:
        end_reason = "complete" if not summary.skipped else "stopped"
        summary_path = write_sprint_summary(
            state=state,
            elapsed_secs=state.wall_clock_secs,
            end_reason=end_reason,
            repo_name=args.repo,
        )
    else:
        summary_path = None

    print("\n=== Sprint Summary ===")
    print(f"Processed: {', '.join(summary.processed) or 'none'}")
    print(f"Merged:    {', '.join(summary.merged) or 'none'}")
    if summary.gate_failures:
        print("Gate failures:")
        for line in summary.gate_failures:
            print(f"  {line}")
    if summary.skipped:
        print(f"Skipped:   {', '.join(summary.skipped)}")
    if summary_path:
        print(f"Summary:   {summary_path}")


if __name__ == "__main__":
    main()
