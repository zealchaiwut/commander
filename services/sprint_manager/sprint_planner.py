"""Plan-next-sprint selection engine (issue #861).

Pure, dependency-injected logic for the **Plan next sprint** action. Given the
active milestone's open backlog, the carry-over tickets from the most recent
completed sprint, a capacity budget, an estimator hook, and a dependency-order
hook, it selects and orders the tickets for the next sprint *without* touching
GitHub or the filesystem.

Every external effect (GitHub reads, the estimator agent, the DAG builder) is
injected as a callable or passed in as plain data, so the selection rules —
capacity packing, carry-over priority, estimated-first preference, lazy
estimation of unsized tickets, dependency ordering, idempotency, and the
empty-state guards — are unit-testable in isolation. The router/service layer
(``sprints_service.plan_next_sprint``) wires the real GitHub + estimator + DAG
adapters into these functions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Result status codes (the service layer maps these to HTTP shapes / UI states).
STATUS_OK = "ok"                    # a draft was selected
STATUS_EMPTY = "empty"              # nothing to plan (zero cap / no eligible tickets)
STATUS_CONFLICT = "conflict"        # a pending-sign-off draft already exists (AC11)
STATUS_NO_MILESTONE = "no_milestone"  # no active milestone (AC1 guard)

# Default size -> minutes table. Mirrors estimation_config defaults; the service
# passes the project-configured table when it differs.
DEFAULT_SIZE_MINUTES: dict[str, int] = {"S": 5, "M": 15, "L": 30, "XL": 60}

# The lifecycle status a freshly planned sprint is written with. Visually
# distinct from "draft"/"running"/"complete" (AC9).
PENDING_SIGNOFF_STATUS = "pending-signoff"


@dataclass
class PlanTicket:
    """A candidate ticket for the next sprint.

    ``size`` is one of ``S/M/L/XL`` (or ``None`` when unsized). ``minutes`` is
    the resolved capacity cost; when ``None`` it is derived from ``size`` via the
    size->minutes table. ``carry_over`` marks tickets carried from the previous
    sprint so they keep priority during selection and ordering.
    """

    number: int
    title: str = ""
    size: Optional[str] = None
    minutes: Optional[int] = None
    files: list[str] = field(default_factory=list)
    carry_over: bool = False


@dataclass
class PlanResult:
    status: str
    reason: str = ""
    selected: list[PlanTicket] = field(default_factory=list)
    total_minutes: int = 0
    next_sprint_number: Optional[int] = None
    existing_pending_label: Optional[str] = None


# ── pure helpers (milestone / backlog / carry-over derivation) ────────────────

def _milestone_title(issue: dict) -> Optional[str]:
    m = issue.get("milestone")
    if isinstance(m, dict):
        return m.get("title")
    return m


def active_milestone(milestones: list[dict]) -> Optional[dict]:
    """Pick the active milestone: the open milestone with the nearest due date.

    Milestones with no due date sort after dated ones; closed milestones are
    ignored. Returns ``None`` when no open milestone exists — the **Plan next
    sprint** button is only shown when an active milestone exists (AC1).
    """
    open_ms = [m for m in milestones if (m.get("state") or "open") == "open"]
    if not open_ms:
        return None

    def _key(m: dict):
        due = m.get("dueOn") or m.get("due_on")
        # dated milestones first (0), ascending due date; undated last (1).
        return (0, due) if due else (1, "")

    return sorted(open_ms, key=_key)[0]


def select_milestone_backlog(
    issues: list[dict], milestone_title: str, *, is_backlog: Callable[[dict], bool]
) -> list[dict]:
    """Open backlog issues belonging to the active milestone (AC3).

    Only issues that are (a) open, (b) assigned to ``milestone_title``, and
    (c) still in the backlog column are eligible — tickets already in a sprint
    stage (in-progress/SIT/UAT) or in another milestone are excluded.
    """
    result = []
    for iss in issues:
        if iss.get("state") == "closed":
            continue
        if _milestone_title(iss) != milestone_title:
            continue
        if not is_backlog(iss):
            continue
        result.append(iss)
    return result


def carry_over_tickets(
    prev_sprint_issues: list[dict], *, is_finished: Callable[[dict], bool]
) -> list[dict]:
    """Unfinished tickets from the most recent completed sprint (AC4).

    A ticket is carried over when it is not finished (not closed / not in a
    terminal UAT/done state). Finished tickets stay behind.
    """
    return [iss for iss in prev_sprint_issues if not is_finished(iss)]


# ── DAG ordering ──────────────────────────────────────────────────────────────

def dag_order(tickets: list[PlanTicket]) -> list[PlanTicket]:
    """Order selected tickets by the dependency DAG (AC8).

    Wraps ``services.sprint_manager.dag_builder.build_dag`` over the tickets'
    ``files`` so no ticket appears before a dependency. Within a topological
    layer tickets dispatch in ascending issue-number order (smaller/foundational
    first) — the operator-expected order, and the same order the preview-dag UI
    shows, so the live run matches the preview instead of running newest-first
    (GitHub's default selection order).

    On a dependency cycle or when the DAG builder is unavailable it degrades to
    the input order unchanged rather than failing the plan.
    """
    if len(tickets) < 2:
        return list(tickets)
    try:
        from services.sprint_manager.dag_builder import build_dag, CycleError
    except Exception:  # noqa: BLE001 — degrade gracefully if builder absent
        return list(tickets)

    by_id = {f"#{t.number}": t for t in tickets}
    # Order key: carry-over tickets first (AC4 — unfinished work from the prior
    # sprint keeps priority), then ascending issue number (smaller/foundational
    # first) — the operator-expected order and what the preview-dag shows for
    # fresh sprints. Feeding the builder in this order also makes the lower number
    # own a shared file (dispatch first) instead of GitHub's newest-first default.
    def _order_key(t) -> tuple:
        return (not getattr(t, "carry_over", False), t.number)
    dag_tickets = sorted(tickets, key=_order_key)
    dag_input = [{"id": f"#{t.number}", "files_touched": list(t.files)} for t in dag_tickets]
    result = build_dag(dag_input)
    if isinstance(result, CycleError):
        return list(tickets)

    ordered: list[PlanTicket] = []
    for layer in result.layers:
        # Within a parallel-safe layer: carry-over first, then ascending number.
        for tid in sorted(layer, key=lambda x: _order_key(by_id[x])):
            t = by_id.get(tid)
            if t is not None:
                ordered.append(t)
    # Safety net: append any ticket the builder dropped, preserving input order.
    seen = {f"#{t.number}" for t in ordered}
    for t in tickets:
        if f"#{t.number}" not in seen:
            ordered.append(t)
    return ordered


# ── cost resolution ───────────────────────────────────────────────────────────

def _cost(ticket: PlanTicket, size_minutes: dict[str, int]) -> Optional[int]:
    """Capacity cost of a ticket in minutes, or ``None`` when unknown."""
    if ticket.minutes is not None:
        return ticket.minutes
    if ticket.size:
        return size_minutes.get(ticket.size)
    return None


# ── the planner ───────────────────────────────────────────────────────────────

def plan_next_sprint(
    *,
    has_active_milestone: bool,
    capacity_minutes: int,
    carry_over: list[PlanTicket],
    backlog: list[PlanTicket],
    next_sprint_number: int,
    existing_pending_label: Optional[str] = None,
    estimate_fn: Callable[[PlanTicket], Optional[PlanTicket]] = lambda t: None,
    order_fn: Callable[[list[PlanTicket]], list[PlanTicket]] = dag_order,
    size_minutes: Optional[dict[str, int]] = None,
) -> PlanResult:
    """Select and order tickets for the next sprint.

    Rules (acceptance criteria of issue #861):

    - **AC1**  No active milestone → ``STATUS_NO_MILESTONE`` (button is hidden).
    - **AC11** A pending-sign-off draft already exists → ``STATUS_CONFLICT``
      (the caller prompts to replace/cancel rather than silently duplicating).
    - **AC10** Capacity ``<= 0`` or no eligible tickets → ``STATUS_EMPTY`` with
      an informative reason; no sprint is created.
    - **AC4**  Carry-over tickets are considered first (priority).
    - **AC6**  Already-estimated backlog tickets are preferred over unsized ones.
    - **AC5**  Total selected minutes never exceed ``capacity_minutes``.
    - **AC7**  Unsized tickets are run through ``estimate_fn`` only while capacity
      remains; ones the estimator cannot size (returns ``None``) are excluded
      rather than blocking the draft.
    - **AC8**  The final order comes from ``order_fn`` (the dependency DAG).
    """
    sm = size_minutes or DEFAULT_SIZE_MINUTES

    if not has_active_milestone:
        return PlanResult(status=STATUS_NO_MILESTONE,
                          reason="No active milestone — nothing to plan.")

    if existing_pending_label:
        return PlanResult(
            status=STATUS_CONFLICT,
            reason=(f"A pending-sign-off sprint ({existing_pending_label}) already "
                    "exists. Replace it or cancel."),
            existing_pending_label=existing_pending_label,
            next_sprint_number=next_sprint_number,
        )

    if capacity_minutes <= 0:
        return PlanResult(status=STATUS_EMPTY,
                          reason="Sprint capacity is zero — set a capacity before planning.",
                          next_sprint_number=next_sprint_number)

    # Candidate order: carry-over first (AC4), then estimated backlog before
    # unsized backlog (AC6); original order is otherwise preserved.
    for t in carry_over:
        t.carry_over = True
    estimated_backlog = [t for t in backlog if _cost(t, sm) is not None]
    unsized_backlog = [t for t in backlog if _cost(t, sm) is None]
    candidates = list(carry_over) + estimated_backlog + unsized_backlog

    remaining = capacity_minutes
    selected: list[PlanTicket] = []
    for t in candidates:
        cost = _cost(t, sm)
        if cost is None:
            # Unsized — estimate lazily, and only while capacity remains (AC7).
            if remaining <= 0:
                continue
            est = estimate_fn(t)
            if est is None:
                continue  # cannot estimate in time → exclude, do not block (AC7)
            t = est
            cost = _cost(t, sm)
            if cost is None:
                continue
        if cost <= remaining:
            selected.append(t)
            remaining -= cost
        # else: skip — never exceed capacity (AC5); keep trying smaller tickets.

    if not selected:
        return PlanResult(status=STATUS_EMPTY,
                          reason="No eligible tickets fit the sprint capacity.",
                          next_sprint_number=next_sprint_number)

    ordered = order_fn(selected)
    total = sum(_cost(t, sm) or 0 for t in ordered)
    return PlanResult(status=STATUS_OK, selected=ordered, total_minutes=total,
                      next_sprint_number=next_sprint_number)
