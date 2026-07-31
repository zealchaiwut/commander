# 2026-07-02-8-unify-run-lock-sentinel

> Status: decided | provisional

## Context

Two guard layers, different semantics:
- Orchestrator sets `COMMANDER_SPRINT_RUNNING=<label>`.
- `assert_run_mutable` checks `== "1"` (inert in manager subprocesses).
- `update_ticket.py` treats any truthy value as locked.

The mismatch means `assert_run_mutable` never actually locks when the label
value is non-"1" (e.g. a label string like `sprint-50`).

## Options

- **A ★ Unify on truthy-check** in `assert_run_mutable` (match
  `update_ticket.py`); the label value is useful context — keep setting it.
- **B Unify on `"1"`** everywhere and pass the label separately.
- **C Leave documented.**

## Decision

**A — unify on truthy-check** (provisional — auto-adopted ★ recommendation
after interactive timeouts; operator may veto) in `assert_run_mutable`; keep
setting the label value for context.

## Consequences

- `state_machine.run_lock_active()` and
  `github_client._refuse_if_sprint_running` both now treat any non-empty
  `COMMANDER_SPRINT_RUNNING` value as locked, matching `update_ticket.py`.
- Two pre-existing tests asserting the old exact-`"1"` semantics were updated
  (`test_754__run_mutable_labels.py`).
- Production never sets the var to `"0"`, so treating any non-empty value as
  truthy costs nothing.

## Implemented-by (#N)

#1689 (`fix/1686-1698-flow-decisions`)
