# 2026-07-02-11-reconcile-ttl-and-cursor

> Status: decided | provisional

## Context

Three small reconcile mechanics with reliability issues:
- **(a)** `ready_to_merge↔needs_rework` can flap during mirror lag — the
  natural-run guard covers only the demote direction.
- **(b)** The 60 s throttle timestamp is recorded before the pass, so a failed
  pass blocks retries for the full TTL.
- **(c)** The 40-row sweep cap can starve the tail on projects with many
  non-final sprints.

## Options

- **A ★ Fix (b) and (c)** (record TTL on success; rotate/cursor the sweep
  window) — cheap. Leave (a): it self-heals and a symmetric guard risks masking
  real rework.
- **B Fix all three** including a promote-direction lag guard.
- **C None** — all self-heal eventually.

## Decision

**A — fix (b) TTL-on-success and (c) sweep cursor** (provisional — auto-adopted
★ recommendation after interactive timeouts; operator may veto); leave (a) flap
as self-healing.

## Consequences

- `routers/sprint_reconcile_service.py`:
  - **(b)** TTL stamped only after a successful pass (not before).
  - **(c)** Per-project rotating cursor over eligible rows so a project with
    >40 eligible terminal sprints gets full coverage across sweeps.
- **(a)** left as-is — it self-heals and a symmetric promote-direction guard
  risks masking real rework transitions.

## Implemented-by (#N)

#1690 (`fix/1686-1698-flow-decisions`)
