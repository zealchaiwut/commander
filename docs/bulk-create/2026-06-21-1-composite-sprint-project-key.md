# Composite (label, project) key for the sprints lifecycle table

**Date:** 2026-06-21
**Sprint label:** NEW
**Default labels:** enhancement, backend, touches-db-schema
**Status:** drafted

Incident: the `sprints` lifecycle table uses **`label` as its sole PRIMARY KEY**
(`_SPRINTS_TABLE_DDL` in `apps/dashboard/db.py`, `label TEXT PRIMARY KEY`).
Sprint labels are only unique *per repo*, so two projects that both have a
`sprint-66` collide on one row. Observed on the UAT clone:

- `sprint-66` row = `zealchaiwut/perf-coach`, state `running` (a stale row),
  while its children `sprint-66.1..66.6` belong to `zealchaiwut/commander`.
  perf-coach's `sprint-66` had **overwritten** commander's `sprint-66` base row
  via `transition_sprint_state`'s `ON CONFLICT(label) DO UPDATE` — so the board
  showed commander's lineage with perf-coach's base state. `sprint_history` has
  the same disease (a crux `sprint-5` masked by a vector-search `sprint-5`).

This is silent cross-project data corruption: whichever project writes a
same-numbered sprint last wins, clobbering the other's state, project, parent
linkage, and run artifacts. The real fix is a composite `(label, project)`
primary key plus project-scoped reads/writes throughout the lifecycle layer.

**Risk:** highest blast radius of the recent sprint-state work — touches the
schema and every `WHERE label = ?` call site. Phase it; migrate carefully;
existing collided rows cannot be un-merged (the overwritten copy is already
lost) so the migration backfills `project` and flags survivors for manual
repair rather than pretending to recover data.

**Prereqs already shipped (do not redo):** `parent_label` is persisted +
backfilled (B1); `get_sprint(label, project=...)` already does a project-scoped
read with a label-only fallback; orphan `running` rows are swept
(`_sweep_orphan_db_running_rows`). Build on those.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Backfill the sprints.project column so every row is project-attributed before the key change. Today many `sprints` (and `sprint_history`) rows have empty/NULL `project` (legacy rows, cross-project bleed). Add a one-time migration in `apps/dashboard/db.py` (run from `_create_sprint_lifecycle_tables`, idempotent, after the existing `_backfill_child_parent_labels`) that fills `project` for rows where it is empty, resolving the owner/repo from, in precedence order: (1) `agent_runs.project` for the same `sprint_label`; (2) the project whose `.commander/sprints/<label>-plan.json` or `<label>-state.json` exists on disk (walk known project roots from projects.json); (3) leave empty if unresolved and log a warning with the label. Add `scripts/backfill_sprint_project.py --dry-run/--apply` wrapping the same resolver for operators to run on UAT and PRD. Do NOT change the primary key in this ticket. 

Acceptance: (1) after migration, `SELECT count(*) FROM sprints WHERE project=''` is 0 on a repo with resolvable history, or every remaining empty row is logged; (2) script dry-run prints per-row resolution source; (3) idempotent — second run changes nothing; (4) pytest covers each resolution branch with a temp DB + fake projects.json.
---
Detect and report existing (label, project) collisions before the key migration. A collision = two projects that each legitimately own a sprint with the same label, where only one row currently survives in `sprints` (the other was overwritten by ON CONFLICT(label)). Add `scripts/audit_sprint_collisions.py` that cross-references `sprints` + `sprint_history` + `agent_runs` + per-clone plan.json/state.json to list every label that appears under more than one project across those sources, showing which project currently holds the `sprints` row and which project(s) lost it. Output a markdown table and a JSON manifest under `.commander/runtime/sprint-collisions.json`. This is read-only diagnostics — no writes. Surface a `GET /api/debug/sprint-collisions` endpoint returning the same manifest so it is visible from the dashboard.

Acceptance: (1) on the live UAT data the report lists `sprint-66` (perf-coach vs commander) and the crux/vector `sprint-5` collision; (2) manifest distinguishes "survivor" vs "lost" project per label; (3) endpoint returns the manifest; (4) pytest covers a synthetic two-project collision.
---
Migrate the sprints table to a composite (label, project) PRIMARY KEY. SQLite cannot ALTER a primary key, so rebuild: create `sprints_new` with `PRIMARY KEY (label, project)` (carry every existing column + the run-artifact columns), copy all rows from `sprints` (dedupe on (label,project) keeping the most-recently-updated), drop `sprints`, rename `sprints_new` → `sprints`, recreate indexes. Gate the rebuild on a schema-version check so it runs exactly once and is a no-op afterwards; run it inside `_create_sprint_lifecycle_tables` AFTER the project backfill ticket (rows must be project-attributed first, else they collapse under the new key). Preserve the `state` CHECK constraint and all run-artifact columns. Keep `project` NOT NULL DEFAULT '' so a still-empty-project row remains insertable (it just keys on ('label','')). Update `_migrate_sprints_state_check` / `_migrate_sprints_run_artifacts` to cooperate with the rebuilt table.

Acceptance: (1) after migration two rows `('sprint-66','zealchaiwut/perf-coach')` and `('sprint-66','zealchaiwut/commander')` can coexist; (2) migration is idempotent (version-gated) and preserves all existing columns/values; (3) the state CHECK still rejects bad states; (4) pytest: fresh DB gets composite PK; a pre-migration single-PK DB with sample rows upgrades without data loss; inserting two same-label different-project rows succeeds.
---
Make every sprints write project-scoped. Update `transition_sprint_state` ON CONFLICT target from `(label)` to `(label, project)` and ensure the INSERT always supplies a non-null project (derive base/parent as today). Audit and fix all lifecycle writers in `db.py` that target a row by label alone — `_set_sprint_terminal`, `record_sprint_start/finish/ready_to_merge/needs_rework`, `update_sprint_run_counts`, `ingest_sprint_run_artifact`, `set_sprint_summary_*`, and the `DELETE FROM sprints WHERE label = ?` in the `deleted` transition — so each scopes by `(label, project)`. Where a writer currently has no project argument, thread it through from the caller (sprint_manager `_sprint_db_set_state_sm` already passes project; dashboard callers pass repo). A write with empty project must NOT update another project's row.

Acceptance: (1) writing `sprint-66` for perf-coach never mutates commander's `sprint-66` row and vice-versa; (2) `record_sprint_finish('sprint-66', project=A)` updates only A's row; (3) deleting a sprint removes only the (label, project) row; (4) pytest covers cross-project isolation for start/finish/needs_rework/delete.
---
Make every sprints read project-scoped. Audit all `SELECT ... FROM sprints WHERE label = ?` (and `get_sprint_children`, and `children_of` in startup.py which does `WHERE parent_label = ?`) so they take and apply a `project` filter. `get_sprint(label, project)` already does this — make `project` effectively required at call sites that know the repo, and keep the label-only fallback only for genuinely project-agnostic callers (log when the fallback is hit so remaining ambiguity is visible). Scope `get_sprint_children(parent_label, project)` and `children_of(parent_label, project_root)` by the owning project so a lineage never pulls another project's same-numbered children. Update `is_sprint_running`, the board endpoint, reconcile service, bulk-complete/finish merge-step builders, and sprint_history_service reads to pass project. 

Acceptance: (1) `get_sprint_children('sprint-66', project=commander)` returns only commander's 66.x; (2) the board for perf-coach never shows commander's sprint-66 lineage or vice-versa; (3) bulk-complete merge steps for one project's lineage never include another project's branches; (4) grep shows no remaining label-only `WHERE label = ?` in lifecycle read paths without a logged fallback; (5) pytest covers two projects with identical sprint numbers rendering independent boards/history.
---
Data repair + regression guard for the live collisions. Using the audit manifest, write `scripts/repair_sprint_collisions.py --apply` that, for each "lost" (label, project) pair, recreates the missing `sprints` row from the best available source (plan.json/state.json/agent_runs) with the correct project and state, WITHOUT touching the surviving row. For the known cases: restore commander's `sprint-66` base row (state from its plan.json/lineage) so its 66.x children re-link, and clear the stale perf-coach `sprint-66` running row (already covered by the orphan sweep, but assert it here). Add a regression test that simulates the historical overwrite path and proves the composite key now prevents it. Document the composite-key invariant in `docs/architecture/` (sprint lifecycle): label is unique only per project; all lifecycle reads/writes are (label, project)-scoped.

Acceptance: (1) after repair on UAT, commander's sprint-66 lineage shows its own base state and the perf-coach sprint-66 ghost is gone; (2) re-running the old overwrite sequence under the composite key leaves both projects' rows intact; (3) architecture doc states the invariant; (4) full sprint-lifecycle + bulk-complete + board test suites pass.
```

## Notes

- **Order matters:** project backfill (ticket 1) and collision audit (ticket 2)
  MUST land before the PK rebuild (ticket 3); the rebuild collapses rows on
  `(label, project)`, so empty-project rows would merge incorrectly if run first.
- **Cannot un-merge lost data:** a row already overwritten by `ON CONFLICT(label)`
  is gone. Ticket 6 *reconstructs* the lost row from plan.json/state.json/
  agent_runs — it does not recover the original DB row. Set expectations
  accordingly in UAT.
- **SQLite PK change = table rebuild.** No `ALTER ... PRIMARY KEY`. Version-gate
  the rebuild so it runs once; keep `_migrate_sprints_state_check` and
  `_migrate_sprints_run_artifacts` working against the rebuilt table.
- **Already shipped, build on it:** `parent_label` persistence + backfill (B1),
  `_sweep_orphan_db_running_rows`, board lineage-from-DB supplement, and the
  display-only merged-PR finished signal. The composite key makes the
  per-project reads in those features exact instead of best-effort.
- **Deploy:** schema migration runs at dashboard startup (uvicorn restart).
  Run the backfill + audit scripts on UAT first, eyeball the manifest, then
  repair; repeat on PRD. Back up `commander.db` before the PK rebuild.
- **Test gate:** `touches-db-schema` — run the full lifecycle/board/bulk-complete
  suites, not just new tests, before merge.
