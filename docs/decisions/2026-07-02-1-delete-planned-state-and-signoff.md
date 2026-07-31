# 2026-07-02-1-delete-planned-state-and-signoff

> Status: decided

## Context

`planned` exists in the sprint-state enum and `_LEGAL_SPRINT_EDGES` but nothing
ever writes it. The preflight gate shipped as the plan.json `signoff` field with
its own approve/reject endpoints. Two unused mechanisms cluttering the lifecycle.

## Options

- **A ★ Delete `planned`; canonize `signoff`.** Remove from enum/edges;
  document plan.json `signoff` as the official gate. Least churn; matches
  reality.
- **B Wire it.** Sign-off approval writes `planned` to the DB; signoff becomes
  a detail behind the enum. Completes "DB is sole lifecycle source" but touches
  the run/sign-off flow.
- **C Leave as documented wart.**

## Decision

**Park/deprecate BOTH** — `planned` state AND the plan.json `signoff` gate are
deprecated (too hard to stabilize). Also deprecate the **advisor** and **brief**
features in the same pass. Remove/disable rather than wire up; revisit when the
platform is stable.

## Consequences

- `planned` removed from `_SPRINT_STATES`, `LIFECYCLE_STATES`, and
  `_LEGAL_SPRINT_EDGES` in `apps/dashboard/db.py`; kept only as a legacy-read
  value canonicalizing to `draft`.
- Sign-off is already default-disabled via `config.sprint_signoff_disabled()`
  (approve/reject endpoints already 404; run-guard already no-ops by default).
- Docs updated in `sprint-lifecycle.md` and `3_sprint-flow.md`.

## Implemented-by (#N)

#1686 (`fix/1686-1698-flow-decisions`)
