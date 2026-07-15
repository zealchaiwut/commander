"""Sprint summary generation helpers for sprint_manager.

Contains: generate_sprint_summary, write_sprint_summary,
create_summary_github_issue, _prompt_learnings, and their
private helpers — extracted from sprint_manager.py (issue #1287)
as a pure structural move with no behavioral changes.

sprint_manager.py re-imports and re-exports all symbols so all
existing call sites remain unmodified.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from services.logging import log as structured_log  # noqa: E402
from services.sprint_manager import agent_browser_runner  # noqa: E402
from services.sprint_manager.alerts import dispatch_alerts  # noqa: E402
from services.sprint_manager.paths import _state_path, _summary_path  # noqa: E402
from services.sprint_manager.state import SprintState  # noqa: E402
from services.sprint_manager.timekeeping import (  # noqa: E402
    SPRINTS_DIR,
    _BANGKOK_TZ,
    _bangkok_now,
    _to_bangkok,
    _utcnow,
)

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig

try:
    from services.sprint_manager import suite_health_gate as _suite_health_gate
    _SUITE_HEALTH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _suite_health_gate = None  # type: ignore[assignment]
    _SUITE_HEALTH_AVAILABLE = False


class _SmMergedView:
    """Proxy for sprint_manager attributes that works regardless of which
    sys.modules key the caller used to import sprint_manager.

    Problem: the same sprint_manager.py can be loaded under two keys —
    ``"sprint_manager"`` (via sys.path insert) and
    ``"services.sprint_manager.sprint_manager"`` (package path) — creating
    two separate module objects.  A monkeypatch applied to one is invisible
    to the other.

    Strategy: for each attribute lookup, check both aliases.
    If summary.py itself defines the attribute (e.g. create_summary_github_issue),
    compare each alias's value to the local original — a differing value means
    it was monkeypatched, so prefer it.
    If summary.py does NOT define the attribute (e.g. github_client, _r), we
    cannot compare; return the first non-None value found, preferring the
    package-path alias so tests that import via that path see their patches.
    """

    def __getattr__(self, name: str):
        _self = sys.modules.get("services.sprint_manager.summary")
        # Use object.__getattribute__ to avoid recursion; fall back to None.
        try:
            original = object.__getattribute__(_self, name) if _self is not None else None
        except AttributeError:
            original = None

        # Pass 1: find a patched (or any available) value.
        # Check package-path alias first so patches applied there are seen.
        for _key in ("services.sprint_manager.sprint_manager", "sprint_manager"):
            _mod = sys.modules.get(_key)
            if _mod is None:
                continue
            val = getattr(_mod, name, None)
            if val is None:
                continue
            if original is None:
                # No local baseline to compare → return first found.
                return val
            if val is not original:
                # Value differs from our own definition → must be patched.
                return val

        # Pass 2: no patched value found; return any available value.
        for _key in ("services.sprint_manager.sprint_manager", "sprint_manager"):
            _mod = sys.modules.get(_key)
            if _mod is not None:
                val = getattr(_mod, name, None)
                if val is not None:
                    return val

        # Last resort: direct import.
        import services.sprint_manager.sprint_manager as _sm_mod  # noqa: PLC0415
        return getattr(_sm_mod, name)


_sm_ref = _SmMergedView()


# ── constants ─────────────────────────────────────────────────────────────────

LEARNINGS_STUB = (
    "_TODO: replace this stub with your retrospective notes._\n\n"
    "What went well? What should we do differently next sprint?"
)


# ── private helpers ───────────────────────────────────────────────────────────

def _follow_up_action(category: Optional[str]) -> str:
    _FC = _sm_ref.FailureCategory
    mapping = {
        _FC.HANG:            "Investigate subprocess logs; check for infinite loops or network waits. Retry manually.",
        _FC.CRASH:           "Examine the coder/tester log for the exception. Fix the underlying issue and retry.",
        _FC.GATE_FAIL:       "Review the gate output in the GitHub comment. Fix failing tests or linting errors.",
        _FC.TESTER_REJECTED: "The tester did not advance the issue to UAT. Review the tester log and re-run tester.",
        _FC.RETRY_EXHAUSTED: "Max retries reached. Manually investigate and fix the issue.",
        _FC.CODER_NO_WORK:   "Coder produced no feature branch. Review the AC and re-run the coder.",
        _FC.PYTEST_FAIL:     "Pytest gate failed. Review the GitHub comment, fix the failing tests, and re-run.",
        _FC.LINT_FAIL:       "Lint gate failed. Fix the ruff errors noted in the GitHub comment and re-run.",
        _FC.MERGE_CONFLICT:  "Merge-preview gate detected conflicts. Resolve conflicts against develop and re-run.",
    }
    return mapping.get(category or "", "Review the issue manually.")


def _load_screenshot_url_map(sprints_dir: Path, sprint_num, issue_num) -> Optional[dict]:
    """Read an optional {filename: raw_url} manifest written at upload time (#712)."""
    manifest = (
        agent_browser_runner.sprint_screenshot_dir(sprints_dir, sprint_num, issue_num)
        / "urls.json"
    )
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_screenshots_section(
    state: SprintState,
    sprints_dir: Path,
    repo_name: Optional[str],
) -> list[str]:
    """Build the per-ticket Screenshots section lines for the sprint summary (#712).

    Scans ``<sprints_dir>/sprint-<N>/screenshots/issue-<N>/`` for each ticket and,
    for any with captured browser screenshots, emits a sub-section with the count
    and inline images (or links when a URL manifest is absent). Returns an empty
    list when no ticket has screenshots, so the section is omitted entirely.
    """
    n = state.sprint_number if state.sprint_number is not None else state.sprint_label
    blocks: list[str] = []
    for issue in state.issues:
        try:
            url_map = _load_screenshot_url_map(sprints_dir, n, issue.number)
            shots = agent_browser_runner.collect_issue_screenshots(
                sprints_dir, n, issue.number, url_map=url_map
            )
        except Exception:
            shots = []
        if not shots:
            continue
        section = agent_browser_runner.build_screenshot_section(
            shots, heading=f"Issue #{issue.number} — {issue.title}", heading_level=3
        )
        if section:
            blocks.append(section)
    if not blocks:
        return []
    lines = ["## Screenshots", ""]
    for block in blocks:
        lines.append(block)
        lines.append("")
    return lines


def _is_stale_summary(
    body: str,
    state_reason: Optional[str],
    state_file_mtime: Optional[float] = None,
    issue_created_at: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (is_stale, reason) for an existing summary issue body.

    Staleness criteria (any one is sufficient):
    1. Issue was closed with state_reason "not_planned".
    2. Body looks like a stub/failed run (stopped + all TESTER_REJECTED + nothing shipped).
    3. The sprint state file on disk is newer than the GitHub issue (AC-2: state-file
       timestamp check) — i.e. a newer sprint run has already completed locally but its
       results were never reflected in the GitHub issue.
    """
    if state_reason == "not_planned":
        return True, "closed as not_planned"

    has_stopped    = "| End reason | stopped |" in body
    has_zero_dur   = bool(re.search(r"\| Duration \| 0h 0m \d+s \|", body))
    has_rejected   = "TESTER_REJECTED" in body
    no_shipped     = "No issues shipped" in body

    if has_stopped and has_zero_dur and has_rejected and no_shipped:
        return True, "stub-mode run (stopped, zero duration, all TESTER_REJECTED, nothing shipped)"

    if has_stopped and has_rejected and no_shipped:
        return True, "failed-run summary (stopped, all TESTER_REJECTED, nothing shipped)"

    # State-file timestamp check: if the local state file is newer than when the issue
    # was created, the issue was produced by an older sprint run.
    if state_file_mtime is not None and issue_created_at:
        try:
            issue_ts = datetime.fromisoformat(
                issue_created_at.rstrip("Z")
            ).replace(tzinfo=timezone.utc).timestamp()
            if state_file_mtime > issue_ts:
                return True, "state file is newer than summary issue (newer sprint run completed locally)"
        except (ValueError, OSError):
            pass  # malformed timestamp or missing file — skip this check

    return False, ""


# ── public functions ──────────────────────────────────────────────────────────

def generate_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
    sprint_branch: Optional[str] = None,
    sprints_dir: Optional[Path] = None,
    merge_target: Optional[str] = None,
) -> str:
    """Generate a richly-formatted executive summary markdown string.

    When ``sprints_dir`` is provided (issue #712), each ticket's captured UAT
    browser screenshots are listed in a Screenshots section; tickets with no
    browser steps contribute nothing (no regression for legacy runs).

    When ``merge_target`` is provided, ``completed`` is derived from git-verified
    shipped issues only; tickets with status==done that fail git verification are
    moved to the "What Didn't Ship" table with reason "marked done but not
    git-verified on <merge_target>".
    """
    _r = _sm_ref._r
    FailureCategory = _sm_ref.FailureCategory
    _git_verified_shipped_issues = _sm_ref._git_verified_shipped_issues
    _RATE_LIMIT_MAX_RETRIES = _sm_ref._RATE_LIMIT_MAX_RETRIES

    n = state.sprint_number if state.sprint_number is not None else state.sprint_label

    start_ts = _to_bangkok(state.start_timestamp) if state.start_timestamp else _bangkok_now()
    # End = start + wall-clock, in Bangkok time. Previously this used
    # _bangkok_now() (the moment the summary was generated), so a regenerated
    # summary drifted past the real sprint end. Hotfix S1.
    end_ts = _bangkok_now()
    if state.start_timestamp:
        try:
            _start_dt = datetime.strptime(
                state.start_timestamp, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            end_ts = (_start_dt + timedelta(seconds=int(elapsed_secs))).astimezone(
                _BANGKOK_TZ
            ).strftime("%Y-%m-%dT%H:%M:%S+07:00")
        except (ValueError, TypeError):
            end_ts = _bangkok_now()

    h, rem   = divmod(int(elapsed_secs), 3600)
    m_int, s = divmod(rem, 60)
    duration_str = f"{h}h {m_int}m {s}s"

    if merge_target:
        completed = _git_verified_shipped_issues(state, merge_target)
        # done-but-not-git-verified: treat as not shipped for reporting
        _false_done = [
            i for i in state.issues
            if i.status == "done" and i not in completed
        ]
        skipped = [i for i in state.issues if i.status == "skipped"] + _false_done
    else:
        completed = [i for i in state.issues if i.status == "done"]
        skipped   = [i for i in state.issues if i.status == "skipped"]
    pending   = [i for i in state.issues if i.status == "pending"]
    # A "failed" ticket is a skipped one that actually failed (has a failure
    # category or agent_status=failed) — distinct from a legitimately-skipped
    # ticket (e.g. already merged in a prior run). Previously the summary printed
    # len(skipped) for BOTH Skipped and Failed. Hotfix S2.
    failed = [
        i for i in skipped
        if (getattr(i, "agent_status", None) == "failed") or getattr(i, "category", None)
    ]

    # Per-ticket metrics come from in-memory sprint state. The Neon-backed rollup
    # was removed in issue #758 (Neon is export-only now).
    _db_rollup: Optional[dict] = None

    if _db_rollup is not None and _db_rollup["sum_tokens"] > 0:
        total_tokens = _db_rollup["sum_tokens"]
    else:
        total_tokens = state.total_tokens_in + state.total_tokens_out

    # cost_estimate: all agents (coder, tester, preflight) run via Claude Code CLI
    # which is subscription-funded — no raw API charges.
    cost_estimate_usd = 0.0  # noqa: F841

    # Avg ticket time = mean of per-ticket wall durations (coder + tester), NOT
    # wall_clock / completed — the old formula collapsed to the whole run wall
    # time when only one ticket completed (sprint-73 showed "26m" avg). Hotfix S3.
    def _summary_ts_secs(a, b):
        if not a or not b:
            return None
        try:
            _s = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
            _e = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
            return max(0.0, (_e - _s).total_seconds())
        except (ValueError, TypeError):
            return None
    _ticket_durs: list[float] = []
    for _i in state.issues:
        _c = _summary_ts_secs(getattr(_i, "coder_started_at", None),
                              getattr(_i, "coder_finished_at", None)) or 0.0
        _t = _summary_ts_secs(getattr(_i, "tester_started_at", None),
                              getattr(_i, "tester_finished_at", None)) or 0.0
        if _c or _t:
            _ticket_durs.append(_c + _t)
    if _db_rollup is not None and _db_rollup["avg_elapsed_seconds"] is not None:
        avg_ticket_secs = _db_rollup["avg_elapsed_seconds"]
    elif _ticket_durs:
        avg_ticket_secs = sum(_ticket_durs) / len(_ticket_durs)
    else:
        avg_ticket_secs = 0
    avg_h, avg_r     = divmod(int(avg_ticket_secs), 3600)
    avg_m, avg_s     = divmod(avg_r, 60)
    avg_ticket_str   = f"{avg_h}h {avg_m}m {avg_s}s" if _ticket_durs else "--"

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

    # -- Suite health gate (issue #888) --
    # Load health result from JSON; if absent, retroactively mark as not recorded.
    _health_result = None
    if _SUITE_HEALTH_AVAILABLE and sprints_dir is not None:
        _health_result = _suite_health_gate.load_gate_result(state.sprint_label, sprints_dir)

    lines: list[str] = []

    # -- Suite health warning banners (prepended before all other content) --
    if _health_result is None:
        lines += [
            "> ⚠ SUITE HEALTH NOT RECORDED — health gate has not run for this sprint.",
            "",
        ]
    elif _health_result.timed_out:
        lines += [
            "> ⚠ SUITE TIMEOUT — the test suite exceeded the configured time limit.",
            "",
        ]
    elif _health_result.failed > 0:
        lines += [
            f"> ⚠ SUITE FAILING — {_health_result.failed} test(s) failed.",
            "",
        ]

    # -- Header section --
    # Roster = the full set of tickets this sprint owns this run. With the
    # already-merged guard (E2), tickets that passed in a prior run stay in
    # state.issues marked done, so this counts the whole sprint rather than a
    # trimmed re-run subset. Hotfix S4.
    attempted = len(state.issues)
    # Outcome counts lead the table — that is the data the operator scans first
    # (table-reorder request). Failed counts real failures, not len(skipped). S2.
    lines += [
        f"## Sprint {n} -- {end_reason}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Sprint number | {n} |",
        f"| Completed | {len(completed)} |",
        f"| Failed | {len(failed)} |",
        f"| Skipped | {len(skipped) - len(failed)} |",
        f"| Attempted | {attempted} |",
        f"| Start | {start_ts} |",
        f"| End | {end_ts} |",
        f"| Duration | {duration_str} |",
        f"| End reason | {end_reason} |",
        "",
    ]

    # -- Pending UAT Review --
    lines += [
        "## Pending UAT Review",
        "",
        "| Issue # | Title | Time taken | Outcome | Size |",
        "|---|---|---|---|---|",
    ]
    if completed:
        for issue in completed:
            lines.append(f"| #{issue.number} | {issue.title} | -- | merged — awaiting UAT review | -- |")
    else:
        lines.append("| -- | No issues merged this sprint | -- | -- | -- |")
    lines.append("")

    # -- What Didn't Ship --
    lines += [
        "## What Didn't Ship",
        "",
        "| Issue # | Title | Failure category | Reason |",
        "|---|---|---|---|",
    ]
    if skipped:
        _false_done_set = set(
            i.number for i in state.issues
            if i.status == "done" and merge_target and i not in completed
        )
        for issue in skipped:
            if issue.number in _false_done_set:
                cat    = "git-unverified"
                reason = f"marked done but not git-verified on {merge_target}"
            else:
                cat    = issue.category or "unknown"
                reason = (issue.skip_reason or "no reason recorded").replace("|", "/")
            lines.append(f"| #{issue.number} | {issue.title} | {cat} | {reason} |")
    else:
        lines.append("| -- | All issues shipped | -- | -- |")
    lines.append("")

    # -- Suggested follow-up actions --
    if skipped:
        lines += ["## Suggested Follow-up Actions", ""]
        for issue in skipped:
            action = _follow_up_action(issue.category)
            lines.append(f"- **#{issue.number} {issue.title}** ({issue.category or 'unknown'}): {action}")
        lines.append("")

    # -- Dead Letter (issue #1942) --
    _dead_letter = getattr(state, "dead_letter", []) or []
    if _dead_letter:
        lines += [
            "## Dead Letter",
            "",
            "Tickets that exhausted all fix rounds:",
            "",
            "| Issue # | Title | Attempts | Last Error |",
            "|---|---|---|---|",
        ]
        for dl in _dead_letter:
            tid      = dl.get("ticket_id", "?")
            title    = dl.get("title", "")
            attempts = dl.get("attempts", 0)
            last_err = (dl.get("last_error") or "").replace("|", "/")
            if len(last_err) > 120:
                last_err = last_err[:120] + "…"
            lines.append(f"| #{tid} | {title} | {attempts} | {last_err} |")
        lines.append("")

    # -- Stats --
    cost_str = "$0.00 (all agents subscription-funded via Claude Code)"

    # Suite Health row (issue #888)
    if _health_result is None:
        _suite_health_row = "| Suite Health | ⚠ not recorded |"
    elif _health_result.timed_out:
        _suite_health_row = "| Suite Health | ⚠ timeout |"
    elif _health_result.failed > 0:
        _suite_health_row = (
            f"| Suite Health | ⚠ {_health_result.failed} failed,"
            f" {_health_result.passed} passed,"
            f" {_health_result.skipped} skipped,"
            f" {_health_result.duration_seconds}s |"
        )
    else:
        _suite_health_row = (
            f"| Suite Health | ✅ {_health_result.passed} passed,"
            f" {_health_result.skipped} skipped,"
            f" {_health_result.duration_seconds}s |"
        )

    lines += [
        "## Stats",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total Tokens | {total_tokens} |",
        f"| Avg ticket time | {avg_ticket_str} |",
        f"| Quality-gate pass rate | {gate_pass_rate}% |",
        f"| Tester rejections | {tester_rejections} |",
        f"| Merge conflicts | {merge_conflicts} |",
        f"| Cost estimate | {cost_str} |",
        _suite_health_row,
        "",
    ]

    # -- Per-Agent Durations (issue #764) --
    # Per-ticket coder/tester wall-clock durations from the sprint state
    # timestamps, plus totals per agent. Surfaces the per-agent resolution that
    # the blended actual_elapsed_seconds metric collapses.
    def _secs_between(start: Optional[str], end: Optional[str]) -> Optional[int]:
        if not start or not end:
            return None
        try:
            s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return round((e - s).total_seconds())

    def _fmt_dur(secs: Optional[int]) -> str:
        if secs is None:
            return "--"
        mm, ss = divmod(int(secs), 60)
        return f"{mm}m {ss}s"

    agent_rows = []
    coder_total = 0
    tester_total = 0
    for issue in state.issues:
        c_secs = _secs_between(
            getattr(issue, "coder_started_at", None),
            getattr(issue, "coder_finished_at", None),
        )
        t_secs = _secs_between(
            getattr(issue, "tester_started_at", None),
            getattr(issue, "tester_finished_at", None),
        )
        if c_secs is None and t_secs is None:
            continue
        if c_secs is not None:
            coder_total += c_secs
        if t_secs is not None:
            tester_total += t_secs
        agent_rows.append(
            f"| #{issue.number} | {_fmt_dur(c_secs)} | {_fmt_dur(t_secs)} |"
        )

    lines += ["## Per-Agent Durations", ""]
    if agent_rows:
        lines += [
            "| Issue # | Coder | Tester |",
            "|---|---|---|",
        ]
        lines += agent_rows
        lines.append(
            f"| **Totals** | **{_fmt_dur(coder_total)}** | **{_fmt_dur(tester_total)}** |"
        )
    else:
        lines.append("_No per-agent timing recorded this sprint._")
    lines.append("")

    # -- Rate Limit Events --
    if state.rate_limit_events:
        lines += ["## Rate Limit Events", ""]
        lines += [
            "| Issue # | Role | Attempt | Delay (s) | Exhausted | Timestamp |",
            "|---|---|---|---|---|---|",
        ]
        for ev in state.rate_limit_events:
            exhausted = "Yes" if ev.get("exhausted") else "No"
            lines.append(
                f"| #{ev.get('issue_num', '?')} "
                f"| {ev.get('role', '?')} "
                f"| {ev.get('attempt', '?')}/{_RATE_LIMIT_MAX_RETRIES} "
                f"| {ev.get('delay_secs', '?')} "
                f"| {exhausted} "
                f"| {ev.get('timestamp', '?')} |"
            )
        lines.append("")

    # -- Screenshots (issue #712) --
    if sprints_dir is not None:
        screenshots_block = _build_screenshots_section(state, sprints_dir, repo_name)
        if screenshots_block:
            lines += screenshots_block

    # -- Carried Over --
    lines += ["## Carried Over", ""]
    carried_items: list[str] = []
    for issue in pending:
        carried_items.append(f"- #{issue.number} {issue.title} -- candidate for next sprint")
    for issue in (open_issues or []):
        num   = issue.get("number", "?")
        title = issue.get("title", "")
        carried_items.append(f"- #{num} {title} -- candidate for next sprint")
    if carried_items:
        lines.extend(carried_items)
    else:
        lines.append("No issues carried over.")
    lines.append("")

    # -- Key Learnings --
    lines += [
        "## Key Learnings",
        "",
        LEARNINGS_STUB,
        "",
    ]

    # -- Links --
    all_links: list[str] = [
        f"- [Sprint {n} issues on GitHub]({sprint_filter_url})",
    ]
    for issue in state.issues:
        link = f"https://github.com/{r}/issues/{issue.number}"
        all_links.append(f"- [Issue #{issue.number} -- {issue.title[:50]}]({link})")

    lines += ["## Links", ""]
    if len(all_links) > 3:
        lines.append("<details>")
        lines.append(f"<summary>{len(all_links)} links -- click to expand</summary>")
        lines.append("")
        lines.extend(all_links)
        lines.append("")
        lines.append("</details>")
    else:
        lines.extend(all_links)
    lines.append("")

    # -- Next Step (sprint branch PR instructions) --
    effective_sprint_branch = sprint_branch or f"sprint/{state.sprint_label}"
    r = _r(repo_name)
    lines += [
        "## Next Step",
        "",
        f"The sprint branch `{effective_sprint_branch}` is ready for review.",
        "When UAT is complete, open a PR to promote it to `develop`:",
        "",
        "```bash",
        f"gh pr create --base develop --head {effective_sprint_branch} --repo {r}",
        "```",
        "",
    ]

    # -- Footer --
    lines.append(f"_Generated by sprint-manager v1.0 on {_bangkok_now()}_")

    return "\n".join(lines)


def create_summary_github_issue(
    content: str,
    sprint_number: Optional[int],
    sprint_label: str,
    repo_name: Optional[str] = None,
    force_summary: bool = False,
    state_file_path: Optional[Path] = None,
) -> tuple[Optional[int], Optional[str]]:
    """AC-2: Create a GitHub issue with the summary markdown as the body.

    AC-1/AC-6: Before creating, searches GitHub (open + closed) for an issue
    with the exact title.  If one already exists and is stale (or force_summary
    is set), updates it in place.  Otherwise skips creation (AC-2).
    If none exists, creates the issue (AC-3).

    state_file_path: optional path to the sprint state JSON file; when provided
    its mtime is compared to the existing issue's createdAt to detect staleness
    (AC-2: state-file timestamp check).
    """
    github_client = _sm_ref.github_client
    _ensure_github_labels = _sm_ref._ensure_github_labels
    _summary_sprint_display = _sm_ref._summary_sprint_display

    n      = _summary_sprint_display(sprint_label, sprint_number)
    title  = f"Sprint {n} Executive Summary"
    labels = ["docs", f"sprint-{n}", "sprint-summary"]

    # AC-1 / AC-6: deduplication check — search both open and closed states
    try:
        existing = github_client.search_issues_by_title(title, repo_name=repo_name)
    except Exception as e:
        structured_log.warn("dedup_search_failed", f"deduplication search failed: {e}", exc=str(e))
        existing = []

    if existing:
        found       = existing[0]
        existing_num = found.get("number")
        existing_url = found.get("url", "")
        existing_state = found.get("state", "")

        # Fetch full issue to get body and stateReason for staleness check
        full_issue: dict = {}
        try:
            full_issue = github_client.get_issue(existing_num, repo_name=repo_name)
        except Exception as e:
            structured_log.warn("summary_issue_fetch_failed", f"could not fetch existing summary issue body: {e}", exc=str(e))

        # Compute state-file mtime for the timestamp staleness check (best-effort)
        state_file_mtime: Optional[float] = None
        if state_file_path is not None:
            try:
                state_file_mtime = state_file_path.stat().st_mtime
            except OSError:
                pass

        is_stale, stale_reason = _is_stale_summary(
            body             = full_issue.get("body", ""),
            state_reason     = full_issue.get("stateReason"),
            state_file_mtime = state_file_mtime,
            issue_created_at = full_issue.get("createdAt"),
        )

        if force_summary or is_stale:
            action = "--force-summary" if force_summary else f"stale ({stale_reason})"
            sys.stdout.write(str(f"  [summary] Existing issue #{existing_num} is {action} — updating in place.") + "\n")
            try:
                github_client.update_issue_body(existing_num, content, repo_name=repo_name)
            except Exception as e:
                structured_log.warn("summary_issue_update_failed", f"failed to update summary issue body: {e}", exc=str(e))
            if existing_state == "closed":
                try:
                    github_client.reopen_issue(existing_num, repo_name=repo_name)
                    sys.stdout.write(str(f"  [summary] Reopened issue #{existing_num}.") + "\n")
                except Exception as e:
                    structured_log.warn("summary_issue_reopen_failed", f"failed to reopen summary issue: {e}", exc=str(e))
            comment = (
                f"Summary updated after fresh sprint run on {_utcnow()}. "
                f"Previous content was from a failed run."
            )
            try:
                github_client.add_comment(existing_num, comment, repo_name=repo_name)
            except Exception as e:
                structured_log.warn("summary_comment_failed", f"failed to add update comment: {e}", exc=str(e))
            return existing_num, existing_url

        # Valid existing summary — skip creation
        sys.stdout.write(str(f"  [summary] Issue already exists: #{existing_num} {existing_url}"
            f" (state={existing_state}) — skipping creation.") + "\n")
        return existing_num, existing_url

    # AC-3: no duplicate found — create as normal
    # Ensure sprint-summary label exists with muted indigo color
    try:
        r = _sm_ref._r(repo_name)
        subprocess.run(
            ["gh", "label", "create", "sprint-summary",
             "--color", "6D28D9",
             "--description", "Sprint executive summary issues",
             "--repo", r, "--force"],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        pass
    _ensure_github_labels(labels, repo_name=repo_name)

    # Retry the create on transient gh failures — a single failed `gh` call here
    # (e.g. a flaky `gh issue list`/`create`) is what dropped sprint 79's summary
    # issue, leaving the sprint stuck on the board (no cross-machine finished
    # signal). Best-effort still: give up to 3 attempts before returning None.
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            issue_num, url = github_client.create_issue(
                title=title, body=content, labels=labels, repo_name=repo_name
            )
            sys.stdout.write(str(f"  Summary GitHub issue created: {url}") + "\n")
            return issue_num, url
        except Exception as e:
            last_exc = e
            if attempt < 2:
                sys.stdout.write(str(
                    f"  [summary] create attempt {attempt + 1} failed ({e}); retrying...") + "\n")
                sys.stdout.flush()
                time.sleep(2 * (attempt + 1))
    structured_log.warn(
        "summary_issue_create_failed",
        f"failed to create summary GitHub issue after 3 attempts: {last_exc}",
        exc=str(last_exc),
    )
    return None, None


def _prompt_learnings(
    content: str,
    path: Path,
    sprint_number: Optional[int],
    sprint_label: str,
    summary_issue_num: Optional[int],
    repo_name: Optional[str] = None,
) -> str:
    """AC-3: Interactive learnings prompt."""
    _r = _sm_ref._r

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
        structured_log.warn("editor_failed", f"editor failed: {e}", exc=str(e))
        new_learnings = ""
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    if not new_learnings or new_learnings == LEARNINGS_STUB.strip():
        sys.stdout.write(str("  No learnings entered; keeping placeholder.") + "\n")
        return content

    updated = content.replace(LEARNINGS_STUB, new_learnings)

    path.write_text(updated, encoding="utf-8")
    sys.stdout.write(str(f"  Key Learnings updated in {path}") + "\n")

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
            sys.stdout.write(str(f"  GitHub issue #{summary_issue_num} body updated with learnings.") + "\n")
        except Exception as e:
            structured_log.warn("github_update_failed", f"failed to update GitHub issue body: {e}", exc=str(e))
        finally:
            try:
                tmp2.unlink()
            except Exception:
                pass

    return updated


def write_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    alert_modes: Optional[list[str]] = None,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
    sprint_branch: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    dry_run: bool = False,
    force_summary: bool = False,
    merge_target: Optional[str] = None,
    run_health_gate: bool = False,
) -> Optional[Path]:
    """Write summary file, create GitHub issue, prompt for learnings (AC-1/2/3).

    AC-5: When dry_run=True the function writes the local summary file and
    prints a dry-run notice, but does NOT create or search GitHub issues.

    Returns None when end_reason is "cancelled" (issue #365 AC-3/AC-5).

    When merge_target is provided, git-verified counts are used in the summary
    and any done-but-unverified tickets are flagged in the not-shipped table.

    ``run_health_gate=True`` (issue #888): triggers the full pytest suite
    before generating the summary so health metrics appear in the Stats table.
    """
    create_summary_github_issue = _sm_ref.create_summary_github_issue
    _log_shipped_status_git_mismatch = _sm_ref._log_shipped_status_git_mismatch
    _fail_loud_shipped_reconciliation = _sm_ref._fail_loud_shipped_reconciliation

    eff_repo = repo_name or (cfg.repo_name if cfg else None)

    # Guard: skip local file and GitHub issue for cancelled sprints (issue #365 AC-3, AC-5).
    if end_reason == "cancelled":
        sys.stdout.write(str("  [cancel] Sprint was cancelled — skipping summary file and GitHub issue.") + "\n")
        return None

    if merge_target:
        _log_shipped_status_git_mismatch(state, merge_target)
        _fail_loud_shipped_reconciliation(state, merge_target, "Sprint summary")

    eff_sprints_dir: Path = cfg.sprints_dir if cfg is not None else SPRINTS_DIR

    # AC-1 (issue #888): auto-run suite health gate when requested.
    if run_health_gate and _SUITE_HEALTH_AVAILABLE:
        sys.stdout.write(str("  [health-gate] Running full test suite...") + "\n")
        try:
            _suite_health_gate.run_gate(
                sprint_label=state.sprint_label,
                sprints_dir=eff_sprints_dir,
            )
            sys.stdout.write(str("  [health-gate] Done.") + "\n")
        except Exception as _hg_exc:
            structured_log.warn(
                "health_gate_failed",
                f"suite health gate raised: {_hg_exc}",
                exc=str(_hg_exc),
                sprint_label=state.sprint_label,
            )

    content = generate_sprint_summary(
        state,
        elapsed_secs,
        end_reason=end_reason,
        open_issues=open_issues,
        repo_name=eff_repo,
        sprint_branch=sprint_branch,
        sprints_dir=eff_sprints_dir,
        merge_target=merge_target,
    )
    path = _summary_path(state.sprint_number, state.sprint_label, cfg=cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    sys.stdout.write(str(f"  Sprint summary written to {path}") + "\n")

    # Dispatch via all configured alert channels (issue #24)
    if alert_modes:
        title = f"Sprint {state.sprint_label} summary"
        dispatch_alerts(alert_modes, title=title, body=content[:2000], cfg=cfg, repo=eff_repo,
                        sprint_label=state.sprint_label)

    # AC-5: skip GitHub issue creation entirely for dry runs
    if dry_run:
        sys.stdout.write(str("  [dry-run] would create summary GitHub issue") + "\n")
        return path

    # AC-2: Create GitHub issue (best-effort); deduplication handled inside
    state_path = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
    try:
        summary_issue_num, summary_issue_url = create_summary_github_issue(
            content         = content,
            sprint_number   = state.sprint_number,
            sprint_label    = state.sprint_label,
            repo_name       = eff_repo,
            force_summary   = force_summary,
            state_file_path = state_path,
        )
    except Exception as exc:
        structured_log.warn("summary_issue_create_failed", f"create_summary_github_issue raised: {exc}", exc=str(exc))
        summary_issue_num, summary_issue_url = None, None

    # Store summary_issue_url in state JSON and in-memory state (final save uses to_dict).
    if summary_issue_url:
        state.summary_issue_url = summary_issue_url
        try:
            if state_path.exists():
                state_dict = json.loads(state_path.read_text())
            else:
                state_dict = state.to_dict()
            state_dict["summary_issue_url"] = summary_issue_url
            state_path.write_text(json.dumps(state_dict, indent=2))
        except Exception as e:
            structured_log.warn("state_file_update_failed", f"could not update state file with summary_issue_url: {e}", exc=str(e))

    # AC-3: Interactive learnings prompt
    _prompt_learnings(
        content=content,
        path=path,
        sprint_number=state.sprint_number,
        sprint_label=state.sprint_label,
        summary_issue_num=summary_issue_num,
        repo_name=eff_repo,
    )

    return path
