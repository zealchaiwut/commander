#!/usr/bin/env python3
"""Generate acceptance criteria for a GitHub issue that lacks them.

Reads the issue title + body, asks Claude (Sonnet 4.6, matching BA quality) to
write an Acceptance Criteria checklist plus UAT Steps, and APPENDS them to the
issue body. Append-only and idempotent: if the body already has an
"## Acceptance Criteria" section, it is left untouched.

Used by the dashboard's "Fix all pre-flight issues" action and runnable
standalone:

    python3 fill_acceptance_criteria.py --issue <N> [--repo <owner/repo>] [--force]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).parent.parent.parent

_AC_HEADING_RE = re.compile(r"^\s*#{1,6}\s*acceptance\s+criteria\b", re.IGNORECASE | re.MULTILINE)

_MAX_RETRIES = 2
_RETRY_DELAYS = [3, 6]


def fetch_issue(issue_num: int, repo: str) -> dict:
    """Fetch issue number/title/body from GitHub via gh CLI."""
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_num), "--repo", repo,
         "--json", "number,title,body"],
        capture_output=True, text=True, check=True,
    )
    import json
    return json.loads(result.stdout)


def has_acceptance_criteria(body: str) -> bool:
    """True if the body already contains an Acceptance Criteria heading."""
    return bool(_AC_HEADING_RE.search(body or ""))


def _extract_ac_markdown(text: str) -> Optional[str]:
    """Pull the generated markdown starting at the Acceptance Criteria heading.

    Tolerates a model preamble before the first heading; strips code fences.
    """
    if not text:
        return None
    cleaned = re.sub(r"```(?:markdown|md)?\s*", "", text)
    cleaned = cleaned.replace("```", "").strip()
    m = _AC_HEADING_RE.search(cleaned)
    if m:
        return cleaned[m.start():].strip()
    # No heading found — unusable.
    return None


def generate_acceptance_criteria(
    issue_num: int, title: str, body: str,
) -> tuple[Optional[str], Optional[str]]:
    """Invoke Claude to write AC + UAT steps. Returns (markdown, error_type).

    error_type is one of None | "missing_claude" | "model_error" |
    "parse_error" | "network_error".
    """
    prompt = f"""You are a business analyst writing acceptance criteria for a software ticket.

Given the ticket below, write its acceptance criteria and UAT steps. Use this exact structure and nothing else:

## Acceptance Criteria

- [ ] <specific, testable criterion>
- [ ] <...>

## UAT Steps

1. <step a human follows to verify, with the expected result>
2. <...>

Rules:
- 3 to 7 acceptance criteria, each independently testable and specific to THIS ticket.
- Ground them in the title and body; do not invent unrelated scope.
- UAT steps must be concrete actions with an observable expected result.
- Output ONLY the two markdown sections above. No preamble, no closing remarks.

Ticket #{issue_num}
Title: {title}

Body:
{body or "(no body provided)"}
"""

    cmd = [
        "claude",
        "--model", "claude-sonnet-4-6",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "-p", prompt,
    ]
    # Strip ANTHROPIC_API_KEY so claude authenticates via the Claude subscription
    # (keychain), not the credit-less API key — same as the estimator/coder/tester.
    _agent_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    total_attempts = _MAX_RETRIES + 1
    error_type: Optional[str] = None
    for attempt in range(1, total_attempts + 1):
        error_type = None
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180, env=_agent_env,
            )
        except subprocess.TimeoutExpired:
            error_type = "network_error"
        except FileNotFoundError:
            return None, "missing_claude"

        if error_type is None:
            if result.returncode != 0:
                error_type = "model_error"
            else:
                ac = _extract_ac_markdown(result.stdout)
                if ac:
                    return ac, None
                error_type = "parse_error"

        if attempt < total_attempts:
            time.sleep(_RETRY_DELAYS[attempt - 1])
        elif error_type == "model_error" and result.stderr:
            # result is bound whenever error_type == "model_error".
            print(result.stderr[:400], file=sys.stderr)

    return None, error_type


def append_to_issue(issue_num: int, repo: str, current_body: str, ac_markdown: str) -> None:
    """Append the AC markdown to the issue body (preserving existing content)."""
    new_body = (current_body or "").rstrip() + "\n\n" + ac_markdown.strip() + "\n"
    subprocess.run(
        ["gh", "issue", "edit", str(issue_num), "--repo", repo, "--body", new_body],
        check=True, capture_output=True, text=True,
    )


def fill_issue(issue_num: int, repo: str, force: bool = False) -> tuple[str, Optional[str]]:
    """Generate + append AC for one issue.

    Returns (status, error) where status is "filled" | "skipped" | "failed".
    "skipped" means the issue already had acceptance criteria (idempotent).
    """
    data = fetch_issue(issue_num, repo)
    body = data.get("body") or ""
    if has_acceptance_criteria(body) and not force:
        return "skipped", None
    ac, err = generate_acceptance_criteria(issue_num, data.get("title", ""), body)
    if not ac:
        return "failed", err
    append_to_issue(issue_num, repo, body, ac)
    return "filled", None


def main() -> None:
    p = argparse.ArgumentParser(description="Generate acceptance criteria for a GitHub issue.")
    p.add_argument("--issue", "-i", type=int, required=True, help="Issue number")
    p.add_argument("--repo", "-r", default=None, help="owner/repo (auto-detected if omitted)")
    p.add_argument("--force", action="store_true", help="Regenerate even if AC already present")
    args = p.parse_args()

    repo = args.repo
    if not repo:
        out = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True, text=True, check=True,
        )
        repo = out.stdout.strip()

    print(f"Filling acceptance criteria for #{args.issue} in {repo} …")
    try:
        status, err = fill_issue(args.issue, repo, force=args.force)
    except subprocess.CalledProcessError as e:
        print(f"Error: gh failed for #{args.issue}: {e.stderr or e}", file=sys.stderr)
        sys.exit(1)

    if status == "skipped":
        print(f"  #{args.issue}: already has acceptance criteria — skipped")
    elif status == "filled":
        print(f"  #{args.issue}: acceptance criteria added")
    else:
        print(f"Error: could not generate AC for #{args.issue} ({err})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
