# Board tab — single aggregate API refactor

**Date:** 2026-06-29
**Sprint label:** NEW
**Default labels:** enhancement, backend
**Status:** drafted

Refactor the Board tab from ~`5 + 3N` API calls per refresh into ONE cached,
server-computed `GET /api/board` endpoint. Today `loadSprintMgmt` makes base
calls (`sprint-management/issues`, `running-all`, capacity, `summaries`,
`estimates/batch`) PLUS a per-sprint fan-out (`preview-dag`, `dep-order`,
`estimates/batch`) — one set per card — so ~20+ requests for ~6 sprints. The
fan-out also drives recurring render races (mini-rail patch ordering, stale
snapshots). Design contract: `docs/milestones/board-aggregate-api.md`.

**Constraints (apply to every ticket):**
- Aggregate reads the local `issues` mirror + SQLite ONLY — **zero `gh` calls
  per request** (reuse the mirror-backed paths already used by reconcile). Moving
  the N+1 onto live `gh` server-side is a regression.
- Reuse existing compute units (preview-dag builder, dep-order, run-stats,
  estimates/batch, backlog query, capacity) in one in-process pass — no HTTP
  self-calls.
- New routes live on a mounted router (COMMANDER_GATE_MONOLITH — no route added
  to `server.py`); logic in a sibling `_service` module, handlers stay thin.
- The SSE live channel stays as-is (snapshot from the aggregate, deltas over it).
- Roll out behind a flag; the old multi-call path stays as fallback. Scope is
  the **Board tab only** (History/Running are later); the running nav pill is
  out of scope.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add a server-computed Board aggregate model + `GET /api/board?project=<owner/repo>` (board tab refactor 1/3). Goal: return the entire Board model in one JSON so the frontend stops fanning out per-sprint. Create `apps/dashboard/routers/board_service.py` (logic) + a thin route on an already-mounted router (no route in server.py, per COMMANDER_GATE_MONOLITH). The response shape: `{project, generated_at, sections:{running[], needs_rework[], ready_to_merge[], draft[], lineage[], backlog:{count,tickets[]}}, capacity, summaries:{label:summary}}`. Each sprint card object must carry INLINE everything the card needs today via separate calls — lifecycle state, tickets, mini-rail (preview-dag levels + pre-flight hints), dep-order badge data, estimate hours, run/outcome stats. Build it in ONE in-process pass by calling the EXISTING compute units (the preview-dag builder, dep-order, run_stats_service, estimates batch, backlog query, capacity) keyed over the project's sprint set — do NOT make HTTP self-calls and do NOT add a second estimator pass. **Read the local `issues` mirror + SQLite only — zero `gh` subprocess calls per request** (mirror is already kept fresh and is what reconcile uses). Section bucketing + lineage collapse must match the current board (draft/planned → draft, rerun chains collapsed into lineage groups, running/needs_rework/ready_to_merge as today). No caching in this ticket (added next). Tests: model shape + required inline fields present per card; section bucketing incl. a rerun lineage collapsed correctly; an assertion that handling one request issues ZERO `gh` calls (patch/spy the gh transport). UAT: `curl /api/board?project=zealchaiwut/commander` returns all sections with inline mini-rail/dep-order/estimates and makes no gh calls (check rate_limit before/after is unchanged).
---
Add a per-project cache + invalidation for `GET /api/board` (board tab refactor 2/3). Depends on the aggregate endpoint. Add an in-memory per-project board snapshot cache with a short TTL (default ~8s, env/setting overridable) that coalesces rapid refreshes; the response includes `cache:{hit,ttl_s}`. Add `invalidate_board(project)` and call it from EVERY board-mutating write so the next load is fresh immediately rather than waiting out the TTL: sprint run/cancel, rerun, move-to-sprint / sprint-label change, batch-labels, reconcile-apply, bulk-complete, sprint create/delete/rename, and goal/schedule edits. Keep the computation itself mirror/DB-only (no gh). Be careful the cache is keyed by project (owner/repo) and never serves another project's snapshot (we have a history of cross-project bleed — scope strictly). Tests: cache hit within TTL returns the same snapshot without recomputing; TTL expiry recomputes; `invalidate_board` forces a recompute on next call; a write through each listed endpoint invalidates the cache for that project only (not others). UAT: load the board, move a ticket to a sprint, reload — the board reflects the move within one TTL or immediately; a second project's board is unaffected.
---
Make the Board frontend consume `GET /api/board` behind a feature flag, with the old multi-call path as fallback (board tab refactor 3/3, frontend). Depends on the aggregate endpoint + cache. Add a `COMMANDER_BOARD_AGGREGATE` feature flag (config.commander_features, env + global settings, default OFF). In `loadSprintMgmt` (apps/dashboard/static/src/sprint-board/board-render.js): when the flag is ON, make ONE `fetch('/api/board?project=')` and render every section + card from the inline payload; the per-sprint fan-out (`preview-dag`, `dep-order`, per-sprint `estimates/batch`) and the base `summaries`/`capacity` calls become NO-OPS because the data is already inline. When the flag is OFF, keep the current multi-call path exactly as-is (fallback). The existing SSE live-update layer must keep working — apply running/live deltas on top of the aggregate snapshot (the aggregate is the initial/refresh snapshot only; do not remove the EventSource wiring). Do not change the running nav pill. Rebuild the bundle (`npm run build`). Frontend label: enhancement, frontend. Tests: with the flag ON, a board load issues exactly one `/api/board` request and no per-sprint `preview-dag`/`dep-order` requests (assert via a fetch spy / count); with the flag OFF, the old calls still fire. UAT: toggle the flag on, hard-refresh the board — it renders identically to the flag-off board (section membership, mini-rails, dep-order badges, estimates, backlog) and the Network panel shows a single `/api/board` call plus the SSE stream.
```
