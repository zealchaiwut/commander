# 2026-07-02-6-draft-db-row-at-create

> Status: decided | provisional

## Context

`auto_run=false` rerun children exist only as plan.json (`needs_rework`/`queued`),
no DB row — `sprint_state.current()` cannot see them. Related: sprint creation
writes no DB row at all (missing row = implicit `draft`). The DB is incomplete
pre-run.

## Options

- **A ★ Write a DB row at creation/queue time** (`draft` at create, or a
  queued marker at rerun-queue), making the DB genuinely complete. Pairs
  naturally with Q1 option A (with `planned`/signoff deprecated, use `draft` at
  create and at rerun-queue).
- **B Accept plan.json as the pre-run store;** DB authority starts at first
  dispatch. Document only (done).

## Decision

**A — write a DB row at creation/queue time** (provisional — auto-adopted ★
recommendation after interactive timeouts; operator may veto) so the DB is
complete pre-run. With Q1 resolved: use `draft` at create and at rerun-queue.

## Consequences

- New `db.ensure_sprint_draft_row(label, project)` — idempotent `INSERT OR IGNORE`
  of a `draft` row, never clobbering an existing row in any state.
- Wired into sprint creation (`routers/sprints_service.py`) and the
  `auto_run=false` rerun-queue path (`routers/sprint_run.py`), both best-effort
  alongside existing plan.json writes.
- Queued-child DB row kept at `draft` rather than mirroring plan.json's
  `needs_rework`/`queued` value — that value is display-only (to show a Run
  button in History), not a real lifecycle transition. Writing it into the DB
  would violate state-machine invariants.
- A never-created sprint still reads as `"unknown"` via `sprint_state.current()`
  until this helper runs; legacy pre-#1693 sprints are unaffected.

## Implemented-by (#N)

#1693 (`fix/1686-1698-flow-decisions`)
