"""Cross-thread serialization primitives for concurrent pipeline mode (issue #738).

When the opt-in pipeline (issue #737) runs a coder worker and a tester worker at
the same time, two ticket lifecycles are legitimately active at once — one
``in-progress`` (coder) and one ``SIT`` (tester). Without coordination, two kinds
of races appear:

  1. Concurrent label writes. ``state_machine.transition`` is a read-modify-write
     (fetch labels → diff → ``gh issue edit`` → verify). Two threads editing the
     same issue, or interleaving fetch/verify windows, can leave duplicate or
     ghost status labels. Serializing every status-label write removes the window.

  2. Concurrent ``develop`` (or sprint-branch) merges. ``finish_feature.py`` does
     a fetch/merge/push against the shared target branch. Two merges overlapping
     produce non-fast-forward pushes, lost merges, or a corrupt history. Merges
     must land one at a time.

This module owns the two locks plus a pure reconciliation function that computes
the *correct* status-label state given who the coder and tester are actively
working — used both to assert the invariants in tests and to sweep ghost labels
back to a valid state after a crash (issue #738 AC5).

The primitives are intentionally process-local (``threading`` locks). The
pipeline runs both workers in one process, so a process-local lock is the right
scope; serial mode acquires an uncontended lock and behaves exactly as before.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterable, Optional

# Status labels managed by the two concurrent owners. Kept as module constants so
# this module has no hard dependency on state_machine import order; they mirror
# state_machine.STATE_LABELS for IN_PROGRESS and SIT.
IN_PROGRESS_LABEL = "in-progress"
SIT_LABEL = "SIT"

# Serializes every develop/sprint-branch merge so only one merge runs at a time.
# A plain (non-reentrant) Lock: a merge never nests inside another merge, and a
# second concurrent attempt must block until the first releases.
_develop_merge_lock = threading.Lock()

# Serializes status-label writes so transition()'s read-modify-write is atomic
# across threads. Reentrant so a transition that internally triggers another
# guarded write on the same thread does not self-deadlock.
_label_transition_lock = threading.RLock()


@contextmanager
def develop_merge_guard():
    """Serialize a develop/sprint-branch merge.

    Only one thread holds this at a time; a concurrent merge attempt blocks until
    the in-flight merge completes (issue #738 AC4 — never two concurrent merges).
    """
    _develop_merge_lock.acquire()
    try:
        yield
    finally:
        _develop_merge_lock.release()


@contextmanager
def label_transition_guard():
    """Serialize a status-label write across pipeline workers.

    Makes the fetch→edit→verify of a single transition atomic relative to any
    other guarded transition, so concurrent coder/tester label writes cannot
    interleave into duplicate or ghost labels (issue #738 AC1–AC3, AC5).
    """
    _label_transition_lock.acquire()
    try:
        yield
    finally:
        _label_transition_lock.release()


def reconcile_status_labels(
    labels_by_issue: dict,
    *,
    coder_active: Optional[int] = None,
    tester_active: Optional[int] = None,
) -> dict:
    """Return the corrected label set per issue enforcing the pipeline invariants.

    Given the *currently observed* labels for a set of tickets and which ticket
    (if any) the coder and tester are actively working, compute the desired
    label set so that:

      * At most one ticket carries ``in-progress`` — the coder's active ticket
        (issue #738 AC1). Every other ticket loses any stale ``in-progress``.
      * At most one ticket carries ``SIT`` — the tester's active ticket
        (AC2). Every other ticket loses any stale ``SIT``.
      * The ``SIT`` → ``UAT`` (and other terminal) labels of unrelated tickets
        are left untouched (AC3 — only the active owners' transient labels move).
      * A label-less limbo ticket (crash between remove and add, AC5) is
        reconciled back to its valid state: the coder's ticket regains
        ``in-progress`` and the tester's ticket regains ``SIT``.

    Only the two transient status labels (``in-progress``, ``SIT``) are added or
    removed. All other labels — terminal status (``UAT``, ``needs-rework``,
    ``backlog``) and non-status labels (``enhancement``, ``sprint-NN``) — are
    preserved verbatim.
    """
    out: dict = {}
    for issue, labels in labels_by_issue.items():
        s = set(labels)
        if issue == coder_active:
            # Coder's ticket: exactly in-progress among the two transient labels.
            s.discard(SIT_LABEL)
            s.add(IN_PROGRESS_LABEL)
        elif issue == tester_active:
            # Tester's ticket: exactly SIT among the two transient labels.
            s.discard(IN_PROGRESS_LABEL)
            s.add(SIT_LABEL)
        else:
            # Anyone else: never holds a transient label — strip ghosts.
            s.discard(IN_PROGRESS_LABEL)
            s.discard(SIT_LABEL)
        out[issue] = s

    # Resolve limbo for active owners absent from the observed snapshot entirely
    # (e.g. a crash wiped the only status label and the ticket fell out of view).
    if coder_active is not None and coder_active not in out:
        out[coder_active] = {IN_PROGRESS_LABEL}
    if tester_active is not None and tester_active not in out:
        out[tester_active] = {SIT_LABEL}

    return out


def ghost_status_labels(
    labels_by_issue: dict,
    *,
    coder_active: Optional[int] = None,
    tester_active: Optional[int] = None,
) -> dict:
    """Return only the transient status labels that should be *removed* per issue.

    Convenience for a sweep step: maps each issue to the set of ghost
    ``in-progress``/``SIT`` labels it currently holds but should not, given the
    active coder/tester. Issues with nothing to remove are omitted.
    """
    desired = reconcile_status_labels(
        labels_by_issue, coder_active=coder_active, tester_active=tester_active
    )
    ghosts: dict = {}
    transient = {IN_PROGRESS_LABEL, SIT_LABEL}
    for issue, labels in labels_by_issue.items():
        stale = (set(labels) & transient) - desired.get(issue, set())
        if stale:
            ghosts[issue] = stale
    return ghosts


def _as_set(labels: Iterable) -> set:
    return set(labels)
