# Commander Design Document

## Architecture Overview

Commander consists of:
- FastAPI backend (`apps/dashboard/server.py`) serving real-time sprint board
- SQLite DB (`dashboard.db`) for agent events and lifecycle state
- Optional Neon/Postgres for sprint metadata (disabled locally)
- Frontend bundle (`static/dist/bundle.js`) built via esbuild

## Sprint Lifecycle — Composite Key Invariant

**Critical invariant:** Sprint identity is `(label, project)`, not `label` alone.

All reads, writes, and bulk operations MUST scope to both fields:
- Create sprint: insert `(label, project, state, ...)`
- Query child sprints: `WHERE parent_label = ? AND project = ?`
- Update state: `WHERE label = ? AND project = ?`
- Bulk complete: `UPDATE sprints SET state = 'complete' WHERE label LIKE ?N AND project = ?`

This prevents cross-project collisions (e.g., commander's sprint-66 being overwritten by perf-coach's sprint-66).

## Repair Strategy (Issue #1465)

When historical single-key overwrites orphan sprints:
1. Audit manifest lists affected `(label, project)` pairs
2. Repair script queries `plan.json` → `state.json` → `agent_runs` for best source
3. Recreate base row with correct composite key
4. Regression test replays overwrite sequence, asserts both projects' rows intact

## Test & Verify

- Sprint lifecycle tests confirm composite-key scoping
- Bulk-complete tests verify project-scoped filtering
- Board tests render correct lineage per project
- Regression test blocks future single-key regressions
