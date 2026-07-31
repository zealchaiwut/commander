# 2026-07-02-12-delete-lineage-fully-in-develop

> Status: decided | provisional

## Context

`_lineage_fully_in_develop` is defined in `sprint_reconcile_service.py` for
B2 auto-complete of superseded ancestors; tests assert its behavior; but nothing
in production calls it. The comment says `needs_rework→completed` is never
reconciler-driven, yet `_sprint_db_mark_merged_completed` does its own
verification. The function may be dead code.

## Options

- **A ★ Delete it + its tests,** or fold its check into
  `_sprint_db_mark_merged_completed` if that path's verification is weaker.
  Decide after comparing the two checks.
- **B Wire it:** sweep auto-completes superseded ancestors once lineage is
  verified merged.

## Decision

**A — delete or fold** `_lineage_fully_in_develop` (provisional — auto-adopted
★ recommendation after interactive timeouts; operator may veto) after comparing
with `_sprint_db_mark_merged_completed`'s check; no wiring into the sweep.

## Consequences

- Compared the two paths. All three callers of `_sprint_db_mark_merged_completed`
  (finish/Merge Sprint, bulk-complete, complete-step in `sprint_finish.py`)
  already do their own merge verification before calling it — bulk-complete's
  `_bulk_complete_merge_pending` scans the entire chain, which is strictly
  stronger than `_lineage_fully_in_develop`'s single-label check.
- `_lineage_fully_in_develop` **deleted** outright — zero production callers.
- Its two dedicated tests in `test_hotfix_lineage_completion.py` updated to
  exercise the real guard (`startup._sprint_merge_chain_pending`) instead.
- Stale docstring reference in `test_1464__sprint_cross_project_isolation.py`
  fixed.
- Comment in `sprint_reconcile_service.py` tightened to name actual call sites.

## Implemented-by (#N)

#1694 (`fix/1686-1698-flow-decisions`)
