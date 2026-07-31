# 2026-07-02-4-consolidate-lineage-into-db

> Status: decided | provisional

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
- Merge-topology resolvers (`_sprint_merge_parent_label`,
  `_merge_steps_for_sprint_chain` in `startup.py`) still read plan.json first —
  switching them to prefer the DB column is follow-up work, not done here.

## Implemented-by (#N)

#1691 (`fix/1686-1698-flow-decisions`)
