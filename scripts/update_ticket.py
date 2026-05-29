#!/usr/bin/env python3
"""Move a GitHub issue to a new status by delegating to transition() in state_machine.

Status labels (in-progress, sit, uat, needs-rework) are written exclusively via
transition() so all label changes are logged in one place.

Non-status labels (blocked, estimated, uat-approved) are applied directly and do
not flow through the state machine.

Usage:
  python3 scripts/update_ticket.py --issue 42 --status in-progress
  python3 scripts/update_ticket.py --issue 42 --status sit
  python3 scripts/update_ticket.py --issue 42 --status uat
  python3 scripts/update_ticket.py --issue 42 --status blocked
  python3 scripts/update_ticket.py --issue 42 --status uat-approved
  python3 scripts/update_ticket.py --issue 42 --status estimated
  python3 scripts/update_ticket.py --issue 42 --status needs-rework

Exits with code 0 on success, non-zero on failure.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(_DASHBOARD_DIR / ".env")
import github_client
from services.logging import log as structured_log
from services.run_id import mint_run_id
from services.sprint_manager.state_machine import TicketState, TransitionError, transition

# Statuses that map directly to TicketState via transition()
_STATE_MAP: dict[str, TicketState] = {
    "in-progress": TicketState.IN_PROGRESS,
    "sit":         TicketState.SIT,
    "uat":         TicketState.UAT,
    "needs-rework": TicketState.NEEDS_REWORK,
}

# Non-status labels handled via direct gh calls (add-only, no state-machine)
_DIRECT_LABEL_MAP: dict[str, dict] = {
    "blocked": {
        "add":   ["blocked"],
        "remove": [],
        "close": None,
    },
    "uat-approved": {
        "add":   ["UAT-approved"],
        "remove": ["UAT"],
        "close": "completed",
    },
    "estimated": {
        "add":   ["estimated"],
        "remove": [],
        "close": None,
    },
}

_ALL_STATUSES = list(_STATE_MAP) + list(_DIRECT_LABEL_MAP)


def _load_env():
    env = _DASHBOARD_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _apply_direct(issue: int, mapping: dict, repo: str) -> None:
    for lbl in mapping["add"]:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue), "--repo", repo, "--add-label", lbl],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f'failed to add label "{lbl}": {result.stderr.strip()}')
        print(f'applied label "{lbl}"')

    for lbl in mapping["remove"]:
        result = subprocess.run(
            ["gh", "issue", "edit", str(issue), "--repo", repo, "--remove-label", lbl],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and "not labeled" not in result.stderr.lower():
            sys.exit(f'failed to remove label "{lbl}": {result.stderr.strip()}')
        if result.returncode == 0:
            print(f'removed label "{lbl}"')

    if mapping.get("close"):
        close_result = subprocess.run(
            ["gh", "issue", "close", str(issue), "--repo", repo,
             "--reason", mapping["close"]],
            capture_output=True, text=True,
        )
        if close_result.returncode != 0:
            sys.exit(f"Error closing issue: {close_result.stderr.strip()}")


def main():
    _load_env()

    _run_id = mint_run_id("manual")
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="update_ticket")

    parser = argparse.ArgumentParser(description="Update issue status")
    parser.add_argument("--issue",  type=int, required=True)
    parser.add_argument("--status", required=True, choices=_ALL_STATUSES)
    parser.add_argument("--force",  action="store_true",
                        help="No-op (retained for backwards compatibility)")
    parser.add_argument("--repo",   default=None,
                        help="Override repo (owner/name). Defaults to auto-detected repo.")
    args = parser.parse_args()
    structured_log.set_context(issue_num=args.issue)
    structured_log.info(
        "ticket_update_start",
        f"updating issue #{args.issue} to status {args.status!r}",
        issue_num=args.issue,
        status=args.status,
    )

    if args.repo:
        repo = args.repo
    else:
        try:
            repo = github_client.repo()
        except ValueError as e:
            sys.exit(str(e))

    if args.status in _STATE_MAP:
        target_state = _STATE_MAP[args.status]
        try:
            transition(args.issue, target_state, actor="update_ticket", repo=repo)
        except TransitionError as exc:
            sys.exit(str(exc))
    else:
        _apply_direct(args.issue, _DIRECT_LABEL_MAP[args.status], repo)

    url = f"https://github.com/{repo}/issues/{args.issue}"
    print(f"#{args.issue} {url}")


if __name__ == "__main__":
    main()
