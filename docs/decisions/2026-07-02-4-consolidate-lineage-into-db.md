# 2026-07-02-4-consolidate-lineage-into-db

> Status: decided | implemented

## Context

Three stores, three notions of sprint lineage:
- plan.json `parent` = immediate parent (drives merge topology)
- DB `sprints.parent_label` = base label (drives History grouping)
- state.json `rerun_into` = forward pointer

This redundancy is an ongoing source of drift and confusion.

## Options

- **A ★ Consolidate into DB:** add `immediate_parent` column alongside
  `parent_label` (base); write both at rerun; plan.json/state.json copies
  become dual-writes to retire later.
- **B Leave as-is,** now documented.

## Decision

**A — consolidate into DB** (provisional — auto-adopted ★ recommendation after
interactive timeouts; operator may veto): add `immediate_parent` column beside
`parent_label`; rerun writes both; file copies become dual-writes to retire later.

## Consequences

- `immediate_parent` added to `sprints` table (additive column, same migration
  idiom as other run-artifact columns).
- `db.set_sprint_immediate_parent()` writes it from the rerun endpoint
  (`routers/sprint_run.py`) alongside the existing plan.json `parent` write,
  creating a placeholder `draft` row for queued children if none exists yet.
- Value survives the later `running` transition unchanged.
- **Merge-topology resolvers now read DB first** (`_sprint_merge_parent_label` in
  `startup.py` and `_immediate_parent_branch` in `sprint_manager.py`), falling
  back to plan.json when DB `immediate_parent` is NULL (dual-write gap), then to
  the base sprint branch with a loud warning.
- `_backfill_immediate_parent_labels()` in `db.py` (called from
  `_create_sprint_lifecycle_tables`) heals NULL rows by reading plan.json —
  mirrors the `_backfill_child_parent_labels()` pattern for `parent_label`.
- Missing `immediate_parent` is now a loud data-defect warning, not a silent
  fallback to the wrong base.

## Implemented-by (#N)

#1691 (`fix/1686-1698-flow-decisions`) — column added, write wired up
#2048 (`feature/2048-lineage-sprints-immediate-parent-is-writ`) — readers switched to DB, backfill added, loud fallback
