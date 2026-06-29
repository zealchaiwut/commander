# Running + History tabs — aggregate API refactor (sprint 2)

**Date:** 2026-06-29
**Sprint label:** NEW
**Default labels:** enhancement, backend
**Status:** drafted

Sprint 2 of the dashboard API-load refactor. Sprint 1 collapsed the **Board**
tab into one cached `GET /api/board` (see
`docs/bulk-create/2026-06-29-1-board-aggregate-api.md` +
`docs/milestones/board-aggregate-api.md`). This sprint applies the same pattern
to the **History** and **Running** tabs.

- **History** today: one paginated `GET /api/sprints/history` (already
  consolidated) PLUS a per-card `GET /api/sprints/{label}/run-stats` fan-out and
  a background reconcile sweep on every load — the run-stats fan-out is the
  remaining N+1.
- **Running** today: already an SSE channel (`/live`) + a separate logs stream;
  only the initial snapshot is a small fan-out.

**Constraints (apply to every ticket — same as sprint 1):**
- Aggregate reads the local `issues` mirror + SQLite ONLY — **zero `gh` calls per
  request** (the mirror is what reconcile uses).
- Reuse the existing compute units (run_stats_service, the history feed builder,
  the running snapshot) in one in-process pass — no HTTP self-calls.
- New/changed routes live on already-mounted routers (COMMANDER_GATE_MONOLITH —
  no route added to `server.py`); logic in `_service` modules, handlers stay thin.
- The SSE live channel + the logs stream stay as-is.
- Roll out behind a flag; the old path stays as fallback.
- "Refresh all" (the explicit reconcile sweep) stays a separate, on-demand action
  — it is NOT folded into the per-load aggregate.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Inline per-sprint run-stats into the History feed so the tab stops fanning out (history refactor 1/3). Today `GET /api/sprints/history` returns the rows, then the frontend fetches `GET /api/sprints/{label}/run-stats` once per card — the remaining N+1 on the History tab. Extend the history feed (sprint_history_service) so each returned row carries its run-stats block INLINE (the same payload run_stats_service.sprint_run_stats produces: stat chips / split-bar / gantt segments), computed in ONE in-process pass over the page's sprints by calling the existing run_stats compute unit — do NOT self-call the HTTP endpoint and do NOT change the run-stats shape the frontend already renders. Read the local issues mirror + SQLite only — zero `gh` per request. Keep the existing pagination + active_only behavior. Do NOT fold the background reconcile sweep into this — "Refresh all" stays a separate on-demand action. Tests: a history response includes the inline run-stats block per row with the same fields the per-card endpoint returns; zero `gh` calls while building the feed (spy the gh transport); pagination/active_only unchanged. UAT: `curl /api/sprints/history?project=zealchaiwut/commander` returns rows each with an inline run-stats object, and a History load makes no per-card `/run-stats` requests.
---
Make the History frontend consume the inline run-stats behind a flag, with the per-card fetch as fallback (history refactor 2/3, frontend). Depends on the inline-run-stats feed. Reuse the `COMMANDER_BOARD_AGGREGATE` flag (or add `COMMANDER_HISTORY_AGGREGATE` if you want them independent — pick one and note it). In history.js: when the flag is ON, render each card's run-stats from the inline block already on the history row and SKIP the per-card `GET /api/sprints/{label}/run-stats` fetch (the `_histFetchRunStats`/`run-stats` path becomes a no-op when the data is present); when OFF, keep the current per-card fetch (fallback). Do not change the "Refresh all" / reconcile button or the expand/collapse behavior. Rebuild the bundle (`npm run build`). Frontend label: enhancement, frontend. Tests: with the flag ON, expanding/loading History issues no `/run-stats` requests (assert via a fetch spy) and the stat chips/split-bar/gantt render identically to the flag-off path; with OFF, the per-card fetch still fires. UAT: toggle the flag, open History, expand a few cards — run-stats render identically and the Network panel shows zero `/run-stats` calls.
---
Consolidate the Running tab's initial snapshot into one call, keeping SSE + logs separate (running refactor 3/3). The Running pane already updates live via the SSE `/live` channel and streams logs separately; only the initial paint still assembles state from a small fan-out. Add (or reuse) a single `GET /api/running?project=` snapshot that returns the running sprint(s)' current status + per-ticket progress the Running pane needs for first paint, computed mirror/DB-only (zero `gh`). The frontend Running view loads this ONE snapshot for initial render, then applies SSE deltas on top exactly as today (do NOT remove or change the EventSource wiring or the logs stream). Behind the same refactor flag, with the current path as fallback. Do NOT touch the running nav pill. Rebuild the bundle. Frontend label: enhancement, frontend. Tests: the snapshot endpoint returns running status with zero `gh` calls; with the flag ON, opening Running issues one `/api/running` request plus the SSE stream (no extra status fan-out). UAT: open the Running tab during a live sprint — it paints from one snapshot call and continues updating live via SSE; logs still stream.
```
