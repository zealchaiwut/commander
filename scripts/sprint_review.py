#!/usr/bin/env python3
"""Sprint Pre-flight Review — BA sanity-check pass before sprint-run dispatches agents.

For each open backlog issue in a sprint, spawns a single claude CLI subprocess to
review all issues in one pass for staleness, conflicts, vague AC, and missing info.
Prints a structured report and presents an interactive prompt when stdout is a TTY.

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
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── path setup ────────────────────────────────────────────────────────────────

SCRIPTS_DIR   = Path(__file__).parent
REPO_ROOT     = SCRIPTS_DIR.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINTS_DIR   = DASHBOARD_DIR / "sprints"

sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(DASHBOARD_DIR / ".env")

import github_client  # noqa: E402


# ── constants ─────────────────────────────────────────────────────────────────

PREFLIGHT_TIMEOUT_SEC = int(os.environ.get("PREFLIGHT_TIMEOUT_SEC", "120"))

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

DEFAULT_PREFLIGHT_PROMPT = """\
You are a BA reviewing GitHub issues for sprint readiness. Review ALL issues below in one pass.

For each issue, check:
- ok: issue is clear, actionable, and ready for a coder
- stale: body references a #N issue number that is now closed, or mentions a feature/file that has since been merged
- conflict: body references file paths that also appear in another sprint issue's body
- needs-update: AC section present but contains vague phrases ("works correctly", "handles errors", "is fast") or has fewer than 2 checkbox items
- missing-info: no "## Acceptance Criteria" section found in body

Suggested actions: proceed (ok), rewrite (stale/needs-update), reorder (conflict), skip (missing-info)

Sprint label: {sprint_label}
Repository: {repo}

Issues to review:
{issues_json}

Respond with ONLY a JSON object keyed by issue number (as string). Each value must have:
  "status": one of ok|stale|conflict|needs-update|missing-info
  "notes": one-sentence explanation
  "suggested_action": one of proceed|rewrite|skip|reorder

Example:
{{"42": {{"status": "ok", "notes": "clear AC, ready to implement", "suggested_action": "proceed"}}, "43": {{"status": "missing-info", "notes": "no Acceptance Criteria section found", "suggested_action": "skip"}}}}

Respond with ONLY the JSON object, no other text."""


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


# ── claude CLI subprocess agent call ─────────────────────────────────────────

def _spawn_preflight_agent(issues_json: str, repo: str, sprint_label: str) -> dict:
    """Spawn a single claude CLI call to review all issues in one pass.

    Returns a dict keyed by issue number (int) with {status, notes, suggested_action}.
    On timeout or parse failure, returns an empty dict (caller falls back to ok).
    """
    prompt = DEFAULT_PREFLIGHT_PROMPT.format(
        sprint_label=sprint_label,
        repo=repo,
        issues_json=issues_json,
    )

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    _model = "claude-haiku-4-5"
    try:
        from services.sprint_manager.model_routing import apply_provider_env
        _model = apply_provider_env(env, _model, repo=repo, role="preflight")
    except ImportError:
        pass  # standalone invocation without repo root on sys.path — direct Anthropic

    try:
        proc = subprocess.run(
            [
                "claude",
                "--model", _model,
                "--dangerously-skip-permissions",
                "-p", prompt,
            ],
            capture_output=True,
            text=True,
            timeout=PREFLIGHT_TIMEOUT_SEC,
            env=env,
        )
        stdout = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        sys.stderr.write(str(f"\n  WARNING: preflight agent timed out after {PREFLIGHT_TIMEOUT_SEC}s "
            "— all issues marked ok.") + "\n")
        return {}

    # Extract JSON object from output (model may wrap in backticks or add prose)
    json_match = re.search(r"\{.*\}", stdout, re.DOTALL)
    if not json_match:
        sys.stderr.write(str(f"\n  WARNING: preflight agent returned no JSON — all issues marked ok.\n"
            f"           stdout: {stdout[:200]!r}") + "\n")
        return {}

    try:
        raw = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        sys.stderr.write(str(f"\n  WARNING: preflight agent JSON parse error ({exc}) — all issues marked ok.") + "\n")
        return {}

    # Normalise: coerce string keys to int, validate status/action values
    valid_statuses = {"ok", "stale", "conflict", "needs-update", "missing-info"}
    valid_actions  = {"proceed", "rewrite", "skip", "reorder"}
    result: dict[int, dict] = {}
    for key, val in raw.items():
        try:
            num = int(key)
        except (ValueError, TypeError):
            continue
        if not isinstance(val, dict):
            continue
        status = val.get("status", "ok")
        action = val.get("suggested_action", "proceed")
        if status not in valid_statuses:
            status = "ok"
        if action not in valid_actions:
            action = "proceed"
        result[num] = {
            "status":           status,
            "notes":            val.get("notes", ""),
            "suggested_action": action,
        }
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
    """Run deterministic checks and return (status, notes) before calling the agent.

    Returns ("", "") if no pre-classification applies (let the agent decide).
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


# ── review all issues via agent (one call) ────────────────────────────────────

def run_reviews(
    issues: list[dict],
    repo_name: Optional[str],
    sprint_label: str,
) -> list[ReviewResult]:
    """Review all issues: deterministic pre-classification first, then a single agent call
    for any issues that pass pre-classification."""
    action_map = {
        "stale":        "rewrite",
        "conflict":     "reorder",
        "needs-update": "rewrite",
        "missing-info": "skip",
    }

    pre_classified: dict[int, ReviewResult] = {}
    needs_agent: list[dict] = []

    for issue in issues:
        pre_status, pre_notes = _pre_classify(issue, issues, repo_name)
        if pre_status:
            pre_classified[issue["number"]] = ReviewResult(
                number           = issue["number"],
                title            = issue["title"],
                status           = pre_status,
                notes            = pre_notes,
                suggested_action = action_map.get(pre_status, "skip"),
            )
        else:
            needs_agent.append(issue)

    agent_results: dict[int, dict] = {}
    if needs_agent:
        issues_for_agent = [
            {
                "number": i["number"],
                "title":  i["title"],
                "body":   (i.get("body") or "")[:3000],
            }
            for i in needs_agent
        ]
        repo = github_client._r(repo_name)
        agent_results = _spawn_preflight_agent(
            json.dumps(issues_for_agent, indent=2),
            repo,
            sprint_label,
        )

    results: dict[int, ReviewResult] = dict(pre_classified)
    for issue in needs_agent:
        num   = issue["number"]
        title = issue["title"]
        if num in agent_results:
            r = agent_results[num]
            results[num] = ReviewResult(
                number           = num,
                title            = title,
                status           = r["status"],
                notes            = r["notes"],
                suggested_action = r["suggested_action"],
            )
        else:
            # Agent returned nothing for this issue — approve with skip-review note
            results[num] = ReviewResult(
                number           = num,
                title            = title,
                status           = "ok",
                notes            = "review skipped (claude CLI not found)",
                suggested_action = "proceed",
            )

    # Return in original issue order
    return [results[i["number"]] for i in issues if i["number"] in results]


# ── report printing (AC-5) ────────────────────────────────────────────────────

def _format_title(title: str, max_len: int = 30) -> str:
    """Truncate title to max_len chars."""
    if len(title) <= max_len:
        return title
    return title[:max_len - 1] + "…"


def print_report(sprint_label: str, results: list[ReviewResult]) -> None:
    """Print the formatted pre-sprint review report to stdout."""
    n = len(results)
    sys.stdout.write(str(f"\n{'═' * 50}") + "\n")
    sys.stdout.write(str(f"Pre-sprint review: {sprint_label} · {n} issue{'s' if n != 1 else ''}") + "\n")
    sys.stdout.write(str(f"{'═' * 50}\n") + "\n")

    for r in results:
        symbol = SYMBOLS.get(r.status, "  ")
        title_col  = _format_title(r.title, 32)
        status_col = r.status.ljust(14)
        action_col = f"→ {r.suggested_action}"
        sys.stdout.write(str(f"  {symbol}  #{r.number:<4} {title_col:<32} {status_col} {action_col}") + "\n")
        if r.status != "ok" and r.notes:
            sys.stdout.write(str(f"            {r.notes}") + "\n")

    sys.stdout.write("\n")

    # Summary line
    counts = _count_statuses(results)
    parts  = []
    for key in ("ok", "stale", "conflict", "needs-update", "missing-info"):
        val = counts.get(key, 0)
        if val:
            parts.append(f"{val} {key}")
    sys.stdout.write(str(f"  Summary: {' · '.join(parts) if parts else 'no issues'}") + "\n")

    # Token budget estimate
    total_tokens = n * TOKENS_PER_ISSUE
    sys.stdout.write(str(f"  Est. token budget: {total_tokens // 1000} k ({n} issue{'s' if n != 1 else ''} at default M={TOKENS_PER_ISSUE // 1000} k each)") + "\n")
    sys.stdout.write("\n")


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
        sys.stdout.write(str("[preflight] Non-interactive mode: auto-approving all issues.") + "\n")
        return results

    sys.stdout.write(str("  Proceed? [A]pprove all  [S]kip flagged  [E]dit per-issue  [Q]uit") + "\n")
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
            sys.stdout.write(str(f"\n  Skipping {len(skipped)} flagged issue(s): "
                  + ", ".join(f"#{r.number}" for r in skipped)) + "\n")
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
                sys.stdout.write(str(f"  Skipping #{r.number}") + "\n")
        return kept

    else:  # Q or anything else
        sys.stdout.write(str("Preflight aborted.") + "\n")
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

    all_results     — every issue that was reviewed
    approved_issues — issues the user approved to run (may be a subset)

    If the claude CLI is not found, prints a warning and returns all issues
    approved with status="ok", notes="review skipped (claude CLI not found)".
    """
    if not shutil.which("claude"):
        sys.stdout.write(str("[preflight] claude CLI not found — skipping review, proceeding with all issues") + "\n")
        issues = _fetch_sprint_issues(sprint_label, repo_name)
        pseudo = [
            ReviewResult(
                number=i["number"],
                title=i["title"],
                status="ok",
                notes="review skipped (claude CLI not found)",
                suggested_action="proceed",
            )
            for i in issues
        ]
        _write_and_log(sprint_label, pseudo, sprints_dir)
        return pseudo, pseudo

    # Fetch backlog issues
    issues = _fetch_sprint_issues(sprint_label, repo_name)
    if not issues:
        sys.stdout.write(str(f"[preflight] No open backlog issues found for {sprint_label!r}.") + "\n")
        return [], []

    sys.stdout.write(str(f"[preflight] Reviewing {len(issues)} issue(s) for {sprint_label!r} ...") + "\n")

    results = run_reviews(issues, repo_name, sprint_label)

    # Print report
    print_report(sprint_label, results)

    # Write JSON (AC-8) — always written regardless of TTY/prompt
    out_path = _write_and_log(sprint_label, results, sprints_dir)
    sys.stdout.write(str(f"[preflight] Review written to {out_path}") + "\n")

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
        sys.stderr.write(str(f"[preflight] Warning: could not fetch issues — {e}") + "\n")
        return []

    # Filter to backlog only
    result = []
    for issue in all_issues:
        labels = {lbl["name"] for lbl in issue.get("labels", [])}
        # Classify: backlog = not in-progress / SIT / UAT / UAT-approved
        status_labels = {"in-progress", "SIT", "UAT", "UAT-approved", "needs-rework", "need-rework", "blocked"}  # READ-only: backward compat
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


# ── CLI helpers ──────────────────────────────────────────────────────────────

def _resolve_sprints_dir(cfg_sprints_dir: Path, commander_dir: Path) -> Path:
    """Return the correct sprints directory for a project, avoiding double .commander paths.

    load_config() resolves paths relative to the .commander/ directory (commander_dir).
    If the sprint.yaml was generated with the old flat-layout template it uses
    '.commander/sprints/' as the value, which resolves to
    .commander/.commander/sprints/ — a double-prefix that is always wrong.

    Fix: when the resolved path contains a '.commander/.commander' segment, fall back
    to the canonical 'commander_dir / sprints' (i.e. .commander/sprints/).
    """
    resolved = cfg_sprints_dir.resolve()
    # Detect double .commander (e.g. .commander/.commander/sprints)
    parts = resolved.parts
    for i, part in enumerate(parts):
        if part == ".commander" and i + 1 < len(parts) and parts[i + 1] == ".commander":
            canonical = commander_dir / "sprints"
            sys.stderr.write(str(f"  [preflight] Corrected double .commander path in sprints_dir: "
                f"{resolved} → {canonical}") + "\n")
            return canonical
    return resolved


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
    p.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "Path to .commander/sprint.yaml config file.  "
            "When provided, sprints_dir and repo are read from it.  "
            "Overrides auto-discovery."
        ),
    )

    args = p.parse_args()

    # ── Resolve sprints_dir from SprintConfig (Bug 1 fix) ────────────────────
    # Import lazily to avoid a circular dependency (sprint_manager imports us).
    sprints_dir: Optional[Path] = None
    repo_name: Optional[str] = args.repo

    try:
        from sprint_manager import discover_config, load_config  # noqa: PLC0415
        _sm_available = True
    except Exception:
        _sm_available = False

    def _try_load_config(path: Path) -> Optional[object]:
        """Load config without calling sys.exit on validation errors."""
        try:
            return load_config(path)  # type: ignore[possibly-undefined]
        except SystemExit as e:
            sys.stderr.write(str(f"  Warning: could not load config {path} — {e}; "
                "using commander default sprints directory.") + "\n")
            return None

    if _sm_available:
        if args.config:
            config_path = Path(args.config).expanduser().resolve()
            if not config_path.exists():
                p.error(f"Config file not found: {config_path}")
            sys.stdout.write(str(f"  Using config: {config_path}") + "\n")
            cfg = _try_load_config(config_path)
            if cfg is not None:
                sprints_dir = _resolve_sprints_dir(cfg.sprints_dir, config_path.parent)
                if not repo_name:
                    repo_name = cfg.repo_name
        else:
            discovered = discover_config()  # type: ignore[possibly-undefined]
            if discovered:
                sys.stdout.write(str(f"  Auto-discovered config: {discovered}") + "\n")
                cfg = _try_load_config(discovered)
                if cfg is not None:
                    sprints_dir = _resolve_sprints_dir(cfg.sprints_dir, discovered.parent)
                    if not repo_name:
                        repo_name = cfg.repo_name

    run_preflight(
        sprint_label = args.sprint_label,
        repo_name    = repo_name,
        sprints_dir  = sprints_dir,
        interactive  = True,
    )


if __name__ == "__main__":
    main()
