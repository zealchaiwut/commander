# Milestone: Board tab — single aggregate API

**Status:** planned
**Scope:** Board tab only (History + Running are separate, later milestones)
**Owner:** —
**Goal:** Collapse the Board tab's ~20+ API calls per refresh into ONE cached,
server-computed endpoint, killing the per-sprint N+1 fan-out and a class of
frontend race bugs, while keeping live updates on the SSE layer.

---

## Why

The Board tab fans out per sprint. On every refresh it makes roughly:

**Base (once):**
1. `GET /api/sprint-management/issues` — sprints + tickets + backlog
2. `GET /api/sprints/running-all` — running state for the cards
3. capacity (`_smgmtEnsureCapData`)
4. `GET /api/sprints/summaries` — finished-sprint summaries
5. `GET /api/estimates/batch` — backlog estimate hours

**Per active sprint (× N):**
6. `GET /api/sprints/{label}/preview-dag` — the mini-rail
7. `GET /api/sprints/{label}/dep-order` — dep-order badge
8. `GET /api/estimates/batch` (per sprint header)

So **≈ `5 + 3N`** requests (~20+ for ~6 active sprints). The cost is the `3N`
fan-out, not the base 5. The fan-out also drives recurring frontend bugs
(mini-rail patch races, stale snapshots, dead handlers) because the board is
assembled from many async responses that can arrive out of order.

## What (TO-BE)

**One endpoint:** `GET /api/board?project=<owner/repo>` → the whole board model
in a single JSON, computed server-side in one pass from SQLite + the issues
mirror (zero GitHub quota), cached with a short TTL and invalidated on writes.

The frontend renders one consistent payload. Live running updates continue to
arrive via the existing SSE channel (the aggregate is the snapshot; SSE carries
deltas on top) — SSE is **not** a per-refresh call and is out of this count.

### Response contract (draft)

```jsonc
{
  "project": "owner/repo",
  "generated_at": "<iso>",
  "cache": { "hit": true, "ttl_s": 8 },
  "sections": {
    "running":      [ <sprintCard> ],   // lifecycle running
    "needs_rework": [ <sprintCard> ],
    "ready_to_merge": [ <sprintCard> ],
    "draft":        [ <sprintCard> ],
    "lineage":      [ <lineageGroup> ], // collapsed rerun chains
    "backlog":      { "count": N, "tickets": [ <ticket> ] }
  },
  "capacity": { ... },
  "summaries": { "<label>": <summary> }
}
```

`<sprintCard>` carries everything the card needs **inline** — state, tickets,
mini-rail (preview-dag levels + pre-flight hints), dep-order badge, estimate
hours, run/outcome stats. No follow-up per-sprint fetch.

## Design

- **One pass, server-side.** Reuse the existing compute units rather than
  reinventing: the preview-dag builder, dep-order, `estimates/batch`,
  `run-stats`, the backlog query, capacity. Call them in a single batched pass
  keyed by the project's sprint set — not once per HTTP request.
- **Mirror/DB only — zero gh per request.** The aggregate must read the local
  `issues` mirror + SQLite (already mirror-backed for reconcile). No live `gh`
  fan-out (that would just move the N+1 to the server and burn quota).
- **Cache + invalidation.** In-memory per-project snapshot, short TTL
  (≈5–10 s) to coalesce rapid refreshes, PLUS explicit invalidation on every
  board-mutating write: run/cancel, rerun, move-to-sprint, label change,
  reconcile-apply, bulk-complete, create/delete/rename. Stale board is the main
  failure mode — invalidation correctness is the hard part.
- **Live layer untouched.** Running cards stay live via SSE deltas applied over
  the snapshot. The aggregate provides the initial/refresh snapshot only.
- **Progressive option (decide in Phase 1):** if the cached aggregate can't
  reliably return in < ~300 ms, keep a fast base payload + lazily-loaded heavy
  bits rather than one blocking call that paints a blank board.

## Phases

### Phase 1 — Backend: the board model + cache (no frontend change)
- New `routers/board_service.py` (logic) + a thin route on a mounted router
  (`GET /api/board`), per COMMANDER_GATE_MONOLITH (no route in server.py).
- Assemble `sections` from the lifecycle DB + mirror in one pass; inline
  mini-rail/dep-order/estimates/run-stats per sprint by calling the existing
  builders in-process (no HTTP self-calls).
- Per-project in-memory cache with TTL + an `invalidate_board(project)` hook;
  wire the hook into every board-mutating endpoint.
- Unit tests: model shape, section bucketing, lineage collapse, cache
  hit/TTL/invalidation, zero-gh assertion.

### Phase 2 — Frontend: consume behind a flag
- `loadSprintMgmt` gains an aggregate path: one `fetch('/api/board')`, render
  from the payload; the per-sprint fan-out (preview-dag/dep-order/estimates)
  becomes a no-op when the data is already inline.
- Gate on a feature flag (`COMMANDER_BOARD_AGGREGATE` / commander_features), old
  multi-call path retained as fallback.
- Keep the SSE delta application working over the aggregate snapshot.

### Phase 3 — Measure, cut over, remove the old path
- Compare first-byte + total load AS-IS vs TO-BE on a real project (≥5 sprints).
- Parity check: rendered board identical between paths.
- Flip the flag default on; after a soak, delete the dead per-sprint fan-out
  fetches + the old base calls superseded by the aggregate.

## Rollout / flags
- `COMMANDER_BOARD_AGGREGATE` (env + global settings), default OFF until Phase 3.
- Old multi-call path is the fallback for the whole rollout — given how fragile
  the board render is, do NOT hard-cut.

## Risks & mitigations
| Risk | Mitigation |
|---|---|
| First-byte slower than progressive | Cache aggressively; keep progressive option if > ~300 ms |
| Stale board (cache invalidation bugs) | Short TTL + explicit invalidate on every write; SSE for running |
| Big regression in fragile render code | One tab, behind a flag, old path as fallback, parity check |
| GitHub quota | Mirror/DB only; zero-gh test asserts no `gh` subprocess per request |
| Live updates break | SSE layer untouched; snapshot + deltas tested together |

## Acceptance
- Board load issues **1** request (+ SSE), down from ~`5 + 3N`.
- Cached `/api/board` p50 < ~300 ms, zero `gh` calls per request.
- Rendered board is byte-parity with the old path on a ≥5-sprint project.
- Mutations (run/move/reconcile/bulk-complete) reflect within one TTL or
  immediately via invalidation.

## Out of scope
- History + Running tabs (separate milestones; reuse this pattern).
- The running nav pill.
- The SSE live channel (kept as-is).
- On-demand action endpoints (create/delete/rename/split/reconcile/run) — they
  only gain an `invalidate_board()` call, no behavior change.
