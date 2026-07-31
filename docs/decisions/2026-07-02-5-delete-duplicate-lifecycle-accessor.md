# 2026-07-02-5-delete-duplicate-lifecycle-accessor

> Status: decided | provisional

## Context

Two canonical lifecycle read accessors with different contracts:
- `apps/dashboard/sprint_state.py` returns `"unknown"` for a missing row.
- `apps/dashboard/routers/sprint_state.py` returns `None`.

The contract says one sanctioned reader. Having two with different return values
creates subtle bugs at call sites that branch on the falsy/truthy distinction.

## Options

- **A ★ Delete the routers copy;** migrate its callers to the top-level
  accessor. Mechanical.
- **B Keep both,** document the difference.

## Decision

**A — delete the routers accessor** (provisional — auto-adopted ★ recommendation
after interactive timeouts; operator may veto); migrate callers to
`apps/dashboard/sprint_state.py`.

## Consequences

- `apps/dashboard/routers/sprint_state.py` deleted (it was never a mounted
  FastAPI router despite the location — a plain module).
- Its one caller (`routers/sprint_history_service.py`) migrated to the
  top-level accessor, adjusting the `or _normalize_state(...)` fallback (which
  relied on `None` being falsy) to an explicit `is None or == "unknown"` check.

## Implemented-by (#N)

#1692 (`fix/1686-1698-flow-decisions`)
