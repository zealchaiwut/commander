# 2026-07-02-3-sweep-auto-settle-confirmed-orphans

> Status: decided

## Context

`_github_reconcile_row` can settle an orphaned `running` sprint (PID file
present, process dead), but the auto sweep skips `running` rows — only the
per-sprint Reconcile button reaches that branch. Confirmed orphans that are not
manually reconciled stay stuck.

## Options

- **A ★ Let the sweep settle confirmed orphans** (PID-file-present AND
  process-dead only; PID-file-absent still left alone per issue #1095).
- **B Keep button-only** — conservative; a false-positive orphan settle during
  a live run would be bad, and the button exists.

## Decision

**A — auto-settle confirmed orphans** in the sweep (PID-file-present AND
process-dead only; PID-file-absent untouched per #1095).

## Consequences

- Removing `running` from `reconcile_project`'s skip list was not sufficient
  alone: `reconcile_sprint_label` always called
  `transition_sprint_state(actor="reconcile")`, but `db.py`'s edge guard
  requires `actor="manager"` for `running→{ready_to_merge,needs_rework}`.
- Pre-fix: orphan settling silently failed even via the per-sprint button.
- Fixed by using `actor="manager"` specifically for the confirmed-orphan
  running→terminal edge (the reconciler confirmed the manager process is dead,
  so acting with equivalent authority is the intent, not a bypass); terminal↔
  terminal reconcile transitions keep `actor="reconcile"`.
- Tests in `tests/reconciler/test_reconcile_running_sprint.py` updated (two
  tests asserted the old broken `actor="reconcile"` expectation).

## Implemented-by (#N)

#1697 (`fix/1686-1698-flow-decisions`)
