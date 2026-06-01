#!/usr/bin/env python3
"""Thin CLI wrapper: maps --status to a TicketState and delegates to transition().

Usage:
  python3 scripts/update_ticket.py --issue 42 --status in-progress
  python3 scripts/update_ticket.py --issue 42 --status sit
  python3 scripts/update_ticket.py --issue 42 --status uat
  python3 scripts/update_ticket.py --issue 42 --status needs-rework

All label writes go through state_machine.transition() — the single source of
truth for status label changes.  The --force and --merge-sha flags are accepted
but ignored (UAT safeguard is now handled by sprint_manager, not here).

Exits 0 on success, non-zero on failure.
"""
import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(_DASHBOARD_DIR / ".env")
import github_client
from services.run_id import mint_run_id
from services.logging import log as structured_log
from services.sprint_manager.state_machine import transition, TicketState, TransitionError

STATUS_TO_STATE: dict[str, TicketState] = {
    "in-progress":  TicketState.IN_PROGRESS,
    "sit":          TicketState.SIT,
    "uat":          TicketState.UAT,
    "needs-rework": TicketState.NEEDS_REWORK,
    "queued":       TicketState.QUEUED,
}

# Statuses that don't map to a TicketState; handled as passthrough label ops.
_PASSTHROUGH_STATUSES = {"blocked", "uat-approved", "estimated"}


def _load_env():
    env = _DASHBOARD_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def main():
    _load_env()

    _run_id = mint_run_id("manual")
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="update_ticket")

    all_statuses = sorted(STATUS_TO_STATE) + sorted(_PASSTHROUGH_STATUSES)
    parser = argparse.ArgumentParser(description="Update issue status via transition()")
    parser.add_argument("--issue",  type=int, required=True)
    parser.add_argument("--status", required=True, choices=all_statuses)
    parser.add_argument("--force",  action="store_true",
                        help="Accepted for backward compatibility; has no effect.")
    parser.add_argument("--repo",   default=None,
                        help="Override repo (owner/name). Defaults to auto-detected.")
    parser.add_argument("--merge-sha", default=None,
                        help="Accepted for backward compatibility; has no effect.")
    parser.add_argument("--target-branch", default=None,
                        help="Accepted for backward compatibility; has no effect.")
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

    if args.status in _PASSTHROUGH_STATUSES:
        # Passthrough: these statuses don't correspond to TicketState values.
        # Emit a structured error — callers should use gh CLI directly.
        print(
            f"Status '{args.status}' is not managed by transition(); "
            f"apply this label directly with 'gh issue edit --add-label {args.status}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    target_state = STATUS_TO_STATE[args.status]
    try:
        changed = transition(
            args.issue,
            target_state,
            actor="update_ticket",
            repo=repo,
        )
        if changed:
            print(f"transition: #{args.issue} → {args.status}")
        else:
            print(f"transition: #{args.issue} already in {args.status} (no-op)")
    except TransitionError as e:
        print(f"transition failed: {e}", file=sys.stderr)
        sys.exit(1)

    url = f"https://github.com/{repo}/issues/{args.issue}"
    print(f"#{args.issue} {url}")


if __name__ == "__main__":
    main()
