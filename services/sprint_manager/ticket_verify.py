"""Verify a ticket actually advanced after a dispatch step (issue #2328).

#2324 fixed "the agent failed and exited 0". This module covers the cases where
the agent ran *successfully* and the ticket still did not move. All three were
observed in real runs on viral-radar sprint-7:

  1. **The lifecycle was skipped.** #80's tester passed; PR #85 stayed open and
     the label stayed `backlog`. Dispatch moved on believing it was finished.

  2. **Contradictory labels.** #82 ended up carrying `needs-rework` *and* `UAT`
     at once — its tester set the former mid-run and never cleared it. That is
     not cosmetic: `POST /rerun` resets every ticket carrying `needs-rework`, so
     a rerun would have reset merged, verified work back to `backlog`.

  3. **A negative verdict in a successful run.** #83's tester completed, wrote a
     full report, and explicitly *recommended `needs-rework` and declined to
     merge*. Dispatch recorded `ok=True`. The verdict was in the report; nothing
     read it.

The judgments below are pure functions so they can be tested without GitHub.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

REWORK_LABEL = "needs-rework"
TERMINAL_LABELS = ("UAT", "UAT-approved", "done")
POST_CODER_LABELS = ("SIT", "UAT", "UAT-approved", "done")

# Phrases an agent uses when it completed but is telling you NOT to proceed.
# Deliberately narrow: a false positive stops a good run, so each pattern must
# express a recommendation, not merely mention the words.
_REWORK_PATTERNS = (
    r"recommend(?:ed|ing)?\s+\*{0,2}needs-rework",
    r"recommend(?:ed|ing)?\s+(?:a\s+)?rework",
    r"not\s+merging\s+as[- ]is",
    r"did\s+not\s+merge",
    r"declin(?:e|ed|ing)\s+to\s+merge",
    r"should\s+not\s+be\s+merged",
    r"blocking\s+(?:the\s+)?merge",
)


def report_recommends_rework(text: str) -> tuple[bool, str]:
    """True when an agent's own report says the work should not be merged.

    The agent is the one that read the acceptance criteria. When it says the
    ticket is not done, that outranks the fact that its process exited cleanly.
    """
    if not text:
        return False, ""
    lowered = text.lower()
    for pattern in _REWORK_PATTERNS:
        m = re.search(pattern, lowered)
        if m:
            excerpt = text[max(0, m.start() - 90): m.end() + 90].strip().replace("\n", " ")
            return True, f"agent recommended rework: …{excerpt}…"
    return False, ""


def labels_are_contradictory(labels: list[str]) -> tuple[bool, str]:
    """True when a ticket holds `needs-rework` alongside a terminal label.

    A ticket in both states is in no valid state, and leaving it there arms the
    rerun endpoint to undo finished work.
    """
    names = set(labels or [])
    if REWORK_LABEL not in names:
        return False, ""
    clash = names.intersection(TERMINAL_LABELS)
    if clash:
        return True, (
            f"ticket carries {REWORK_LABEL} and {'/'.join(sorted(clash))} at once — "
            "a rerun would reset finished work"
        )
    return False, ""


def ticket_advanced(labels: list[str], step: str) -> tuple[bool, str]:
    """Whether the ticket's labels moved as the step should have moved them."""
    names = set(labels or [])

    if REWORK_LABEL in names:
        return False, f"ticket is labelled {REWORK_LABEL}"

    if step == "coder":
        if names.intersection(POST_CODER_LABELS):
            return True, ""
        return False, (
            "coder finished but the ticket is still not at SIT "
            f"(labels: {', '.join(sorted(names)) or 'none'})"
        )

    if step == "tester":
        if names.intersection(TERMINAL_LABELS):
            return True, ""
        return False, (
            "tester passed but the ticket never reached UAT "
            f"(labels: {', '.join(sorted(names)) or 'none'})"
        )

    return True, ""


def verify_step(
    *,
    step: str,
    issue: int,
    repo: Optional[str],
    report: str,
    fetch_labels: Callable[[int, Optional[str]], list[str]],
    fetch_open_pr: Optional[Callable[[int, Optional[str]], Any]] = None,
) -> tuple[bool, str]:
    """Return (ok, reason) for a step whose agent reported success.

    Checks are ordered cheapest-and-most-decisive first: the agent's own verdict,
    then label sanity, then whether the ticket moved, then whether a PR is still
    open behind a passing tester.
    """
    rework, why = report_recommends_rework(report)
    if rework:
        return False, why

    try:
        labels = fetch_labels(issue, repo)
    except Exception as exc:
        # Cannot verify is not the same as verified. Refuse rather than assume.
        return False, f"could not read labels for #{issue}: {exc}"

    clash, why = labels_are_contradictory(labels)
    if clash:
        return False, why

    advanced, why = ticket_advanced(labels, step)
    if not advanced:
        return False, why

    if step == "tester" and fetch_open_pr is not None:
        try:
            open_pr = fetch_open_pr(issue, repo)
        except Exception:
            open_pr = None
        if open_pr:
            return False, (
                f"tester passed but PR #{open_pr} is still open — nothing merged"
            )

    return True, ""
