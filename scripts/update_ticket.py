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

The caller hint embedded in actor="update_ticket:<hint>" makes activity events
attributable; it derives from --actor, then CLAUDE_AGENT_ROLE, then "cli".

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
    "blocked":      TicketState.BLOCKED,
    "queued":       TicketState.QUEUED,
}

# Statuses that don't map to a TicketState; transition() does not own them.
_PASSTHROUGH_STATUSES = {"uat-approved", "estimated"}


def _caller_hint(explicit: str | None) -> str:
    """Resolve the caller hint embedded in the transition actor.

    Precedence: explicit --actor > CLAUDE_AGENT_ROLE env > "cli".
    """
    if explicit:
        return explicit
    role = os.environ.get("CLAUDE_AGENT_ROLE", "").strip()
    return role or "cli"


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
    parser.add_argument("--actor", default=None,
                        help="Caller hint embedded in the transition actor "
                             "(actor='update_ticket:<hint>'). Defaults to "
                             "CLAUDE_AGENT_ROLE, then 'cli'.")
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
        # These statuses don't correspond to TicketState values, so transition()
        # does not own them. They are not status labels — apply with the gh CLI.
        sys.stderr.write(str(f"Status '{args.status}' is not a managed status label; "
            f"apply it directly with the gh CLI.") + "\n")
        sys.exit(1)

    target_state = STATUS_TO_STATE[args.status]
    actor = f"update_ticket:{_caller_hint(args.actor)}"

    _RUN_MUTABLE_STATES = frozenset({
        TicketState.IN_PROGRESS, TicketState.SIT, TicketState.UAT, TicketState.NEEDS_REWORK,
    })

    try:
        if sprint_label := os.environ.get("COMMANDER_SPRINT_RUNNING"):
            if target_state not in _RUN_MUTABLE_STATES:
                raise ValueError(
                    f"Cannot transition to {target_state} during sprint run "
                    f"({sprint_label!r}); outside RUN_MUTABLE_LABELS"
                )
        changed = transition(
            args.issue,
            target_state,
            actor=actor,
            repo=repo,
        )
        if changed:
            sys.stdout.write(str(f"transition: #{args.issue} → {args.status}") + "\n")
        else:
            sys.stdout.write(str(f"transition: #{args.issue} already in {args.status} (no-op)") + "\n")
    except ValueError as e:
        sys.stderr.write(str(f"transition blocked: {e}") + "\n")
        sys.exit(1)
    except TransitionError as e:
        sys.stderr.write(str(f"transition failed: {e}") + "\n")
        sys.exit(1)

    url = f"https://github.com/{repo}/issues/{args.issue}"
    sys.stdout.write(str(f"#{args.issue} {url}") + "\n")


if __name__ == "__main__":
    main()
