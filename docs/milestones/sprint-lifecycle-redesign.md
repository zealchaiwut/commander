# Milestone — Sprint Lifecycle Redesign

> **Status: CLOSED — P0–P4 all shipped** through sprints 73.x (lifecycle
> commits `ca32b8c`…`d9f1e54` on `develop`). Tracking file for the fixes agreed
> on 2026-06-12; kept for history. Forward-looking work moved to
> [`post-lifecycle-backlog.md`](post-lifecycle-backlog.md).
> **Design contract:** [`../architecture/sprint-lifecycle.md`](../architecture/sprint-lifecycle.md)
> — reread that doc before picking up any item here or re-opening the design
> discussion. Code evidence (file:line) below was audited against branch
> `fix/sprint-rerun-uat-tester` on 2026-06-12.

## Why

Sprint 68.6 ran three times under one label and simultaneously showed
*Completed* (board), *Cancelled* (history badge), *0 tickets* (history card),
and a 44-minute wall time (run-stats) — four sources of truth disagreeing.
The design doc unifies them: GitHub = state truth, DB = metrics truth, disk =
write-once artifact; one lifecycle enum; child branches merge to base; no
same-label re-runs.

## P0 — Stop the bleeding

- [x] **Block same-label re-dispatch.** *Done 2026-06-12.*
  - Board: has-rework cards (with or without tickets) now render the Re-run →
    child button; same-label "Run Sprint" only exists for first attempts
    (`static/src/sprint-board/board-render.js`, `_smgmtCardHtml`).
  - Server: `POST /api/sprints/run` rejects labels whose plan state is
    terminal with 409 (`_reject_terminal_label_redispatch`, plus
    `_TERMINAL_PLAN_STATES = {completed, cancelled}` in
    `apps/dashboard/server.py`).
  - Re-runs reuse the existing child-sprint flow (`rerun-preview` / `rerun`
    endpoints) — nothing rebuilt.
  - Tests: `tests/test_lifecycle_p0__same_label_redispatch_guard.py`.
- [x] **Restart the dashboard** so the live history feed picks up the
  agent-runs backfill (fixes the "0 tickets" card for 68.6 with zero code).
  *Done 2026-06-12: history API now returns #894 for sprint-68.6.*

## P1 — Lifecycle enum (foundation)

- [x] **New unified enum.** *Done 2026-06-12.*
  - `db.py`: `LIFECYCLE_STATES` + `canonical_lifecycle()` display mapping
    (`cancelled`/`failed`→`needs_rework`, `finished`→`completed`,
    `planning`→`draft`); sprints-table CHECK extended with an in-place table
    rebuild for pre-P1 databases (forward-only — legacy rows kept verbatim).
  - `server.py`: `_VALID_PLAN_STATES` / `_TERMINAL_PLAN_STATES` /
    `_NOT_RUNNING_PLAN_STATES` carry the new states; new plan.json files are
    created as `draft`; outcome + finish-card endpoints return a `lifecycle`
    field alongside the legacy pane `state`.
  - Sprint manager writes the terminal state **at the source**:
    `ready_to_merge` when every ticket passed, `needs_rework` on any failure
    (`end_reason=ticket-failures` / `no-dispatchable-tickets`).
- [x] **`cancelled` write sites replaced** with `needs_rework` + `end_reason`
  (*process lost* for orphan-PID sweeps ×3 and DB reconcile, *stopped by
  user* for the cancel endpoint and the sprint manager SIGTERM handler).
  `db.record_sprint_cancel/fail` are deprecated aliases that write
  `needs_rework`.
- [x] **Cancel endpoint** posts the reason to the sprint summary issue
  (best-effort comment) and never labels tickets `need-rework` — failure
  labeling stays exclusive to the sprint manager's failure paths.
- [x] **`partial_finished` derived server-side** in
  `sprint_history_service._finalize_records`: a terminal parent with
  unsettled children shows `partial_finished` (+ `partial_children`); when
  the last child completes the parent flips to `completed`. The client-side
  `_histIsPartialCompleted` inference was removed.
- [x] **History heuristics retired**: `_infer_failed_lifecycle` deleted; the
  only promotion kept is the recorded fact "failed tickets ⇒ needs_rework".
  History chips/verbs use the unified vocabulary (Resume verb retired —
  needs_rework re-runs into a child).
- Tests: `tests/test_lifecycle_p1__unified_enum.py` (+ contract updates in
  805/806/507/757 suites).

## P2 — Branch / merge model

- [x] **Child sprint branches off base branch.** *Done 2026-06-12.*
  - `_create_sprint_branch(sprint_branch, parent_ref=…)` creates base sprints
    off `develop`, child sprints off `sprint/sprint-N`.
  - `run_sprint` defaults `target_branch` to `_sprint_branch_for_label(label)` so
    per-ticket merges land on the active sprint branch; child branches are still
    created off the base via `parent_ref=_base_sprint_branch(label)`.
- [x] **End-of-run PR targets base; no auto-merge to develop.**
  - `_create_sprint_pr(..., pr_base=…)` creates child→base PRs only; the
    auto-merge block to develop was removed.
- [x] **Finish Sprint → Merge Sprint** (`server.py` finish endpoints): merge
  leftover child branches in label order → base → develop, close issues,
  **keep all labels** (label-strip removed). UI strings updated in
  `finish-modal.js`, `board-render.js`, `project.html`, bundle.
  - Tests: `tests/test_lifecycle_p2__branch_merge_model.py`.

## P3 — Reconciliation (GitHub → API → disk-as-cache)

- [x] **Per-label run artifacts.** *Done 2026-06-12.*
  - ``_state_path`` / ``_summary_path`` write ``{label}-state.json`` and
    ``{label}-summary-*.md`` so child sprints no longer overwrite siblings.
  - ``sprint_artifact_service.resolve_state_path`` reads per-label files with
    a legacy base-file fallback when ``sprint_label`` matches.
- [x] **End-of-run disk → DB ingest.**
  - ``db.ingest_sprint_run_artifact`` stores issues, tokens, duration,
    reconciliation, summary URL, PR, post-sprint block on the ``sprints`` row.
  - Sprint manager calls ``_sprint_db_ingest_run_sm`` after the final
    ``state.save()`` at run end.
  - Outcome endpoint serves ingested DB rows first; history lifecycle rows
    skip disk enrichment when ``run_ingested_at`` is set (legacy disk fallback
    kept for pre-P3 rows).
- [x] **Background GitHub reconcile on history refresh.**
  - ``GET /api/sprints/history?project=…`` schedules
    ``sprint_reconcile_service.reconcile_project_background`` (stale-while-
    revalidate) and broadcasts ``sprint_reconciled`` when lifecycle drift is
    corrected.
  - Tests: ``tests/test_lifecycle_p3__reconciliation.py``.

## P4 — UI

- [x] Board: unified lifecycle badges; remove the Cancelled card variant
  (`board-render.js`, CSS `project.html`); no run-stats or retry history on
  the board (`_smgmtOutcomeLogHtml` removed from card render).
- [x] History: `partial_finished` rows show child-sprint links instead of "0
  tickets"; duration stays on DB `started_at → ended_at`; auto-refresh via
  `/events` SSE (`sprint_finished`, `sprint_reconciled`) and board auto-refresh
  when History sub-view is open; verbs gated by unified lifecycle state.
- [x] Run-stats: emit `crash` only for `needs_rework` sprints
  (`routers/run_stats_service.py`).
- Tests: ``tests/test_lifecycle_p4__ui_polish.py``.

## Notes

- Pre-P3 history/outcome rows without ``run_ingested_at`` still read disk as a
  legacy fallback; new runs ingest at end-of-run automatically.
- The documentor's auto-managed sprint-history region moved with the rename
  `docs/milestones.md` → `docs/todo.md`; documentor config must follow before
  the next sprint finishes (see todo.md note).
