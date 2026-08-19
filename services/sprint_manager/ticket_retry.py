"""Shared reset logic for retrying failed tickets (issues #2311, #2318).

`scripts/retry_ticket.py` (CLI) and `POST /api/sprints/{label}/rerun` (endpoint)
both call into here, so the two cannot drift apart.

The old rerun was deleted in #2250 for three specific reasons, catalogued in
#2311. None of them may recur, so they are stated as invariants of this module:

  * It **never mints child sprint labels.** The old rerun created
    ``sprint-N.1/.2/.3``, fragmenting one logical sprint across four labels and
    breaking sign-off and PR flows. Nothing here creates a label.
  * It **never reorders tickets.** The old rerun reversed order and once queued
    a delete-the-tests ticket ahead of the deletions it covered. Nothing here
    sequences anything.
  * It **never writes sprint lifecycle state.** A cancelled sprint stuck at
    ``needs_rework`` and same-label re-dispatch 409'd forever. This module calls
    ``github_client.update_labels`` directly and never
    ``state_machine.transition``.

Resetting is also deliberately separate from dispatching: a reset can be
inspected before anything runs. Merging the two would recreate the old rerun's
worst habit of doing several things at once.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REWORK_LABEL = "needs-rework"
DISPATCHABLE_LABEL = "backlog"


@dataclass
class TicketResetResult:
    issue: int
    changed: bool
    reason: str
    sidecar_cleared: bool = False

    def to_dict(self) -> dict:
        return {
            "issue": self.issue,
            "changed": self.changed,
            "reason": self.reason,
            "sidecar_cleared": self.sidecar_cleared,
        }


@dataclass
class SprintResetResult:
    sprint_label: str
    reset: list[int] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    dry_run: bool = False

    @property
    def is_noop(self) -> bool:
        return not self.reset

    def to_dict(self) -> dict:
        return {
            "sprint_label": self.sprint_label,
            "reset": list(self.reset),
            "skipped": list(self.skipped),
            "dry_run": self.dry_run,
            "noop": self.is_noop,
            "summary": self.summary(),
        }

    def summary(self) -> str:
        if self.is_noop:
            return (
                f"No tickets in {self.sprint_label} were in a failed state — "
                "nothing to reset."
            )
        verb = "Would reset" if self.dry_run else "Reset"
        listed = ", ".join(f"#{n}" for n in self.reset)
        return f"{verb} {len(self.reset)} ticket(s) in {self.sprint_label}: {listed}"


def sidecar_path(issue_num: int, repo_root: Path) -> Path:
    """Path of the stale failure sidecar written by a failed dispatch."""
    return repo_root / ".commander" / "runtime" / f"last-failure-{issue_num}.json"


def clear_sidecar(issue_num: int, repo_root: Path) -> bool:
    """Delete the failure sidecar when present. Returns True when one was removed."""
    path = sidecar_path(issue_num, repo_root)
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError as exc:
        sys.stderr.write(f"Warning: could not delete sidecar {path}: {exc}\n")
        return False


def reset_ticket(
    issue_num: int,
    *,
    github_client,
    repo: Optional[str] = None,
    repo_root: Path,
    labels: Optional[list[str]] = None,
    dry_run: bool = False,
) -> TicketResetResult:
    """Reset one ticket to a dispatchable state.

    Idempotent: a ticket already sitting at ``backlog`` with no rework label is
    reported as unchanged rather than being edited again.

    ``labels`` may be passed when the caller already fetched them, so a sprint
    reset does not re-fetch every issue individually.
    """
    if labels is None:
        issue = github_client.get_issue_live(issue_num, repo_name=repo)
        labels = [lbl["name"] for lbl in issue.get("labels", [])]

    has_rework = REWORK_LABEL in labels
    has_backlog = DISPATCHABLE_LABEL in labels

    if not has_rework and has_backlog:
        return TicketResetResult(
            issue=issue_num,
            changed=False,
            reason="already dispatchable",
        )

    if dry_run:
        return TicketResetResult(
            issue=issue_num,
            changed=True,
            reason=f"would swap {REWORK_LABEL} -> {DISPATCHABLE_LABEL}",
            sidecar_cleared=sidecar_path(issue_num, repo_root).exists(),
        )

    # update_labels runs `gh issue edit` directly: it never creates a label and
    # never touches sprint lifecycle state.
    github_client.update_labels(
        issue_num,
        add=[DISPATCHABLE_LABEL],
        remove=[REWORK_LABEL] if has_rework else [],
        repo_name=repo,
    )

    cleared = clear_sidecar(issue_num, repo_root)
    return TicketResetResult(
        issue=issue_num,
        changed=True,
        reason=f"{REWORK_LABEL} -> {DISPATCHABLE_LABEL}",
        sidecar_cleared=cleared,
    )


def issues_for_label(github_client, sprint_label: str, repo: Optional[str] = None) -> list[dict]:
    """Return every issue carrying ``sprint_label``.

    ``github_client.list_issues`` takes an integer sprint number and builds the
    label itself, which covers ``sprint-1027`` but not a child label such as
    ``sprint-1.1``. Child labels are never minted here, but historical ones
    exist in both repos, so a non-numeric label falls back to filtering the
    open-issue list rather than raising.
    """
    suffix = sprint_label.removeprefix("sprint-")
    try:
        return github_client.list_issues(int(suffix), repo_name=repo)
    except (ValueError, TypeError):
        issues = github_client.list_all_open_issues(repo_name=repo, limit=200)
        return [
            i for i in issues
            if any(lbl.get("name") == sprint_label for lbl in i.get("labels", []))
        ]


def reset_sprint(
    sprint_label: str,
    *,
    github_client,
    repo: Optional[str] = None,
    repo_root: Path,
    dry_run: bool = False,
) -> SprintResetResult:
    """Reset every failed ticket in a sprint, keeping the sprint's own label.

    Tickets are visited in the order GitHub returns them; no ordering is
    imposed, because ticket sequencing belongs to the operator (#2311).
    """
    result = SprintResetResult(sprint_label=sprint_label, dry_run=dry_run)

    issues = issues_for_label(github_client, sprint_label, repo)

    for issue in issues:
        num = int(issue.get("number"))
        labels = [lbl["name"] for lbl in issue.get("labels", [])]

        if REWORK_LABEL not in labels:
            result.skipped.append(
                {"issue": num, "reason": f"not in {REWORK_LABEL}"}
            )
            continue

        outcome = reset_ticket(
            num,
            github_client=github_client,
            repo=repo,
            repo_root=repo_root,
            labels=labels,
            dry_run=dry_run,
        )
        if outcome.changed:
            result.reset.append(num)
        else:
            result.skipped.append({"issue": num, "reason": outcome.reason})

    return result
