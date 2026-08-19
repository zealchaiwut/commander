#!/usr/bin/env python3
"""Reset a failed ticket to a dispatchable state and print the next command.

The manual equivalent is:
  gh issue edit N --remove-label needs-rework --add-label backlog

This script does that plus:
  - Clears the stale failure sidecar at .commander/runtime/last-failure-<N>.json
  - Prints the exact /coder and /tester invocations for the issue

Hard constraints (learned from the old rerun failures):
  - Does NOT mint child sprint labels (sprint-N.1, sprint-N.2, etc.)
  - Does NOT reorder tickets — ticket sequencing is the operator's responsibility
  - Does NOT touch sprint lifecycle state — uses update_labels() directly,
    never state_machine.transition(), so sprint_manager state is untouched

Usage:
  python3 scripts/retry_ticket.py --issue 42
  python3 scripts/retry_ticket.py --issue 42 --repo owner/repo
  python3 scripts/retry_ticket.py --issue 42 --dry-run

Prints:
  Reset #<N>: needs-rework -> backlog
  Sidecar cleared: .commander/runtime/last-failure-<N>.json
  Next:
    /coder work on issue <N>
    /tester verify issue <N>

Exits 0 on success (or dry-run), non-zero on error.
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


def _load_env():
    env = _DASHBOARD_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


from services.sprint_manager.ticket_retry import (  # noqa: E402
    REWORK_LABEL,
    reset_ticket,
    sidecar_path,
)


def _sidecar_path(issue_num: int) -> Path:
    """Return the path to the last-failure sidecar for an issue.

    Thin wrapper kept for the existing tests; the implementation now lives in
    services.sprint_manager.ticket_retry so the CLI and the /rerun endpoint
    (#2318) share exactly one behaviour.
    """
    return sidecar_path(issue_num, _REPO_ROOT)


def main():
    _load_env()

    parser = argparse.ArgumentParser(
        description="Reset a failed ticket to dispatchable state (needs-rework → backlog)"
    )
    parser.add_argument("--issue", type=int, required=True, help="Issue number to reset")
    parser.add_argument("--repo",  default=None,
                        help="Override repo (owner/name). Defaults to auto-detected.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without making any changes")
    args = parser.parse_args()

    if args.repo:
        repo = args.repo
    else:
        try:
            repo = github_client.repo()
        except ValueError as e:
            sys.exit(str(e))

    # Fetch the issue to confirm it exists and check current labels
    try:
        issue = github_client.get_issue_live(args.issue, repo_name=repo)
    except Exception as e:
        sys.exit(f"Could not fetch issue #{args.issue}: {e}")

    current_labels = [lbl["name"] for lbl in issue.get("labels", [])]

    if REWORK_LABEL not in current_labels:
        if "backlog" in current_labels:
            sys.stdout.write(
                f"#{args.issue} already has 'backlog' label (no needs-rework to clear).\n"
            )
        else:
            sys.stderr.write(
                f"Warning: #{args.issue} does not have the 'needs-rework' label "
                f"(current labels: {', '.join(current_labels) or 'none'}). "
                f"Proceeding to add 'backlog' anyway.\n"
            )

    url = f"https://github.com/{repo}/issues/{args.issue}"

    if args.dry_run:
        sys.stdout.write(f"[dry-run] Would remove 'needs-rework', add 'backlog' on #{args.issue}\n")
        sidecar = _sidecar_path(args.issue)
        if sidecar.exists():
            sys.stdout.write(f"[dry-run] Would delete sidecar: {sidecar}\n")
        else:
            sys.stdout.write(f"[dry-run] No sidecar found at {sidecar}\n")
        sys.stdout.write(f"[dry-run] Next commands:\n")
        sys.stdout.write(f"  /coder work on issue {args.issue}\n")
        sys.stdout.write(f"  /tester verify issue {args.issue}\n")
        return

    # Shared with POST /api/sprints/{label}/rerun (#2318) so the two paths
    # cannot drift. update_labels() calls `gh issue edit` directly — it does
    # NOT touch sprint lifecycle state and does NOT mint child labels.
    sidecar = _sidecar_path(args.issue)
    try:
        outcome = reset_ticket(
            args.issue,
            github_client=github_client,
            repo=repo,
            repo_root=_REPO_ROOT,
            labels=current_labels,
        )
    except Exception as e:
        sys.exit(f"Label update failed for #{args.issue}: {e}")

    if outcome.changed:
        sys.stdout.write(f"Reset #{args.issue}: {outcome.reason}\n")
    else:
        sys.stdout.write(f"#{args.issue} unchanged: {outcome.reason}\n")

    if outcome.sidecar_cleared:
        sys.stdout.write(f"Sidecar cleared: {sidecar.relative_to(_REPO_ROOT)}\n")
    else:
        sys.stdout.write(f"No sidecar at {sidecar.relative_to(_REPO_ROOT)}\n")

    # Print the exact invocations for the operator
    sys.stdout.write("Next:\n")
    sys.stdout.write(f"  /coder work on issue {args.issue}\n")
    sys.stdout.write(f"  /tester verify issue {args.issue}\n")
    sys.stdout.write(f"#{args.issue} {url}\n")


if __name__ == "__main__":
    main()
