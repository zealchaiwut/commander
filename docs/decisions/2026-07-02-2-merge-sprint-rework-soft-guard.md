# 2026-07-02-2-merge-sprint-rework-soft-guard

> Status: decided

## Context

Plain finish closes ALL sprint issues including `needs-rework` ones;
bulk-complete and complete-step refuse on rework. Five paths reach `completed`
with inconsistent guards — a sprint can silently close failed tickets.

## Options

- **A ★ Add a soft guard to finish:** confirmation modal warns "N rework
  tickets will be closed" — human can still override (human click = sign-off).
- **B Hard guard:** finish refuses like bulk-complete; operator must re-run or
  manually clear rework first.
- **C Intended:** human sign-off overrides everything; document only (done).

## Decision

**A — soft guard.** Finish confirmation modal warns "N rework tickets will be
closed"; human can override. Never close failed work silently.

## Consequences

- `_finish_rework_tickets()`/`_finish_sprint_issues()` pair in `sprint_finish.py`
  computes tickets that have not reached UAT; both `finish_sprint` and
  `start_finish_sprint_bg` recompute fresh from GitHub before merging.
- Returns 409 with `{code, message, rework_tickets}` unless `confirm_rework: true`
  is set.
- `finish-preview` now also returns `rework_tickets` so the modal can warn before
  the user clicks Merge Sprint.
- Frontend (`finish-modal.js`/`project.html`): warning banner + required override
  checkbox wired into the request body; error rendering updated for new 409 shape.
- Note: the guard was originally written against `POST .../finish` (synchronous),
  but the dashboard UI calls `POST .../finish-bg` (`routers/finish_progress.py`).
  Both paths received the guard.

## Implemented-by (#N)

#1696 (`fix/1686-1698-flow-decisions`)
