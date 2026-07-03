# P1 Critical Bugs — Broken Features, DB Poisoning, Sprint Deadlock

**Date:** 2026-07-02
**Sprint label:** NEW
**Default labels:** bug
**Status:** drafted

Source: `docs/bug-audit-2026-07-02.md` (multi-agent audit, adversarially
verified). This batch = the four P1s. All are unconditional breakage:
two dead features, one DB-poisoning path, one whole-sprint deadlock.

## Prompts

```
Fix github_client.list_milestones: three duplicate definitions, effective one POSTs instead of GETs.

apps/dashboard/github_client.py defines list_milestones three times (lines ~610, ~961, ~1059 — later two marked noqa: F811); Python binds the last. That definition calls _json("api", f"repos/{r}/milestones", "-f", "state=all", "-f", "per_page=100") with no -X GET. gh api defaults to POST when -f params are present, so every call hits the CREATE-milestone endpoint and GitHub returns HTTP 422 — verified live. Consequences: POST /api/sprints/plan-next (sprints_service.py:318, no try/except) 500s every time so sprint planning is unusable; the Roadmap tab (roadmap_service.py:159) catches the error as _GH_UNAVAILABLE and permanently renders zero milestones.

Fix: delete the duplicate definitions, keep ONE list_milestones that issues a real GET (use -X GET and --paginate like the first definition), and normalize the return shape callers expect (active_milestone accepts dueOn/due_on). Add a regression test asserting the gh invocation includes -X GET, and a test that plan-next and roadmap surface milestones from a stubbed response. Grep for other gh api call sites using -f without -X GET and fix any siblings in the same file.

AC must cover: single definition remains; call issues GET not POST; plan-next returns 200 with milestones; Roadmap renders milestones; no other -f-without-GET read call sites in github_client.py.
---
Fix composite-PK poisoning: _backfill_sprint_project UPDATE collision + unscoped lifecycle writers creating phantom project='' rows.

Two coupled defects. (a) apps/dashboard/db.py:996-1007 _backfill_table runs UPDATE sprints SET project=? WHERE label=? AND (project='' OR project IS NULL) with no protection against the target (label, project) row already existing. sprints has PRIMARY KEY (label, project), and the migration's dedup deliberately keeps both a ('label','') row and a scoped row. When the resolver maps the '' row to the same repo, the UPDATE raises sqlite3.IntegrityError — and because _backfill_sprint_project runs from _create_sprint_lifecycle_tables at ~15 lifecycle entry points (get_sprint, transition_sprint_state, list_sprints_lifecycle, ingest_sprint_run_artifact...), EVERY sprint read/write then fails until manual DB surgery. Reproduced on a scratch DB. (b) The '' rows keep being created: apps/dashboard/startup.py:410 startup plan.json sweep writes lifecycle transitions without project=, and db.py transition_sprint_state/_set_sprint_terminal/record_sprint_finish default project="".

Fix: (1) make the backfill merge-safe — if the scoped target row exists, merge (prefer non-null/newer fields) or delete the '' row instead of UPDATE; wrap per-row so one bad row cannot poison the sweep. (2) Pass project= at the startup sweep call site and audit remaining default-"" writers; a write with empty project must never create a new row when any scoped row for that label exists. (3) One-off cleanup for existing ('label','') duplicates.

AC must cover: seeded duplicate pair + agent_runs row no longer raises IntegrityError on get_sprint/record_sprint_finish; startup sweep writes scoped rows; regression test replaying the reproduction; existing composite-key tests still pass.
---
Harden concurrent scheduler workers: unhandled stage exception must fail the ticket, not deadlock the sprint.

services/sprint_manager/concurrent_scheduler.py:227 worker() calls code_fn/test_fn with no try/except. If a stage raises, the thread dies with the ticket still in coding_set and never moved to terminal, so _finished() never becomes true; remaining workers block in cond.wait() with no timeout and the join() loop (~line 286) hangs the sprint process forever — a silent zombie sprint. A concrete production raiser exists: WorktreePool.acquire() raises RuntimeError when a slot cannot be recreated (worktree_pool.py:225) and propagates through pipeline._coder_stage line ~798 where the acquire sits OUTSIDE the try/finally; handle_post_tester/_run_quality_gates can also raise. The same structural flaw exists in pipeline._run_pipeline's coder_loop/tester_loop (pipeline.py:261, 285).

Fix: wrap each stage call in try/except in BOTH schedulers (concurrent_scheduler worker and pipeline coder_loop/tester_loop); on exception record the ticket as FAILED (record_failure sidecar, needs-rework transition per lifecycle rules), move it to terminal, notify the condition variable, and continue. Consider a watchdog/timeout on cond.wait as defense in depth.

AC must cover: injected exception in code_fn results in ticket FAILED + sprint run completes with end_reason=ticket-failures (no hang); same for test_fn; same for pipeline mode; needs-rework label applied at point of failure.
---
Handle StageResult.RETRY_FREE in the concurrent scheduler — free-retry tickets are silently dropped as FAIL.

services/sprint_manager/concurrent_scheduler.py:~270: the result dispatch handles OK/RETRY/FAIL but not StageResult.RETRY_FREE, so a stage returning RETRY_FREE (infra failures that must not consume fix rounds — rate limit, hang redispatch) falls through and the ticket is treated as failed instead of re-queued without consuming its retry budget. The sequential and pipeline paths honor RETRY_FREE; only the concurrent scheduler drops it.

Fix: add the RETRY_FREE branch mirroring the sequential path — re-queue the ticket without decrementing the fix-round budget, preserving attempt bookkeeping and log messages. Add a unit test driving a stage that returns RETRY_FREE once then OK, asserting the ticket ends UAT-passed with the free retry not counted against COMMANDER_MAX_FIX_ROUNDS.

AC must cover: RETRY_FREE re-queues without consuming budget in concurrent mode; parity test comparing sequential vs concurrent handling for the same stage-result sequence.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| — | (not yet posted) | — |
