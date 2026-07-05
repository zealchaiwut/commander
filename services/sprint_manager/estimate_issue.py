#!/usr/bin/env python3
"""Estimate a GitHub issue via the Issue Estimator agent.

Fetches issue details, invokes the estimator agent (Haiku 4.5) via `claude -p`,
parses the JSON output, and saves to .commander/estimates/issue-<N>.json.

Usage:
    python3 estimate_issue.py --issue <N> [--repo <owner/repo>]
                              [--save-comment] [--save-label] [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# This file lives at services/sprint_manager/estimate_issue.py
# Repo root is two levels up
REPO_ROOT = Path(__file__).parent.parent.parent
AGENT_PATH = REPO_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.run_id import mint_run_id  # noqa: E402
from services.logging import log as structured_log  # noqa: E402

_SIZING_DIR = Path(__file__).parent
if str(_SIZING_DIR) not in sys.path:
    sys.path.insert(0, str(_SIZING_DIR))
from sizing import minutes_from_letter as _minutes_from_letter  # noqa: E402
from calibration import (  # noqa: E402
    CalibrationResult,
    load_calibration,
    calibration_prompt_section,
    db_calibration_records,
    sqlite_calibration_records,
)
from services.sprint_manager.estimation_config import get_estimation_cfg as _get_estimation_cfg  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def find_commander_dir(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from start (default: CWD) looking for a .commander/ directory."""
    current = (start or Path.cwd()).resolve()
    while True:
        candidate = current / ".commander"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_agent_instructions() -> str:
    """Read estimator.md and strip YAML frontmatter."""
    if not AGENT_PATH.exists():
        return ""
    content = AGENT_PATH.read_text()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content.strip()


def parse_files_to_touch(body: str) -> list:
    """Extract repo-relative paths from the '## Files to touch' section.

    Returns a deduplicated list of path strings (preserving first-seen order).
    Returns [] when the section is absent or contains no parseable paths.
    """
    in_section = False
    in_comment = False
    seen: set = set()
    paths: list = []
    for line in body.split("\n"):
        if re.match(r"^#+\s+Files to touch", line, re.IGNORECASE):
            in_section = True
            continue
        if not in_section:
            continue
        if re.match(r"^#", line):
            break
        stripped = line.strip()
        # Handle multi-line HTML comments.
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if "<!--" in stripped:
            if "-->" not in stripped:
                in_comment = True
            continue
        # Collect non-empty paths.
        if stripped and stripped not in seen:
            seen.add(stripped)
            paths.append(stripped)
    return paths


def extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from agent output."""
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned)

    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        pass

    # Brace-matching scan for the first top-level object
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def fetch_issue(issue_num: int, repo: str) -> dict:
    """Fetch issue details from GitHub via REST (gh api).

    `gh issue view` goes through GraphQL, whose 5000/hr budget the dashboard
    shares and exhausts during estimation bursts; REST has a separate budget.
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/issues/{issue_num}"],
        capture_output=True, text=True, check=True,
    )
    raw = json.loads(result.stdout)
    return {
        "number": raw.get("number"),
        "title": raw.get("title", ""),
        "body": raw.get("body") or "",
        "labels": [{"name": lbl.get("name", "")} for lbl in (raw.get("labels") or [])],
    }


_ESTIMATOR_MAX_RETRIES = 3
_ESTIMATOR_RETRY_DELAYS = [2, 4, 8]  # exponential backoff seconds before retry attempts 2, 3, 4


def run_estimator(
    issue_num: int,
    issue_data: dict,
    calibration: Optional[CalibrationResult] = None,
    project: Optional[str] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """Invoke the estimator agent via `claude -p` and return (result, error_type).

    On success returns (parsed_dict, None).
    On failure returns (None, error_type) where error_type is one of:
      "network_error", "model_error", "parse_error", "missing_claude".

    Retries up to _ESTIMATOR_MAX_RETRIES times on network errors, model errors
    (non-zero exit), and parse errors.  Delays follow exponential backoff
    (2s, 4s, 8s).  Each retry emits a structured log entry with attempt number,
    error type, and delay_seconds.  --no-session-persistence prevents session-file
    conflicts when multiple estimations run concurrently during bulk create.

    *project* is used to resolve per-project estimation config from the settings
    table (size_minutes, buffer_pct). Defaults are used when omitted or when the
    settings layer is unavailable.
    """
    estimation_cfg = _get_estimation_cfg(project=project)
    instructions = load_agent_instructions()

    title = issue_data.get("title", "")
    body  = issue_data.get("body") or "(no body)"

    cal_section = calibration_prompt_section(calibration) if calibration is not None else ""

    prompt = f"""{instructions}

{cal_section}
---

Now estimate this issue:

**Title:** {title}
**Number:** #{issue_num}

**Body:**
{body}

Output ONLY the JSON object. No other text."""

    # Strip ANTHROPIC_API_KEY so the claude CLI authenticates via the Claude
    # subscription (keychain) instead of the API key. The configured key has no
    # credit balance, so leaving it set makes claude exit non-zero with
    # "Credit balance is too low" — surfaced upstream as a bogus model_error.
    # Matches how the dashboard strips the key for coder/tester subprocesses.
    _agent_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    from services.sprint_manager.model_routing import apply_provider_env
    _estimator_model = apply_provider_env(
        _agent_env, "claude-haiku-4-5-20251001", repo=project, role="estimator",
    )
    cmd = [
        "claude",
        "--model", _estimator_model,
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "-p", prompt,
    ]

    # Tag hook-recorded token_usage rows with the model (and role, if the
    # launcher didn't already set CLAUDE_AGENT_ROLE).
    _agent_env["CLAUDE_MODEL"] = _estimator_model
    _agent_env.setdefault("CLAUDE_AGENT_ROLE", "estimator")
    if project:
        _agent_env.setdefault("COMMANDER_PROJECT", project)

    # Total attempts = initial + _ESTIMATOR_MAX_RETRIES (e.g. 4 = 1 + 3).
    total_attempts = _ESTIMATOR_MAX_RETRIES + 1
    for attempt in range(1, total_attempts + 1):
        error_type: Optional[str] = None

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
                env=_agent_env,
            )
        except subprocess.TimeoutExpired:
            error_type = "network_error"
            structured_log.error(
                "estimator_timeout",
                "estimator agent timed out after 180s",
                issue_num=issue_num,
                timeout_secs=180,
            )
        except FileNotFoundError:
            sys.stderr.write(str("Error: claude CLI not found in PATH") + "\n")
            return None, "missing_claude"

        if error_type is None:
            if result.returncode != 0:
                error_type = "model_error"
            else:
                parsed = extract_json(result.stdout)
                if parsed is None:
                    error_type = "parse_error"
                    structured_log.error(
                        "estimator_parse_error",
                        "could not parse JSON from agent output",
                        issue_num=issue_num,
                        output_preview=result.stdout[:200],
                    )
                else:
                    parsed["issue_number"] = issue_num
                    # Normalize files_touched: absent or non-list → []
                    if not isinstance(parsed.get("files_touched"), list):
                        parsed["files_touched"] = []
                    # Merge explicit '## Files to touch' paths into files_likely_affected.
                    # Union: explicit paths first (always included), then inferred paths
                    # not already listed. When section is absent/empty nothing changes.
                    explicit = parse_files_to_touch(body)
                    if explicit:
                        inferred = parsed.get("files_likely_affected") or []
                        explicit_set = set(explicit)
                        merged = explicit + [p for p in inferred if p not in explicit_set]
                        parsed["files_likely_affected"] = merged
                    # Ensure both size and minutes are present; derive missing field
                    # using settings-resolved size_minutes (falls back to sizing.py defaults).
                    size_val = parsed.get("size", "")
                    if "minutes" not in parsed or not isinstance(parsed.get("minutes"), int):
                        size_minutes = estimation_cfg.get("size_minutes", {})
                        parsed["minutes"] = size_minutes.get(size_val, _minutes_from_letter(size_val))
                    parsed["body_hash"] = hashlib.sha256(body.encode()).hexdigest()
                    # Attach calibration sources so consumers know which tiers were calibrated
                    if calibration is not None:
                        parsed["calibration_sources"] = calibration.sources
                    # Estimated size lives in the local estimate JSON written by
                    # the caller. Issue #758 removed the Neon sprint_tickets mirror.
                    return parsed, None

        # error_type is set — decide whether to retry or fail.
        retries_used = attempt - 1
        retries_remaining = _ESTIMATOR_MAX_RETRIES - retries_used

        if retries_remaining > 0:
            delay = _ESTIMATOR_RETRY_DELAYS[retries_used]
            structured_log.warn(
                "estimator_retry",
                "estimation failed, retrying",
                issue_num=issue_num,
                attempt=attempt,
                error_type=error_type,
                delay_seconds=delay,
            )
            sys.stderr.write(str(f"Warning: estimator failed for #{issue_num} (attempt {attempt}/{total_attempts},"
                f" error_type={error_type}), retrying in {delay}s…") + "\n")
            time.sleep(delay)
        else:
            structured_log.error(
                "estimator_failed",
                "all retries exhausted",
                issue_num=issue_num,
                attempt=attempt,
                error_type=error_type,
            )
            if error_type == "model_error" and result.stderr:
                sys.stderr.write(str(result.stderr[:500]) + "\n")
            sys.stderr.write(str(f"Error: estimator failed for #{issue_num} after {_ESTIMATOR_MAX_RETRIES} retries"
                f" (final error_type={error_type})") + "\n")

    return None, error_type


def post_comment(issue_num: int, repo: str, estimate: dict) -> None:
    """Post the estimate as a structured comment on the issue."""
    size       = estimate.get("size", "?")
    minutes    = estimate.get("minutes") or _minutes_from_letter(size) if size != "?" else "?"
    hours      = estimate.get("estimated_hours", "?")
    confidence = estimate.get("confidence", "?")
    files      = estimate.get("files_likely_affected", [])
    depends_on = estimate.get("depends_on", [])
    blocks     = estimate.get("blocks", [])
    risk_flags = estimate.get("risk_flags", [])
    summary    = estimate.get("summary", "")

    files_str  = "\n".join(f"  - `{f}`" for f in files) if files else "  - (none)"
    risk_str   = ", ".join(f"`{r}`" for r in risk_flags) if risk_flags else "none"
    deps_str   = ", ".join(f"#{d}" for d in depends_on) if depends_on else "none"
    blocks_str = ", ".join(f"#{b}" for b in blocks) if blocks else "none"

    body = f"""## Estimate

| Field | Value |
|---|---|
| Size | **{size}** |
| Minutes | {minutes} |
| Estimated hours | {hours}h |
| Confidence | {confidence} |
| Risk flags | {risk_str} |
| Depends on | {deps_str} |
| Blocks | {blocks_str} |

**Files likely affected:**
{files_str}

**Summary:** {summary}

---
*Generated by Issue Estimator (Haiku 4.5)*"""

    subprocess.run(
        ["gh", "issue", "comment", str(issue_num), "--repo", repo, "--body", body],
        check=True,
    )
    sys.stdout.write(str(f"  Posted estimate comment on #{issue_num}") + "\n")


def apply_estimated_status(issue_num: int, repo: str) -> bool:
    """Apply the 'estimated' status label via update_ticket.py with retry-and-warn logic.

    Returns True on success, False if update_ticket.py exited with code 2 (partial failure).
    Propagates other non-zero exit codes as warnings but does not raise.
    """
    update_ticket = REPO_ROOT / "scripts" / "update_ticket.py"
    result = subprocess.run(
        [sys.executable, str(update_ticket), "--issue", str(issue_num), "--status", "estimated",
         "--repo", repo],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        sys.stdout.write(str(f"  Applied 'estimated' label to #{issue_num}") + "\n")
        return True
    if result.returncode == 2:
        sys.stderr.write(str(f"Warning: update_ticket.py --status estimated exited with code 2 for "
            f"#{issue_num} — label operations failed after all retries. "
            f"A warning comment has been posted to the issue.") + "\n")
        return False
    # Unexpected non-zero exit
    stderr_text = result.stderr.strip()
    sys.stderr.write(str(f"Warning: update_ticket.py --status estimated failed (exit {result.returncode})"
        f" for #{issue_num}: {stderr_text}") + "\n")
    return False


def apply_label(issue_num: int, repo: str, size: str) -> None:
    """Apply size-S/M/L/XL label to the issue, creating it if needed."""
    valid = {"S", "M", "L", "XL"}
    if size not in valid:
        structured_log.warn("estimate_invalid_size", f"unknown size {size!r}, skipping label", issue_num=issue_num, size=size)
        return

    size_descriptions = {"S": "1–5 min", "M": "~15 min", "L": "~30 min", "XL": ">30 min"}
    label = f"size-{size}"
    subprocess.run(
        [
            "gh", "label", "create", label, "--repo", repo, "--force",
            "--color", "0075ca", "--description", f"Estimated size {size} ({size_descriptions.get(size, size)})",
        ],
        capture_output=True,
    )
    subprocess.run(
        ["gh", "issue", "edit", str(issue_num), "--repo", repo, "--add-label", label],
        check=True,
    )
    sys.stdout.write(str(f"  Applied label '{label}' to #{issue_num}") + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def estimate_draft_file(draft_path: Path) -> None:
    """Estimate an unposted draft from a JSON file of {title, body}.

    Used by bulk-create to size a draft *before* the GitHub issue exists, so
    sizes can inform sprint assignment. Runs the same text-based estimator as a
    real issue (it only reads title+body), then prints the estimate JSON to
    stdout. No GitHub writes, no label/comment, no cache file — the caller owns
    persistence (the size label is applied at post time).
    """
    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        sys.stderr.write(str(f"Error: could not read draft file {draft_path}: {e}") + "\n")
        sys.exit(1)

    issue_data = {"title": draft.get("title", ""), "body": draft.get("body", "")}
    # issue_num=0 → used only for logging/body_hash; the estimate is text-based.
    estimate, err = run_estimator(0, issue_data)
    if not estimate:
        sys.stderr.write(str(f"Error: draft estimation failed ({err})") + "\n")
        sys.exit(1)
    estimate.pop("issue_number", None)  # no issue yet
    sys.stdout.write(str(json.dumps(estimate)) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description="Estimate a GitHub issue via the Issue Estimator agent.")
    p.add_argument("--issue", "-i", type=int, default=None, help="Issue number")
    p.add_argument("--repo", "-r", default=None, help="owner/repo (auto-detected if omitted)")
    p.add_argument("--save-comment", action="store_true", help="Post structured estimate as issue comment")
    p.add_argument("--save-label", action="store_true", help="Apply size-S/M/L/XL label to issue")
    p.add_argument("--force", action="store_true", help="Re-run estimator even if cached result exists")
    p.add_argument("--draft-file", default=None,
                   help="Estimate an unposted draft from a JSON file {title, body}; prints estimate to stdout")
    p.add_argument("--calibration-sprint", default=None, help="Past sprint label to pull DB calibration records from")
    p.add_argument("--commander-dir", default=None,
                   help="Canonical .commander/ directory to write issue-N.json into (overrides CWD walk and "
                        "COMMANDER_PROJECT_ROOT env var)")
    args = p.parse_args()

    # Draft mode: size text before any issue exists (bulk-create pre-post estimate).
    if args.draft_file:
        _run_id = mint_run_id("manual")
        os.environ["COMMANDER_RUN_ID"] = _run_id
        structured_log.set_context(run_id=_run_id, source="manual")
        estimate_draft_file(Path(args.draft_file))
        return

    if args.issue is None:
        p.error("--issue is required unless --draft-file is given")

    # Mint or adopt run_id for this invocation
    _run_id = mint_run_id("manual")
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="manual")

    # Auto-detect repo
    repo = args.repo
    if not repo:
        try:
            out = subprocess.run(
                ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                capture_output=True, text=True, check=True,
            )
            repo = out.stdout.strip()
        except Exception:
            sys.stderr.write(str("Error: --repo not specified and auto-detection failed") + "\n")
            sys.exit(1)

    # Find .commander dir: --commander-dir > COMMANDER_PROJECT_ROOT env var > CWD walk
    _env_root = os.environ.get("COMMANDER_PROJECT_ROOT")
    if args.commander_dir:
        commander_dir = Path(args.commander_dir)
    elif _env_root:
        commander_dir = Path(_env_root)
    else:
        commander_dir = find_commander_dir()
    if not commander_dir:
        sys.stderr.write(str("Error: could not find .commander/ directory (searched from CWD upward)") + "\n")
        sys.exit(1)

    estimates_dir = commander_dir / "estimates"
    estimates_dir.mkdir(parents=True, exist_ok=True)
    estimate_path = estimates_dir / f"issue-{args.issue}.json"

    # Load calibration data — prefer DB records when --calibration-sprint given.
    _db_cal_records: list = []
    if args.calibration_sprint:
        _db_cal_records = db_calibration_records(args.calibration_sprint, estimates_dir)
        if _db_cal_records:
            sys.stdout.write(str(f"Calibration (DB): {len(_db_cal_records)} records from sprint {args.calibration_sprint!r}") + "\n")
    # Neon-independent fallback (issue #766): when no sprint-state records are
    # available (e.g. DATABASE_URL unset), read samples from the local SQLite
    # store instead of silently falling through to generic defaults.
    if not _db_cal_records:
        _db_cal_records = sqlite_calibration_records()
        if _db_cal_records:
            sys.stdout.write(str(f"Calibration (SQLite): {len(_db_cal_records)} records from local store") + "\n")
    calibration = load_calibration(commander_dir, db_records=_db_cal_records or None)
    for w in calibration.warnings:
        sys.stderr.write(str(f"Warning [calibration]: {w}") + "\n")
    if calibration.calibration_path:
        sys.stdout.write(str(f"Calibration: {calibration.calibration_path} ({calibration.record_count} records)") + "\n")
    else:
        sys.stdout.write(str("Calibration: none loaded — using generic defaults") + "\n")

    # Return cached result unless --force
    if estimate_path.exists() and not args.force:
        sys.stdout.write(str(f"Cached: {estimate_path}") + "\n")
        sys.stdout.write(str("  (pass --force to re-run)") + "\n")
        estimate = json.loads(estimate_path.read_text())
        sys.stdout.write(str(json.dumps(estimate, indent=2)) + "\n")
        if args.save_comment:
            post_comment(args.issue, repo, estimate)
        if args.save_label:
            apply_label(args.issue, repo, estimate.get("size", ""))
            apply_estimated_status(args.issue, repo)
        return

    # Fetch issue and run estimator
    sys.stdout.write(str(f"Fetching issue #{args.issue} from {repo} ...") + "\n")
    try:
        issue_data = fetch_issue(args.issue, repo)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(str(f"Error: could not fetch issue #{args.issue}: {e}") + "\n")
        sys.exit(1)

    sys.stdout.write(str(f"Running estimator (Haiku 4.5) for #{args.issue} ...") + "\n")
    estimate, _ = run_estimator(args.issue, issue_data, calibration=calibration)
    if not estimate:
        sys.exit(1)

    estimate_path.write_text(json.dumps(estimate, indent=2))
    sys.stdout.write(str(f"Saved: {estimate_path}") + "\n")
    sys.stdout.write(str(json.dumps(estimate, indent=2)) + "\n")

    if args.save_comment:
        post_comment(args.issue, repo, estimate)
    if args.save_label:
        apply_label(args.issue, repo, estimate.get("size", ""))
        # Apply the 'estimated' status label via the resilient update_ticket.py path
        # so downstream consumers (sprint_estimator loop, dashboard) can identify
        # already-estimated tickets (issue #267).
        apply_estimated_status(args.issue, repo)


if __name__ == "__main__":
    main()
