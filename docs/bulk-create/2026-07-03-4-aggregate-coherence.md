# Aggregate Coherence — Invalidation Push, Shared Cache, Observability, Flag Cutover

**Date:** 2026-07-03
**Sprint label:** sprint-112
**Default labels:** backend, performance
**Status:** posted

Source: 2026-07-03 gap analysis of the API-refactor arc (sprint-102 board
aggregate, sprint-103 running/history snapshots, plus the 2026-07-03-1..3
drafted batches), verified against develop @ 4043bd1b. These six tickets fill
the gaps between those workstreams: nothing here duplicates them. Sequencing:
prompts 1-4 belong right after sprint-102; prompt 5 (cutover) runs after
sprint-103 plus a soak cycle; prompt 6 (docs) closes the arc.

Note: two further gaps were folded elsewhere rather than drafted here — the
board-payload extension went into #1636/#1638 triage notes (sprint-102), and
the multi-slot lanes log fan-out was folded into the SSE prompt of
2026-07-03-2-frontend-call-reduction.md.

## Prompts

```
Broadcast board invalidation over the existing /events SSE so open clients refetch instead of going stale.

Sprint-102's invalidate_board (#1643) only clears the SERVER cache. Open clients learn of board changes solely via sprint_finished/sprint_reconciled on /events (project.html:13919) or timers — mutations from another tab, from the sprint_manager subprocess (label transitions reach the DB via the 60s mirror sync, not the 13 endpoints #1643 hooks), or direct GitHub edits leave every open board stale until manual refresh.

Fix: (1) have invalidate_board(project) also broadcast a board_invalidated {project} event on the existing /events broadcast mechanism (logs_service._subscribers); (2) emit the same event from the issues-mirror sync when a sync actually changes issue rows for a project (not on 304s); (3) frontend: when the board-aggregate flag is ON, listen for board_invalidated on the existing /events EventSource and debounce-refetch /api/board (≥2s debounce, only when tab visible per the visibility-guard ticket). Document the precedence rule in docs/milestones/board-aggregate-api.md: SSE-pushed invalidation wins over TTL; TTL is the fallback ceiling; a manual refresh always bypasses.

AC must cover: mutating a sprint via API in one client refetches the board in another connected client within the debounce window; a mirror sync that changes rows emits exactly one event per project; 304 syncs emit nothing; no refetch storm under rapid successive mutations (debounce test); flag OFF = no listener behavior change.
---
Extract a shared per-project aggregate-cache helper and move /api/home aggregation out of startup.py onto it.

Three aggregation layers are growing three ad-hoc cache patterns: the board cache (sprint-102 #1642), /api/home's _home_cache (startup.py:1737+, 30s per-slug, invalidated ONLY by settings writes at routers/settings_service.py:167 — sprint mutations never invalidate it), and the coming running/history caches (sprint-103). #1643's mutation hooks feed only the board cache.

Fix: (1) extract a shared helper (e.g. apps/dashboard/aggregate_cache.py): per-project key, TTL, invalidate(project), cache:{hit,ttl_s} response metadata, hit/miss counters (see the observability ticket); (2) port the board cache (#1642's implementation if landed, else implement #1642 on this helper directly); (3) move _home_project_data/_home_cache out of startup.py into a service module using the same helper, and register home invalidation on the same mutation hooks as invalidate_board so sprint mutations refresh the home payload too; (4) keep response shapes byte-identical.

AC must cover: board and home caches share the helper; sprint mutation invalidates BOTH board and home entries for that project only (cross-project isolation test); /api/home response unchanged for a fixture project; startup.py line count decreases (COMMANDER_GATE_MONOLITH-friendly); settings-write invalidation still works.
---
Add API-call-volume and cache-hit observability: GET /api/debug/api-volume.

The arc's cutover decisions ("compare AS-IS vs TO-BE", docs/milestones/board-aggregate-api.md Phase 3) and several batch ACs assume call-count measurement, but nothing counts requests: HTTP middleware exists (apps/dashboard/server.py:307/313) yet has no counters; github_client._cached, the mirror fallback path, _home_cache, and the new aggregate caches expose no hit/miss stats; token_usage covers LLM cost only.

Fix: in-memory counters only, no persistence. (1) Per-path request counter in the existing middleware (normalize path params, e.g. /api/sprints/{label}/live). (2) Hit/miss counters on github_client._cached, the mirror-vs-gh-fallback branch, and each aggregate cache (via the shared helper's counters). (3) A gh-subprocess counter in the one place gh commands are spawned. (4) GET /api/debug/api-volume returning counts since process start: top-N paths by count, per-cache hit rates, gh subprocess total, uptime. Follows the existing /api/debug/* convention (e.g. /api/debug/token-usage/by-agent-model).

AC must cover: known request sequence produces exact expected counts; cache hit/miss increments verified for one cached and one uncached call; gh counter increments on a forced fallback; endpoint output shape stable and documented in the response itself (self-describing keys); counter overhead is O(1) dict increments (no locks beyond what middleware already holds, single-process uvicorn).
---
Shared call-count-budget test harness so API-call regressions go red.

#1638, #1640, #1644, #1648 and the call-reduction batch each spec their own one-off fetch-spy or counter assertion; nothing enforces the budgets after those tickets close — the next board feature can quietly re-add a per-sprint fetch loop with no failing test. tests/ has no shared instrumentation helper.

Fix: (1) backend helper: pytest fixture that monkeypatches the gh runner and (if the observability ticket landed) reads the middleware counters, exposing assert_call_budget(path_pattern, max_calls) and assert_zero_gh(); (2) frontend-path helper: a patched-fetch counter usable from the existing HTML-harness tests; (3) a budget test module encoding the arc's targets: board load = 1 aggregate call (flag ON), home load ≤ 4, history feed load = 1, running tab first paint = 1, zero gh subprocess per aggregate request; budgets that depend on unshipped tickets are marked xfail with the ticket number so they flip to enforced as each lands.

AC must cover: helper importable from both new and existing test modules; deliberately adding an extra fetch in a fixture page fails the budget test; xfail-to-pass flip demonstrated for at least one budget; documented one-liner usage in tests/README or module docstring.
---
Flag cutover and legacy-path removal: flip COMMANDER_BOARD_AGGREGATE, COMMANDER_HISTORY_AGGREGATE, and the running-snapshot flag to default-ON, then delete the old fan-out paths.

Three tickets add feature flags (#1638 board, #1640 history, #1646 running) and none removes them — docs/milestones/board-aggregate-api.md:108 defines Phase 3 (measure, cut over, remove old path) but no issue exists for it, and sprint-103 has no removal story. Classic flag debt: three flags, three dead fallback code paths.

Fix, per flag (board, history, running — can ship as three commits in one ticket): (1) parity check: with /api/debug/api-volume numbers and a side-by-side render comparison (aggregate vs legacy) on a fixture project, confirm no data regression; (2) flip the default to ON in config.py; (3) after one soak sprint cycle with no rollback, delete the legacy multi-call/per-card-fetch code paths and the flag plumbing from board-render.js, history.js, and the inline running view, plus the flag entries in config.py; (4) rebuild the bundle. PREREQUISITE: sprint-102 and sprint-103 fully landed and soaked; the observability ticket provides the before/after numbers.

AC must cover: defaults ON; legacy paths and flag reads deleted (grep-clean for the three flag names); call budgets from the harness ticket still green; bundle rebuilt; before/after call-volume numbers recorded in the PR description.
---
Docs: cache inventory and invalidation-contract table in docs/architecture.

docs/architecture/1_state-and-source-of-truth.md documents the 30s github_client cache, the 60s issues mirror, and _home_cache, but predates the board/aggregate caches, the client-side session caches and short-TTL nav-status cache from the call-reduction batch, and the PR-lookup caches from the mirror-routing batch. Five-plus cache layers with no single statement of TTLs, invalidation triggers, or acceptable staleness per surface.

Fix: docs-only. Add a cache-inventory section to 1_state-and-source-of-truth.md (or a new docs/architecture/caching.md linked from it): one table row per layer — layer name, location, keying, TTL, invalidation trigger(s), staleness contract (what surface reads it and how stale is acceptable), added-by ticket. Include the precedence rule from the invalidation-push ticket (SSE invalidation > TTL; manual refresh bypasses). State the single-process caveat (in-memory caches assume uvicorn single-process; a --workers deployment breaks them).

AC must cover: every cache layer present in code at merge time has a row; precedence rule stated; single-process caveat stated; linked from the architecture hub doc.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| 1785 | Broadcast board_invalidated over SSE for live client refresh | — |
| 1786 | Extract shared aggregate-cache helper; move /api/home out of startup.py | — |
| 1787 | Add API call-volume and cache-hit observability endpoint | XL |
| 1788 | Add shared call-count-budget test harness | — |
| 1789 | Cut over aggregate flags and delete legacy fan-out paths | L |
| 1790 | Add cache inventory and invalidation-contract table to architecture docs | L |
