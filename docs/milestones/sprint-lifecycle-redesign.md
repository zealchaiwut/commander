# Milestone — Sprint Lifecycle Redesign

> **Status:** P0 done; P1–P4 not started. Tracking file for the fixes agreed
> on 2026-06-12.
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

- [ ] New enum `draft → planned → running → ready_to_merge | needs_rework →
  completed` (+ derived `partial_finished`, + `deleted`) in plan.json
  (`_VALID_PLAN_STATES`, `server.py:4988`), the `sprints` DB table, and both
  panes. Display mapping for legacy rows (`cancelled`→`needs_rework`,
  `finished`→`completed`); forward-only, no DB rewrite.
- [ ] Replace all five `cancelled` write sites with `needs_rework` +
  `end_reason` (`server.py:444, 5228, 5284, 5416, 6829-6832`).
- [ ] Cancel endpoint (`server.py:6770-6841`): post the reason
  (`stopped by user` / `process lost`) to the sprint summary issue; never
  label tickets `need-rework` on a user cancel.
- [ ] Derive `partial_finished` server-side from children's states
  (generalize the client-side `has_rerun_child` chip,
  `static/project.html:10242-10254`).
- [ ] Retire the history failed-state heuristics once the enum is written at
  the source (`routers/sprint_history_service.py:465-500`).

## P2 — Branch / merge model

- [ ] Child sprint branches created **off the base branch**, not develop
  (`services/sprint_manager/sprint_manager.py:2761-2789`).
- [ ] End-of-run PR for a child targets **base**, not develop; remove the
  end-of-run auto-merge to develop (`sprint_manager.py:5403-5443`). Passing
  work merges child → base even on a `needs_rework` run (UAT label = merged
  to base).
- [ ] Rework Finish Sprint → **Merge Sprint** (`server.py:9947-10090`): merge
  leftover child branches in label order → base → develop, close issues and
  PRs, **keep all labels** (delete the label-strip at `server.py:10044`).
  Merge Sprint is the single human UAT sign-off — no per-ticket gate.

## P3 — Reconciliation (GitHub → API → disk-as-cache)

- [ ] Per-label run artifacts: sprint manager currently writes one shared
  `sprint-68-state.json` for every child — siblings overwrite each other
  (worked around by `_sprint_has_own_run_outcome`, `server.py:8044`).
- [ ] End-of-run disk → DB ingest; remove render-time disk reads from the
  outcome endpoint (`server.py:8100-8160`) and history service
  (`routers/sprint_history_service.py` steps 2–3).
- [ ] Background GitHub reconcile triggered on browser refresh
  (stale-while-revalidate): serve cached DB row instantly, reconcile
  labels/issues/PRs/summary issue in the background, push deltas.

## P4 — UI

- [ ] Board: unified lifecycle badges; remove the Cancelled card variant
  (`board-render.js:700`, CSS `project.html:2546`); no run-stats or retry
  history on the board.
- [ ] History: `partial_finished` rows instead of "0 tickets"; duration = DB
  `started_at → ended_at` only; auto-refresh via the existing
  `sprint_finished` broadcast (`server.py:10068` → `_histLoadLedger`);
  verbs gated by state (`needs_rework` → Re-run; `ready_to_merge` /
  `completed` → Merge, Delete only).
- [ ] Run-stats: emit `crash` only for `needs_rework` sprints (today always
  emitted, `routers/run_stats_service.py:204-207`, filtered client-side at
  `project.html:10305`).

## Notes

- Until P3 lands, every new sub-sprint silently overwrites its siblings'
  outcome data (shared state file) — keep re-runs minimal in the meantime.
- The documentor's auto-managed sprint-history region moved with the rename
  `docs/milestones.md` → `docs/todo.md`; documentor config must follow before
  the next sprint finishes (see todo.md note).
