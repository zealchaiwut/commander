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

from services.run_id import mint_run_id
from services.logging import log as structured_log


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
    """Fetch issue details from GitHub using gh CLI."""
    result = subprocess.run(
        [
            "gh", "issue", "view", str(issue_num),
            "--repo", repo,
            "--json", "number,title,body,labels",
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


_ESTIMATOR_MAX_RETRIES = 3
_ESTIMATOR_RETRY_DELAYS = [2, 4, 8]  # exponential backoff seconds before retry attempts 2, 3, 4


def run_estimator(issue_num: int, issue_data: dict) -> Optional[dict]:
    """Invoke the estimator agent via `claude -p` and return parsed JSON.

    Retries up to _ESTIMATOR_MAX_RETRIES times on network errors, model errors
    (non-zero exit), and parse errors.  Delays follow exponential backoff
    (2s, 4s, 8s).  Each retry emits a structured log entry with attempt number,
    error type, and delay_seconds.  --no-session-persistence prevents session-file
    conflicts when multiple estimations run concurrently during bulk create.
    """
    instructions = load_agent_instructions()

    title = issue_data.get("title", "")
    body  = issue_data.get("body") or "(no body)"

    prompt = f"""{instructions}

---

Now estimate this issue:

**Title:** {title}
**Number:** #{issue_num}

**Body:**
{body}

Output ONLY the JSON object. No other text."""

    cmd = [
        "claude",
        "--model", "claude-haiku-4-5-20251001",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "-p", prompt,
    ]

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
            print("Error: claude CLI not found in PATH", file=sys.stderr)
            return None

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
                    parsed["body_hash"] = hashlib.sha256(body.encode()).hexdigest()
                    return parsed

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
            print(
                f"Warning: estimator failed for #{issue_num} (attempt {attempt}/{total_attempts},"
                f" error_type={error_type}), retrying in {delay}s…",
                file=sys.stderr,
            )
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
                print(result.stderr[:500], file=sys.stderr)
            print(
                f"Error: estimator failed for #{issue_num} after {_ESTIMATOR_MAX_RETRIES} retries"
                f" (final error_type={error_type})",
                file=sys.stderr,
            )

    return None


def post_comment(issue_num: int, repo: str, estimate: dict) -> None:
    """Post the estimate as a structured comment on the issue."""
    size       = estimate.get("size", "?")
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
    print(f"  Posted estimate comment on #{issue_num}")


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
        print(f"  Applied 'estimated' label to #{issue_num}")
        return True
    if result.returncode == 2:
        print(
            f"Warning: update_ticket.py --status estimated exited with code 2 for "
            f"#{issue_num} — label operations failed after all retries. "
            f"A warning comment has been posted to the issue.",
            file=sys.stderr,
        )
        return False
    # Unexpected non-zero exit
    stderr_text = result.stderr.strip()
    print(
        f"Warning: update_ticket.py --status estimated failed (exit {result.returncode})"
        f" for #{issue_num}: {stderr_text}",
        file=sys.stderr,
    )
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
    print(f"  Applied label '{label}' to #{issue_num}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Estimate a GitHub issue via the Issue Estimator agent.")
    p.add_argument("--issue", "-i", type=int, required=True, help="Issue number")
    p.add_argument("--repo", "-r", default=None, help="owner/repo (auto-detected if omitted)")
    p.add_argument("--save-comment", action="store_true", help="Post structured estimate as issue comment")
    p.add_argument("--save-label", action="store_true", help="Apply size-S/M/L/XL label to issue")
    p.add_argument("--force", action="store_true", help="Re-run estimator even if cached result exists")
    args = p.parse_args()

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
            print("Error: --repo not specified and auto-detection failed", file=sys.stderr)
            sys.exit(1)

    # Find .commander dir
    commander_dir = find_commander_dir()
    if not commander_dir:
        print("Error: could not find .commander/ directory (searched from CWD upward)", file=sys.stderr)
        sys.exit(1)

    estimates_dir = commander_dir / "estimates"
    estimates_dir.mkdir(parents=True, exist_ok=True)
    estimate_path = estimates_dir / f"issue-{args.issue}.json"

    # Return cached result unless --force
    if estimate_path.exists() and not args.force:
        print(f"Cached: {estimate_path}")
        print("  (pass --force to re-run)")
        estimate = json.loads(estimate_path.read_text())
        print(json.dumps(estimate, indent=2))
        if args.save_comment:
            post_comment(args.issue, repo, estimate)
        if args.save_label:
            apply_label(args.issue, repo, estimate.get("size", ""))
            apply_estimated_status(args.issue, repo)
        return

    # Fetch issue and run estimator
    print(f"Fetching issue #{args.issue} from {repo} ...")
    try:
        issue_data = fetch_issue(args.issue, repo)
    except subprocess.CalledProcessError as e:
        print(f"Error: could not fetch issue #{args.issue}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Running estimator (Haiku 4.5) for #{args.issue} ...")
    estimate = run_estimator(args.issue, issue_data)
    if not estimate:
        sys.exit(1)

    estimate_path.write_text(json.dumps(estimate, indent=2))
    print(f"Saved: {estimate_path}")
    print(json.dumps(estimate, indent=2))

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
