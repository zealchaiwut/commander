#!/usr/bin/env python3
"""Sprint Pre-flight Review — BA sanity-check pass before sprint-run dispatches agents.

For each open backlog issue in a sprint, calls the Anthropic API concurrently to
review the issue for staleness, conflicts, vague AC, and missing info. Prints a
structured report and presents an interactive prompt when stdout is a TTY.

Usage (standalone):
    python3 dashboard/scripts/sprint_review.py sprint-3
    python3 dashboard/scripts/sprint_review.py sprint-3 --repo owner/repo

Usage (via sprint_manager.py):
    python3 dashboard/scripts/sprint_manager.py sprint-3 --preflight

Run from the git root of the repository (NOT from dashboard/).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── path setup ────────────────────────────────────────────────────────────────

SCRIPTS_DIR   = Path(__file__).parent
DASHBOARD_DIR = SCRIPTS_DIR.parent
SPRINTS_DIR   = DASHBOARD_DIR / "sprints"

sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(DASHBOARD_DIR / ".env")

import github_client  # noqa: E402


# ── constants ─────────────────────────────────────────────────────────────────

MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_WORKERS = 5

# Vague-phrase patterns that trigger needs-update
VAGUE_PHRASES = [
    r"works correctly",
    r"handles errors",
    r"is fast",
    r"works as expected",
    r"handles edge cases",
    r"properly",
    r"efficiently",
]

# File path token pattern (AC-3: conflict detection)
FILE_PATH_PATTERN = re.compile(r"[\w/.-]+\.(?:py|js|html|sh|md|json)\b")

# Status symbols for report
SYMBOLS = {
    "ok":           "✅",
    "stale":        "⚠ ",
    "conflict":     "🔴",
    "needs-update": "⚠ ",
    "missing-info": "❓",
}

# Token budget per issue (default M=40k tokens)
TOKENS_PER_ISSUE = 40_000


# ── review data structures ────────────────────────────────────────────────────

class ReviewResult:
    """Result of reviewing a single issue."""

    def __init__(
        self,
        number: int,
        title: str,
        status: str,
        notes: str,
        suggested_action: str,
    ) -> None:
        self.number           = number
        self.title            = title
        self.status           = status
        self.notes            = notes
        self.suggested_action = suggested_action

    def to_dict(self) -> dict:
        return {
            "number":           self.number,
            "title":            self.title,
            "status":           self.status,
            "notes":            self.notes,
            "suggested_action": self.suggested_action,
        }


# ── Anthropic API call (raw HTTP, no SDK) ─────────────────────────────────────

def _call_anthropic(api_key: str, prompt: str) -> dict:
    """Call the Anthropic Messages API and return the parsed JSON response dict.

    Returns a dict with keys: status, notes, suggested_action.
    Raises urllib.error.HTTPError or ValueError on failure.
    """
    payload = {
        "model": MODEL,
        "max_tokens": 256,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        headers={
            "Content-Type":         "application/json",
            "x-api-key":            api_key,
            "anthropic-version":    ANTHROPIC_VERSION,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")

    data    = json.loads(raw)
    content = data.get("content", [{}])[0].get("text", "").strip()

    # Extract JSON from the response (model may wrap it in backticks)
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if not json_match:
        raise ValueError(f"No JSON object in API response: {content!r}")

    result = json.loads(json_match.group(0))

    # Validate required fields
    valid_statuses = {"ok", "stale", "conflict", "needs-update", "missing-info"}
    valid_actions  = {"proceed", "rewrite", "skip", "reorder"}
    if result.get("status") not in valid_statuses:
        result["status"] = "ok"
    if result.get("suggested_action") not in valid_actions:
        result["suggested_action"] = "proceed"
    if "notes" not in result:
        result["notes"] = ""

    return result


# ── pre-classification helpers (AC-3) ─────────────────────────────────────────

def _is_closed_issue(issue_num: int, repo_name: Optional[str]) -> bool:
    """Check if a referenced issue is closed via gh CLI."""
    r = github_client._r(repo_name)
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(issue_num), "--repo", r, "--json", "state"],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        return data.get("state", "open").lower() == "closed"
    except Exception:
        return False


def _detect_stale(body: str, repo_name: Optional[str]) -> Optional[str]:
    """Return a note if the body references a closed issue number, else None."""
    refs = re.findall(r"#(\d+)", body)
    for ref_str in refs:
        ref_num = int(ref_str)
        if _is_closed_issue(ref_num, repo_name):
            return f"references #{ref_num} (closed)"
    return None


def _detect_conflict(body: str, other_bodies: list[tuple[int, str]]) -> Optional[str]:
    """Return a note if body shares file paths with another sprint issue, else None."""
    my_files = set(FILE_PATH_PATTERN.findall(body))
    for other_num, other_body in other_bodies:
        other_files = set(FILE_PATH_PATTERN.findall(other_body))
        overlap     = my_files & other_files
        if overlap:
            sample = next(iter(overlap))
            return f"overlaps {sample} with #{other_num}"
    return None


def _detect_needs_update(body: str) -> Optional[str]:
    """Return a note if the AC is vague or has < 2 checkbox items."""
    # Check for AC section
    ac_match = re.search(r"^#+\s+Acceptance Criteria", body, re.MULTILINE | re.IGNORECASE)
    if not ac_match:
        return None  # missing-info takes precedence, handled separately

    # Count checkbox items
    ac_start = ac_match.end()
    next_section = re.search(r"^#+\s+", body[ac_start:], re.MULTILINE)
    ac_body = body[ac_start: ac_start + next_section.start()] if next_section else body[ac_start:]
    checkboxes = re.findall(r"^\s*-\s+\[[ x]\]", ac_body, re.MULTILINE)
    if len(checkboxes) < 2:
        return f"AC section has only {len(checkboxes)} checkbox item(s)"

    # Check for vague phrases
    for phrase in VAGUE_PHRASES:
        if re.search(phrase, ac_body, re.IGNORECASE):
            return f'contains vague phrase: "{phrase}"'

    return None


def _detect_missing_info(body: str) -> Optional[str]:
    """Return a note if no AC section is found."""
    if not re.search(r"^#+\s+Acceptance Criteria", body, re.MULTILINE | re.IGNORECASE):
        return "no Acceptance Criteria section found"
    return None


def _pre_classify(
    issue: dict,
    all_issues: list[dict],
    repo_name: Optional[str],
) -> tuple[str, str]:
    """Run deterministic checks and return (status, notes) before calling the API.

    Returns ("", "") if no pre-classification applies (let the API decide).
    """
    body       = issue.get("body") or ""
    issue_num  = issue["number"]

    # missing-info (highest priority)
    note = _detect_missing_info(body)
    if note:
        return "missing-info", note

    # stale
    note = _detect_stale(body, repo_name)
    if note:
        return "stale", note

    # conflict — compare against all other issues
    other_bodies = [
        (other["number"], other.get("body") or "")
        for other in all_issues
        if other["number"] != issue_num
    ]
    note = _detect_conflict(body, other_bodies)
    if note:
        return "conflict", note

    # needs-update
    note = _detect_needs_update(body)
    if note:
        return "needs-update", note

    return "", ""


# ── build review prompt (AC-2) ────────────────────────────────────────────────

def _build_prompt(issue: dict, sprint_issues: list[dict]) -> str:
    """Build the structured review prompt for a single issue."""
    cross_refs = "\n".join(
        f"  - #{i['number']}: {i['title']}"
        for i in sprint_issues
        if i["number"] != issue["number"]
    )

    body_excerpt = (issue.get("body") or "")[:3000]

    return f"""You are a BA reviewing a GitHub issue for sprint readiness.

Issue #{issue['number']}: {issue['title']}

Body (truncated to 3000 chars):
{body_excerpt}

Other sprint issues (for cross-issue context):
{cross_refs or '  (none)'}

Review this issue and respond with ONLY a JSON object using this exact schema:
{{"status": "ok|stale|conflict|needs-update|missing-info", "notes": "<one-sentence explanation>", "suggested_action": "proceed|rewrite|skip|reorder"}}

Status definitions:
- ok: issue is clear, actionable, and ready for a coder
- stale: body references a #N issue number that is now closed, or mentions a feature/file that has since been merged
- conflict: body references file paths that also appear in another sprint issue's body
- needs-update: AC section present but contains vague phrases ("works correctly", "handles errors", "is fast") or has fewer than 2 checkbox items
- missing-info: no "## Acceptance Criteria" section found in body

Respond with ONLY the JSON object, no other text."""


# ── review a single issue ─────────────────────────────────────────────────────

def review_issue(
    issue: dict,
    all_issues: list[dict],
    api_key: str,
    repo_name: Optional[str],
) -> ReviewResult:
    """Review a single issue, using pre-classification + API fallback."""
    number = issue["number"]
    title  = issue["title"]

    # Try deterministic pre-classification first (fast, no API call)
    pre_status, pre_notes = _pre_classify(issue, all_issues, repo_name)

    # Map pre-classified status to a suggested_action
    action_map = {
        "stale":        "rewrite",
        "conflict":     "reorder",
        "needs-update": "rewrite",
        "missing-info": "skip",
    }

    if pre_status:
        return ReviewResult(
            number           = number,
            title            = title,
            status           = pre_status,
            notes            = pre_notes,
            suggested_action = action_map.get(pre_status, "skip"),
        )

    # Fall back to API for nuanced review
    prompt = _build_prompt(issue, all_issues)
    try:
        result = _call_anthropic(api_key, prompt)
        return ReviewResult(
            number           = number,
            title            = title,
            status           = result["status"],
            notes            = result["notes"],
            suggested_action = result["suggested_action"],
        )
    except Exception as exc:
        # On API failure, default to ok so the sprint can proceed
        return ReviewResult(
            number           = number,
            title            = title,
            status           = "ok",
            notes            = f"API review failed: {exc}",
            suggested_action = "proceed",
        )


# ── concurrent review (AC-4) ─────────────────────────────────────────────────

def run_reviews(
    issues: list[dict],
    api_key: str,
    repo_name: Optional[str],
) -> list[ReviewResult]:
    """Review all issues concurrently using a ThreadPoolExecutor."""
    results: dict[int, ReviewResult] = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_num = {
            pool.submit(review_issue, issue, issues, api_key, repo_name): issue["number"]
            for issue in issues
        }
        for future in as_completed(future_to_num):
            num = future_to_num[future]
            try:
                results[num] = future.result()
            except Exception as exc:
                # Should not normally reach here since review_issue catches internally
                results[num] = ReviewResult(
                    number           = num,
                    title            = next(i["title"] for i in issues if i["number"] == num),
                    status           = "ok",
                    notes            = f"review error: {exc}",
                    suggested_action = "proceed",
                )

    # Return in original issue order
    num_order = [i["number"] for i in issues]
    return [results[n] for n in num_order if n in results]


# ── report printing (AC-5) ────────────────────────────────────────────────────

def _format_title(title: str, max_len: int = 30) -> str:
    """Truncate title to max_len chars."""
    if len(title) <= max_len:
        return title
    return title[:max_len - 1] + "…"


def print_report(sprint_label: str, results: list[ReviewResult]) -> None:
    """Print the formatted pre-sprint review report to stdout."""
    n = len(results)
    print(f"\n{'═' * 50}")
    print(f"Pre-sprint review: {sprint_label} · {n} issue{'s' if n != 1 else ''}")
    print(f"{'═' * 50}\n")

    for r in results:
        symbol = SYMBOLS.get(r.status, "  ")
        title_col  = _format_title(r.title, 32)
        status_col = r.status.ljust(14)
        action_col = f"→ {r.suggested_action}"
        print(f"  {symbol}  #{r.number:<4} {title_col:<32} {status_col} {action_col}")
        if r.status != "ok" and r.notes:
            print(f"            {r.notes}")

    print()

    # Summary line
    counts = _count_statuses(results)
    parts  = []
    for key in ("ok", "stale", "conflict", "needs-update", "missing-info"):
        val = counts.get(key, 0)
        if val:
            parts.append(f"{val} {key}")
    print(f"  Summary: {' · '.join(parts) if parts else 'no issues'}")

    # Token budget estimate
    total_tokens = n * TOKENS_PER_ISSUE
    print(f"  Est. token budget: {total_tokens // 1000} k ({n} issue{'s' if n != 1 else ''} at default M={TOKENS_PER_ISSUE // 1000} k each)")
    print()


def _count_statuses(results: list[ReviewResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


# ── interactive prompt (AC-6, AC-7) ──────────────────────────────────────────

def prompt_user(results: list[ReviewResult]) -> list[ReviewResult]:
    """Show interactive prompt and return the list of issues to include in the run.

    AC-7: When stdout is not a TTY, auto-selects 'Approve all' and returns all issues.
    AC-6: When stdout is a TTY, shows prompt with A/S/E/Q options.
    Exits with code 1 on Q.
    """
    if not sys.stdout.isatty():
        print("[preflight] Non-interactive mode: auto-approving all issues.")
        return results

    print("  Proceed? [A]pprove all  [S]kip flagged  [E]dit per-issue  [Q]uit")
    try:
        choice = input("  > ").strip().upper()
    except (EOFError, KeyboardInterrupt):
        choice = "Q"

    if choice == "A":
        return results

    elif choice == "S":
        kept    = [r for r in results if r.status == "ok"]
        skipped = [r for r in results if r.status != "ok"]
        if skipped:
            print(f"\n  Skipping {len(skipped)} flagged issue(s): "
                  + ", ".join(f"#{r.number}" for r in skipped))
        return kept

    elif choice == "E":
        kept = []
        for r in results:
            if r.status == "ok":
                kept.append(r)
                continue
            try:
                answer = input(f"  Skip #{r.number} ({r.title[:40]})? [y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "y"
            if answer != "y":
                kept.append(r)
            else:
                print(f"  Skipping #{r.number}")
        return kept

    else:  # Q or anything else
        print("Preflight aborted.")
        sys.exit(1)


# ── JSON output (AC-8) ───────────────────────────────────────────────────────

def _sprint_number_from_label(sprint_label: str) -> str:
    """Extract the sprint number/suffix from a sprint label like 'sprint-3'."""
    m = re.search(r"(\d+)", sprint_label)
    return m.group(1) if m else sprint_label


def write_preflight_json(
    sprint_label: str,
    results: list[ReviewResult],
    sprints_dir: Optional[Path] = None,
) -> Path:
    """Write the preflight JSON file and return its path."""
    out_dir = sprints_dir or SPRINTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    n        = _sprint_number_from_label(sprint_label)
    today    = datetime.now().strftime("%Y-%m-%d")
    out_path = out_dir / f"sprint-{n}-preflight-{today}.json"

    counts = _count_statuses(results)
    data   = {
        "sprint_label": sprint_label,
        "reviewed_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issues":       [r.to_dict() for r in results],
        "summary": {
            "ok":           counts.get("ok", 0),
            "stale":        counts.get("stale", 0),
            "conflict":     counts.get("conflict", 0),
            "needs_update": counts.get("needs-update", 0),
            "missing_info": counts.get("missing-info", 0),
        },
    }

    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


# ── main review function (shared between standalone + sprint_manager) ─────────

def run_preflight(
    sprint_label: str,
    repo_name: Optional[str]  = None,
    sprints_dir: Optional[Path] = None,
    interactive: bool = True,
) -> tuple[list[ReviewResult], list[ReviewResult]]:
    """Run the preflight review and return (all_results, approved_issues).

    all_results    — every issue that was reviewed
    approved_issues — issues the user approved to run (may be a subset)

    AC-9: If ANTHROPIC_API_KEY is unset/empty, prints warning and returns
    all issues as approved (no API calls made).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print(
            "[preflight] ANTHROPIC_API_KEY not set — skipping review, proceeding with all issues"
        )
        # Fetch issues to build the approved list even without API
        issues = _fetch_sprint_issues(sprint_label, repo_name)
        pseudo = [
            ReviewResult(
                number=i["number"],
                title=i["title"],
                status="ok",
                notes="review skipped (no API key)",
                suggested_action="proceed",
            )
            for i in issues
        ]
        _write_and_log(sprint_label, pseudo, sprints_dir)
        return pseudo, pseudo

    # Fetch backlog issues
    issues = _fetch_sprint_issues(sprint_label, repo_name)
    if not issues:
        print(f"[preflight] No open backlog issues found for {sprint_label!r}.")
        return [], []

    print(f"[preflight] Reviewing {len(issues)} issue(s) for {sprint_label!r} ...")

    # Concurrent review
    results = run_reviews(issues, api_key, repo_name)

    # Print report
    print_report(sprint_label, results)

    # Write JSON (AC-8) — always written regardless of TTY/prompt
    out_path = _write_and_log(sprint_label, results, sprints_dir)
    print(f"[preflight] Review written to {out_path}")

    # Interactive prompt (AC-6/7)
    if interactive:
        approved = prompt_user(results)
    else:
        approved = results

    return results, approved


def _fetch_sprint_issues(sprint_label: str, repo_name: Optional[str]) -> list[dict]:
    """Fetch open backlog issues with the sprint label."""
    r = github_client._r(repo_name)
    try:
        out = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo",  r,
                "--label", sprint_label,
                "--state", "open",
                "--json",  "number,title,labels,body",
                "--limit", "200",
            ],
            capture_output=True, text=True, check=True,
        )
        all_issues = json.loads(out.stdout)
    except Exception as e:
        print(f"[preflight] Warning: could not fetch issues — {e}", file=sys.stderr)
        return []

    # Filter to backlog only
    result = []
    for issue in all_issues:
        labels = {lbl["name"] for lbl in issue.get("labels", [])}
        # Classify: backlog = not in-progress / SIT / UAT / UAT-approved
        status_labels = {"in-progress", "SIT", "UAT", "UAT-approved", "needs-rework", "blocked"}
        if not (labels & status_labels):
            result.append(issue)
    return sorted(result, key=lambda i: i["number"])


def _write_and_log(
    sprint_label: str,
    results: list[ReviewResult],
    sprints_dir: Optional[Path],
) -> Path:
    """Write preflight JSON and return the path."""
    out_path = write_preflight_json(sprint_label, results, sprints_dir)
    return out_path


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Pre-flight review: BA sanity-check before sprint dispatch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "sprint_label",
        metavar="<sprint-label>",
        help="GitHub sprint label to review (e.g. sprint-3)",
    )
    p.add_argument(
        "--repo",
        default=None,
        metavar="owner/repo",
        help="GitHub repo override (default: auto-detect from git remote)",
    )

    args = p.parse_args()

    run_preflight(
        sprint_label = args.sprint_label,
        repo_name    = args.repo,
        interactive  = True,
    )


if __name__ == "__main__":
    main()
